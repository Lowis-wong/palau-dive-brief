"""Monthly refit of Malakal tide constants (Project 2). Runs on GitHub Actions.

Fetches the last ~14 months of hourly water level (UHSLC fast delivery via ERDDAP, topped up with
IOC real-time), fits the 37-constituent least-squares model to the latest 365 days, checks it,
and only then replaces data/tide_constants.json. Writes a short report either way.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import harmonics as H  # noqa: E402
import step1 as S1  # noqa: E402

CONST = ROOT / "data" / "tide_constants.json"
REPORT = ROOT / "data" / "tide_constants_report.json"
T0 = pd.Timestamp("2000-01-01")


def th(idx):
    return ((idx - T0) / pd.Timedelta(hours=1)).to_numpy(float)


def main() -> None:
    now = datetime.now(timezone.utc)
    rep: dict = {"run_utc": now.strftime("%Y-%m-%d %H:%M"), "replaced": False, "checks": {}}
    old = json.loads(CONST.read_text()) if CONST.exists() else None
    fast = S1.fetch_erddap("global_hourly_fast", now.year - 2, now.year)
    ioc_wide = S1.fetch_ioc(days=90)
    src = {"fast": fast}
    ioc_h = S1.ioc_hourly(ioc_wide) if ioc_wide is not None else None
    wl, _ = S1.merge_water_level(src, ioc_h)
    end = wl.last_valid_index()
    win = wl[end - pd.Timedelta(days=365):end]
    cov = float(win.notna().mean())
    rep["fit_end_utc"] = str(end)
    rep["checks"]["coverage"] = round(cov, 3)
    names = H.select_constituents(24 * 365, include_long_period=True)
    m = H.fit(th(win.index), win.to_numpy(), names)
    resid = win.to_numpy() - H.predict(th(win.index), m)
    rep["checks"]["resid_rms_m"] = round(float(np.nanstd(resid)), 4)

    # datum: keep NOAA MLLW offset consistent by matching the mean of NOAA hourly predictions over the window
    import urllib.parse
    import urllib.request
    q = urllib.parse.urlencode({"product": "predictions", "application": "palau_dive_brief", "station": "1841367",
                                "begin_date": (end - pd.Timedelta(days=364)).strftime("%Y%m%d"),
                                "end_date": end.strftime("%Y%m%d"), "datum": "MLLW", "time_zone": "gmt",
                                "units": "metric", "interval": "h", "format": "json"})
    d = json.loads(urllib.request.urlopen("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?" + q, timeout=120).read())
    noaa = pd.Series([float(r["v"]) for r in d["predictions"]], index=pd.to_datetime([r["t"] for r in d["predictions"]]))
    ours = pd.Series(H.predict(th(noaa.index), m), index=noaa.index)
    mllw = float((noaa - ours).mean())
    # timing vs NOAA: best shift (expected: ours ~15-18 min earlier)
    idx6 = pd.date_range(noaa.index[0] - pd.Timedelta(hours=2), noaa.index[-1] + pd.Timedelta(hours=2), freq="6min")
    o6 = pd.Series(H.predict(th(idx6), m), index=idx6)  # exact 6-min prediction, no interpolation needed
    best = min(((L, float((noaa - mllw - o6.shift(freq=f"{L}min").reindex(noaa.index)).std())) for L in range(-60, 61, 3)),
               key=lambda x: x[1])
    rep["checks"]["shift_to_match_noaa_min"] = best[0]
    m2 = m["amps"]["M2"]
    rep["checks"]["M2"] = [round(m2[0], 4), round(m2[1], 2)]
    ok = cov >= 0.8 and rep["checks"]["resid_rms_m"] < 0.15 and 3 <= best[0] <= 30
    if old:
        om2 = next(c for c in old["constituents"] if c["name"] == "M2")
        dA = abs(m2[0] - om2["amp"])
        dP = abs((m2[1] - om2["phase_deg"] + 180) % 360 - 180)
        rep["checks"]["M2_change"] = [round(dA, 4), round(dP, 2)]
        ok = ok and dA < 0.03 and dP < 5
    rep["passed"] = bool(ok)
    if ok:
        out = {"station": "Malakal (UHSLC 007 / IOC mala)", "epoch_utc": "2000-01-01T00:00:00", "units": "m",
               "fit_start_utc": str(win.index[0]), "fit_end_utc": str(end), "coverage": round(cov, 3),
               "mllw_offset_m": round(mllw, 4),
               "note": "h(t) = mean + sum(a_k cos(w_k t) + b_k sin(w_k t)), t in hours since epoch, w in deg/h; add mllw_offset_m for NOAA MLLW datum",
               "mean": round(float(m["coef"][0]), 5),
               "constituents": [{"name": k, "speed_deg_h": H.SPEEDS[k], "a": round(float(m["coef"][1 + 2 * i]), 5),
                                 "b": round(float(m["coef"][2 + 2 * i]), 5), "amp": round(m["amps"][k][0], 4),
                                 "phase_deg": round(m["amps"][k][1], 2)} for i, k in enumerate(names)]}
        CONST.write_text(json.dumps(out, indent=1))
        rep["replaced"] = True
    REPORT.write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:  # noqa: BLE001
        import traceback
        REPORT.write_text(json.dumps({"run_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                                      "replaced": False, "error": f"{type(ex).__name__}: {ex}",
                                      "traceback": traceback.format_exc()[-3000:]}, indent=1))
        print(traceback.format_exc())
        sys.exit(1)
