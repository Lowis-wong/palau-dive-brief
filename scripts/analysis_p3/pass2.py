"""Project 3, pass 2: per-scene statistics inside the sampling boxes and along the channel axis.

Needs analysis/project3/boxes.geojson (EPSG:32653; properties: channel, code) and the
scenes.csv from pass 1 (read from the analysis-data branch).

Usage: python scripts/analysis_p3/pass2.py OUT_DIR
Outputs: box_stats.csv (one row per scene x box), axis_profiles.csv.gz, pass2_summary.json
"""
from __future__ import annotations

import gzip
import io
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (ROOT, SCL_BAD, SCL_WATER, dedupe, dn_offset, log, read_window,  # noqa: E402
                    search_items)

BOXES = ROOT / "analysis" / "project3" / "boxes.geojson"


def rasterize_poly(coords, tr, shape):
    """Boolean mask of pixel centres inside a polygon (ray casting, no extra dependencies)."""
    poly = np.asarray(coords)
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]]
    x = tr.c + (cols + 0.5) * tr.a
    y = tr.f + (rows + 0.5) * tr.e
    inside = np.zeros(shape, bool)
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        cond = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi)
        inside ^= cond
        j = i
    return inside


def load_boxes():
    gj = json.loads(BOXES.read_text())
    boxes, axes = {}, {}
    for f in gj["features"]:
        p = f["properties"]; g = f["geometry"]
        ch = p["channel"].lower(); code = p["code"].upper()
        if g["type"] == "Polygon":
            boxes.setdefault(ch, {})[code] = g["coordinates"][0]
        elif g["type"] == "MultiPolygon":
            boxes.setdefault(ch, {})[code] = g["coordinates"][0][0]
        elif g["type"] in ("LineString", "MultiLineString"):
            c = g["coordinates"] if g["type"] == "LineString" else g["coordinates"][0]
            axes[ch] = c
    return boxes, axes


def bounds_of(ch, boxes, axes, pad=200):
    pts = [pt for poly in boxes[ch].values() for pt in poly] + list(axes.get(ch, []))
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    b = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    return tuple(float(np.floor(v / 20) * 20) if i < 2 else float(np.ceil(v / 20) * 20) for i, v in enumerate(b))


def axis_points(line, step=20.0):
    pts = np.asarray(line, float)
    seg = np.diff(pts, axis=0)
    L = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0], np.cumsum(L)])
    d = np.arange(0, cum[-1] + 1e-6, step)
    xs = np.interp(d, cum, pts[:, 0]); ys = np.interp(d, cum, pts[:, 1])
    return d, xs, ys


def process(item, ch, bbox, masks, axis):
    off = dn_offset(item)
    try:
        b = {}
        for k in ("blue", "green", "red", "nir"):
            a, tr = read_window(item["assets"][k], bbox)
            v = (a.astype(np.float32) + off) / 10000.0
            v[a == 0] = np.nan
            b[k] = v
        shape = b["red"].shape
        sw, _ = read_window(item["assets"]["swir16"], bbox, out_res=10)
        scl, _ = read_window(item["assets"]["scl"], bbox, out_res=10)
        sw = (sw[: shape[0], : shape[1]].astype(np.float32) + off) / 10000.0
        scl = scl[: shape[0], : shape[1]]
    except Exception as e:  # noqa: BLE001
        return item["id"], None, str(e)[:150]
    bad = np.isin(scl, list(SCL_BAD)) | ~np.isfinite(b["red"])
    water = scl == SCL_WATER
    floor = np.nanpercentile(sw[water & ~bad], 2) if (water & ~bad).sum() > 200 else np.nan
    glint = np.clip(sw - (floor if np.isfinite(floor) else 0), 0, None)
    rows = []
    for code, m in masks.items():
        m = m[: shape[0], : shape[1]]
        n = int(m.sum())
        ok = m & ~bad
        r = {"id": item["id"], "channel": ch, "code": code, "n_px": n, "valid_frac": round(ok.sum() / max(n, 1), 3),
             "water_frac": round((m & water).sum() / max(n, 1), 3), "swir_floor": None if not np.isfinite(floor) else round(float(floor), 5)}
        if ok.sum() >= 10:
            for k in ("blue", "green", "red", "nir"):
                r[k] = round(float(np.nanmedian(b[k][ok])), 5)
                r[k + "_gc"] = round(float(np.nanmedian((b[k] - glint)[ok])), 5)
            r["swir16"] = round(float(np.nanmedian(sw[ok])), 5)
            r["red_p25_gc"] = round(float(np.nanpercentile((b["red"] - glint)[ok], 25)), 5)
        rows.append(r)
    prof = None
    if axis is not None:
        d, xs, ys = axis
        c = ((xs - tr.c) / tr.a).astype(int); rr = ((ys - tr.f) / tr.e).astype(int)
        inb = (c >= 0) & (c < shape[1]) & (rr >= 0) & (rr < shape[0])
        vals = np.full(len(d), np.nan, np.float32)
        red_gc = b["red"] - glint
        red_gc[bad] = np.nan
        # 3x3 median around each axis point
        for i in np.where(inb)[0]:
            w = red_gc[max(rr[i] - 1, 0): rr[i] + 2, max(c[i] - 1, 0): c[i] + 2]
            if np.isfinite(w).sum() >= 5:
                vals[i] = np.nanmedian(w)
        prof = vals
    return item["id"], (rows, prof), None


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    boxes, axes = load_boxes()
    raw = subprocess.run(["git", "-C", str(ROOT), "show", "origin/analysis-data:project3/scenes.csv"], check=True, capture_output=True).stdout
    scenes = pd.read_csv(io.BytesIO(raw))
    items = {i["id"]: i for i in dedupe(search_items())}
    log("items", len(items), "scenes", len(scenes))
    all_rows, prof_rows, errors = [], [], 0
    summary = {}
    for ch in boxes:
        bbox = bounds_of(ch, boxes, axes)
        # template grid from one scene
        any_id = next(i for i in scenes["id"] if i in items)
        a, tr = read_window(items[any_id]["assets"]["red"], bbox)
        masks = {code: rasterize_poly(poly, tr, a.shape) for code, poly in boxes[ch].items()}
        axis = axis_points(axes[ch]) if ch in axes else None
        sel = scenes[(scenes[f"{ch}_valid"] >= 0.95) & (scenes[f"{ch}_core_clear"] >= 0.5)]
        sel = sel[sel["id"].isin(items)]
        log(f"[{ch}] boxes {list(masks)} px {[int(m.sum()) for m in masks.values()]}; scenes {len(sel)}")
        summary[ch] = {"scenes_read": int(len(sel)), "box_px": {k: int(m.sum()) for k, m in masks.items()}}
        with ThreadPoolExecutor(12) as ex:
            futs = [ex.submit(process, items[i], ch, bbox, masks, axis) for i in sel["id"]]
            for k, f in enumerate(futs):
                sid, res, err = f.result()
                if err:
                    errors += 1
                    continue
                rows, prof = res
                all_rows += rows
                if prof is not None:
                    prof_rows.append({"id": sid, "channel": ch, **{f"d{int(dd)}": (None if not np.isfinite(v) else round(float(v), 5)) for dd, v in zip(axis[0], prof)}})
                if k % 100 == 0:
                    log(f"[{ch}] {k}/{len(futs)}")
    pd.DataFrame(all_rows).to_csv(out / "box_stats.csv", index=False)
    with gzip.open(out / "axis_profiles.csv.gz", "wt") as fh:
        pd.DataFrame(prof_rows).to_csv(fh, index=False)
    summary["errors"] = errors
    (out / "pass2_summary.json").write_text(json.dumps(summary, indent=2))
    log("done", json.dumps(summary))


if __name__ == "__main__":
    main()
