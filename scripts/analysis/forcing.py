"""Resumable download of ERA5 forcing (waves + weather) from Open-Meteo, one file per point-year.

Open-Meteo weights each call by days x variables, so long hourly archives hit the
per-minute / per-hour limits. This script:
  * skips point-years already present in OUT (except the current year),
  * paces requests and backs off on HTTP 429,
  * stops cleanly before --budget-min so the workflow can publish what it has;
    the next run continues where it stopped.

    python scripts/analysis/forcing.py --out DIR [--start 1980] [--budget-min 70]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

UA = {"User-Agent": "palau-dive-brief-analysis (github actions)"}
WAVE_POINTS = {"W": (7.5, 134.0), "E": (7.5, 135.0), "S": (6.5, 134.5)}
WX_POINTS = {"koror": (7.34, 134.48)}
WAVE_VARS = ["wave_height", "wave_direction", "wave_period", "swell_wave_height", "swell_wave_direction",
             "swell_wave_period", "wind_wave_height"]
WX_VARS = ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "pressure_msl", "precipitation"]
LOG: list[str] = []


def log(*a):
    line = f"[{datetime.now(timezone.utc):%H:%M:%S}] " + " ".join(str(x) for x in a)
    print(line, flush=True)
    LOG.append(line)


class Limited(Exception):
    pass


def fetch(url: str) -> dict:
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
                d = json.loads(r.read())
            if d.get("error"):
                raise RuntimeError(d.get("reason"))
            return d
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            if e.code == 429:
                wait = 65 if "inute" in body else 0
                log(f"  429: {body.strip()[:160]}")
                if not wait:  # hourly/daily limit: stop this run
                    raise Limited(body)
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {body}")
        except (TimeoutError, urllib.error.URLError) as e:
            log(f"  network: {e}")
            time.sleep(10 * (i + 1))
    raise RuntimeError("gave up after retries")


def to_frame(d: dict) -> pd.DataFrame:
    hr = d["hourly"]
    idx = pd.to_datetime(hr.pop("time"))
    return pd.DataFrame(hr, index=pd.DatetimeIndex(idx, name="time_utc"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--start", type=int, default=1980)
    ap.add_argument("--budget-min", type=float, default=70)
    args = ap.parse_args()
    out = args.out / "forcing"
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    now = datetime.now(timezone.utc)
    end_wave = (now - timedelta(days=7)).date()
    end_wx = (now - timedelta(days=6)).date()
    jobs = []
    for y in range(now.year, args.start - 1, -1):  # newest first: recent years matter most
        for p, (lat, lon) in WAVE_POINTS.items():
            jobs.append(("wave", p, lat, lon, y))
        for p, (lat, lon) in WX_POINTS.items():
            jobs.append(("wx", p, lat, lon, y))
    done = skipped = 0
    variant = {"models": "era5_ocean"}
    status = "complete"
    for kind, p, lat, lon, y in jobs:
        f = out / f"{kind}_{p}_{y}.csv.gz"
        if f.exists() and y < now.year:
            skipped += 1
            continue
        if (time.time() - t0) / 60 > args.budget_min:
            status = "budget"
            break
        end = end_wave if kind == "wave" else end_wx
        s, e = f"{y}-01-01", (f"{y}-12-31" if y < end.year else end.isoformat())
        try:
            if kind == "wave":
                q = {"latitude": lat, "longitude": lon, "start_date": s, "end_date": e, "timezone": "GMT",
                     "hourly": ",".join(WAVE_VARS)}
                try:
                    d = fetch("https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode({**q, **variant}))
                except RuntimeError as ex:
                    if variant and "400" in str(ex):
                        log(f"  models=era5_ocean rejected ({ex}); retrying without models")
                        variant = {}
                        d = fetch("https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode(q))
                    else:
                        raise
            else:
                q = {"latitude": lat, "longitude": lon, "start_date": s, "end_date": e, "timezone": "GMT",
                     "hourly": ",".join(WX_VARS), "wind_speed_unit": "kn", "models": "era5"}
                d = fetch("https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(q))
            df = to_frame(d)
            df.to_csv(f, float_format="%.3f")
            done += 1
            log(f"  {kind} {p} {y}: {len(df)} rows, grid {d.get('latitude')},{d.get('longitude')}")
        except Limited:
            status = "rate_limited"
            break
        except Exception as ex:  # noqa: BLE001
            log(f"  {kind} {p} {y} failed: {ex}")
        time.sleep(8)  # ~ 7.5 calls/min keeps weighted use under the per-minute limit
    total = len(jobs)
    have = len(list(out.glob("*.csv.gz")))
    state = {"status": status, "fetched_this_run": done, "skipped_existing": skipped, "files": have, "jobs": total,
             "wave_variant": variant, "run_utc": now.strftime("%Y-%m-%d %H:%M"), "minutes": round((time.time() - t0) / 60, 1)}
    (out / "_state.json").write_text(json.dumps(state, indent=1))
    with open(out / "_log.txt", "a") as fh:
        fh.write("\n".join(LOG) + "\n")
    log(state)


if __name__ == "__main__":
    main()
