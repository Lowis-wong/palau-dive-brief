"""Project 10 stage E (post-processing): map products derived from the SDB v3 composite.

Reads the published outputs of sdb.py (analysis-data/project10/sdb/) and writes analysis-data/project10/products/:
  depth_quality.tif     1 = good (optically-deep fraction < 0.2 and IQR < 3 m), 2 = fair, 0 = no data
  depth_display.tif     depth (m below MLLW) for maps: quality 1-2 only, tiny isolated patches removed,
                        values >= 18 m set to 18 (shown as "18 m+", see SDB_RESULTS.md section 3)
  uncertainty_m.tif     1-sigma estimate: cross-validated RMSE for the pixel's depth band combined with the
                        between-scene spread (IQR / 1.349)
  hillshade.tif         hillshade of the smoothed depth surface (azimuth 315, altitude 45, z x3), uint8
  rgb_display.tif       glint-corrected multi-year median RGB, stretched to uint8 for basemaps
  contours.gpkg         depth contours (1-6, 8, 10, 12, 15 m) from the smoothed display depth
  atl24_cells.gpkg      ATL24 calibration cells with line, fold and cross-validated prediction
  products.json         thresholds and counts

Usage: python scripts/analysis_p10/derive.py OUT_DIR
"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "analysis_p3"))
from aoi import EPSG, WEST_UTM  # noqa: E402
from common import log, write_tif  # noqa: E402

RAW = "https://raw.githubusercontent.com/Lowis-wong/palau-dive-brief/analysis-data/project10/sdb/"
X0, Y0, X1, Y1 = WEST_UTM
GOOD_DEEPFRAC, GOOD_IQR = 0.2, 3.0
FAIR_DEEPFRAC, FAIR_IQR = 0.5, 5.0
MIN_PATCH_PX = 10
DISPLAY_CAP = 18.0
CONTOURS = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15]
# cross-validated RMSE of the v3 composite by depth band (SDB_RESULTS.md section 2)
CV_RMSE = [(0, 2, 0.49), (2, 5, 0.88), (5, 10, 1.50), (10, 15, 1.62), (15, 20, 2.15), (20, 99, 4.84)]


def fetch(name):
    import rasterio
    with urllib.request.urlopen(RAW + name, timeout=300) as r:
        data = r.read()
    with rasterio.MemoryFile(data) as mf, mf.open() as ds:
        return ds.read(), ds.transform


def nan_smooth(a, sigma):
    from scipy import ndimage
    v = np.isfinite(a)
    num = ndimage.gaussian_filter(np.where(v, a, 0.0), sigma)
    den = ndimage.gaussian_filter(v.astype(float), sigma)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    return np.where(v, out, np.nan)


def main():
    from scipy import ndimage
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "analysis_out/project10") / "products"
    out.mkdir(parents=True, exist_ok=True)
    (d,), tr = fetch("depth_mllw_median_unmasked.tif")
    (df,), _ = fetch("optically_deep_frac.tif")
    (iqr,), _ = fetch("depth_iqr.tif")
    rgb, _ = fetch("rgb_deglint_median.tif")
    log("inputs read", d.shape)

    v = np.isfinite(d) & np.isfinite(df) & np.isfinite(iqr)
    good = v & (df < GOOD_DEEPFRAC) & (iqr < GOOD_IQR)
    fair = v & ~good & (df < FAIR_DEEPFRAC) & (iqr < FAIR_IQR)
    keep = good | fair
    lab, n = ndimage.label(keep, structure=np.ones((3, 3)))
    sizes = ndimage.sum(np.ones_like(lab), lab, np.arange(1, n + 1))
    small = np.isin(lab, np.arange(1, n + 1)[sizes < MIN_PATCH_PX])
    keep &= ~small
    q = np.zeros(d.shape, np.uint8)
    q[keep & good] = 1
    q[keep & fair] = 2
    write_tif(out / "depth_quality.tif", q, tr, dtype="uint8")

    disp = np.where(keep, np.clip(d, -1.0, DISPLAY_CAP), np.nan).astype(np.float32)
    write_tif(out / "depth_display.tif", disp, tr, nodata=np.nan)

    rm = np.full(d.shape, np.nan, np.float32)
    for a, b, r in CV_RMSE:
        m = keep & (d >= a) & (d < b)
        rm[m] = r
    rm[keep & (d < 0)] = CV_RMSE[0][2]
    unc = np.where(keep, np.sqrt(rm ** 2 + (iqr / 1.349) ** 2), np.nan).astype(np.float32)
    write_tif(out / "uncertainty_m.tif", unc, tr, nodata=np.nan)

    sm = nan_smooth(disp, 1.2)
    z = -np.nan_to_num(sm, nan=DISPLAY_CAP + 2) * 3.0
    gy, gx = np.gradient(z, 10.0)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az, alt = np.radians(315.0), np.radians(45.0)
    hs = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    hs = np.where(np.isfinite(sm), np.clip(hs, 0, 1) * 254 + 1, 0).astype(np.uint8)
    write_tif(out / "hillshade.tif", hs, tr, dtype="uint8")

    r = rgb.astype(np.float32) / 1e4
    lo = np.percentile(r[:, r[1] > 0], 0.5, axis=1)[:, None, None]
    hi = np.percentile(r[:, r[1] > 0], 99.5, axis=1)[:, None, None]
    st = np.clip((r - lo) / (hi - lo), 0, 1) ** 0.75
    st = np.where(r[1] > 0, np.round(st * 254 + 1), 0).astype(np.uint8)
    write_tif(out / "rgb_display.tif", st, tr, dtype="uint8")

    # contours
    import contourpy
    import geopandas as gpd
    from shapely.geometry import LineString
    xs = X0 + (np.arange(d.shape[1]) + 0.5) * 10
    ys = Y1 - (np.arange(d.shape[0]) + 0.5) * 10
    zz = np.ma.masked_invalid(sm)
    gen = contourpy.contour_generator(xs, ys, zz, line_type="Separate")
    rows = []
    for lev in CONTOURS:
        for seg in gen.lines(lev):
            if len(seg) >= 4:
                ls = LineString(seg)
                if ls.length >= 60:
                    rows.append({"depth_m": lev, "major": lev in (5, 10, 15), "geometry": ls.simplify(2.0)})
    cg = gpd.GeoDataFrame(rows, crs=f"EPSG:{EPSG}")
    cg.to_file(out / "contours.gpkg", layer="contours", driver="GPKG")

    # ATL24 calibration cells
    with urllib.request.urlopen(RAW + "cells_cv.csv.gz", timeout=300) as r_:
        cells = pd.read_csv(io.BytesIO(r_.read()), compression="gzip")
    cells["x"] = X0 + (cells["ix"] + 0.5) * 10
    cells["y"] = Y1 - (cells["iy"] + 0.5) * 10
    cells["cv_residual"] = cells["cv_pred"] - cells["depth"]
    pg = gpd.GeoDataFrame(cells.drop(columns=["ix", "iy"]), geometry=gpd.points_from_xy(cells["x"], cells["y"]),
                          crs=f"EPSG:{EPSG}")
    pg.to_file(out / "atl24_cells.gpkg", layer="atl24_cells", driver="GPKG")

    info = {"thresholds": {"good": {"deep_frac_lt": GOOD_DEEPFRAC, "iqr_lt_m": GOOD_IQR},
                           "fair": {"deep_frac_lt": FAIR_DEEPFRAC, "iqr_lt_m": FAIR_IQR},
                           "min_patch_px": MIN_PATCH_PX, "display_cap_m": DISPLAY_CAP},
            "pixels": {"good": int((q == 1).sum()), "fair": int((q == 2).sum()),
                       "dropped_small_patches": int(small.sum()), "km2_total": round(float(keep.sum()) / 1e4, 2)},
            "contour_levels": CONTOURS, "contour_lines": int(len(cg)), "cv_rmse_by_band": CV_RMSE,
            "atl24_cells": int(len(cells))}
    (out / "products.json").write_text(json.dumps(info, indent=1))
    log("products", info["pixels"], "contours", len(cg))


if __name__ == "__main__":
    main()
