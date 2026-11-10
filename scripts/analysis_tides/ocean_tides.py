"""Project 1: predict OPEN-OCEAN tides just outside Palau's reef from global tide models.

Runs on GitHub Actions. Models (free, no registration):
  * EOT20 (DGFI-TUM, altimetry-based, 1/8 deg, CC BY 4.0)
  * GOT4.10c and GOT5.6 (NASA GSFC)
Writes hourly predictions (UTC, metres) for 2023-2025 at offshore points, plus a log of
what worked. The comparison with Malakal (inside the lagoon) is done afterwards.

    python ocean_tides.py OUT_DIR MODELS_DIR
"""
from __future__ import annotations

import inspect
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(sys.argv[1])
MODELS_DIR = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
LOG: list[str] = []
INFO: dict = {"errors": {}, "models": {}}

POINTS = {
    # lat, lon. Offshore, beyond the reef, so the global grids have open water.
    "W_ulong": (7.30, 134.05),     # ~15 km west of Ulong Channel
    "W_german": (7.12, 134.08),    # ~15 km WSW of German Channel / Blue Corner
    "W_far": (7.30, 133.80),       # 40 km west, reference
    "S_peleliu": (6.85, 134.25),
    "E_babeldaob": (7.45, 134.80),
    "N_kayangel": (8.20, 134.65),
    "malakal_cell": (7.33, 134.45),  # model value at the gauge (extrapolated, for reference only)
}


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    LOG.append(line)


def main():
    import pyTMD
    INFO["pyTMD"] = getattr(pyTMD, "__version__", "?")
    log("pyTMD", INFO["pyTMD"])
    for p in sorted(MODELS_DIR.rglob("*"))[:300]:
        if p.is_file():
            LOG.append(f"  file {p.relative_to(MODELS_DIR)} {p.stat().st_size // 1024} KB")
    t = pd.date_range("2023-01-01", "2025-12-31 23:00", freq="1h")
    delta = ((t - pd.Timestamp(datetime(2000, 1, 1))) / pd.Timedelta(seconds=1)).to_numpy(dtype=float)
    fn = pyTMD.compute.tide_elevations
    log("tide_elevations signature:", str(inspect.signature(fn))[:800])
    for model in ("EOT20", "GOT4.10", "GOT5.6"):
        try:
            cols = {}
            for name, (la, lo) in POINTS.items():
                x = np.full(len(t), lo)
                y = np.full(len(t), la)
                try:
                    z = fn(x, y, delta, DIRECTORY=str(MODELS_DIR), MODEL=model, EPOCH=(2000, 1, 1, 0, 0, 0),
                           TYPE="drift", TIME="UTC", EPSG=4326, EXTRAPOLATE=True, CUTOFF=30)
                except TypeError as ex:
                    log(f"  {model}: TypeError {ex}; retrying with lower-case keywords")
                    z = fn(x, y, delta, directory=str(MODELS_DIR), model=model, epoch=(2000, 1, 1, 0, 0, 0),
                           type="drift", standard="UTC", crs=4326, extrapolate=True, cutoff=30)
                z = np.ma.filled(np.ma.asarray(z, dtype=float), np.nan).ravel()
                cols[name] = z
                log(f"  {model} {name}: n={np.isfinite(z).sum()} std={np.nanstd(z):.3f}")
            df = pd.DataFrame(cols, index=pd.DatetimeIndex(t, name="time_utc"))
            df.to_csv(OUT / f"ocean_tide_{model.replace('.', '_')}.csv.gz", float_format="%.4f")
            INFO["models"][model] = "ok"
        except Exception as ex:  # noqa: BLE001
            INFO["errors"][model] = f"{type(ex).__name__}: {ex}"
            log(f"!! {model} failed: {ex}\n{traceback.format_exc()}")
    INFO["points"] = POINTS


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:  # noqa: BLE001
        INFO["errors"]["main"] = f"{type(ex).__name__}: {ex}"
        log(traceback.format_exc())
    finally:
        (OUT / "info.json").write_text(json.dumps(INFO, indent=1, default=str))
        (OUT / "log.txt").write_text("\n".join(LOG))
