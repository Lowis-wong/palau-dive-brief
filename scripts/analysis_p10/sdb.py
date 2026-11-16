"""Project 10 stages C-E: per-scene Sentinel-2 satellite-derived bathymetry calibrated with ICESat-2 ATL24.

Per scene (one per date; tiles 53NMH + 53NMJ merged over the west box):
  1. SCL screen (clear water fraction), then read B2 B3 B4 B8 B11 SCL.
  2. Mask cloud / shadow / land; sun-glint correction (Hedley) with B8 regressed over optically deep water.
  3. Stumpf log-ratio indices: pG = ln(1000 B2)/ln(1000 B3), pR = ln(1000 B2)/ln(1000 B4).
  4. Fit pG and pR to ATL24 depth + scene water level (both on MLLW), robust linear fits.
     Blend: red model below 2 m (red estimate), green above 3.5 m, linear weight between (Caballero & Stumpf 2020);
     red is never used where the green estimate exceeds 8 m.
  5. Back to MLLW, store as int16 cm. Optically deep pixels flagged.
  Leave-lines-out cross-validation: lines = RGT x beam pair; 4 folds.
Composite: per-pixel median, IQR and count over accepted scenes; deglinted RGB median.

Usage: python scripts/analysis_p10/sdb.py OUT_DIR
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
from aoi import WEST_UTM  # noqa: E402
from common import SCL_BAD, dedupe, dn_offset, load_wl, log, read_window, search_items, write_tif, yearly_models  # noqa: E402
from pilot import level_at, mllw_from_obs  # noqa: E402

X0, Y0, X1, Y1 = WEST_UTM
W, H = (X1 - X0) // 10, (Y1 - Y0) // 10
NODATA = -32768
N_LOG = 1000.0
CLEAR_MIN = 0.35          # min clear-water fraction of the box to read a scene
FIT_MIN_N = 300           # min calibration cells for the green model
FIT_MIN_R2 = 0.6
GREEN_RANGE = (1.0, 22.0)  # target (instantaneous) depth range used to fit the green model
RED_RANGE = (0.0, 6.0)
BLEND = (2.0, 3.5)
GREEN_DEG = 2             # depth = quadratic in pG, depth-balanced weights (v1 linear unweighted compressed depths > 8 m)
ATL24_URL = "https://raw.githubusercontent.com/Lowis-wong/palau-dive-brief/analysis-data/project10/atl24_points.csv.gz"
S: dict = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "grid": [X0, Y0, X1, Y1, W, H]}


def transform():
    from rasterio.transform import Affine
    return Affine(10, 0, X0, 0, -10, Y1)


# ------------------------------------------------------------------ calibration cells
def load_cells(mllw: float) -> pd.DataFrame:
    p = Path("analysis_out/project10/atl24_points.csv.gz")
    src = p if p.exists() else ATL24_URL
    d = pd.read_csv(src, usecols=["x", "y", "depth_mllw", "confidence", "rgt", "gt", "pass_id"])
    d = d[(d["confidence"] >= 0.6) & d["depth_mllw"].between(0.3, 30)]
    d["ix"] = ((d["x"] - X0) // 10).astype(int)
    d["iy"] = ((Y1 - d["y"]) // 10).astype(int)
    d = d[(d["ix"] >= 0) & (d["ix"] < W) & (d["iy"] >= 0) & (d["iy"] < H)]
    d["line"] = d["rgt"].astype(int).astype(str) + "_" + ((d["gt"].astype(int) - 10) // 20).astype(str)
    c = d.groupby(["iy", "ix"]).agg(depth=("depth_mllw", "median"), n=("depth_mllw", "size"),
                                     sd=("depth_mllw", "std"), line=("line", lambda s: s.mode().iat[0]),
                                     passes=("pass_id", "nunique")).reset_index()
    # deep returns are sparse (median 1-2 photons per 10 m cell), so single-photon cells are kept;
    # false bottoms in deep water are removed by the isolation test below instead
    c = c[c["sd"].fillna(0) < 1.5].reset_index(drop=True)
    # isolated cells (likely false bottoms in deep water): need >= 3 other cells within 30 m
    occ = np.zeros((H, W), np.int16)
    occ[c["iy"], c["ix"]] = 1
    from scipy import ndimage
    nb = ndimage.convolve(occ, np.ones((7, 7), np.int16), mode="constant")
    c = c[nb[c["iy"], c["ix"]] >= 4].reset_index(drop=True)
    # folds: lines balanced by cell count
    lines = c.groupby("line").size().sort_values(ascending=False)
    folds, load = {}, [0, 0, 0, 0]
    for ln, n in lines.items():
        k = int(np.argmin(load)); folds[ln] = k; load[k] += n
    c["fold"] = c["line"].map(folds)
    S["cells"] = {"n": int(len(c)), "lines": lines.to_dict(), "fold_sizes": load}
    log("calibration cells", len(c), "lines", len(lines), "fold sizes", load)
    return c


# ------------------------------------------------------------------ reading
def screen(group):
    """group: list of items for one date. Returns clear-water fraction of the box using SCL."""
    try:
        scl = np.zeros((H, W), np.uint8)
        for it in group:
            a, _ = read_window(it["assets"]["scl"], WEST_UTM, out_res=10)
            a = a[:H, :W]
            take = (scl == 0) & (a > 0)
            scl[take] = a[take]
        ok = (scl > 0) & ~np.isin(scl, list(SCL_BAD))
        return float(ok.mean())
    except Exception as e:  # noqa: BLE001
        log("screen error", group[0]["id"], str(e)[:120])
        return 0.0


def read_scene(group):
    try:
        out = {}
        for it in group:
            off = dn_offset(it)
            bands = {}
            for k in ("blue", "green", "red", "nir"):
                a, _ = read_window(it["assets"][k], WEST_UTM)
                bands[k] = a[:H, :W]
            for k in ("swir16", "scl"):
                a, _ = read_window(it["assets"][k], WEST_UTM, out_res=10)
                bands[k] = a[:H, :W]
            have = bands["blue"] > 0
            out.setdefault("_have", np.zeros((H, W), bool))
            fill = have & ~out["_have"]
            for k, a in bands.items():
                if k not in out:
                    out[k] = np.zeros((H, W), np.float32 if k != "scl" else np.uint8)
                if k == "scl":
                    out[k][fill] = a[fill]
                else:
                    out[k][fill] = (a[fill].astype(np.float32) + off) / 1e4
            out["_have"] |= have
        return out
    except Exception as e:  # noqa: BLE001
        log("read error", group[0]["id"], str(e)[:160])
        return None


# ------------------------------------------------------------------ per-scene processing
def depth_weights(y, bin_m=1.0, floor=20):
    """Equalise the influence of each 1 m depth bin (calibration cells are ~60 % shallower than 2 m)."""
    b = np.floor(y / bin_m).astype(int)
    _, inv, cnt = np.unique(b, return_inverse=True, return_counts=True)
    return 1.0 / np.maximum(cnt[inv], floor)


def robust_fit(x, y, iters=3, deg=1, weighted=False):
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    keep = np.ones(len(x), bool)
    coef = None
    for _ in range(iters):
        if keep.sum() < 30:
            return None
        w = depth_weights(y[keep]) if weighted else np.ones(keep.sum())
        coef = np.polyfit(x[keep], y[keep], deg, w=np.sqrt(w))
        r = y - np.polyval(coef, x)
        s = 1.4826 * np.median(np.abs(r[keep] - np.median(r[keep])))
        keep = np.abs(r) < max(3 * s, 0.5)
    r = y[keep] - np.polyval(coef, x[keep])
    r2 = 1 - np.var(r) / np.var(y[keep])
    out = {"coef": [float(v) for v in coef], "n": int(keep.sum()), "rmse": float(np.sqrt(np.mean(r ** 2))),
           "r2": float(r2), "kept_frac": float(keep.mean()), "xmin": float(np.percentile(x[keep], 0.5)),
           "xmax": float(np.percentile(x[keep], 99.5))}
    # slope at the low end of the fitted range (must be positive: deeper -> larger ratio)
    out["m1"] = float(np.polyval(np.polyder(coef), out["xmin"])) if deg > 1 else float(coef[0])
    if deg > 1:
        # quadratic must stay monotonic over the calibrated range
        xs = np.linspace(out["xmin"], out["xmax"], 50)
        out["monotonic"] = bool(np.all(np.polyval(np.polyder(coef), xs) > 0))
    return out


def predict(pg, pr, fg, fr):
    # clamp the ratio to the calibrated range so a quadratic cannot run away
    dg = np.polyval(fg["coef"], np.clip(pg, fg["xmin"], fg["xmax"] + 0.02))
    if fr is None:
        return dg
    dr = np.polyval(fr["coef"], pr)
    # switch on the red estimate (Caballero & Stumpf 2020), but never use red where green says > 8 m:
    # the red band is pure noise once the bottom is deeper than a few metres
    w = np.clip((dr - BLEND[0]) / (BLEND[1] - BLEND[0]), 0, 1)  # 0 -> red, 1 -> green
    w = np.where(dg > 8, 1.0, w)
    w = np.where(np.isfinite(dr), w, 1.0)
    return np.where(np.isfinite(dr), (1 - w) * dr + w * dg, dg)


def fit_scene(pg, pr, target, cells_mask):
    g = cells_mask & np.isfinite(pg) & (target >= GREEN_RANGE[0]) & (target <= GREEN_RANGE[1])
    fg = robust_fit(pg[g], target[g], deg=GREEN_DEG, weighted=True)
    if fg is not None and GREEN_DEG > 1 and not fg.get("monotonic", True):
        fg = robust_fit(pg[g], target[g], deg=1, weighted=True)
    r = cells_mask & np.isfinite(pr) & (target >= RED_RANGE[0]) & (target <= RED_RANGE[1])
    fr = robust_fit(pr[r], target[r]) if r.sum() >= 50 else None
    if fr is not None and (fr["r2"] < 0.3 or fr["m1"] <= 0):
        fr = None
    return fg, fr


def process(item_date, data, cells, h_rel, stacks, k_scene, cv_pred, dump):
    b2, b3, b4, b8, b11, scl = (data[k] for k in ("blue", "green", "red", "nir", "swir16", "scl"))
    have = data["_have"]
    bad = ~have | np.isin(scl, list(SCL_BAD))
    ndvi = (b8 - b4) / np.maximum(b8 + b4, 1e-4)
    # vegetated islands (bright NIR, high NDVI) or bright SWIR (sand, rock, exposed reef, strong glint/foam);
    # NDVI alone is not used: clear deep water has tiny red and NIR, so its NDVI is noisy and often positive
    land = ((b8 > 0.08) & (ndvi > 0.2)) | (b11 > 0.08)
    water = ~bad & ~land
    rec = {"date": item_date, "clear_water_frac": float(water.mean())}
    if water.sum() < 50000:
        rec["reject"] = "little water"; return rec
    # optically deep candidates: darkest water in (green - NIR), which is nearly glint-free,
    # so the deep set keeps the full range of glint for the regression below
    gm = b3 - b8
    lo, hi = np.percentile(gm[water], [1, 8])
    deep = water & (gm >= lo) & (gm <= hi)
    rec["deep_n"] = int(deep.sum())
    # Hedley glint correction with NIR
    xn = b8[deep]
    nmin = float(np.percentile(xn, 1))
    slopes = {}
    corr = {}
    for k, b in (("b2", b2), ("b3", b3), ("b4", b4)):
        y = b[deep]
        if np.var(xn) > 1e-10:
            s = float(np.cov(xn, y)[0, 1] / np.var(xn))
        else:
            s = 0.0
        s = float(np.clip(s, 0, 1.5))
        slopes[k] = s
        corr[k] = b - s * (b8 - nmin)
    rec.update({"glint_nir_deep_med": float(np.median(xn)), "glint_nir_deep_p90": float(np.percentile(xn, 90)),
                "slopes": slopes})
    c2, c3, c4 = corr["b2"], corr["b3"], corr["b4"]
    ok = water & (c2 > 0.0012) & (c3 > 0.0012)
    with np.errstate(invalid="ignore", divide="ignore"):
        pg = np.where(ok, np.log(N_LOG * c2) / np.log(N_LOG * c3), np.nan).astype(np.float32)
        pr = np.where(ok & (c4 > 0.0015), np.log(N_LOG * c2) / np.log(N_LOG * c4), np.nan).astype(np.float32)
    # optically deep: green not above the deep-water level
    g_deep = c3[deep]
    gd_med, gd_sd = float(np.median(g_deep)), float(1.4826 * np.median(np.abs(g_deep - np.median(g_deep))))
    pg_deep = pg[deep & ok]
    pg_cut = float(np.nanpercentile(pg_deep, 5)) if np.isfinite(pg_deep).sum() > 100 else np.inf
    od = ok & ((c3 < gd_med + 3 * gd_sd + 0.0005) | (pg >= pg_cut))
    rec["pg_deep_p5"] = pg_cut
    rec.update({"green_deep_med": gd_med, "green_deep_sd": gd_sd, "optically_deep_frac": float(od.sum() / max(ok.sum(), 1))})

    iy, ix = cells["iy"].values, cells["ix"].values
    pg_c, pr_c = pg[iy, ix], pr[iy, ix]
    target = cells["depth"].values + h_rel
    cm = ok[iy, ix] & ~od[iy, ix]
    rec["cal_cells"] = int(cm.sum())
    j = dump["n"]
    dump["pg"][j], dump["pr"][j] = pg_c, pr_c
    dump["valid"][j] = cm
    dump["h"][j] = h_rel
    dump["date"].append(item_date)
    dump["n"] += 1
    fg, fr = fit_scene(pg_c, pr_c, target, cm)
    rec["fit_green"], rec["fit_red"] = fg, fr
    if fg is None or fg["n"] < FIT_MIN_N or fg["r2"] < FIT_MIN_R2 or fg["m1"] <= 0:
        rec["reject"] = "poor fit"; return rec
    # max depth reached by calibration -> extinction cap
    cal_t = target[cm & np.isfinite(pg_c)]
    cap = float(np.percentile(cal_t, 99)) + 1.0
    d_inst = predict(pg, pr, fg, fr)
    d = d_inst - h_rel
    valid = ok & ~od & np.isfinite(d) & (d_inst <= cap) & (d > -2)
    dcm = np.where(valid, np.round(np.clip(d, -2, 40) * 100), NODATA).astype(np.int16)
    stacks["depth"][k_scene] = dcm
    stacks["deepflag"][k_scene] = (od | (ok & (d_inst > cap))).astype(np.uint8) + 2 * ok.astype(np.uint8)
    rgb = np.stack([c4, c3, c2])
    stacks["rgb"][k_scene] = np.where(water, np.clip(np.round(rgb * 1000), 1, 255), 0).astype(np.uint8)
    rec["cap_inst_m"] = cap
    # cross-validation predictions at held-out cells
    for f in range(4):
        tr = cm & (cells["fold"].values != f)
        fgf, frf = fit_scene(pg_c, pr_c, target, tr)
        if fgf is None or fgf["m1"] <= 0:
            continue
        te = (cells["fold"].values == f) & ok[iy, ix] & ~od[iy, ix]
        p_inst = predict(pg_c[te], pr_c[te], fgf, frf)
        p = p_inst - h_rel
        p = np.where(p_inst <= cap, p, np.nan)
        cv_pred[k_scene, np.where(te)[0]] = p
    rec["accepted"] = True
    return rec


# ------------------------------------------------------------------ composite
def composite(stacks, n, out: Path):
    tr = transform()
    med = np.full((H, W), np.nan, np.float32)
    iqr = np.full((H, W), np.nan, np.float32)
    cnt = np.zeros((H, W), np.int16)
    deepfrac = np.full((H, W), np.nan, np.float32)
    rgb = np.zeros((3, H, W), np.float32)
    for r0 in range(0, H, 50):
        r1 = min(H, r0 + 50)
        blk = stacks["depth"][:n, r0:r1].astype(np.float32)
        blk[blk == NODATA] = np.nan
        blk /= 100
        c = np.isfinite(blk).sum(0)
        with np.errstate(all="ignore"):
            q = np.nanpercentile(blk, [25, 50, 75], axis=0)
        cnt[r0:r1] = c
        m = c >= 3
        med[r0:r1] = np.where(m, q[1], np.nan)
        iqr[r0:r1] = np.where(m, q[2] - q[0], np.nan)
        fl = stacks["deepflag"][:n, r0:r1]
        seen = (fl & 2) > 0
        with np.errstate(all="ignore"):
            deepfrac[r0:r1] = ((fl & 1) > 0).sum(0) / np.maximum(seen.sum(0), 1)
        deepfrac[r0:r1][seen.sum(0) == 0] = np.nan
        rb = stacks["rgb"][:n, :, r0:r1].astype(np.float32)
        rb[rb == 0] = np.nan
        with np.errstate(all="ignore"):
            rgb[:, r0:r1] = np.nanmedian(rb, axis=0) / 1000
    # pixels mostly flagged optically deep -> not trusted
    med_masked = np.where((deepfrac < 0.5) | ~np.isfinite(deepfrac), med, np.nan)
    write_tif(out / "depth_mllw_median.tif", med_masked.astype(np.float32), tr, nodata=np.nan)
    write_tif(out / "depth_mllw_median_unmasked.tif", med, tr, nodata=np.nan)
    write_tif(out / "depth_iqr.tif", iqr, tr, nodata=np.nan)
    write_tif(out / "depth_count.tif", cnt, tr, dtype="int16")
    write_tif(out / "optically_deep_frac.tif", deepfrac, tr, nodata=np.nan)
    write_tif(out / "rgb_deglint_median.tif", (np.nan_to_num(rgb) * 1e4).astype(np.int16), tr, dtype="int16")
    return med_masked


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "analysis_out/project10") / "sdb"
    out.mkdir(parents=True, exist_ok=True)
    wl = load_wl()
    models = yearly_models(wl, range(2017, datetime.now(timezone.utc).year + 1))
    mllw = mllw_from_obs(wl)
    cells = load_cells(mllw)

    items = dedupe(search_items(2017))
    by_date: dict[str, list] = {}
    for it in items:
        by_date.setdefault(it["datetime"][:10], []).append(it)
    dates = sorted(by_date)
    log("dates", len(dates))
    with ThreadPoolExecutor(8) as ex:
        clear = dict(zip(dates, ex.map(lambda d: screen(by_date[d]), dates)))
    pd.Series(clear, name="clear_water_frac").to_csv(out / "screen.csv")
    use = [d for d in dates if clear[d] >= CLEAR_MIN]
    log("dates passing screen", len(use))
    S["screen"] = {"dates": len(dates), "passed": len(use)}

    tmp = Path("/tmp/p10stacks"); tmp.mkdir(exist_ok=True)
    N = len(use)
    stacks = {"depth": np.lib.format.open_memmap(tmp / "depth.npy", "w+", np.int16, (N, H, W)),
              "deepflag": np.lib.format.open_memmap(tmp / "flag.npy", "w+", np.uint8, (N, H, W)),
              "rgb": np.lib.format.open_memmap(tmp / "rgb.npy", "w+", np.uint8, (N, 3, H, W))}
    cv_pred = np.full((N, len(cells)), np.nan, np.float32)
    dump = {"pg": np.full((N, len(cells)), np.nan, np.float16), "pr": np.full((N, len(cells)), np.nan, np.float16),
            "valid": np.zeros((N, len(cells)), bool), "h": np.zeros(N, np.float32), "date": [], "n": 0}
    recs, k = [], 0

    def batched():
        with ThreadPoolExecutor(6) as ex:
            for i in range(0, len(use), 12):
                chunk = use[i:i + 12]
                yield from zip(chunk, ex.map(lambda d: read_scene(by_date[d]), chunk))

    if True:
        for d, data in batched():
            if data is None:
                recs.append({"date": d, "reject": "read error"}); continue
            it = by_date[d][0]
            ts = pd.Timestamp(it["datetime"])
            if ts.tzinfo is not None:
                ts = ts.tz_convert("UTC").tz_localize(None)
            t = pd.DatetimeIndex([ts])
            lev, src = level_at(t, wl, models)
            h_rel = float(lev[0] - mllw)
            try:
                rec = process(d, data, cells, h_rel, stacks, k, cv_pred, dump)
            except Exception:  # noqa: BLE001
                rec = {"date": d, "reject": "error", "trace": traceback.format_exc()[-600:]}
            rec.update({"time_utc": str(t[0]), "h_above_mllw": round(h_rel, 3), "level_src": src[0],
                        "tiles": "+".join(sorted(i["tile"] for i in by_date[d]))})
            if rec.get("accepted"):
                rec["k"] = k; k += 1
            recs.append(rec)
            log(d, "accepted" if rec.get("accepted") else rec.get("reject"),
                {kk: (round(v["r2"], 3), v["n"], round(v["rmse"], 2)) for kk, v in
                 (("g", rec.get("fit_green")), ("r", rec.get("fit_red"))) if isinstance(v, dict)})
            del data
    flat = []
    for r in recs:
        f = {kk: v for kk, v in r.items() if not isinstance(v, dict)}
        for kk in ("fit_green", "fit_red"):
            if isinstance(r.get(kk), dict):
                f.update({f"{kk[4]}_{a}": b for a, b in r[kk].items()})
        if isinstance(r.get("slopes"), dict):
            f.update({f"glint_slope_{a}": b for a, b in r["slopes"].items()})
        flat.append(f)
    nd = dump["n"]
    np.savez_compressed(out / "cells_ratios.npz", pg=dump["pg"][:nd], pr=dump["pr"][:nd], valid=dump["valid"][:nd],
                        h=dump["h"][:nd], date=np.array(dump["date"]))
    sc = pd.DataFrame(flat)
    sc.to_csv(out / "scenes_sdb.csv", index=False, float_format="%.5f")
    S["scenes"] = {"read": len(recs), "accepted": k,
                   "reject_reasons": sc["reject"].value_counts().to_dict() if "reject" in sc else {}}
    log("accepted scenes", k)
    if k == 0:
        (out / "summary.json").write_text(json.dumps(S, indent=1, default=str)); return

    med = composite(stacks, k, out)
    # cross-validation of the composite: median over scenes of held-out predictions
    cvp = cv_pred[:k]
    with np.errstate(all="ignore"):
        cv_med = np.nanmedian(cvp, axis=0)
        cv_n = np.isfinite(cvp).sum(0)
    cells["cv_pred"] = cv_med
    cells["cv_n"] = cv_n
    cells["map_depth"] = med[cells["iy"], cells["ix"]]
    cells.to_csv(out / "cells_cv.csv.gz", index=False, float_format="%.3f")
    v = cells[(cells["cv_n"] >= 3) & np.isfinite(cells["cv_pred"])]
    res = v["cv_pred"] - v["depth"]
    bands = {}
    for a, b in [(0, 2), (2, 5), (5, 10), (10, 15), (15, 20), (20, 25), (0, 15)]:
        m = v["depth"].between(a, b, inclusive="left")
        if m.sum():
            bands[f"{a}-{b}"] = {"n": int(m.sum()), "bias": round(float(res[m].mean()), 3),
                                 "rmse": round(float(np.sqrt((res[m] ** 2).mean())), 3),
                                 "mae": round(float(res[m].abs().mean()), 3)}
    S["cv"] = {"cells_with_cv": int(len(v)), "cells_total": int(len(cells)), "by_depth": bands,
               "gate2_pass": bool(bands.get("0-15", {}).get("rmse", 99) <= 1.5 and abs(bands.get("0-15", {}).get("bias", 99)) <= 0.5)}
    S["coverage"] = {"pixels_with_depth": int(np.isfinite(med).sum()), "km2": round(float(np.isfinite(med).sum()) / 1e4, 2)}
    S["mllw_station_datum_m"] = round(mllw, 3)
    S["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "summary.json").write_text(json.dumps(S, indent=1, default=str))
    log("CV", S["cv"])


if __name__ == "__main__":
    main()
