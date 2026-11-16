"""Shared helpers for Project 3 (Sentinel-2 channel plumes).

Everything is in WGS 84 / UTM 53N (EPSG:32653), the native grid of tile 53NMH,
so windows read straight from the COGs without resampling.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import harmonics  # noqa: E402

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "4")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
os.environ.setdefault("VSI_CACHE", "TRUE")

STAC = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
TILE = "53NMH"
EPSG = 32653

# Areas of interest, 7 x 7 km, UTM 53N metres (xmin, ymin, xmax, ymax).
# "core" is a 2 x 2 km box on the channel mouth used to judge whether a scene is clear there.
AOIS = {
    "ulong": {"bbox": (411800, 801900, 418800, 808900), "core": (416000, 804300, 418000, 806300)},
    "german": {"bbox": (415800, 783700, 422800, 790700), "core": (418800, 786800, 420800, 788800)},
}

# SCL classes
SCL_NODATA, SCL_SAT, SCL_DARK, SCL_SHADOW, SCL_VEG, SCL_BARE, SCL_WATER, SCL_UNCL, SCL_CLM, SCL_CLH, SCL_CIRRUS, SCL_SNOW = range(12)
SCL_BAD = {SCL_NODATA, SCL_SAT, SCL_SHADOW, SCL_CLM, SCL_CLH, SCL_CIRRUS, SCL_SNOW}

BANDS = {"blue": "blue", "green": "green", "red": "red", "nir": "nir", "swir16": "swir16", "scl": "scl", "visual": "visual"}

PALAU = timezone(timedelta(hours=9))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- STAC
def search_items(start_year=2017, max_cloud=90) -> list[dict]:
    """Search year by year (small pages, retries); the API sometimes returns 500 on large requests."""
    from pystac_client import Client
    lon0, lat0, lon1, lat1 = 134.20, 7.10, 134.31, 7.31
    out, seen = [], set()
    now = datetime.now(timezone.utc)
    for y in range(start_year, now.year + 1):
        for half in ((f"{y}-01-01", f"{y}-06-30T23:59:59Z"), (f"{y}-07-01", f"{y}-12-31T23:59:59Z")):
            for attempt in range(5):
                try:
                    cl = Client.open(STAC)
                    s = cl.search(collections=[COLLECTION], bbox=[lon0, lat0, lon1, lat1],
                                  datetime=f"{half[0]}/{half[1]}", limit=100)
                    got = list(s.items_as_dicts())
                    break
                except Exception as e:  # noqa: BLE001
                    log("search retry", half, attempt, str(e)[:100])
                    time.sleep(5 * (attempt + 1))
            else:
                log("search failed", half)
                continue
            n0 = len(out)
            for it in got:
                if it["id"] in seen:
                    continue
                seen.add(it["id"])
                p = it["properties"]
                if (p.get("eo:cloud_cover") or 0) >= max_cloud:
                    continue
                tile = f'{p.get("mgrs:utm_zone")}{p.get("mgrs:latitude_band")}{p.get("mgrs:grid_square")}'
                out.append({
                    "id": it["id"], "datetime": p["datetime"], "tile": tile,
                    "platform": p.get("platform"), "cloud": p.get("eo:cloud_cover"),
                    "baseline": p.get("s2:processing_baseline"),
                    "boa_offset_applied": p.get("earthsearch:boa_offset_applied"),
                    "updated": p.get("updated") or p.get("created"),
                    "nodata_pct": p.get("s2:nodata_pixel_percentage"),
                    "assets": {k: it["assets"][v]["href"] for k, v in BANDS.items() if v in it["assets"]},
                })
            log("search", half[0], len(got), "kept", len(out) - n0)
    return out


def dedupe(items: list[dict]) -> list[dict]:
    """One item per acquisition date on tile 53NMH: newest processing baseline, then newest update."""
    df = [i for i in items if i["tile"] == TILE and set(BANDS) <= set(i["assets"])]
    best: dict[str, dict] = {}
    for i in df:
        d = i["datetime"][:10]
        key = (str(i.get("baseline") or ""), str(i.get("updated") or ""))
        if d not in best or key > (str(best[d].get("baseline") or ""), str(best[d].get("updated") or "")):
            best[d] = i
    return sorted(best.values(), key=lambda i: i["datetime"])


def dn_offset(item: dict) -> int:
    """Processing baseline >= 04.00 stores reflectance with +1000; earth-search may have removed it."""
    try:
        b = float(item.get("baseline") or 0)
    except ValueError:
        b = 0
    if b >= 4.0 and not item.get("boa_offset_applied"):
        return -1000
    return 0


# ---------------------------------------------------------------- raster reads
def read_window(href: str, bbox: tuple, out_res: int | None = None):
    """Read a UTM window; returns (array, transform). If out_res is given, resample by repetition / decimation."""
    import rasterio
    from rasterio.windows import from_bounds
    with rasterio.open(href) as ds:
        w = from_bounds(*bbox, transform=ds.transform).round_offsets().round_lengths()
        a = ds.read(1, window=w, boundless=True, fill_value=0)
        tr = ds.window_transform(w)
        res = ds.res[0]
    if out_res and out_res != res:
        f = res / out_res
        if f > 1:  # upsample (20 m -> 10 m)
            k = int(round(f))
            a = np.repeat(np.repeat(a, k, 0), k, 1)
        else:
            k = int(round(1 / f))
            a = a[::k, ::k]
        from rasterio.transform import Affine
        tr = Affine(out_res, 0, tr.c, 0, -out_res, tr.f)
    return a, tr


def write_tif(path: Path, arr: np.ndarray, transform, dtype=None, nodata=None):
    import rasterio
    path.parent.mkdir(parents=True, exist_ok=True)
    if arr.ndim == 2:
        arr = arr[None]
    dtype = dtype or arr.dtype
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2], count=arr.shape[0],
                       dtype=dtype, crs=f"EPSG:{EPSG}", transform=transform, compress="deflate",
                       predictor=2 if np.dtype(dtype).kind in "iu" else 3, tiled=True,
                       blockxsize=256, blockysize=256, nodata=nodata) as ds:
        ds.write(arr.astype(dtype))


# ---------------------------------------------------------------- tide stage
def load_wl() -> pd.Series:
    """Merged hourly Malakal water level (UTC) from the analysis-data branch (Step 1)."""
    raw = subprocess.run(["git", "-C", str(ROOT), "show", "origin/analysis-data:step1/wl_hourly.csv.gz"],
                         check=True, capture_output=True).stdout
    df = pd.read_csv(io.BytesIO(gzip.decompress(raw)), parse_dates=["time_utc"])
    return df.set_index("time_utc")["wl_m"]


EPOCH = datetime(2000, 1, 1)


def yearly_models(wl: pd.Series, years) -> dict[int, dict]:
    models = {}
    for y in years:
        s = wl[(wl.index >= f"{y}-01-01") & (wl.index < f"{y + 1}-01-01")].dropna()
        if len(s) < 24 * 120:  # fall back to the latest 365 days
            s = wl[wl.index >= wl.index.max() - pd.Timedelta(days=365)].dropna()
        th = (s.index - pd.Timestamp(EPOCH)).total_seconds().values / 3600.0
        names = harmonics.select_constituents(th.max() - th.min(), include_long_period=False)
        models[y] = harmonics.fit(th, s.values, names)
    return models


def tide_stage(t_utc: datetime, models: dict[int, dict]) -> dict:
    """Stage of the Malakal tide at t: minutes from the nearest high water, range of that cycle, etc."""
    m = models.get(t_utc.year) or models[max(models)]
    t0 = t_utc - timedelta(hours=14)
    tt = np.array([t0 + timedelta(minutes=k) for k in range(28 * 60 + 1)])
    th = np.array([(x - EPOCH).total_seconds() / 3600.0 for x in tt])
    h = harmonics.predict(th, m)
    d = np.diff(h)
    hi = [i for i in range(1, len(h) - 1) if d[i - 1] > 0 and d[i] <= 0]
    lo = [i for i in range(1, len(h) - 1) if d[i - 1] < 0 and d[i] >= 0]
    c = 14 * 60
    hw = min(hi, key=lambda i: abs(i - c))
    lws = sorted(lo)
    lb = [i for i in lws if i < hw]
    la = [i for i in lws if i > hw]
    lw_near = min(lws, key=lambda i: abs(i - c)) if lws else None
    rng_parts = []
    if lb:
        rng_parts.append(h[hw] - h[lb[-1]])
    if la:
        rng_parts.append(h[hw] - h[la[0]])
    # daily max range over the Palau calendar day
    rate = (h[c + 1] - h[c - 1]) / 2 * 60  # m/h
    return {
        "hw_utc": tt[hw].strftime("%Y-%m-%d %H:%M"),
        "min_from_hw": int(c - hw),  # >0: after high water (ebb side)
        "min_from_lw": int(c - lw_near) if lw_near is not None else None,
        "range_m": round(float(np.mean(rng_parts)), 3) if rng_parts else None,
        "range_max_m": round(float(max(rng_parts)), 3) if rng_parts else None,
        "level_m": round(float(h[c]), 3),
        "rate_m_h": round(float(rate), 3),
        "stage": "flood" if rate > 0 else "ebb",
        "m2_phase_deg": round(((c - hw) / (360 / harmonics.SPEEDS["M2"] * 60)) * 360, 1),
    }
