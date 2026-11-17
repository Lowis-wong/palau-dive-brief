# Lagoon set-up study — Step 1 (data) results, 2026-10-20

> Mirror of the claude.ai project doc `claude/lagoon-setup-step1.md` (copied 2026-11-17 for traceability).
> Correction noted later: the "NOAA ~12 min early" line below has the wrong sign — NOAA predictions are ~15–18 min LATE (see `project2-feasibility.md`).

**Question**: how much "extra water" (non-tidal set-up) is in the lagoon today, and what drives it (waves, wind, pressure, rain)? Channel current direction depends on the lagoon–ocean head difference.

**Pipeline**: `scripts/analysis/step1.py` (water level), `scripts/analysis/forcing.py` (ERA5, resumable), `scripts/analysis/harmonics.py` (37-constituent LS tide fit). Workflow `.github/workflows/analysis-step1.yml` (push, dispatch, every 3 h until forcing complete). Outputs on branch `analysis-data` under `step1/`: wl_hourly.csv.gz (wl_m, tide_m, resid_m, resid_30d_m, resid_short_m, resid_lp_m, setup_m), wl_sources_hourly, uhslc_*_hourly, ioc_recent_5min, tide_fits.json, noaa_check, forcing/{wave_W|E|S, wx_koror}_{year}.csv.gz, summary.json, log.txt.

**Water level (Malakal, UTC hourly)**
- UHSLC research quality (ERDDAP global_hourly_rqds, ssc_id "mala", record 72) 1980–2024; UHSLC fast (record 70) to 2026-09-30; IOC real-time "mala" 1-min (sensor ra2 matches UHSLC to 5 mm rms after −0.292 m offset; IOC hourly must be the value ON the hour, not hourly mean). Coverage ~99% for 1980–2026 (lowest 2005 88%, 2016 91%, 1987 92%).
- IOC API occasionally drops chunks — retry logic added; needs care for real-time use.
- Tide: per-year LS fit; tide_m = short-period astronomical part only (mean/SA/SSA/MM/MF/MSF excluded from reconstruction to avoid year-boundary jumps).
- NOAA 1841367 predictions vs our fit (Sep 2026): corr 0.99, NOAA ~12 min early [sign wrong, see note above], 4.5 cm rms after shift → could later replace NOAA predictions with our own constants.
- ~35–40% of the raw short residual variance sits in tidal bands (leftover tide), so the set-up signal is Godin-filtered: `setup_m` = 24-24-25 h low-pass of residual minus 30-day mean. Typical std 2.4 cm, 99th pct ≈ 6 cm; typhoon surges up to ~0.27 m filtered (Mike 1990, Jul 2001, Bopha 2012, Haiyan 2013, Surigae 2021 visible).
- 30-day mean (ocean-wide, ENSO/seasonal) ranges −0.33 m (Feb 1998) to +0.31 m (Jun 2025) — affects both sides, not channel flow.

**Forcing (Open-Meteo, ERA5)**
- Waves: models=era5_ocean, points W 7.5N 134.0E, E 7.5N 135.0E, S 6.5N 134.5E. Only TOTAL wave height/direction/period available; swell / wind-wave partitions are empty via Open-Meteo (would need Copernicus CDS account).
- Weather: ERA5 at Koror grid (7.25N, 134.5E): wind, gusts, MSL pressure, precipitation.
- Downloaded 2009/2010–2026 so far (68/188 point-years); backfilling to 1980 automatically (rate-limited, TLS timeouts).
- Seasonality sane: NE trade season waves higher in east (Dec–Mar); W point waves from SW–NW 58–66% of hours Jul–Sep. ERA5 under-resolves typhoon intensity (e.g., Surigae min pressure 1000 hPa at Koror grid).

**Next (Step 2)**: regress setup_m on west/east wave height, period, direction, wind components, pressure (inverse barometer ~1 cm/hPa) and rain; lags and decay time; seasonal split; then Step 3 real-time "Lagoon +x cm" indicator from IOC.
