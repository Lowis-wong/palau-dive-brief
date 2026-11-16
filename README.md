# Palau Dive Brief

A daily pre-dive brief for Palau that works offline on iPhone.

- **Tides**: NOAA CO-OPS predictions for Malakal Harbor (station 1841367), 35 days ahead.
- **Current at each site**: Malakal tide stage shifted by a per-site lag (`data/sites.json`), calibrated from dive logs.
- **Sea state**: Open-Meteo marine + forecast (waves, swell, wind, rain), 7 days.
- **Official**: NWS Guam coastal waters forecast for Koror (zone PMZ161).

GitHub Actions rebuilds `docs/data/brief.json` at about 05:17, 06:41 and 18:07 Palau time (and on every push) and publishes `docs/` to GitHub Pages. The brief is not committed; each run starts from the last published copy as a fallback.

## Use on iPhone
Open the Pages link in Safari → Share → **Add to Home Screen**. Open it once with signal before the boat leaves; everything is then cached for offline use.

## Feedback loop
Log dives in the app → **Copy** → paste into the chat. Claude updates `lag_min`, `strength_factor` and the entry notes in `data/sites.json` and appends to `data/dive_log.csv` (one row per dive, with the saved context in columns).

### Log format (METAR-style)
```
20261116 1110 BLUE CUR N>S 3+ VIS20 PRED RISE1 LAG0 WRONG RMK notes
   MLK RISE 5.9H/L0515 TURN1.0H RNG0.94 NEAP0.67 RATE+6 | WS 07508G12KT SWL012/0.84M/7S WV0.96M FC1116-2120 | RA2.5 PMP0/0.99 CONF7
```
- Line 1: date, entry time (24 h), site (BLUE, SIAES, ULONG, GERMAN, OTHER), `CUR` current found
  (`IN`/`OUT` at channels, `N>S`/`S>N` = from north / from south at corners, `SLACK`, `MIXED`), strength 0–3 with
  optional `-`/`+` (a bit weaker / stronger), visibility m, `PRED` model state + level/5 and lag
  (corners: `RISE`/`FALL` = Malakal rising/falling until `flow_dir` is mapped), match, `RMK` notes.
- Line 2 (context frozen at save): Malakal stage, hours since the last turn / that turn, hours to the next turn, range m,
  tide label + ratio, rate cm/h | weather zone (WS west-south, WN west-north, E east), wind `dddffGggKT` (from),
  swell `dir/height/period`, waves, `FC` forecast or `HC` hindcast + issue time MMDD-HHMM | rain previous 24 h mm,
  swell pump level / west daily-mean waves m, confidence /10. `NOCTX` = no context saved.

## Files
- `data/sites.json` – site library (editable)
- `data/dive_log.csv` – logged dives
- `scripts/tides.py` – tide/current logic
- `scripts/build.py` – fetch + build (`--fixtures tests/fixtures` for offline test)
- `docs/` – the web app (index.html, app.js, style.css, sw.js)
