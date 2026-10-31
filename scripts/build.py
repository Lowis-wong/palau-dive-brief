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
    # Koror (land point) for sky/rain icons and sunrise/sunset in the multi-day outlook
    kq = urllib.parse.urlencode({
        "latitude": 7.34, "longitude": 134.48, "timezone": "Pacific/Palau", "forecast_days": 8, "past_days": 1,
        "hourly": "weather_code,precipitation,precipitation_probability,cloud_cover",
        "daily": "sunrise,sunset"})
    try:
        koror = json.loads(get("https://api.open-meteo.com/v1/forecast?" + kq))
    except Exception as ex:  # noqa: BLE001
        print("koror outlook failed:", ex)
        koror = None
    return {"zones": zones, "koror": koror}


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
    # daily (00-23 h) mean wave height: the measure the Step 2 lagoon analysis was done with
    day_idx = [i for i, t in enumerate(m["time"]) if t.startswith(key)]
    wv = [m["wave_height"][i] for i in day_idx if m.get("wave_height") and m["wave_height"][i] is not None]
    return {
        "zone": z["id"], "name": z["name"],
        "wave_max_m": mx(m.get("wave_height", []), idx_m),
        "wave_mean_m": round(sum(wv) / len(wv), 2) if len(wv) >= 18 else None,
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


def outlook(raw: dict, days: list[date]) -> dict:
    """Per day, four 6-hour blocks of sky/rain (Koror) and wind (west-north zone), plus sun times and
    max wave heights. For browsing the coming days only; not used in any prediction."""
    out: dict = {}
    k = raw.get("koror") or {}
    kh, kd = k.get("hourly") or {}, k.get("daily") or {}
    kidx = {t: i for i, t in enumerate(kh.get("time", []))}
    sun = {t: (kd["sunrise"][i][11:16], kd["sunset"][i][11:16]) for i, t in enumerate(kd.get("time", []))}
    zone = {z["id"]: z for z in raw["zones"]}
    wn = zone.get("west-north")
    fidx = {t: i for i, t in enumerate(wn["forecast"]["time"])} if wn else {}

    def vals(src, idx, key, times):
        arr = src.get(key) or []
        return [arr[idx[t]] for t in times if t in idx and idx[t] < len(arr) and arr[idx[t]] is not None]

    for d in days:
        key = d.isoformat()
        blocks = []
        for h0 in (0, 6, 12, 18):
            times = [f"{key}T{h:02d}:00" for h in range(h0, h0 + 6)]
            codes = vals(kh, kidx, "weather_code", times)
            rain = vals(kh, kidx, "precipitation", times)
            pop = vals(kh, kidx, "precipitation_probability", times)
            b = {"h": h0, "code": max(codes) if codes else None,
                 "rain_mm": round(sum(rain), 1) if rain else None, "pop": max(pop) if pop else None}
            if wn:
                f = wn["forecast"]
                spd = vals(f, fidx, "wind_speed_10m", times)
                gst = vals(f, fidx, "wind_gusts_10m", times)
                drs = vals(f, fidx, "wind_direction_10m", times)
                if spd:
                    import math
                    u = sum(math.sin(math.radians(x)) for x in drs)
                    v = sum(math.cos(math.radians(x)) for x in drs)
                    b.update({"wind_kn": round(sum(spd) / len(spd)), "gust_kn": round(max(gst)) if gst else None,
                              "wind_dir_deg": round(math.degrees(math.atan2(u, v)) % 360) if drs else None})
            blocks.append(b)
        waves = {}
        for zid in ("west-south", "west-north", "east"):
            z = zone.get(zid)
            if z:
                hs = [x for t, x in zip(z["marine"]["time"], z["marine"].get("wave_height", [])) if t.startswith(key) and x is not None]
                if hs:
                    waves[zid] = round(max(hs), 1)
        if any(b.get("code") is not None or b.get("wind_kn") is not None for b in blocks):
            out[key] = {"sunrise": sun.get(key, (None, None))[0], "sunset": sun.get(key, (None, None))[1],
                        "blocks": blocks, "wave_w": max([waves[z] for z in waves if z.startswith("west")], default=None),
                        "wave_e": waves.get("east")}
    return out


# ----------------------------------------------------------------------------- confidence
# West-swell pumping of the lagoon, from the Step 2 analysis (Malakal 1980-2026, ERA5 west point):
# mean set-up by DAILY MEAN west wave height: <2 m ~0, 2-2.5 m +0.7 cm, 2.5-3 m +1.7 cm, >=3 m +2.8 cm.
PUMP_LEVELS = [(3.0, "strong", 3, 2.8), (2.5, "moderate", 2, 1.7), (2.0, "weak", 1, 0.7)]


def swell_pump(zones: list[dict] | None) -> dict | None:
    if not zones:
        return None
    west = [z.get("wave_mean_m") for z in zones if z["zone"].startswith("west") and z.get("wave_mean_m") is not None]
    if not west:
        return None
    h = max(west)
    for lim, name, level, lift in PUMP_LEVELS:
        if h >= lim:
            return {"level": level, "label": name, "west_wave_mean_m": h, "typical_lift_cm": lift}
    return {"level": 0, "label": "none", "west_wave_mean_m": h, "typical_lift_cm": 0.0}


def assess_confidence(day: dict, zones: list[dict] | None) -> dict:
    """Score 1-10 for how far to trust the tide-only prediction today (rule-based until calibrated).

    Start at 10 and subtract:
      tide         ratio <0.7: -3 | <0.85: -2 | <1.15: -1 | spring: 0
      swell pump   daily mean west waves >=3 m: -3 | >=2.5 m: -2 | >=2 m: -1   (+1 more at neap)
    Pressure, wind and rain are not scored: Step 2 found pressure lifts lagoon and ocean together,
    and wind/rain had no measurable effect on lagoon level. Level: 8-10 high, 5-7 medium, 1-4 low."""
    ratio = day["tide_strength"]["ratio"]
    factors: list[str] = []
    breakdown: dict[str, int] = {}
    score = 10
    tide_pen = 3 if ratio < 0.7 else 2 if ratio < 0.85 else 1 if ratio < 1.15 else 0
    if tide_pen:
        factors.append("Neap" if ratio < 0.85 else "Mid tide")
        breakdown["tide"] = -tide_pen
        score -= tide_pen
    pump = swell_pump(zones)
    if pump is None:
        return {"score": max(1, score), "level": "unknown", "factors": factors + ["no weather"],
                "breakdown": breakdown, "tide_only": True}
    if pump["level"]:
        pen = pump["level"] + (1 if ratio < 0.85 else 0)
        factors.append(f"West waves {pump['west_wave_mean_m']:.1f} m")
        breakdown["swell_pump"] = -pen
        score -= pen
    score = max(1, min(10, score))
    level = "high" if score >= 8 else "medium" if score >= 5 else "low"
    return {"score": score, "level": level, "factors": factors, "breakdown": breakdown}


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
        try:
            weather["outlook"] = outlook(raw, days[:9])
        except Exception as ex:  # noqa: BLE001
            print("outlook failed:", ex)

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
        zs = (weather or {}).get("days", {}).get(d["date"])
        d["confidence"] = assess_confidence(d, zs)
        d["swell_pump"] = swell_pump(zs)

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
