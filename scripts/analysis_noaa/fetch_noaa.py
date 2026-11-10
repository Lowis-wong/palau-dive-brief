"""Fetch historical NOAA CO-OPS tide PREDICTIONS for Malakal (1841367): hourly and high/low, 2010-2026, UTC, MLLW."""
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path
import pandas as pd
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "palau-dive-brief-analysis"}
def get(params):
    base = {"product": "predictions", "application": "palau_dive_brief", "station": "1841367", "datum": "MLLW",
            "time_zone": "gmt", "units": "metric", "format": "json"}
    url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?" + urllib.parse.urlencode({**base, **params})
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                d = json.loads(r.read())
            if "predictions" in d:
                return d["predictions"]
            raise RuntimeError(d)
        except Exception as e:  # noqa: BLE001
            print("retry", params.get("begin_date"), e); time.sleep(5 * (i + 1))
    return []
h, hl = [], []
for y in range(2010, 2027):
    h += get({"begin_date": f"{y}0101", "end_date": f"{y}1231", "interval": "h"})
    hl += get({"begin_date": f"{y}0101", "end_date": f"{y}1231", "interval": "hilo"})
    print(y, len(h), len(hl)); time.sleep(1)
pd.DataFrame(h).to_csv(OUT / "noaa_hourly.csv.gz", index=False)
pd.DataFrame(hl).to_csv(OUT / "noaa_hilo.csv.gz", index=False)
