# Palau Dive Brief

A daily pre-dive brief for Palau that works offline on iPhone.

- **Tides**: NOAA CO-OPS predictions for Malakal Harbor (station 1841367), 35 days ahead.
- **Current at each site**: Malakal tide stage shifted by a per-site lag (`data/sites.json`), calibrated from dive logs.
- **Sea state**: Open-Meteo marine + forecast (waves, swell, wind, rain), 7 days.
- **Official**: NWS Guam coastal waters forecast for Koror (zone PMZ161).

GitHub Actions rebuilds `docs/data/brief.json` at 05:30, 06:45 and 18:00 Palau time and publishes `docs/` to GitHub Pages.

## Use on iPhone
Open the Pages link in Safari → Share → **Add to Home Screen**. Open it once with signal before the boat leaves; everything is then cached for offline use.

## Feedback loop
Log dives in the app → **Copy for Claude** → paste into the chat. Claude updates `lag_min`, `strength_factor` and the entry notes in `data/sites.json` and appends to `data/dive_log.csv`.

## Files
- `data/sites.json` – site library (editable)
- `data/dive_log.csv` – logged dives
- `scripts/tides.py` – tide/current logic
- `scripts/build.py` – fetch + build (`--fixtures tests/fixtures` for offline test)
- `docs/` – the web app (index.html, app.js, style.css, sw.js)
