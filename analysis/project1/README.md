# Project 1 — lagoon (Malakal) vs open-ocean tide (2026-11-10)

Ocean tides predicted 2023–2025 at offshore points by `scripts/analysis_tides/ocean_tides.py`
(GitHub Actions, pyTMD 3.1.0; EOT20, GOT4.10c, GOT5.6; outputs on `analysis-data` under `tides/`).
EOT20 long-period constituents removed (Godin low-pass) before comparison. Malakal = Step 1 short-period tide fit.

Results (point W_ulong, 7.30N 134.05E; W_german gives the same):
- Malakal lags the western open ocean: M2 +18–22 min, S2 +21–24, K1 +32–36, O1 +14–19 (three models within ~3 min).
- Malakal amplitude 2–5% LARGER than offshore (no damping).
- Peak |lagoon − ocean| by daily range: <1.0 m ≈ 5–6 cm; 1.0–1.3 ≈ 7–8; 1.3–1.6 ≈ 11–12; 1.6–1.9 ≈ 13–15; >1.9 ≈ 16–18 cm.
- Rate of change at sign reversal: 2.2–2.7 cm/h (neap) to 6.5–7.3 cm/h (spring) → a +2.8 cm swell set-up shifts each
  reversal by ~65 min at neap, ~25 min at spring (moderate +1.7 cm: ~39 / ~15 min).
- Head reversal (outflow starts) ~36–42 min before Malakal HW; inflow starts ~30–36 min before Malakal LW (wide spread).
Caveats: Malakal is in Koror harbour, not at the channels; models are 1/8° and points 15 km offshore; flow inertia
delays the current turn after the head reversal; speed scales ~ sqrt(head).
