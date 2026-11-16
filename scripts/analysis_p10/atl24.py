"""Project 10 pilot, part A only (ATL24 inventory). Usage: python scripts/analysis_p10/atl24.py OUT_DIR"""
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "analysis_out/project10")
out.mkdir(parents=True, exist_ok=True)
wl = pilot.load_wl()
models = pilot.yearly_models(wl, range(2018, datetime.now(timezone.utc).year + 1))
mllw = pilot.mllw_from_obs(wl)
pilot.SUMMARY["mllw_station_datum_m"] = round(mllw, 3)
try:
    pilot.part_a(out, wl, models, mllw)
except Exception:  # noqa: BLE001
    pilot.SUMMARY["errors"]["atl24"] = traceback.format_exc()[-2000:]
    pilot.log("FAILED", traceback.format_exc()[-800:])
(out / "summary_atl24.json").write_text(json.dumps(pilot.SUMMARY, indent=1, default=str))
