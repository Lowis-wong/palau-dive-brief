# Project 2 — feasibility: own harmonic constants vs NOAA (2026-11-10)

> Mirror of the claude.ai project doc `claude/project2-feasibility.md` (copied 2026-11-17). Implemented 2026-11-10: the brief now uses our own constants (`data/tide_constants.json`, monthly refit `.github/workflows/refit-tides.yml`); see `dive-brief-status.md`.

**Backtest**: 187 monthly windows 2011–2026. Fit 37-constituent LS (incl. SA/SSA) on the previous 365 days of Malakal hourly observations, predict the next 35 days; NOAA 1841367 hourly predictions (archive fetched to `analysis-data/noaa/`) scored the same way. ~25,000 highs/lows matched to observed extremes (6-min cubic interpolation of hourly obs).

| | ours (refit monthly) | ours (constants 1 yr old) | ours (90-day data gap) | NOAA |
|---|---|---|---|---|
| timing median (pred − obs) | 0 min | 0 | 0 | **+18 min (late)** |
| timing MAE | 9.0 min | 9.6 | 9.1 | 17.5 |
| 90% of highs/lows within | ±18 min | ±18 | ±18 | ±30 |
| hourly scatter (bias removed) | 4.6 cm | 5.1 | 4.6 | 8.5 |
| height error sd | 4.7 cm | 5.1 | 4.8 | 6.3 |

- NOAA lateness stable every year 2011–2026 (median +12 to +18 min); best-fit shift −18 min. Confirmed with two independent observation sources (UHSLC hourly, IOC 1-min real-time, which agree to 5 mm at the hour).
- CORRECTION: Step 1 summary said "NOAA ~12 min early" — wrong sign; NOAA predictions are LATE.
- The remaining ~9 min MAE for ours is mostly weather and hourly sampling in the observations themselves (floor).

**Implications**
- Brief HW/LW currently ~15–18 min late; Tide Forecast app matches NOAA, so it is late too.
- Site lags are relative to whatever prediction is used; switching shifts all windows ~18 min earlier. Channel lags (0 min, uncalibrated) were set relative to NOAA; Project 1 used true Malakal timing.
- Operations: refit monthly (or even yearly) from UHSLC fast + IOC; robust to data gaps (1-year-old constants still ~2x better than NOAA). Keep NOAA fetch as fallback/cross-check. Heights to be referenced to MLLW via NOAA offset.

Verdict: feasible, low risk, clear gain. Pending Lowis's decisions on display (show NOAA/table time alongside?) before implementing.
