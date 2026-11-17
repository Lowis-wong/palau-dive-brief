"""Project 10 stage F: mooring-buoy candidates from boats moored in Sentinel-2 imagery (~10:35 local).

Refines the pilot (pilot.py part B) and extends it to all four sites:
  * 3 x 3 km box per site; per date the tile (53NMH / 53NMJ) that covers the box best.
  * Scene quality: box clear fraction >= 0.9 (SCL) and at most MAX_BLOBS small bright blobs
    (busy scenes = whitecaps, broken cloud, glint).
  * Boat pixel: NIR (B8) more than NIR_EXCESS above the box water median.
  * Blob: 1-3 pixels (a 10-25 m boat); larger blobs are wakes, foam, cloud or reef.
  * Location filter: not on reef flats or in very shallow water (SDB depth < MIN_DEPTH_M), and not where the
    pixel is bright in more than BRIGHT_FREQ_MAX of clear scenes (persistent features).
  * Clustering: kernel density of blob centroids (sigma 1.5 px); peaks >= MIN_SCENES distinct days; members within
    30 m of the peak.  Candidate class from the share of clear days with a boat and the spatial spread.
Outputs (OUT_DIR/moorings/): candidates.csv, candidates.gpkg, <site>/detections.csv, <site>/density.tif,
<site>/composite_tci.tif, <site>/chips.png, summary.json

Usage: python scripts/analysis_p10/boats.py OUT_DIR
"""
from __future__ import annotations

import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "analysis_p3"))
from aoi import EPSG, SITES, box  # noqa: E402
from common import SCL_BAD, dedupe, dn_offset, log, read_window, search_items, write_tif  # noqa: E402
from pilot import read_tci  # noqa: E402

HALF = 1500
NIR_EXCESS = 250          # x 1e-4 reflectance above the box water median
BLOB_PX = (1, 3)
MAX_BLOBS = 40            # small blobs per box per scene; busier scenes are skipped
MIN_DEPTH_M = 3.0
BRIGHT_FREQ_MAX = 0.15
MIN_SCENES = 4
SDB_URL = "https://raw.githubusercontent.com/Lowis-wong/palau-dive-brief/analysis-data/project10/sdb/depth_mllw_median_unmasked.tif"
# snap to the 20 m grid shared by the Sentinel-2 tiles (origin 399960 / x00040) and the SDB grid, so pixel
# centres computed from the box corner are exact
BOXES = {k: tuple(int(round(v / 20.0)) * 20 for v in box(k, HALF)) for k in SITES}
S: dict = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "params": {"half_box_m": HALF, "nir_excess": NIR_EXCESS, "blob_px": BLOB_PX, "max_blobs": MAX_BLOBS,
                      "min_depth_m": MIN_DEPTH_M, "bright_freq_max": BRIGHT_FREQ_MAX, "min_scenes": MIN_SCENES}}


def sdb_depth():
    import rasterio
    from rasterio.windows import from_bounds
    with urllib.request.urlopen(SDB_URL, timeout=300) as r:
        data = r.read()
    out = {}
    with rasterio.MemoryFile(data) as mf, mf.open() as ds:
        for k, b in BOXES.items():
            w = from_bounds(*b, transform=ds.transform).round_offsets().round_lengths()
            out[k] = ds.read(1, window=w, boundless=True, fill_value=np.nan)
    return out


def read_box(item, b):
    scl, _ = read_window(item["assets"]["scl"], b, out_res=10)
    nir, _ = read_window(item["assets"]["nir"], b)
    tci, _ = read_tci(item["assets"]["visual"], b)
    n = 2 * round(HALF / 10)
    return scl[:n, :n], (nir[:n, :n].astype(np.int32) + dn_offset(item)), tci[:, :n, :n]


def scan(args):
    date, items = args
    res = {"date": date, "boxes": {}}
    for k, b in BOXES.items():
        best = None
        for it in items:
            try:
                scl, nir, tci = read_box(it, b)
            except Exception as e:  # noqa: BLE001
                res.setdefault("errors", []).append(f"{k} {it['id']}: {str(e)[:100]}")
                continue
            have = float((scl > 0).mean())
            if best is None or have > best[0]:
                best = (have, it["id"], scl, nir, tci)
        if best is None or best[0] < 0.95:
            continue
        _, iid, scl, nir, tci = best
        clear = float((~np.isin(scl, list(SCL_BAD)) & (scl > 0)).mean())
        if clear < 0.9:
            continue
        res["boxes"][k] = (iid, scl, nir, tci, clear)
    return res


def chips(path, cand, det, tcis, dates, comp, site):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if not len(cand):
        return
    cand = cand.head(12).reset_index(drop=True)
    ncol = 7
    fig, axs = plt.subplots(len(cand), ncol, figsize=(ncol * 1.8, len(cand) * 1.9))
    axs = np.atleast_2d(axs)
    di = {d: i for i, d in enumerate(dates)}
    for i, h in cand.iterrows():
        r, c = int(round(h["row"])), int(round(h["col"]))
        sl = (slice(max(r - 15, 0), r + 15), slice(max(c - 15, 0), c + 15))
        axs[i, 0].imshow(np.moveaxis(comp[:, sl[0], sl[1]], 0, -1))
        axs[i, 0].set_title(f"{h['cand_id']} {h['class']} n={h['n_days']}", fontsize=7)
        near = det[np.hypot(det["row"] - h["row"], det["col"] - h["col"]) <= 3].drop_duplicates("date").head(ncol - 1)
        for j, (_, dr) in enumerate(near.iterrows(), start=1):
            axs[i, j].imshow(np.moveaxis(tcis[di[dr["date"]]][:, sl[0], sl[1]], 0, -1))
            axs[i, j].set_title(dr["date"], fontsize=7)
        for ax in axs[i]:
            ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle(f"{site}: mooring candidates (300 m chips; col 1 = multi-year median)", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main():
    from pyproj import Transformer
    from rasterio.transform import Affine
    from scipy import ndimage

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "analysis_out/project10") / "moorings"
    out.mkdir(parents=True, exist_ok=True)
    depth = sdb_depth()
    items = dedupe(search_items(2017))
    by_date: dict[str, list] = {}
    for it in items:
        by_date.setdefault(it["datetime"][:10], []).append(it)
    dates = sorted(by_date)
    log("dates", len(dates))
    acc = {k: {"bright": None, "valid": None, "det": [], "tci": [], "dates": [], "skipped_busy": 0} for k in BOXES}
    n = 2 * round(HALF / 10)
    with ThreadPoolExecutor(8) as ex:
        for i, r in enumerate(ex.map(scan, [(d, by_date[d]) for d in dates])):
            if i % 50 == 0:
                log("scanned", i)
            for e in r.get("errors", []):
                S.setdefault("errors", []).append(e)
            for k, (iid, scl, nir, tci, clear) in r["boxes"].items():
                a = acc[k]
                if a["bright"] is None:
                    a["bright"] = np.zeros((n, n), np.int32); a["valid"] = np.zeros((n, n), np.int32)
                bad = np.isin(scl, list(SCL_BAD)) | (nir <= 0)
                ref = float(np.median(nir[~bad]))
                cand = (~bad) & (nir - ref > NIR_EXCESS)
                lab, nl = ndimage.label(cand, structure=np.ones((3, 3)))
                rows = []
                if nl:
                    idx = np.arange(1, nl + 1)
                    size = ndimage.sum(np.ones_like(lab), lab, idx)
                    peak = ndimage.maximum(nir - ref, lab, idx)
                    com = ndimage.center_of_mass(np.clip(nir - ref, 0, None), lab, idx)
                    for j, sz in enumerate(size):
                        if BLOB_PX[0] <= sz <= BLOB_PX[1]:
                            rows.append({"date": r["date"], "scene": iid, "row": com[j][0], "col": com[j][1],
                                         "size": int(sz), "peak": float(peak[j])})
                a["valid"] += ~bad
                a["bright"] += cand
                if len(rows) > MAX_BLOBS:
                    a["skipped_busy"] += 1
                    continue
                a["det"].extend(rows)
                a["tci"].append(tci)
                a["dates"].append(r["date"])

    t_inv = Transformer.from_crs(EPSG, 4326, always_xy=True)
    allc, summ = [], {}
    for k, b in BOXES.items():
        a = acc[k]
        d = out / k
        d.mkdir(parents=True, exist_ok=True)
        if a["bright"] is None or not a["dates"]:
            summ[k] = {"clear_days": 0}; continue
        tr = Affine(10, 0, b[0], 0, -10, b[3])
        nd = len(a["dates"])
        freq = a["bright"] / np.maximum(a["valid"], 1)
        dep = depth[k][:n, :n]
        det = pd.DataFrame(a["det"])
        if len(det):
            ri = det["row"].round().astype(int).clip(0, n - 1)
            ci = det["col"].round().astype(int).clip(0, n - 1)
            det["bright_freq"] = freq[ri, ci]
            det["sdb_depth"] = dep[ri, ci]
            det["keep"] = (det["bright_freq"] <= BRIGHT_FREQ_MAX) & ~(det["sdb_depth"] < MIN_DEPTH_M)
            det["x"] = b[0] + (det["col"] + 0.5) * 10
            det["y"] = b[3] - (det["row"] + 0.5) * 10
        det.to_csv(d / "detections.csv", index=False, float_format="%.2f")
        dk = det[det["keep"]] if len(det) else det
        grid = np.zeros((n, n))
        if len(dk):
            np.add.at(grid, (dk["row"].round().astype(int).clip(0, n - 1), dk["col"].round().astype(int).clip(0, n - 1)), 1)
        dens = ndimage.gaussian_filter(grid, 1.5) * (2 * np.pi * 1.5 ** 2)
        write_tif(d / "density.tif", dens.astype(np.float32), tr)
        comp = np.median(np.stack(a["tci"]), axis=0).astype("uint8")
        write_tif(d / "composite_tci.tif", comp, tr, dtype="uint8")
        peaks = np.argwhere((dens == ndimage.maximum_filter(dens, size=7)) & (dens >= MIN_SCENES * 0.6))
        cands = []
        for pr, pc in peaks:
            near = dk[np.hypot(dk["row"] - pr, dk["col"] - pc) <= 3]
            ndays = near["date"].nunique()
            if ndays < MIN_SCENES:
                continue
            rr, cc = near["row"].median(), near["col"].median()
            spread = float(np.median(np.hypot(near["row"] - rr, near["col"] - cc)) * 10)
            yrs = pd.to_datetime(near["date"]).dt.year
            cands.append({"site": k, "row": rr, "col": cc, "n_days": int(ndays), "share_of_clear_days": round(ndays / nd, 3),
                          "spread_m": round(spread, 1), "median_size_px": float(near["size"].median()),
                          "first": near["date"].min(), "last": near["date"].max(),
                          "years_with_boat": int(yrs.nunique()),
                          "sdb_depth_m": None if not np.isfinite(dep[int(round(rr)), int(round(cc))]) else
                          round(float(dep[int(round(rr)), int(round(cc))]), 1)})
        cd = pd.DataFrame(cands)
        if len(cd):
            # merge peaks closer than 40 m (keep the stronger)
            cd = cd.sort_values("n_days", ascending=False).reset_index(drop=True)
            keep = []
            for i, r_ in cd.iterrows():
                if all(np.hypot(r_["row"] - cd.at[j, "row"], r_["col"] - cd.at[j, "col"]) * 10 > 40 for j in keep):
                    keep.append(i)
            cd = cd.loc[keep].reset_index(drop=True)
            cd["x"] = b[0] + (cd["col"] + 0.5) * 10
            cd["y"] = b[3] - (cd["row"] + 0.5) * 10
            cd["lon"], cd["lat"] = t_inv.transform(cd["x"].values, cd["y"].values)
            cd["class"] = np.where((cd["share_of_clear_days"] >= 0.08) & (cd["spread_m"] <= 25) & (cd["years_with_boat"] >= 3), "A",
                                   np.where((cd["share_of_clear_days"] >= 0.04) & (cd["years_with_boat"] >= 2), "B", "C"))
            prefix = {"blue-corner": "BC", "siaes-corner": "SC", "ulong-channel": "UC", "german-channel": "GC"}[k]
            cd["cand_id"] = [f"{prefix}{i + 1:02d}" for i in range(len(cd))]
            chips(d / "chips.png", cd, dk, a["tci"], a["dates"], comp, k)
            allc.append(cd)
        summ[k] = {"clear_days": nd, "busy_days_skipped": a["skipped_busy"], "detections": int(len(det)),
                   "kept_detections": int(len(dk)), "candidates": int(len(cd)),
                   "by_class": cd["class"].value_counts().to_dict() if len(cd) else {}}
        log(k, summ[k])
    S["sites"] = summ
    if allc:
        import geopandas as gpd
        ca = pd.concat(allc, ignore_index=True)
        cols = ["cand_id", "site", "class", "lat", "lon", "x", "y", "n_days", "share_of_clear_days", "spread_m",
                "years_with_boat", "first", "last", "sdb_depth_m", "median_size_px"]
        ca[cols].to_csv(out / "candidates.csv", index=False, float_format="%.6f")
        g = gpd.GeoDataFrame(ca[cols], geometry=gpd.points_from_xy(ca["x"], ca["y"]), crs=f"EPSG:{EPSG}")
        g.to_file(out / "candidates.gpkg", layer="candidates", driver="GPKG")
    S["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "summary.json").write_text(json.dumps(S, indent=1, default=str))


if __name__ == "__main__":
    main()
