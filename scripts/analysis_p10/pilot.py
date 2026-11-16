"""Project 10 pilot (gate 1).

Part A  ICESat-2 ATL24 inventory over the west reef box (SlideRule public service, no login).
Part B  Moored-boat test: can Sentinel-2 at ~10:35 find boats tied to the buoys at Blue Corner and German Channel?

Usage: python scripts/analysis_p10/pilot.py OUT_DIR
Outputs (OUT_DIR):
  atl24_points.csv.gz        every ATL24 bathymetry photon in the box, with tide level and MLLW depth
  atl24_summary.json         counts by confidence, pass, depth band, site; gate-1 check
  boats/<site>/detections.csv, hotspots.csv, hits.tif, bright_freq.tif, valid.tif, composite_tci.tif, chips.png
  boats_summary.json, summary.json
"""
from __future__ import annotations

import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "analysis_p3"))
from aoi import BOAT_BOXES, EPSG, SITES, WEST_LONLAT  # noqa: E402
from common import (EPOCH, SCL_BAD, dedupe, dn_offset, harmonics, load_wl, log, read_window,  # noqa: E402
                    search_items, write_tif, yearly_models)

SUMMARY: dict = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "errors": {}}


# ---------------------------------------------------------------- tide level / MLLW
def mllw_from_obs(wl: pd.Series, start="2007-01-01", end="2026-01-01") -> float:
    """Mean of daily lower-low water (Palau calendar days) over a ~19-year window, in the station datum."""
    s = wl[(wl.index >= start) & (wl.index < end)].dropna()
    local = s.copy()
    local.index = local.index + pd.Timedelta(hours=9)
    daily = local.groupby(local.index.date).agg(["min", "count"])
    daily = daily[daily["count"] >= 20]
    return float(daily["min"].mean())


def level_at(times: pd.DatetimeIndex, wl: pd.Series, models: dict) -> np.ndarray:
    """Observed hourly level linearly interpolated (gaps <= 3 h), else harmonic prediction."""
    obs = wl.dropna()
    t_obs = obs.index.values.astype("datetime64[s]").astype(np.int64)
    t = times.values.astype("datetime64[s]").astype(np.int64)
    out = np.interp(t, t_obs, obs.values, left=np.nan, right=np.nan)
    i = np.searchsorted(t_obs, t)
    i0 = np.clip(i - 1, 0, len(t_obs) - 1)
    i1 = np.clip(i, 0, len(t_obs) - 1)
    gap = (t_obs[i1] - t_obs[i0]) > 3 * 3600
    out[gap | (i == 0) | (i >= len(t_obs))] = np.nan
    src = np.where(np.isnan(out), "pred", "obs")
    for k in np.where(np.isnan(out))[0]:
        ts = pd.Timestamp(times[k]).tz_localize(None) if pd.Timestamp(times[k]).tzinfo else pd.Timestamp(times[k])
        m = models.get(ts.year) or models[max(models)]
        th = (ts.to_pydatetime() - EPOCH).total_seconds() / 3600.0
        out[k] = float(harmonics.predict(np.array([th]), m)[0])
    return out, src


# ---------------------------------------------------------------- Part A: ATL24
def part_a(out: Path, wl: pd.Series, models: dict, mllw: float):
    from pyproj import Transformer
    from sliderule import sliderule

    from sliderule import earthdata
    import geopandas as gpd

    sliderule.init(verbose=False)
    lon0, lat0, lon1, lat1 = WEST_LONLAT
    poly = [{"lon": lon0, "lat": lat0}, {"lon": lon1, "lat": lat0}, {"lon": lon1, "lat": lat1},
            {"lon": lon0, "lat": lat1}, {"lon": lon0, "lat": lat0}]
    granules = earthdata.ams(short_name="ATL24", polygon=poly)
    if isinstance(granules, dict):
        granules = list(granules)
    granules = sorted(set(granules or []))
    log("ATL24: granules", len(granules))
    stats = {"granules": len(granules), "ok": 0, "empty": 0, "failed": []}

    def one(res):
        for parms in ({"atl24": {"compact": False}}, {}):
            try:
                g = sliderule.run("atl24x", dict(parms, poly=poly), resources=[res])
                if g is None:
                    continue
                return res, g
            except Exception as e:  # noqa: BLE001
                err = str(e)[:150]
        return res, None

    parts = []
    with ThreadPoolExecutor(4) as ex:
        for res, g in ex.map(one, granules):
            if g is None:
                stats["failed"].append(res)
            elif len(g) == 0:
                stats["empty"] += 1
            else:
                stats["ok"] += 1
                g = g.copy()
                g["granule"] = res
                parts.append(g)
    log("ATL24: granule stats", {k: (v if not isinstance(v, list) else len(v)) for k, v in stats.items()})
    SUMMARY["atl24_granules"] = stats
    gdf = gpd.GeoDataFrame(pd.concat(parts)) if parts else gpd.GeoDataFrame()
    log("ATL24: rows", len(gdf), "columns", list(gdf.columns))
    if len(gdf) == 0:
        SUMMARY["atl24"] = {"n_rows": 0}
        return
    df = pd.DataFrame(gdf.drop(columns=[c for c in ("geometry", "time_ns") if c in gdf.columns]))
    df["lon"] = gdf.geometry.x.values
    df["lat"] = gdf.geometry.y.values
    if isinstance(gdf.index, pd.DatetimeIndex):
        idx = gdf.index
    elif "time_ns" in gdf.columns:
        idx = pd.to_datetime(gdf["time_ns"])
    else:
        idx = pd.to_datetime(gdf.index)
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    df["time_utc"] = idx
    df = df.reset_index(drop=True)
    if "class_ph" in df:
        df = df[df["class_ph"] == 40].copy()
    tr = Transformer.from_crs(4326, EPSG, always_xy=True)
    df["x"], df["y"] = tr.transform(df["lon"].values, df["lat"].values)
    df["depth_inst"] = df["surface_h"] - df["ortho_h"]
    lev, src = level_at(pd.DatetimeIndex(df["time_utc"]), wl, models)
    df["level_m"] = lev
    df["level_src"] = src
    df["depth_mllw"] = df["depth_inst"] - (df["level_m"] - mllw)
    df["pass_id"] = df["rgt"].astype(int).astype(str) + "_" + df["cycle"].astype(int).astype(str)
    df["beam_id"] = df["pass_id"] + "_" + df["spot"].astype(int).astype(str)
    keep = [c for c in ["time_utc", "lat", "lon", "x", "y", "ortho_h", "surface_h", "depth_inst", "level_m", "level_src",
                        "depth_mllw", "confidence", "rgt", "cycle", "spot", "gt", "pass_id", "beam_id", "night_flag",
                        "sigma_tvu", "sigma_thu", "kd", "surface_roughness", "quality_ph", "granule"] if c in df]
    df[keep].to_csv(out / "atl24_points.csv.gz", index=False, float_format="%.4f")

    s: dict = {"n_rows": int(len(df)), "mllw_station_datum_m": round(mllw, 3),
               "level_src": df["level_src"].value_counts().to_dict(),
               "time_range": [str(df["time_utc"].min()), str(df["time_utc"].max())]}
    for thr in (0.0, 0.5, 0.6, 0.7, 0.8):
        d = df[(df["confidence"] >= thr) & df["depth_mllw"].between(0.5, 25)]
        s[f"conf>={thr}"] = {
            "n": int(len(d)), "passes": int(d["pass_id"].nunique()), "beams": int(d["beam_id"].nunique()),
            "depth_bands": {f"{a}-{b}": int(d["depth_mllw"].between(a, b, inclusive="left").sum())
                            for a, b in [(0.5, 2), (2, 5), (5, 10), (10, 15), (15, 20), (20, 25)]},
            "night_n": int((d.get("night_flag", pd.Series(dtype=int)) == 1).sum()),
            "max_depth": round(float(d["depth_mllw"].max()), 2) if len(d) else None,
            "sites_within_1500m": {k: int((np.hypot(d["x"] - v["utm"][0], d["y"] - v["utm"][1]) < 1500).sum())
                                   for k, v in SITES.items()},
        }
    g = df[(df["confidence"] >= 0.6) & df["depth_mllw"].between(0.5, 25)]
    bands_ok = all(g["depth_mllw"].between(a, b, inclusive="left").sum() >= 30 for a, b in [(2, 6), (6, 12), (12, 18)])
    s["gate1"] = {"n": int(len(g)), "passes": int(g["pass_id"].nunique()), "bands_2_18_each_ge30": bool(bands_ok),
                  "pass": bool(len(g) >= 500 and g["pass_id"].nunique() >= 6 and bands_ok)}
    per_pass = g.groupby("pass_id").agg(n=("depth_mllw", "size"), dmin=("depth_mllw", "min"),
                                        dmax=("depth_mllw", "max"), t=("time_utc", "min"))
    per_pass.sort_values("t").to_csv(out / "atl24_passes.csv")
    SUMMARY["atl24"] = s
    log("ATL24 gate1", s["gate1"])


# ---------------------------------------------------------------- Part B: moored boats
UNION = (min(b[0] for b in BOAT_BOXES.values()), min(b[1] for b in BOAT_BOXES.values()),
         max(b[2] for b in BOAT_BOXES.values()), max(b[3] for b in BOAT_BOXES.values()))


def sub(arr, tr, bbox, res):
    c0 = int(round((bbox[0] - tr.c) / res)); c1 = int(round((bbox[2] - tr.c) / res))
    r0 = int(round((tr.f - bbox[3]) / res)); r1 = int(round((tr.f - bbox[1]) / res))
    return arr[..., r0:r1, c0:c1]


def read_tci(href, bbox):
    import rasterio
    from rasterio.windows import from_bounds
    with rasterio.open(href) as ds:
        w = from_bounds(*bbox, transform=ds.transform).round_offsets().round_lengths()
        return ds.read(window=w, boundless=True, fill_value=0), ds.window_transform(w)


def scan(item):
    try:
        scl, trs = read_window(item["assets"]["scl"], UNION)
        boxes = {}
        for k, b in BOAT_BOXES.items():
            s = sub(scl, trs, b, 20)
            clear = float((~np.isin(s, list(SCL_BAD))).mean()) if s.size else 0.0
            boxes[k] = clear
        if max(boxes.values()) < 0.9:
            return {"id": item["id"], "clear": boxes}
        nir, trn = read_window(item["assets"]["nir"], UNION)
        tci, trt = read_tci(item["assets"]["visual"], UNION)
        nir = nir.astype(np.int32) + dn_offset(item)
        res = {"id": item["id"], "date": item["datetime"][:10], "clear": boxes, "data": {}}
        for k, b in BOAT_BOXES.items():
            if boxes[k] < 0.9:
                continue
            s = np.repeat(np.repeat(sub(scl, trs, b, 20), 2, 0), 2, 1)
            res["data"][k] = (sub(nir, trn, b, 10), s, sub(tci, trt, b, 10))
        return res
    except Exception as e:  # noqa: BLE001
        return {"id": item["id"], "error": str(e)[:200]}


def part_b(out: Path):
    from rasterio.transform import Affine
    from scipy import ndimage

    items = [i for i in dedupe(search_items(2017)) if i["tile"] == "53NMH"]
    log("boats: scenes on 53NMH", len(items))
    acc = {k: {"hits": None, "bright": None, "valid": None, "det": [], "tci": [], "dates": []} for k in BOAT_BOXES}
    with ThreadPoolExecutor(8) as ex:
        for n, r in enumerate(ex.map(scan, items)):
            if n % 50 == 0:
                log("boats: scanned", n)
            if "error" in r:
                SUMMARY["errors"].setdefault("scan", []).append(r["error"])
                continue
            for k, (nir, scl, tci) in r.get("data", {}).items():
                a = acc[k]
                shp = nir.shape
                if a["hits"] is None:
                    a["hits"] = np.zeros(shp, np.int32); a["bright"] = np.zeros(shp, np.int32)
                    a["valid"] = np.zeros(shp, np.int32)
                sc = np.zeros(shp, scl.dtype)
                h, w = min(shp[0], scl.shape[0]), min(shp[1], scl.shape[1])
                sc[:h, :w] = scl[:h, :w]
                bad = np.isin(sc, list(SCL_BAD)) | (nir <= 0)
                ref = float(np.median(nir[~bad]))
                cand = (~bad) & (nir - ref > 250)
                a["valid"] += ~bad
                a["bright"] += cand
                lab, nl = ndimage.label(cand, structure=np.ones((3, 3)))
                if nl:
                    idx = np.arange(1, nl + 1)
                    size = ndimage.sum(np.ones_like(lab), lab, idx)
                    peak = ndimage.maximum(nir - ref, lab, idx)
                    com = ndimage.center_of_mass(np.clip(nir - ref, 0, None), lab, idx)
                    for j, sz in enumerate(size):
                        if 1 <= sz <= 8:
                            rr, cc = com[j]
                            a["det"].append({"scene": r["id"], "date": r["date"], "row": rr, "col": cc,
                                             "size": int(sz), "peak": float(peak[j])})
                    small = np.isin(lab, idx[(size >= 1) & (size <= 8)])
                    a["hits"] += small
                a["tci"].append(tci)
                a["dates"].append(r["date"])
    bs = {}
    for k, b in BOAT_BOXES.items():
        a = acc[k]
        d = out / "boats" / k
        d.mkdir(parents=True, exist_ok=True)
        if a["hits"] is None:
            bs[k] = {"clear_scenes": 0}
            continue
        tr = Affine(10, 0, b[0], 0, -10, b[3])
        nsc = len(a["tci"])
        freq = a["bright"] / np.maximum(a["valid"], 1)
        write_tif(d / "hits.tif", a["hits"], tr, dtype="int16")
        write_tif(d / "valid.tif", a["valid"], tr, dtype="int16")
        write_tif(d / "bright_freq.tif", freq.astype("float32"), tr)
        comp = np.median(np.stack(a["tci"]), axis=0).astype("uint8")
        write_tif(d / "composite_tci.tif", comp, tr, dtype="uint8")
        det = pd.DataFrame(a["det"])
        if len(det):
            det["x"] = b[0] + (det["col"] + 0.5) * 10
            det["y"] = b[3] - (det["row"] + 0.5) * 10
            r_i = det["row"].round().astype(int).clip(0, freq.shape[0] - 1)
            c_i = det["col"].round().astype(int).clip(0, freq.shape[1] - 1)
            det["bright_freq"] = freq[r_i, c_i]
        det.to_csv(d / "detections.csv", index=False, float_format="%.2f")
        # hotspots: smoothed density of small-blob centroids, excluding persistently bright pixels (reef crest, land)
        hs = []
        if len(det):
            dd = det[det["bright_freq"] < 0.3]
            grid = np.zeros_like(freq)
            np.add.at(grid, (dd["row"].round().astype(int).clip(0, grid.shape[0] - 1),
                             dd["col"].round().astype(int).clip(0, grid.shape[1] - 1)), 1)
            sm = ndimage.gaussian_filter(grid, 1.5) * (2 * np.pi * 1.5 ** 2)
            mx = ndimage.maximum_filter(sm, size=9)
            peaks = np.argwhere((sm == mx) & (sm >= 3))
            for pr, pc in peaks:
                near = dd[np.hypot(dd["row"] - pr, dd["col"] - pc) <= 3]
                if near["scene"].nunique() < 3:
                    continue
                hs.append({"row": float(near["row"].mean()), "col": float(near["col"].mean()),
                           "n_scenes": int(near["scene"].nunique()), "frac_of_clear": round(near["scene"].nunique() / nsc, 3),
                           "spread_m": round(float(np.hypot(near["row"].std(), near["col"].std()) * 10), 1),
                           "median_size_px": float(near["size"].median()), "median_peak": float(near["peak"].median()),
                           "dates": ";".join(sorted(near["date"].unique())[:20])})
        hsd = pd.DataFrame(hs)
        if len(hsd):
            from pyproj import Transformer
            hsd = hsd.sort_values("n_scenes", ascending=False).reset_index(drop=True)
            hsd["x"] = b[0] + (hsd["col"] + 0.5) * 10
            hsd["y"] = b[3] - (hsd["row"] + 0.5) * 10
            t = Transformer.from_crs(EPSG, 4326, always_xy=True)
            hsd["lon"], hsd["lat"] = t.transform(hsd["x"].values, hsd["y"].values)
        hsd.to_csv(d / "hotspots.csv", index=False, float_format="%.6f")
        chips(d / "chips.png", hsd.head(12), det, a, k)
        bs[k] = {"clear_scenes": nsc, "detections": int(len(det)), "hotspots": int(len(hsd)),
                 "top": hsd.head(8)[["lat", "lon", "n_scenes", "frac_of_clear", "spread_m"]].to_dict("records") if len(hsd) else []}
        log("boats", k, bs[k])
    SUMMARY["boats"] = bs


def chips(path, hs, det, a, k):
    """For each hotspot, show up to 6 scenes with a detection there (400 m chips)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if not len(hs):
        return
    ncol = 7
    fig, axs = plt.subplots(len(hs), ncol, figsize=(ncol * 1.8, len(hs) * 1.9))
    axs = np.atleast_2d(axs)
    date_idx = {dt: i for i, dt in enumerate(a["dates"])}
    comp = np.median(np.stack(a["tci"]), axis=0).astype("uint8")
    for i, h in hs.iterrows():
        r, c = int(round(h["row"])), int(round(h["col"]))
        sl = (slice(max(r - 20, 0), r + 20), slice(max(c - 20, 0), c + 20))
        axs[i, 0].imshow(np.moveaxis(comp[:, sl[0], sl[1]], 0, -1))
        axs[i, 0].set_title(f"#{i} median n={h['n_scenes']}", fontsize=7)
        near = det[np.hypot(det["row"] - h["row"], det["col"] - h["col"]) <= 3].drop_duplicates("date").head(ncol - 1)
        for j, (_, dr) in enumerate(near.iterrows(), start=1):
            img = a["tci"][date_idx[dr["date"]]]
            axs[i, j].imshow(np.moveaxis(img[:, sl[0], sl[1]], 0, -1))
            axs[i, j].set_title(dr["date"], fontsize=7)
        for ax in axs[i]:
            ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle(f"{k}: moored-boat hotspots (400 m chips)", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "analysis_out/project10")
    out.mkdir(parents=True, exist_ok=True)
    wl = load_wl()
    models = yearly_models(wl, range(2017, datetime.now(timezone.utc).year + 1))
    mllw = mllw_from_obs(wl)
    SUMMARY["mllw_station_datum_m"] = round(mllw, 3)
    log("MLLW (station datum)", round(mllw, 3))
    for name, fn in (("atl24", lambda: part_a(out, wl, models, mllw)), ("boats", lambda: part_b(out))):
        try:
            fn()
        except Exception:  # noqa: BLE001
            SUMMARY["errors"][name] = traceback.format_exc()[-2000:]
            log("FAILED", name, traceback.format_exc()[-800:])
    SUMMARY["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "summary.json").write_text(json.dumps(SUMMARY, indent=1, default=str))
    log("done")


if __name__ == "__main__":
    main()
