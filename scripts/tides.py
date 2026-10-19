"""Tide and current logic for the Palau dive brief.

Pure functions only (no network). All times are Palau local time (UTC+9, no DST),
represented as naive datetimes.

Model
-----
* The water-level curve comes from NOAA's 6-minute predictions for Malakal Harbor
  when available; otherwise it is reconstructed from the high/low list with a
  cosine between consecutive extremes (good to a few cm for semidiurnal tides).
* Stage: between a low and the next high the tide is "incoming" (flood, ocean ->
  lagoon); between a high and the next low it is "outgoing" (ebb, lagoon -> ocean).
* Flow strength is taken from the rate of change of the water level (m/h). Peak
  rate of a stage is roughly pi * range / (2 * duration).
* Each site shifts the Malakal clock by its lag: the site behaves at time t the way
  Malakal did at t - lag. Lags start as estimates and are calibrated from logs.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

SYNODIC = 29.530588853
REF_NEW_MOON = datetime(2000, 1, 6, 18, 14) + timedelta(hours=9)  # Palau time

# Rate thresholds (cm/h) between flow levels 1|2|3|4|5
LEVEL_EDGES_CM_H = (10, 20, 30, 40)
SLACK_FRACTION = 0.30  # below this share of the stage's peak rate -> "slack"


@dataclass
class Extreme:
    t: datetime
    h: float
    kind: str  # "H" or "L"


def parse_hilo(rows: list[dict]) -> list[Extreme]:
    out = []
    for r in rows:
        out.append(Extreme(datetime.strptime(r["t"], "%Y-%m-%d %H:%M"), float(r["v"]), r["type"].upper()[0]))
    out.sort(key=lambda e: e.t)
    return out


class TideCurve:
    """Water level and rate at any time, from 6-min series (preferred) or hi/lo."""

    def __init__(self, extremes: list[Extreme], series: list[tuple[datetime, float]] | None = None):
        if len(extremes) < 3:
            raise ValueError("need at least 3 tide extremes")
        self.ext = extremes
        self._ext_t = [e.t for e in extremes]
        self.series = series or None
        if self.series:
            self._ser_t = [p[0] for p in self.series]

    # --- level -----------------------------------------------------------------
    def _bracket(self, t: datetime) -> tuple[Extreme, Extreme] | None:
        i = bisect.bisect_right(self._ext_t, t)
        if i == 0 or i >= len(self.ext):
            return None
        return self.ext[i - 1], self.ext[i]

    def level(self, t: datetime) -> float | None:
        if self.series:
            i = bisect.bisect_left(self._ser_t, t)
            if 0 < i < len(self.series):
                (t0, h0), (t1, h1) = self.series[i - 1], self.series[i]
                f = (t - t0).total_seconds() / max((t1 - t0).total_seconds(), 1)
                return h0 + f * (h1 - h0)
            if i == 0 and self._ser_t[0] == t:
                return self.series[0][1]
        b = self._bracket(t)
        if not b:
            return None
        e1, e2 = b
        frac = (t - e1.t).total_seconds() / (e2.t - e1.t).total_seconds()
        return (e1.h + e2.h) / 2 + (e1.h - e2.h) / 2 * math.cos(math.pi * frac)

    def rate(self, t: datetime) -> float | None:
        """dh/dt in m/h (positive = rising = incoming)."""
        a, b = self.level(t - timedelta(minutes=6)), self.level(t + timedelta(minutes=6))
        if a is None or b is None:
            return None
        return (b - a) / 0.2

    # --- stage -----------------------------------------------------------------
    def stage(self, t: datetime) -> dict | None:
        b = self._bracket(t)
        if not b:
            return None
        e1, e2 = b
        dur_h = (e2.t - e1.t).total_seconds() / 3600
        rng = abs(e2.h - e1.h)
        return {
            "direction": "incoming" if e1.kind == "L" else "outgoing",
            "from": e1, "to": e2,
            "range_m": rng,
            "peak_rate": math.pi * rng / (2 * dur_h),
            "elapsed_h": (t - e1.t).total_seconds() / 3600,
            "remaining_h": (e2.t - t).total_seconds() / 3600,
        }


def flow_level(rate_m_h: float) -> int:
    cm = abs(rate_m_h) * 100
    lvl = 1
    for edge in LEVEL_EDGES_CM_H:
        if cm >= edge:
            lvl += 1
    return lvl


def site_state(curve: TideCurve, site: dict, t: datetime) -> dict | None:
    """Predicted current at a site at local time t."""
    tm = t - timedelta(minutes=site.get("lag_min", 0))
    st = curve.stage(tm)
    r = curve.rate(tm)
    if st is None or r is None:
        return None
    eff = abs(r) * site.get("strength_factor", 1.0)
    rel = abs(r) / st["peak_rate"] if st["peak_rate"] > 0 else 0
    direction = "slack" if rel < SLACK_FRACTION else st["direction"]
    return {"direction": direction, "stage": st["direction"], "level": flow_level(eff), "rate_cm_h": round(eff * 100)}


def site_segments(curve: TideCurve, site: dict, day: date, start_h=6, end_h=18, step_min=5) -> list[dict]:
    """Contiguous blocks of incoming / outgoing / slack during the dive day."""
    t = datetime.combine(day, datetime.min.time()) + timedelta(hours=start_h)
    end = datetime.combine(day, datetime.min.time()) + timedelta(hours=end_h)
    segs: list[dict] = []
    while t <= end:
        s = site_state(curve, site, t)
        if s:
            if segs and segs[-1]["direction"] == s["direction"]:
                seg = segs[-1]
                seg["end"] = t
                if s["level"] > seg["peak_level"]:
                    seg["peak_level"], seg["peak_time"] = s["level"], t
            else:
                segs.append({"direction": s["direction"], "start": t, "end": t,
                             "peak_level": s["level"], "peak_time": t})
        t += timedelta(minutes=step_min)
    for s in segs:
        for k in ("start", "end", "peak_time"):
            s[k] = s[k].strftime("%H:%M")
    return segs


def moon(t: datetime) -> dict:
    age = ((t - REF_NEW_MOON).total_seconds() / 86400) % SYNODIC
    illum = (1 - math.cos(2 * math.pi * age / SYNODIC)) / 2
    names = [(1.0, "New moon"), (6.4, "Waxing crescent"), (8.4, "First quarter"),
             (13.8, "Waxing gibbous"), (15.8, "Full moon"), (21.1, "Waning gibbous"),
             (23.1, "Last quarter"), (28.5, "Waning crescent"), (SYNODIC + 1, "New moon")]
    name = next(n for lim, n in names if age < lim)
    return {"age_days": round(age, 1), "illumination": round(illum, 2), "phase": name}


def day_ranges(extremes: list[Extreme], day: date) -> list[float]:
    """Ranges of every stage that starts on this day."""
    out = []
    for a, b in zip(extremes, extremes[1:]):
        if a.t.date() == day:
            out.append(abs(b.h - a.h))
    return out


def tide_strength_label(ratio: float) -> str:
    if ratio >= 1.15:
        return "Spring"
    if ratio <= 0.85:
        return "Neap"
    return "Mid"
