"""Step 1 — data acquisition and quality control for the lagoon set-up study.

Runs on GitHub Actions (open network). Writes everything to OUT (default analysis_out/step1):
  wl_sources_hourly.csv.gz   UHSLC hourly by source (rq_a, rq_b, fast) + IOC hourly, UTC, metres
  wl_hourly.csv.gz           merged hourly water level, tide fit, residuals
  tide_fits.json             per-year constituent amplitudes/phases and fit stats
  forcing_hourly.csv.gz      ERA5 waves (W/E/S points) + ERA5 weather (Koror, west), UTC
  noaa_check.csv.gz          NOAA hourly predictions vs our tide for one month
  summary.json               coverage, offsets, checks, errors
  log.txt                    full log
Each source is fetched independently; a failure is logged and the rest continues.

    python scripts/analysis/step1.py [--start 1980] [--out analysis_out/step1]
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import harmonics as H  # noqa: E402

UA = {"User-Agent": "palau-dive-brief-analysis (github actions; contact via github.com/Lowis-wong)"}
LOG: list[str] = []
SUMMARY: dict = {"errors": {}, "sources": {}, "checks": {}}

WAVE_POINTS = {"W": (7.5, 134.0), "E": (7.5, 135.0), "S": (6.5, 134.5)}
WX_POINTS = {"koror": (7.34, 134.48), "west": (7.5, 134.0)}
WAVE_VARS = ["wave_height", "wave_direction", "wave_period", "wave_peak_period",
             "swell_wave_height", "swell_wave_direction", "swell_wave_period",
             "wind_wave_height", "wind_wave_direction", "wind_wave_period"]
WX_VARS = ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "pressure_msl", "precipitation"]


def log(*a):
    line = f"[{datetime.now(timezone.utc):%H:%M:%S}] " + " ".join(str(x) for x in a)
    print(line, flush=True)
    LOG.append(line)


def get(url: str, tries=3, timeout=120) -> bytes:
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            body = ""
            if hasattr(e, "read"):
                try:
                    body = e.read()[:300].decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    pass
            log(f"  retry {i + 1} {url[:140]} -> {e} {body}")
            last = f"{e} {body}"
            if getattr(e, "code", None) == 400:
                break
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"{url[:160]}: {last}")


def guarded(name):
    def deco(fn):
        def run(*a, **k):
            log(f"== {name}")
            try:
                return fn(*a, **k)
            except Exception as e:  # noqa: BLE001
                SUMMARY["errors"][name] = f"{type(e).__name__}: {e}"
                log(f"!! {name} failed: {e}\n{traceback.format_exc()}")
                return None
        return run
    return deco


# ============================================================================ UHSLC
def parse_uhslc_csv(raw: bytes) -> pd.Series:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", "replace")
    log("  head:", text[:120].replace("\n", " | "))
    df = pd.read_csv(io.StringIO(text), header=None)
    if df.shape[1] < 5:
        raise ValueError(f"unexpected UHSLC columns: {df.shape}")
    df = df.iloc[:, :5]
    df.columns = ["y", "m", "d", "h", "mm"]
    t = pd.to_datetime(dict(year=df.y, month=df.m, day=df.d)) + pd.to_timedelta(df.h, unit="h")
    v = df.mm.astype(float)
    v[(v <= -32000) | (v > 30000)] = np.nan
    s = pd.Series(v.values / 1000.0, index=pd.DatetimeIndex(t, name="time_utc")).sort_index()
    return s[~s.index.duplicated()]


def fetch_erddap(dataset: str, y0: int, y1: int) -> pd.Series:
    parts = []
    for ya in range(y0, y1 + 1, 10):
        yb = min(ya + 9, y1)
        q = (f"time,sea_level,record_id,quality&ssc_id=%22mala%22"
             f"&time%3E={ya}-01-01T00:00:00Z&time%3C={yb + 1}-01-01T00:00:00Z")
        url = f"https://uhslc.soest.hawaii.edu/erddap/tabledap/{dataset}.csv?{q}"
        t = time.time()
        try:
            raw = get(url, tries=2, timeout=240)
        except Exception as e:  # noqa: BLE001
            if "404" in str(e) or "No data" in str(e) or "nRows = 0" in str(e):
                log(f"  {dataset} {ya}-{yb}: no data")
                continue
            raise
        df = pd.read_csv(io.BytesIO(raw), skiprows=[1])  # second row holds units
        log(f"  {dataset} {ya}-{yb}: {len(df)} rows in {time.time() - t:.0f}s; records {sorted(df.record_id.unique())[:5]}")
        parts.append(df)
    if not parts:
        raise RuntimeError(f"{dataset}: no rows")
    df = pd.concat(parts, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    v = pd.to_numeric(df["sea_level"], errors="coerce")
    v[(v <= -32000)] = np.nan
    if v.median() > 50:  # millimetres
        v = v / 1000.0
    SUMMARY["sources"][f"erddap_{dataset}_records"] = {str(k): int(n) for k, n in df.record_id.value_counts().items()}
    s = pd.Series(v.values, index=pd.DatetimeIndex(df["time"], name="time_utc")).sort_index()
    return s[~s.index.duplicated(keep="last")]


@guarded("uhslc")
def fetch_uhslc(start_year: int) -> dict[str, pd.Series]:
    out = {}
    now_y = datetime.now(timezone.utc).year
    for key, ds in (("rq", "global_hourly_rqds"), ("fast", "global_hourly_fast")):
        t = time.time()
        try:
            s = fetch_erddap(ds, start_year if key == "rq" else max(start_year, now_y - 12), now_y)
            out[key] = s
            SUMMARY["sources"][f"uhslc_{key}"] = {
                "via": "erddap", "first": str(s.first_valid_index()), "last": str(s.last_valid_index()),
                "n_valid": int(s.notna().sum()), "mean_m": round(float(s.mean()), 3), "secs": round(time.time() - t)}
            log(f"  {key}: {s.first_valid_index()} .. {s.last_valid_index()} valid={s.notna().sum()}")
        except Exception as e:  # noqa: BLE001
            SUMMARY["errors"][f"uhslc_erddap_{key}"] = str(e)[:400]
            log(f"  erddap {key} failed after {time.time() - t:.0f}s: {e}")
    if "rq" not in out:  # fallback: per-version CSV files
        for k, u in {"rq_a": "https://uhslc.soest.hawaii.edu/data/csv/rqds/pacific/hourly/h007a.csv",
                     "rq_b": "https://uhslc.soest.hawaii.edu/data/csv/rqds/pacific/hourly/h007b.csv"}.items():
            t = time.time()
            try:
                s = parse_uhslc_csv(get(u, tries=2, timeout=180))
                out[k] = s
                SUMMARY["sources"][f"uhslc_{k}"] = {"via": "csv", "first": str(s.first_valid_index()),
                                                    "last": str(s.last_valid_index()), "n_valid": int(s.notna().sum())}
            except Exception as e:  # noqa: BLE001
                SUMMARY["errors"][f"uhslc_csv_{k}"] = str(e)[:400]
                log(f"  csv {k} failed after {time.time() - t:.0f}s: {e}")
    return out


# ============================================================================ IOC
@guarded("ioc")
def fetch_ioc(days: int = 120) -> pd.DataFrame | None:
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    start = end - timedelta(days=days)
    frames = []
    t0 = start
    while t0 < end:
        t1 = min(t0 + timedelta(days=7), end)
        q = urllib.parse.urlencode({"query": "data", "code": "mala", "timestart": t0.strftime("%Y-%m-%dT%H:%M"),
                                    "timestop": t1.strftime("%Y-%m-%dT%H:%M"), "format": "json"})
        try:
            rows = json.loads(get("https://www.ioc-sealevelmonitoring.org/service.php?" + q))
            if isinstance(rows, list) and rows:
                frames.append(pd.DataFrame(rows))
            log(f"  {t0:%Y-%m-%d}: {len(rows) if isinstance(rows, list) else rows}")
        except Exception as e:  # noqa: BLE001
            log(f"  {t0:%Y-%m-%d} failed: {e}")
        t0 = t1
        time.sleep(1)
    if not frames:
        raise RuntimeError("no IOC data")
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["stime"].str.strip())
    df["slevel"] = pd.to_numeric(df["slevel"], errors="coerce")
    wide = df.pivot_table(index="time", columns="sensor", values="slevel", aggfunc="mean").sort_index()
    wide = wide[[c for c in wide.columns if c in ("rad", "prs", "ra2", "ra3", "flt", "enc", "pr2")]]
    SUMMARY["sources"]["ioc"] = {"first": str(wide.index.min()), "last": str(wide.index.max()),
                                 "sensors": {c: int(wide[c].notna().sum()) for c in wide.columns}}
    return wide


def ioc_hourly(wide: pd.DataFrame) -> pd.DataFrame:
    """Robust hourly value per sensor: median of 1-min samples, with spike removal, needs >= 40 samples."""
    out = {}
    for c in wide.columns:
        s = wide[c].dropna()
        med = s.rolling("31min", center=True).median()
        s = s[(s - med).abs() < 0.15]
        step_min = max(1.0, float(np.median(np.diff(s.index.values).astype("timedelta64[s]").astype(float))) / 60)
        g = s.resample("1h")
        h = g.median()
        h[g.count() < (60 / step_min) * 0.66] = np.nan
        out[c] = h
    return pd.DataFrame(out)


# ============================================================================ NOAA
@guarded("noaa_predictions")
def fetch_noaa_hourly(begin: datetime, end: datetime) -> pd.Series:
    q = urllib.parse.urlencode({
        "product": "predictions", "application": "palau_dive_brief", "station": "1841367",
        "begin_date": begin.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"),
        "datum": "MLLW", "time_zone": "gmt", "units": "metric", "interval": "h", "format": "json"})
    d = json.loads(get("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?" + q))
    if "predictions" not in d:
        raise RuntimeError(d)
    s = pd.Series([float(r["v"]) for r in d["predictions"]],
                  index=pd.to_datetime([r["t"] for r in d["predictions"]]), name="noaa")
    return s


# ============================================================================ Open-Meteo
def om_json(url: str) -> dict:
    d = json.loads(get(url, timeout=180))
    if d.get("error"):
        raise RuntimeError(d.get("reason"))
    return d


def om_frame(d: dict, prefix: str) -> pd.DataFrame:
    hr = d["hourly"]
    idx = pd.to_datetime(hr.pop("time"))
    df = pd.DataFrame(hr, index=idx)
    df.columns = [f"{prefix}_{c}" for c in df.columns]
    return df


@guarded("era5_waves")
def fetch_waves(start_year: int) -> pd.DataFrame:
    end = (datetime.now(timezone.utc) - timedelta(days=7)).date()
    frames = []
    variant = None
    variables = list(WAVE_VARS)
    for name, (lat, lon) in WAVE_POINTS.items():
        parts = []
        for y in range(start_year, end.year + 1):
            s, e = f"{y}-01-01", (f"{y}-12-31" if y < end.year else end.isoformat())
            base = {"latitude": lat, "longitude": lon, "start_date": s, "end_date": e, "timezone": "GMT"}
            tried = [variant] if variant else [{"models": "era5_ocean"}, {}]
            d = None
            for v in tried:
                for attempt in range(3):
                    try:
                        d = om_json("https://marine-api.open-meteo.com/v1/marine?" +
                                    urllib.parse.urlencode({**base, **v, "hourly": ",".join(variables)}))
                        variant = v
                        break
                    except Exception as ex:  # noqa: BLE001
                        msg = str(ex)
                        bad = [w for w in variables if w in msg]
                        if bad and len(variables) > 3:
                            log(f"  dropping variables {bad}")
                            variables = [w for w in variables if w not in bad]
                            continue
                        log(f"  waves {name} {y} variant {v}: {ex}")
                        break
                if d:
                    break
            if d:
                parts.append(om_frame(d, f"wave{name}"))
                if y == start_year:
                    log(f"  {name}: grid {d.get('latitude')},{d.get('longitude')} variant={variant}")
            time.sleep(0.6)
        if parts:
            frames.append(pd.concat(parts))
    if not frames:
        raise RuntimeError("no wave data")
    df = pd.concat(frames, axis=1).sort_index()
    df.index.name = "time_utc"
    SUMMARY["sources"]["era5_waves"] = {"variant": variant, "variables": variables, "points": WAVE_POINTS,
                                        "first": str(df.index.min()), "last": str(df.index.max())}
    return df


@guarded("era5_weather")
def fetch_weather(start_year: int) -> pd.DataFrame:
    end = (datetime.now(timezone.utc) - timedelta(days=6)).date()
    frames = []
    for name, (lat, lon) in WX_POINTS.items():
        parts = []
        for y0 in range(start_year, end.year + 1, 5):
            y1 = min(y0 + 4, end.year)
            e = f"{y1}-12-31" if y1 < end.year else end.isoformat()
            q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "start_date": f"{y0}-01-01", "end_date": e,
                                        "hourly": ",".join(WX_VARS), "wind_speed_unit": "kn", "timezone": "GMT",
                                        "models": "era5"})
            try:
                parts.append(om_frame(om_json("https://archive-api.open-meteo.com/v1/archive?" + q), f"wx{name}"))
            except Exception as ex:  # noqa: BLE001
                log(f"  weather {name} {y0}-{y1}: {ex}")
            time.sleep(1)
        if parts:
            frames.append(pd.concat(parts))
    if not frames:
        raise RuntimeError("no weather data")
    df = pd.concat(frames, axis=1).sort_index()
    df.index.name = "time_utc"
    SUMMARY["sources"]["era5_weather"] = {"points": WX_POINTS, "first": str(df.index.min()), "last": str(df.index.max())}
    return df


# ============================================================================ processing
def merge_water_level(uh: dict[str, pd.Series], ioc_h: pd.DataFrame | None) -> tuple[pd.Series, pd.DataFrame]:
    """Priority rq_b > rq_a > fast > IOC. Each source is shifted onto the reference datum by the
    median difference in its overlap with the reference (logged in summary)."""
    src = pd.DataFrame({k: uh[k] for k in ("rq", "rq_a", "rq_b", "fast") if uh and k in uh})
    offsets: dict = {}
    adj: dict[str, pd.Series] = {}
    ref = next((k for k in ("rq", "rq_b", "rq_a", "fast") if k in src), None)
    if ref:
        adj[ref] = src[ref]
        for k in src.columns:
            if k == ref:
                continue
            both = src[[k, ref]].dropna()
            off = 0.0
            if len(both) > 24 * 7:
                d = both[k] - both[ref]
                off = float(d.median())
                offsets[k] = {"vs": ref, "median_diff_m": round(off, 4), "rms_diff_m": round(float(d.std()), 4),
                              "n": len(both)}
            else:
                offsets[k] = {"vs": ref, "overlap_h": len(both), "note": "no usable overlap, no shift"}
            adj[k] = src[k] - off
    base = None
    for k in ("fast", "rq_a", "rq_b", "rq"):
        if k in adj:
            base = adj[k] if base is None else adj[k].combine_first(base)

    ioc_choice = None
    if ioc_h is not None and len(ioc_h.columns):
        best = None
        for c in ioc_h.columns:
            if base is None:
                break
            both = pd.concat([ioc_h[c], base], axis=1, keys=["ioc", "ref"]).dropna()
            if len(both) < 48:
                offsets[f"ioc_{c}"] = {"overlap_h": len(both)}
                continue
            r = both["ioc"].corr(both["ref"])
            diff = both["ioc"] - both["ref"]
            stats = {"n": len(both), "corr": round(float(r), 4), "offset_m": round(float(diff.median()), 4),
                     "rms_after_offset_m": round(float((diff - diff.median()).std()), 4)}
            offsets[f"ioc_{c}"] = stats
            if r > 0.95 and (best is None or stats["rms_after_offset_m"] < best[1]["rms_after_offset_m"]):
                best = (c, stats)
        if best:
            ioc_choice = best[0]
            adj["ioc"] = ioc_h[ioc_choice] - best[1]["offset_m"]
            src["ioc"] = adj["ioc"]
        for c in ioc_h.columns:
            src[f"ioc_raw_{c}"] = ioc_h[c]
    SUMMARY["checks"]["source_offsets"] = offsets
    SUMMARY["checks"]["ioc_sensor_used"] = ioc_choice

    merged = None
    used = pd.Series(dtype=object)
    for k in ("ioc", "fast", "rq_a", "rq_b", "rq"):  # later wins
        if k in adj:
            v = adj[k].dropna()
            merged = v if merged is None else v.combine_first(merged)
            used = pd.Series(k, index=v.index).combine_first(used) if len(used) else pd.Series(k, index=v.index)
    if merged is None:
        raise RuntimeError("no water level data")
    full = pd.date_range(merged.index.min().floor("h"), merged.index.max().ceil("h"), freq="1h", name="time_utc")
    merged = merged.reindex(full)
    src = src.reindex(full)
    src["source_used"] = used.reindex(full)
    return merged, src


def despike(s: pd.Series, k: float = 6.0) -> tuple[pd.Series, int]:
    """Remove isolated spikes relative to a 5-hour running median of the de-tided signal is done later;
    here only gross outliers vs a 25-hour running median."""
    med = s.rolling(25, center=True, min_periods=12).median()
    dev = (s - med).abs()
    mad = dev.rolling(24 * 30, center=True, min_periods=24 * 5).median()
    bad = dev > np.maximum(k * 1.4826 * mad, 1.5)
    return s.mask(bad), int(bad.sum())


def tide_fit(wl: pd.Series) -> tuple[pd.DataFrame, dict]:
    t0 = pd.Timestamp("2000-01-01")
    th = ((wl.index - t0) / pd.Timedelta(hours=1)).to_numpy(dtype=float)
    tide = np.full(len(wl), np.nan)
    fits = {}
    for y in sorted(set(wl.index.year)):
        m = wl.index.year == y
        yv = wl[m]
        n_ok = int(yv.notna().sum())
        cov = n_ok / m.sum()
        if n_ok < 24 * 60:  # need two months minimum
            fits[str(y)] = {"coverage": round(cov, 3), "fitted": False}
            continue
        ok_idx = np.where(yv.notna())[0]
        span = float(ok_idx[-1] - ok_idx[0] + 1)
        names = H.select_constituents(span, include_long_period=cov >= 0.9)
        model = H.fit(th[m], yv.to_numpy(), names)
        pred = H.predict(th[m], model)
        tide[m] = pred
        res = yv.to_numpy() - pred
        fits[str(y)] = {
            "coverage": round(cov, 3), "fitted": True, "n_constituents": len(names),
            "long_period_included": cov >= 0.9, "mean_m": round(float(model["coef"][0]), 4),
            "resid_std_m": round(float(np.nanstd(res)), 4),
            "amps": {k: [round(a, 4), round(p, 1)] for k, (a, p) in model["amps"].items()
                     if k in ("M2", "S2", "N2", "K2", "K1", "O1", "P1", "Q1", "M4", "SA", "SSA")},
        }
    df = pd.DataFrame({"wl_m": wl, "tide_m": tide})
    df["resid_m"] = df.wl_m - df.tide_m
    df["resid_30d_m"] = df.resid_m.rolling(24 * 30, center=True, min_periods=24 * 10).mean()
    df["resid_short_m"] = df.resid_m - df.resid_30d_m
    return df, fits


# ============================================================================ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1980)
    ap.add_argument("--out", type=Path, default=Path("analysis_out/step1"))
    ap.add_argument("--skip-forcing", action="store_true")
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    SUMMARY["run_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    SUMMARY["start_year"] = args.start
    T0 = time.time()
    try:
        run(args, out)
    except Exception as e:  # noqa: BLE001
        SUMMARY["errors"]["main"] = f"{type(e).__name__}: {e}"
        log(f"!! main failed: {e}\n{traceback.format_exc()}")
    finally:
        SUMMARY["runtime_s"] = round(time.time() - T0)
        (out / "summary.json").write_text(json.dumps(SUMMARY, indent=1, default=str))
        (out / "log.txt").write_text("\n".join(LOG))
        log("done")


def run(args, out: Path) -> None:
    uh = fetch_uhslc(args.start) or {}
    for k, v in uh.items():
        v.to_frame("sl_m").to_csv(out / f"uhslc_{k}_hourly.csv.gz", float_format="%.4f")
    ioc_wide = fetch_ioc()
    ioc_h = ioc_hourly(ioc_wide) if ioc_wide is not None else None
    if ioc_wide is not None:
        ioc_wide.resample("5min").median().to_csv(out / "ioc_recent_5min.csv.gz", float_format="%.4f")

    if uh or ioc_h is not None:
        wl, src = merge_water_level(uh, ioc_h)
        wl = wl[wl.index >= pd.Timestamp(f"{args.start}-01-01")]
        src = src[src.index >= pd.Timestamp(f"{args.start}-01-01")]
        src.to_csv(out / "wl_sources_hourly.csv.gz", float_format="%.4f")
        wl, n_spikes = despike(wl)
        SUMMARY["checks"]["spikes_removed"] = n_spikes
        df, fits = tide_fit(wl)
        df.to_csv(out / "wl_hourly.csv.gz", float_format="%.4f")
        (out / "tide_fits.json").write_text(json.dumps(fits, indent=1))
        SUMMARY["checks"]["coverage_by_year"] = {y: f["coverage"] for y, f in fits.items()}
        SUMMARY["checks"]["resid_std_m_by_year"] = {y: f.get("resid_std_m") for y, f in fits.items() if f.get("fitted")}
        try:
            noaa_check(df, out)
        except Exception as e:  # noqa: BLE001
            SUMMARY["errors"]["noaa_check"] = str(e)
        (out / "summary.json").write_text(json.dumps(SUMMARY, indent=1, default=str))

    if not args.skip_forcing:
        waves = fetch_waves(args.start)
        wx = fetch_weather(args.start)
        parts = [p for p in (waves, wx) if p is not None]
        if parts:
            f = pd.concat(parts, axis=1).sort_index()
            f.index.name = "time_utc"
            f.to_csv(out / "forcing_hourly.csv.gz", float_format="%.3f")
            SUMMARY["sources"]["forcing_columns"] = list(f.columns)


def noaa_check(df: pd.DataFrame, out: Path) -> None:
    """NOAA hourly predictions vs our tide fit, most recent full month with observations."""
    last = df.wl_m.last_valid_index()
    if last is None:
        return
    mstart = (last - pd.DateOffset(months=1)).replace(day=1, hour=0)
    mend = mstart + pd.DateOffset(months=1) - pd.Timedelta(hours=1)
    noaa = fetch_noaa_hourly(mstart.to_pydatetime(), mend.to_pydatetime())
    if noaa is None:
        return
    cmp_ = pd.concat([noaa, df.tide_m, df.wl_m], axis=1).loc[mstart:mend].dropna()
    a = cmp_["noaa"] - cmp_["noaa"].mean()
    b = cmp_["tide_m"] - cmp_["tide_m"].mean()
    fine = b.resample("6min").interpolate()
    best = None
    for L in range(-90, 91, 6):
        sh = fine.shift(freq=f"{L}min").reindex(a.index)
        rms = float((a - sh).std())
        if best is None or rms < best[1]:
            best = (L, rms)
    cmp_.to_csv(out / "noaa_check.csv.gz", float_format="%.4f")
    SUMMARY["checks"]["noaa_vs_fit"] = {
        "month": str(mstart.date()), "n": len(cmp_), "rms_diff_m": round(float((a - b).std()), 4),
        "corr": round(float(a.corr(b)), 5), "best_shift_min": best[0], "rms_at_best_shift_m": round(best[1], 4),
        "noaa_minus_obs_mean_m": round(float((cmp_["noaa"] - cmp_["wl_m"]).mean()), 4)}


if __name__ == "__main__":
    main()
