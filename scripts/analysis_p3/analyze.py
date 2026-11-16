"""Project 3, stages E-F: tide/sea-state pairing and the turn-time regression.

Runs locally on the pass-1/pass-2 tables (no imagery needed).
Usage: python scripts/analysis_p3/analyze.py DATA_DIR OUT_DIR
  DATA_DIR holds scenes.csv and box_stats.csv (from analysis-data/project3)
"""
from __future__ import annotations

import gzip
import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
M2_MIN = 360 / 28.9841042 * 60  # 745.2 min
MID = (1.0, 1.6)
MIN_VALID = 0.6
RNG = np.random.default_rng(42)


def west_waves() -> pd.Series:
    """Daily mean west wave height (ERA5 via Open-Meteo, Step 1), indexed by Palau date string."""
    names = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "--name-only", "origin/analysis-data", "step1/forcing/"],
                           capture_output=True, text=True).stdout.split()
    frames = []
    for n in names:
        if "/wave_W_" in n and int(n[-11:-7]) >= 2016:
            raw = subprocess.run(["git", "-C", str(ROOT), "show", f"origin/analysis-data:{n}"], capture_output=True).stdout
            frames.append(pd.read_csv(io.BytesIO(gzip.decompress(raw)), parse_dates=["time_utc"]))
    w = pd.concat(frames)
    w["date"] = (w["time_utc"] + pd.Timedelta(hours=9)).dt.strftime("%Y-%m-%d")
    return w.groupby("date")["wave_height"].mean()


def build_table(data: Path, code_out="OUT1", code_ctrl="CTRL", var="red_gc") -> pd.DataFrame:
    sc = pd.concat([pd.read_csv(data / f"scenes_{c}.csv").assign(channel=c) for c in ("ulong", "german")])
    bs = pd.read_csv(data / "box_stats.csv")
    bs = bs[bs["valid_frac"] >= MIN_VALID]
    wide = bs.pivot_table(index=["id", "channel"], columns="code", values=var)
    wide = wide.reset_index().merge(sc, on=["id", "channel"], how="left")
    if code_out in wide and code_ctrl in wide:
        wide["plume"] = wide[code_out] - wide[code_ctrl]
    wide["theta"] = 2 * np.pi * wide["min_from_hw"] / M2_MIN  # 0 = Malakal high water, >0 after
    try:
        ww = west_waves()
        wide["west_wave_m"] = wide["date"].map(ww)
    except Exception:  # noqa: BLE001
        wide["west_wave_m"] = np.nan
    return wide


def fit_turn(df: pd.DataFrame, y="plume", use_range=True):
    """y = c0 + c1*R + R*(a cos th + b sin th)  ->  R*A*sin(th - phi0); phi0 = turn to ebb (up-crossing)."""
    d = df.dropna(subset=[y, "theta", "range_m"])
    R = d["range_m"].values if use_range else np.ones(len(d))
    th = d["theta"].values
    X = np.column_stack([np.ones(len(d)), d["range_m"].values, R * np.cos(th), R * np.sin(th)])
    coef, *_ = np.linalg.lstsq(X, d[y].values, rcond=None)
    a, b = coef[2], coef[3]
    phi0 = np.arctan2(-a, b)
    amp = np.hypot(a, b)
    resid = d[y].values - X @ coef
    r2 = 1 - resid.var() / d[y].values.var()
    return {"turn_min": float(phi0 / (2 * np.pi) * M2_MIN), "amp": float(amp), "r2": float(r2), "n": int(len(d)),
            "coef": [float(c) for c in coef]}


def wrap_min(m):
    return (m + M2_MIN / 2) % M2_MIN - M2_MIN / 2


def bootstrap(df, y="plume", n=1000, use_range=True):
    base = fit_turn(df, y, use_range)
    vals = []
    idx = np.arange(len(df))
    for _ in range(n):
        s = df.iloc[RNG.choice(idx, len(idx), replace=True)]
        try:
            v = fit_turn(s, y, use_range)["turn_min"]
            vals.append(base["turn_min"] + wrap_min(v - base["turn_min"]))
        except Exception:  # noqa: BLE001
            pass
    vals = np.array(vals)
    base["ci90"] = [float(np.percentile(vals, 5)), float(np.percentile(vals, 95))]
    base["half_width"] = float((base["ci90"][1] - base["ci90"][0]) / 2)
    return base


def binned(df, y="plume", width=30):
    d = df.dropna(subset=[y])
    b = (d["min_from_hw"] // width) * width + width / 2
    g = d.groupby(b)[y]
    return pd.DataFrame({"n": g.size(), "median": g.median(), "q25": g.quantile(0.25), "q75": g.quantile(0.75)})


def main():
    data = Path(sys.argv[1]); out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    res = {}
    for var in ("red_gc", "green_gc", "red"):
        for code in ("OUT1", "OUT2"):
            t = build_table(data, code_out=code, var=var)
            for ch, d in t.groupby("channel"):
                d = d.dropna(subset=["plume"])
                key = f"{ch}|{code}|{var}"
                mid = d[d["range_m"].between(*MID)]
                r = {"all": bootstrap(d), "mid": bootstrap(mid, use_range=False) if len(mid) > 20 else None,
                     "n_hw90": int(d["min_from_hw"].abs().le(90).sum())}
                post2020 = d[d["date"] >= "2020-01-01"]
                r["post2020"] = fit_turn(post2020) if len(post2020) > 20 else None
                calm = d[(d["west_wave_m"] < 2.0) | d["west_wave_m"].isna()]
                r["calm"] = fit_turn(calm) if len(calm) > 20 else None
                res[key] = r
                if code == "OUT1" and var == "red_gc":
                    t2 = d.copy()
                    t2.to_csv(out / f"table_{ch}.csv", index=False)
                    binned(mid).to_csv(out / f"binned_mid_{ch}.csv")
    (out / "results.json").write_text(json.dumps(res, indent=2))
    for k, v in res.items():
        a = v["all"]; m = v["mid"]
        print(f"{k:28s} n={a['n']:4d} hw90={v['n_hw90']:3d} turn={a['turn_min']:+6.0f} min CI90 ±{a['half_width']:.0f} r2={a['r2']:.2f}"
              + (f" | mid n={m['n']} turn={m['turn_min']:+.0f} ±{m['half_width']:.0f}" if m else ""))


if __name__ == "__main__":
    main()
