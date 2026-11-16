"""Project 3, pass 1: scene inventory, clear-sky screening, tide stage, composites and pilot clips.

Usage: python scripts/analysis_p3/pass1.py OUT_DIR
Outputs (OUT_DIR):
  scenes.csv                    one row per acquisition date (tile 53NMH) with clear fractions and tide stage
  <aoi>/composite_rgb.tif       median true colour of clear scenes (8-bit, gamma-stretched for water)
  <aoi>/stats.tif               red median, red p90-p10 (variability), green median, valid count
  <aoi>/ebb_minus_flood.tif     mid-range days: median(ebb HW+1..+4 h) - median(flood HW-4..-1 h), red & green
  <aoi>/flood_rgb.tif, ebb_rgb.tif   median true colour of those two groups (same stretch)
  <aoi>/pilots/*.tif            pilot scenes: true colour (TCI) and glint-corrected red (x10000, int16)
  pilots.csv, summary.json, log.txt
"""
from __future__ import annotations

import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (AOIS, SCL_BAD, SCL_NODATA, SCL_WATER, dedupe, dn_offset, load_wl, log, read_window,  # noqa: E402
                    search_items, tide_stage, write_tif, yearly_models)

MID = (1.0, 1.6)
UNION = (min(a["bbox"][0] for a in AOIS.values()), min(a["bbox"][1] for a in AOIS.values()),
         max(a["bbox"][2] for a in AOIS.values()), max(a["bbox"][3] for a in AOIS.values()))


def sub(arr, tr, bbox, res):
    """Cut a sub-window (UTM bbox) out of an array read for UNION."""
    c0 = int(round((bbox[0] - tr.c) / res)); c1 = int(round((bbox[2] - tr.c) / res))
    r0 = int(round((tr.f - bbox[3]) / res)); r1 = int(round((tr.f - bbox[1]) / res))
    return arr[r0:r1, c0:c1]


def scan_scl(item):
    try:
        a, tr = read_window(item["assets"]["scl"], UNION)
        out = {"id": item["id"]}
        for k, aoi in AOIS.items():
            for part in ("bbox", "core"):
                s = sub(a, tr, aoi[part], 20)
                n = s.size
                valid = s != SCL_NODATA
                bad = np.isin(s, list(SCL_BAD))
                tag = k if part == "bbox" else f"{k}_core"
                out[f"{tag}_valid"] = round(float(valid.mean()), 4)
                out[f"{tag}_clear"] = round(float((~bad).sum() / n), 4)
                out[f"{tag}_water"] = round(float((s == SCL_WATER).sum() / n), 4)
        return out
    except Exception as e:  # noqa: BLE001
        return {"id": item["id"], "error": str(e)[:200]}


def read_scene(item, bbox):
    """Glint-corrected blue/green/red reflectance (10 m) with bad pixels as NaN."""
    off = dn_offset(item)
    bands = {}
    tr = None
    for b in ("blue", "green", "red"):
        a, tr = read_window(item["assets"][b], bbox)
        bands[b] = (a.astype(np.float32) + off) / 10000.0
        bands[b][a == 0] = np.nan
    sw, _ = read_window(item["assets"]["swir16"], bbox, out_res=10)
    scl, _ = read_window(item["assets"]["scl"], bbox, out_res=10)
    shape = bands["red"].shape
    sw = sw[: shape[0], : shape[1]]
    scl = scl[: shape[0], : shape[1]]
    swr = (sw.astype(np.float32) + off) / 10000.0
    water = scl == SCL_WATER
    floor = np.nanpercentile(swr[water], 2) if water.sum() > 500 else np.nanpercentile(swr, 2)
    glint = np.clip(swr - floor, 0, None)
    bad = np.isin(scl, list(SCL_BAD))
    for b in bands:
        v = bands[b] - glint
        v[bad] = np.nan
        bands[b] = v
    return bands, tr


def stretch(rgb):
    """Gamma stretch that keeps deep-water colour differences visible and reef flats unsaturated."""
    v = np.clip(rgb / 0.25, 0, 1) ** 0.5
    out = (v * 254 + 1).astype(np.uint8)
    out[~np.isfinite(rgb)] = 0
    return out


def composites(aoi_key, items, out: Path, scenes: pd.DataFrame):
    aoi = AOIS[aoi_key]
    log(f"[{aoi_key}] composite from {len(items)} scenes")
    stacks = {"blue": [], "green": [], "red": []}
    ids = []
    tr = None

    def job(it):
        try:
            return it["id"], read_scene(it, aoi["bbox"])
        except Exception as e:  # noqa: BLE001
            log("read fail", it["id"], str(e)[:120])
            return it["id"], None

    with ThreadPoolExecutor(12) as ex:
        for sid, res in ex.map(job, items):
            if res is None:
                continue
            bands, t = res
            if tr is None:
                tr = t
                shape = bands["red"].shape
            if bands["red"].shape != shape:
                continue
            for b in stacks:
                stacks[b].append(bands[b])
            ids.append(sid)
    S = {b: np.stack(v) for b, v in stacks.items()}
    log(f"[{aoi_key}] stacked {len(ids)}")
    med = {b: np.nanmedian(S[b], axis=0) for b in S}
    rgb = np.stack([med["red"], med["green"], med["blue"]])
    write_tif(out / aoi_key / "composite_rgb.tif", stretch(rgb), tr, "uint8", nodata=0)
    p90 = np.nanpercentile(S["red"], 90, axis=0)
    p10 = np.nanpercentile(S["red"], 10, axis=0)
    nval = np.isfinite(S["red"]).sum(0).astype(np.float32)
    write_tif(out / aoi_key / "stats.tif", np.stack([med["red"], p90 - p10, med["green"], nval]).astype(np.float32), tr, "float32")

    st = scenes.set_index("id").loc[ids]
    mid = st["range_m"].between(*MID)
    fl = (mid & st["min_from_hw"].between(-240, -60)).values
    eb = (mid & st["min_from_hw"].between(60, 240)).values
    info = {"n_stack": len(ids), "n_flood": int(fl.sum()), "n_ebb": int(eb.sum())}
    if fl.sum() >= 3 and eb.sum() >= 3:
        d = []
        for b in ("red", "green"):
            d.append(np.nanmedian(S[b][eb], 0) - np.nanmedian(S[b][fl], 0))
        d += [np.isfinite(S["red"][eb]).sum(0).astype(np.float32), np.isfinite(S["red"][fl]).sum(0).astype(np.float32)]
        write_tif(out / aoi_key / "ebb_minus_flood.tif", np.stack(d).astype(np.float32), tr, "float32")
        for name, m in (("flood", fl), ("ebb", eb)):
            g = np.stack([np.nanmedian(S[b][m], 0) for b in ("red", "green", "blue")])
            write_tif(out / aoi_key / f"{name}_rgb.tif", stretch(g), tr, "uint8", nodata=0)
    return info


def pilots(aoi_key, scenes: pd.DataFrame, items_by_id, out: Path):
    s = scenes[(scenes["range_m"].between(*MID)) & (scenes[f"{aoi_key}_core_clear"] >= 0.98)
               & (scenes[f"{aoi_key}_clear"] >= 0.9) & (scenes[f"{aoi_key}_valid"] >= 0.99)]
    rows = []
    for stage, lo, hi in (("flood", -240, -120), ("ebb", 120, 240)):
        g = s[s["min_from_hw"].between(lo, hi)].sort_values(f"{aoi_key}_clear", ascending=False).head(10)
        for _, r in g.iterrows():
            it = items_by_id[r["id"]]
            date = r["date"].replace("-", "")
            tag = f"{stage}_{date}_{int(r['min_from_hw']):+04d}"
            try:
                import rasterio
                with rasterio.open(it["assets"]["visual"]) as ds:
                    from rasterio.windows import from_bounds
                    w = from_bounds(*AOIS[aoi_key]["bbox"], transform=ds.transform).round_offsets().round_lengths()
                    tci = ds.read(window=w, boundless=True, fill_value=0)
                    tr = ds.window_transform(w)
                write_tif(out / aoi_key / "pilots" / f"{tag}_tci.tif", tci, tr, "uint8", nodata=0)
                bands, tr2 = read_scene(it, AOIS[aoi_key]["bbox"])
                red = np.where(np.isfinite(bands["red"]), np.clip(bands["red"] * 10000, -32000, 32000), -32768)
                write_tif(out / aoi_key / "pilots" / f"{tag}_red.tif", red.astype(np.int16), tr2, "int16", nodata=-32768)
                rows.append({"aoi": aoi_key, "stage": stage, "tag": tag, **r.to_dict()})
                log(f"[{aoi_key}] pilot {tag}")
            except Exception as e:  # noqa: BLE001
                log("pilot fail", tag, str(e)[:150])
    return rows


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    summary = {"run_utc": datetime.utcnow().isoformat(timespec="seconds")}
    items = search_items()
    log("items found", len(items))
    tiles = pd.Series([i["tile"] for i in items]).value_counts().to_dict()
    summary["items_found"] = len(items); summary["tiles"] = tiles
    items = dedupe(items)
    log("after dedupe", len(items))
    summary["items_dedup"] = len(items)
    by_id = {i["id"]: i for i in items}

    with ThreadPoolExecutor(16) as ex:
        scl = list(ex.map(scan_scl, items))
    df = pd.DataFrame(scl)
    errs = df["error"].notna().sum() if "error" in df else 0
    log("SCL scanned; errors", errs)
    summary["scl_errors"] = int(errs)
    meta = pd.DataFrame([{k: i[k] for k in ("id", "datetime", "platform", "cloud", "baseline", "boa_offset_applied")} for i in items])
    df = meta.merge(df, on="id", how="left")
    df["t_utc"] = pd.to_datetime(df["datetime"]).dt.tz_convert(None)
    df["date"] = (df["t_utc"] + pd.Timedelta(hours=9)).dt.strftime("%Y-%m-%d")
    df["time_palau"] = (df["t_utc"] + pd.Timedelta(hours=9)).dt.strftime("%H:%M")

    wl = load_wl()
    years = sorted(df["t_utc"].dt.year.unique())
    models = yearly_models(wl, years)
    stages = [tide_stage(t.to_pydatetime(), models) for t in df["t_utc"]]
    df = pd.concat([df.reset_index(drop=True), pd.DataFrame(stages)], axis=1)
    df.drop(columns=["datetime"]).to_csv(out / "scenes.csv", index=False)
    log("scenes.csv written", len(df))

    for k in AOIS:
        good = df[(df[f"{k}_core_clear"] >= 0.98) & (df[f"{k}_clear"] >= 0.8) & (df[f"{k}_valid"] >= 0.99)]
        summary[f"{k}_clear_scenes"] = int(len(good))
        summary[f"{k}_clear_mid"] = int(good["range_m"].between(*MID).sum())
        summary[f"{k}_clear_hw90"] = int(good["min_from_hw"].abs().le(90).sum())
        pick = good.sort_values(f"{k}_clear", ascending=False).head(320)
        try:
            summary[f"{k}_composite"] = composites(k, [by_id[i] for i in pick["id"]], out, df)
        except Exception:  # noqa: BLE001
            log(traceback.format_exc())

    rows = []
    for k in AOIS:
        rows += pilots(k, df, by_id, out)
    pd.DataFrame(rows).to_csv(out / "pilots.csv", index=False)
    summary["pilots"] = len(rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log("done", json.dumps(summary, default=str))


if __name__ == "__main__":
    main()
