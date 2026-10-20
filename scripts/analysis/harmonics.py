"""Least-squares tidal harmonic fit (NOAA 37-constituent set), numpy only.

Fitted per calendar year: within one year the 18.6-year nodal modulation is almost
constant, so it is absorbed into the fitted amplitudes and no nodal corrections are
needed. Constituents that cannot be separated with the available record length
(Rayleigh criterion) are dropped for that year.
"""
from __future__ import annotations

import numpy as np

# Speeds in degrees per hour (NOAA CO-OPS harmonic constituent table).
SPEEDS = {
    "M2": 28.9841042, "S2": 30.0, "N2": 28.4397295, "K1": 15.0410686, "M4": 57.9682084,
    "O1": 13.9430356, "M6": 86.9523127, "MK3": 44.0251729, "S4": 60.0, "MN4": 57.4238337,
    "NU2": 28.5125831, "S6": 90.0, "MU2": 27.9682084, "2N2": 27.8953548, "OO1": 16.1391017,
    "LAM2": 29.4556253, "S1": 15.0, "M1": 14.4966939, "J1": 15.5854433, "MM": 0.5443747,
    "SSA": 0.0821373, "SA": 0.0410686, "MSF": 1.0158958, "MF": 1.0980331, "RHO": 13.4715145,
    "Q1": 13.3986609, "T2": 29.9589333, "R2": 30.0410667, "2Q1": 12.8542862, "P1": 14.9589314,
    "2SM2": 31.0158958, "M3": 43.4761563, "L2": 29.5284789, "2MK3": 42.9271398, "K2": 30.0821373,
    "M8": 115.9364166, "MS4": 58.9841042,
}
LONG_PERIOD = {"SA", "SSA", "MM", "MF", "MSF"}


def select_constituents(span_hours: float, include_long_period: bool) -> list[str]:
    """Greedy Rayleigh selection: keep a constituent only if it is separable from all kept ones."""
    order = ["M2", "S2", "K1", "O1", "N2", "K2", "P1", "Q1", "M4", "MS4", "MN4", "M6", "NU2", "MU2",
             "2N2", "L2", "T2", "J1", "OO1", "M1", "MK3", "2MK3", "M3", "S4", "LAM2", "RHO", "2Q1",
             "2SM2", "S1", "R2", "S6", "M8", "SA", "SSA", "MM", "MF", "MSF"]
    keep: list[str] = []
    for c in order:
        if c in LONG_PERIOD and not include_long_period:
            continue
        f = SPEEDS[c] / 360.0  # cycles per hour
        if f > 0 and 1.0 / f > span_hours:  # period longer than the record
            continue
        if all(abs(f - SPEEDS[k] / 360.0) * span_hours >= 1.0 for k in keep):
            keep.append(c)
    return keep


def design(t_hours: np.ndarray, names: list[str]) -> np.ndarray:
    cols = [np.ones_like(t_hours)]
    for n in names:
        w = np.deg2rad(SPEEDS[n])
        cols += [np.cos(w * t_hours), np.sin(w * t_hours)]
    return np.column_stack(cols)


def fit(t_hours: np.ndarray, h: np.ndarray, names: list[str]) -> dict:
    ok = np.isfinite(h)
    A = design(t_hours[ok], names)
    coef, *_ = np.linalg.lstsq(A, h[ok], rcond=None)
    amps = {}
    for i, n in enumerate(names):
        a, b = coef[1 + 2 * i], coef[2 + 2 * i]
        amps[n] = (float(np.hypot(a, b)), float(np.degrees(np.arctan2(b, a)) % 360))
    return {"names": names, "coef": coef, "amps": amps}


def predict(t_hours: np.ndarray, model: dict, include_mean: bool = True) -> np.ndarray:
    y = design(t_hours, model["names"]) @ model["coef"]
    return y if include_mean else y - model["coef"][0]
