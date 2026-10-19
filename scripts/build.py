"""Build docs/data/brief.json for the Palau dive brief.

Runs on GitHub Actions (open network). Standard library only.

    python scripts/build.py                  # live fetch
    python scripts/build.py --fixtures DIR   # offline test using saved responses

Sources
  * NOAA CO-OPS tide predictions, Malakal Harbor 1841367 (hi/lo + 6-minute)
  * Open-Meteo Marine API (waves, swell) and Forecast API (wind, rain)
  * NWS Guam West Micronesia coastal waters forecast (zone PMZ161, Koror)
If a source fails, the previous values in brief.json are kept and flagged stale.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tides  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "brief.json"
SITES = ROOT / "data" / "sites.json"
PALAU = timezone(timedelta(hours=9))
STATION = "1841367"
DAYS_AHEAD = 35
UA = {"User-Agent": "palau-dive-brief (github actions)"}

WEATHER_ZONES = [
    # Offshore points so the ocean wave grid has data.
    {"id": "west-south", "name": "West reef (Blue Corner, German Ch.)", "lat": 7.13, "lon": 134.15},
    {"id": "west-north", "name": "West reef (Ulong, Siaes)", "lat": 7.30, "lon": 134.17},
    {"id": "east", "name": "East reef", "lat": 7.40, "lon": 134.75},
]
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def now_palau() -> datetime:
    return datetime.now(PALAU).replace(tzinfo=None)


def compass(deg) -> str | None:
    return None if deg is None else COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


# ----------------------------------------------------------------------------- fetch
def get(url: str, tries=3) -> bytes:
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def noaa(params: dict) -> dict:
    base = {"application": "palau_dive_brief", "station": STATION, "datum": "MLLW",
            "time_zone": "gmt", "units": "metric", "format": "json", "product": "predictions"}
    url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?" + urllib.parse.urlencode({**base, **params})
    data = json.loads(get(url))
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


def gmt_to_palau(s: str) -> str:
    return (datetime.strptime(s, "%Y-%m-%d %H:%M") + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")


def fetch_tides(start: date, end: date) -> tuple[list[dict], list[dict] | None]:
    b = (start - timedelta(days=1)).strftime("%Y%m%d")
    e = (end + timedelta(days=1)).strftime("%Y%m%d")
    hilo = noaa({"begin_date": b, "end_date": e, "interval": "hilo"})["predictions"]
    for r in hilo:
        r["t"] = gmt_to_palau(r["t"])
    series = []
    try:
        d = start - timedelta(days=1)
        while d <= end + timedelta(days=1):
            d2 = min(d + timedelta(days=9), end + timedelta(days=1))
            chunk = noaa({"begin_date": d.strftime("%Y%m%d"), "end_date": d2.strftime("%Y%m%d"), "interval": "6"})
            for r in chunk["predictions"]:
                series.append({"t": gmt_to_palau(r["t"]), "v": r["v"]})
            d = d2 + timedelta(days=1)
    except Exception as ex:  # noqa: BLE001
        print("6-min series failed, using hi/lo reconstruction:", ex)
        series = None
    return hilo, series


def fetch_weather() -> dict:
    zones = []
    for z in WEATHER_ZONES:
        mq = urllib.parse.urlencode({
            "latitude": z["lat"], "longitude": z["lon"], "timezone": "Pacific/Palau", "forecast_days": 7,
            "past_days": 1,
            "hourly": "wave_height,wave_direction,wave_period,swell_wave_height,swell_wave_direction,"
                      "swell_wave_period,wind_wave_height,sea_surface_temperature"})
        fq = urllib.parse.urlencode({
            "latitude": z["lat"], "longitude": z["lon"], "timezone": "Pacific/Palau", "forecast_days": 7,
            "past_days": 1, "wind_speed_unit": "kn",
            "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation"})
        marine = json.loads(get("https://marine-api.open-meteo.com/v1/marine?" + mq))["hourly"]
        fc = json.loads(get("https://api.open-meteo.com/v1/forecast?" + fq))["hourly"]
        zones.append({**z, "marine": marine, "forecast": fc})
    return {"zones": zones}


def nws_headlines(text: str) -> list[str]:
    """'...SMALL CRAFT ADVISORY IN EFFECT THROUGH TUESDAY AFTERNOON...' -> list of headlines."""
    import re
    out = []
    for m in re.finditer(r"\.\.\.([A-Z][A-Z0-9 ,'/\-]{6,}?)\.\.\.", text):
        h = " ".join(m.group(1).split()).capitalize()
        if h not in out:
            out.append(h)
    return out


def fetch_nws() -> str | None:
    raw = get("https://tgftp.nws.noaa.gov/data/raw/fz/fzpq52.pgum.cwf.pq2.txt").decode("utf-8", "replace")
    up = raw.upper()
    i = up.find("PMZ161")
    if i < 0:
        return None
    j = up.find("$$", i)
    return raw[i: j if j > 0 else i + 2500].strip()


# ----------------------------------------------------------------------------- weather summary
def summarize_zone(z: dict, day: date) -> dict | None:
    m, f = z["marine"], z["forecast"]
    key = day.isoformat()
    idx_m = [i for i, t in enumerate(m["time"]) if t.startswith(key) and 6 <= int(t[11:13]) <= 18]
    idx_f = [i for i, t in enumerate(f["time"]) if t.startswith(key) and 6 <= int(t[11:13]) <= 18]
    if not idx_m and not idx_f:
        return None

    def mx(arr, idx):
        vals = [arr[i] for i in idx if arr[i] is not None]
        return round(max(vals), 1) if vals else None

    def at_peak(arr, ref, idx):
        vals = [(ref[i], arr[i]) for i in idx if ref[i] is not None and arr[i] is not None]
        return max(vals)[1] if vals else None

    # rain in the 24 h before 06:00 of this day (lagoon outflow -> murky ebb)
    t6 = datetime.combine(day, datetime.min.time()) + timedelta(hours=6)
    rain_idx = [i for i, t in enumerate(f["time"])
                if t6 - timedelta(hours=24) <= datetime.strptime(t, "%Y-%m-%dT%H:%M") < t6]
    rain = [f["precipitation"][i] for i in rain_idx if f["precipitation"][i] is not None]
    return {
        "zone": z["id"], "name": z["name"],
        "wave_max_m": mx(m.get("wave_height", []), idx_m),
        "swell_max_m": mx(m.get("swell_wave_height", []), idx_m),
        "swell_dir_deg": at_peak(m.get("swell_wave_direction", []), m.get("swell_wave_height", []), idx_m),
        "wind_dir_deg": at_peak(f.get("wind_direction_10m", []), f.get("wind_speed_10m", []), idx_f),
        "swell_dir": compass(at_peak(m.get("swell_wave_direction", []), m.get("swell_wave_height", []), idx_m)),
        "swell_period_s": at_peak(m.get("swell_wave_period", []), m.get("swell_wave_height", []), idx_m),
        "wind_max_kn": mx(f.get("wind_speed_10m", []), idx_f),
        "gust_max_kn": mx(f.get("wind_gusts_10m", []), idx_f),
        "wind_dir": compass(at_peak(f.get("wind_direction_10m", []), f.get("wind_speed_10m", []), idx_f)),
        "rain_prev24_mm": round(sum(rain), 1) if rain_idx else None,
        "sst_c": mx(m.get("sea_surface_temperature", []), idx_m),
    }


HOURLY_FIELDS = ["wind_kn", "wind_dir", "gust_kn", "swell_m", "swell_dir", "swell_s", "wave_m"]


def hourly_table(zones: list[dict], days: list[date]) -> dict:
    """{date: {zone: [[wind, wind_dir, gust, swell, swell_dir, swell_s, wave] x 24 h]}} for dive-log snapshots."""
    out: dict = {"fields": HOURLY_FIELDS}
    for z in zones:
        m, f = z["marine"], z["forecast"]
        mi = {t: i for i, t in enumerate(m["time"])}
        fi = {t: i for i, t in enumerate(f["time"])}

        def val(src, idx, key, t, nd=1):
            i = idx.get(t)
            arr = src.get(key) or []
            v = arr[i] if i is not None and i < len(arr) else None
            return None if v is None else round(v, nd)

        for d in days:
            rows = []
            for h in range(24):
                t = f"{d.isoformat()}T{h:02d}:00"
                rows.append([val(f, fi, "wind_speed_10m", t), val(f, fi, "wind_direction_10m", t, 0),
                             val(f, fi, "wind_gusts_10m", t), val(m, mi, "swell_wave_height", t, 2),
                             val(m, mi, "swell_wave_direction", t, 0), val(m, mi, "swell_wave_period", t),
                             val(m, mi, "wave_height", t, 2)])
            if any(any(v is not None for v in r) for r in rows):
                out.setdefault(d.isoformat(), {})[z["id"]] = rows
    return out


# ----------------------------------------------------------------------------- confidence
def assess_confidence(day: dict, zones: list[dict] | None) -> dict:
    """How far to trust the tide-only prediction today.

    The tide drives channel flow through the lagoon/ocean level difference. Swell pumping
    water over the reef crest, wind on the surface layer and rain runoff all change that
    difference; they matter most when the tide itself is weak (neap)."""
    ratio = day["tide_strength"]["ratio"]
    reasons, factors = [], []
    if zones:
        west = [z for z in zones if z["zone"].startswith("west")]
        swell = max((z["swell_max_m"] or 0) for z in west) if west else 0
        wave = max((z["wave_max_m"] or 0) for z in west) if west else 0
        wind = max((z["wind_max_kn"] or 0) for z in west) if west else 0
        rain = max((z["rain_prev24_mm"] or 0) for z in zones)
        if wave >= 1.5 or swell >= 1.2:
            factors.append(f"W swell {swell} m" if swell >= 1.2 else f"W waves {wave} m")
            reasons.append(f"Waves {wave} m / swell {swell} m on the west reef can pump water over the crest "
                           "and out through the channels, weakening or reversing incoming flow.")
        if wind >= 15:
            factors.append(f"Wind {round(wind)} kn")
            reasons.append(f"Wind to {wind} kn drives the surface layer: surface and depth may flow differently.")
        if rain >= 20:
            factors.append(f"Rain {round(rain)} mm/24h")
            reasons.append(f"{rain} mm rain in 24 h raises the lagoon: stronger, longer outgoing; murky ebb.")
    if ratio <= 0.85:
        factors.insert(0, "Neap")
    if ratio <= 0.85 and reasons:
        reasons.insert(0, "Neap tide: the tidal push is weak, so the factors below can win.")
    if not zones:
        level = "unknown"
    elif ratio <= 0.85 and reasons:
        level = "low"
    elif reasons or ratio <= 0.85:
        level = "medium"
    else:
        level = "high"
    return {"level": level, "factors": factors, "reasons": reasons}


# ----------------------------------------------------------------------------- assemble
def build_day(curve: tides.TideCurve, ext: list[tides.Extreme], sites: list[dict], day: date,
              mean_max_range: float, low_cut: float) -> dict:
    d0 = datetime.combine(day, datetime.min.time())
    events = [{"time": e.t.strftime("%H:%M"), "kind": e.kind, "h": round(e.h, 2)}
              for e in ext if e.t.date() == day]
    ranges = tides.day_ranges(ext, day)
    ratio = (max(ranges) / mean_max_range) if ranges and mean_max_range else 1.0
    curve_pts = []
    for k in range(0, 24 * 6 + 1):
        t = d0 + timedelta(minutes=10 * k)
        h = curve.level(t)
        curve_pts.append(None if h is None else round(h, 3))

    # Malakal reference stages over the dive day
    ref = {"lag_min": 0, "strength_factor": 1.0}
    ref_segs = tides.site_segments(curve, ref, day)
    day_level = max((s["peak_level"] for s in ref_segs), default=None)

    site_out = []
    for s in sites:
        segs = tides.site_segments(curve, s, day)
        pref = s.get("preferred", "incoming")
        best = [g for g in segs if g["direction"] == pref]
        states = []  # every 10 min 06:00-18:00: "I2" = incoming level 2, "O3", "S1"
        for k in range(73):
            st = tides.site_state(curve, s, d0 + timedelta(hours=6, minutes=10 * k))
            states.append(st["direction"][0].upper() + str(st["level"]) if st else None)
        site_out.append({"id": s["id"], "segments": segs, "states": states,
                         "best": max(best, key=lambda g: (g["end"] > "08:00", g["peak_level"])) if best else None})

    flags = []
    for e in ext:
        if e.t.date() == day and e.kind == "L" and e.h <= low_cut and 6 <= e.t.hour <= 18:
            flags.append(f"Very low tide {e.t:%H:%M} ({e.h:.2f} m): shallow passes and reef crossings may be tight.")
    return {
        "date": day.isoformat(), "weekday": day.strftime("%a"),
        "moon": tides.moon(d0 + timedelta(hours=12)),
        "tide_strength": {"label": tides.tide_strength_label(ratio), "ratio": round(ratio, 2),
                          "max_range_m": round(max(ranges), 2) if ranges else None},
        "flow_level": day_level,
        "events": events, "curve_10min": curve_pts,
        "reference_segments": ref_segs,
        "sites": site_out, "flags": flags,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", type=Path, help="dir with hilo.json [series.json weather.json nws.txt]")
    ap.add_argument("--start", help="first day YYYY-MM-DD (default: today in Palau)")
    ap.add_argument("--days", type=int, default=DAYS_AHEAD)
    args = ap.parse_args()

    now = now_palau()
    # Keep yesterday so a dive logged the morning after still finds its tide/weather context.
    start = date.fromisoformat(args.start) if args.start else now.date() - timedelta(days=1)
    end = start + timedelta(days=args.days - 1)
    try:
        prev = json.loads(OUT.read_text()) if OUT.exists() else {}
    except (ValueError, OSError):
        prev = {}
    status = {}

    # tides
    if args.fixtures:
        hilo = json.loads((args.fixtures / "hilo.json").read_text())["predictions"]
        sp = args.fixtures / "series.json"
        series = json.loads(sp.read_text()) if sp.exists() else None
        status["tides"] = "fixture"
    else:
        try:
            hilo, series = fetch_tides(start, end)
            status["tides"] = "ok" if series else "ok (hi/lo reconstruction)"
        except Exception as ex:  # noqa: BLE001
            print("TIDES FAILED:", ex)
            if not prev:
                raise
            prev.setdefault("status", {})["tides"] = f"stale: {ex}"
            OUT.write_text(json.dumps(prev, separators=(",", ":")))
            return

    ext = tides.parse_hilo(hilo)
    ser = [(datetime.strptime(r["t"], "%Y-%m-%d %H:%M"), float(r["v"])) for r in series] if series else None
    curve = tides.TideCurve(ext, ser)
    last_day = min(end, (ext[-1].t - timedelta(hours=1)).date())

    # reference statistics across the window
    days = [start + timedelta(days=i) for i in range((last_day - start).days + 1)]
    maxes = [max(r) for r in (tides.day_ranges(ext, d) for d in days) if r]
    mean_max_range = statistics.mean(maxes) if maxes else 1.0
    lows = sorted(e.h for e in ext if e.kind == "L")
    low_cut = lows[max(0, int(len(lows) * 0.12) - 1)] if lows else -99

    site_cfg = json.loads(SITES.read_text())["sites"]
    out_days = [build_day(curve, ext, site_cfg, d, mean_max_range, low_cut) for d in days]

    # weather
    weather = prev.get("weather")
    if args.fixtures and (args.fixtures / "weather.json").exists():
        raw = json.loads((args.fixtures / "weather.json").read_text())
        status["weather"] = "fixture"
    elif args.fixtures:
        raw = None
        status["weather"] = "none"
    else:
        try:
            raw = fetch_weather()
            status["weather"] = "ok"
        except Exception as ex:  # noqa: BLE001
            print("WEATHER FAILED:", ex)
            raw = None
            status["weather"] = f"stale: {ex}"
    if raw:
        wdays = {}
        for d in days[:8]:
            zs = [s for s in (summarize_zone(z, d) for z in raw["zones"]) if s]
            if zs:
                wdays[d.isoformat()] = zs
        weather = {"generated_at": now.strftime("%Y-%m-%d %H:%M"), "days": wdays,
                   "hourly": hourly_table(raw["zones"], days[:8])}

    # NWS coastal waters text
    nws = prev.get("nws")
    if not args.fixtures:
        try:
            txt = fetch_nws()
            if txt:
                nws = {"fetched_at": now.strftime("%Y-%m-%d %H:%M"), "text": txt, "headlines": nws_headlines(txt)}
            status["nws"] = "ok" if txt else "zone not found"
        except Exception as ex:  # noqa: BLE001
            print("NWS FAILED:", ex)
            status["nws"] = f"stale: {ex}"
    elif (args.fixtures / "nws.txt").exists():
        t = (args.fixtures / "nws.txt").read_text()
        nws = {"fetched_at": now.strftime("%Y-%m-%d %H:%M"), "text": t, "headlines": nws_headlines(t)}

    for d in out_days:
        d["confidence"] = assess_confidence(d, (weather or {}).get("days", {}).get(d["date"]))

    site_meta = [{k: s.get(k) for k in ("id", "name", "type", "exposure", "zone", "lag_min", "strength_factor", "incoming", "outgoing",
                                     "slack", "hazards", "confidence", "n_logs")} for s in site_cfg]
    brief = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M"), "timezone": "Palau (UTC+9)",
        "station": {"name": "Malakal Harbor", "id": STATION, "datum": "MLLW"},
        "status": status, "sites": site_meta,
        "days": out_days, "weather": weather, "nws": nws,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(brief, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB), {len(out_days)} days, status={status}")


if __name__ == "__main__":
    main()
