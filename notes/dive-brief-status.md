# Palau Dive Brief — project status

> Mirror of the claude.ai project doc `claude/dive-brief-status.md`, snapshot copied 2026-11-17. The project doc is kept up to date by the brief/app chats; this copy may lag behind it.

**Live**: https://lowis-wong.github.io/palau-dive-brief/ (repo: github.com/Lowis-wong/palau-dive-brief, Pages source = GitHub Actions). Commits from Claude use a noreply identity (earlier commits exposed a personal address; Lowis told 2026-11-10).

**Stack**: GitHub Actions (`.github/workflows/build.yml`) on push to main, manual dispatch, and schedule. GitHub delays scheduled runs here by ~3–5 h, so builds are scheduled overnight at 00:11 / 02:13 / 04:17 / 06:41 Palau plus 18:07 (cron 11 15, 13 17, 17 19, 41 21, 7 9 UTC); since 2026-10-22 a fresh brief has been ready before 08:00. cron-job.org attempt abandoned.
Each run fetches the last published brief as fallback, builds `docs/data/brief.json` (NOT committed), mirrors it to the single-commit `brief-data` branch, and deploys `docs/` to Pages. App refetches when brought back to the foreground. Brief covers yesterday + 34 days of tides, 8 days of weather; collapsible "Next days" outlook (tides, sky, wind, waves, sun) + Windy / Tide Forecast links.
**History (since 2026-11-16)**: 14 earlier days kept as well (`HISTORY_DAYS = 15` incl. yesterday): tides from the same constants, wind/waves from Open-Meteo `past_days=16` hindcast; days carry `history: true` and are excluded from the tide statistics (ratio, low cut). In the app they are folded behind a dashed "‹ Past · 14 days" button at the left of the day bar; a past day shows a "Past day … hindcast" note and "Sea state … · hindcast"; NWS alerts are not shown on past days. brief.json ~350 KB.

**Tides (since 2026-11-10, Project 2)**: predicted in `scripts/tides.py` from `data/tide_constants.json` (37-constituent LS fit to the latest 365 days of Malakal observations; heights shifted to NOAA MLLW via `mllw_offset_m`). NOAA 1841367 still fetched: its time is shown per high/low as "tables HH:MM" and it is the fallback if constants are missing. Our times run ~15–18 min earlier than NOAA/Tide Forecast (NOAA is late vs observations). Monthly refit `.github/workflows/refit-tides.yml` (3rd of month 12:37 Palau; also on push of the script): UHSLC fast (ERDDAP) + IOC real-time, QC against current constants (drops >0.5 m departures — IOC ra2 jumped ~3 m around 2026-11-02), checks coverage ≥0.8, residual rms <0.15 m, NOAA shift 3–30 min, M2 change <3 cm / <5°; writes `data/tide_constants_report.json` either way.

**Other sources**: Open-Meteo marine + forecast (zones west-south, west-north, east; Koror point for sky/rain/sun); NWS Guam CWF (zone PMZ161) — routine small craft advisory hidden. NEMO red flag has no feed.

**Model**: site current at time t = Malakal stage at (t − lag_min). Slack when rate < 30% of stage peak. Flow level 1–5 from rate × strength_factor. Tide meter 1–5 from range ratio.
**West swell pump**: daily mean west wave height <2 m none, 2–2.5 weak (≈ +0.7 cm), 2.5–3 moderate (≈ +1.7 cm), ≥3 strong (≈ +2.8 cm).
**Confidence score 1–10**: start 10; tide ratio <0.7 −3, <0.85 −2, <1.15 −1; swell pump −1/−2/−3, +1 at neap, +1 more if day's range < 1.0 m (Project 1). Wind, rain, pressure not scored; rain ≥20 mm/24 h = visibility flag.

**Sites (all unverified estimates; lags relative to our own Malakal predictions)**

| Site | type | zone | lag_min | strength_factor | logs |
|---|---|---|---|---|---|
| Blue Corner / Blue Holes | corner | west-south | 0 | 1.0 | 1 |
| Siaes Corner | corner | west-north | 20 | 1.0 | 0 |
| Ulong Channel | channel | west-north | 0 (was 45; changed 2026-11-10 after Project 1; Project 3 satellite: no signal) | 1.2 | 2 |
| German Channel | channel | west-south | 0 (was 45; Project 3 satellite estimate 0 to +40 min — consistent, too uncertain to calibrate) | 1.0 | 1 |

Channel coordinates corrected 2026-11-16 to the mapped channel mouths (display only, not used in calculations): Ulong 7.2833 / 134.2456, German 7.1210 / 134.2720.

**Corners use compass directions (since 2026-11-16, Lowis: in/out is confusing at corners)**: sites.json `dir_mode: "compass"`, `flow_dir: null`. Lowis does not yet know which way each corner runs on a rising vs falling tide (asked 2026-11-16: "not sure yet" for both). Until `flow_dir` is set (e.g. `{"incoming": "N>S", "outgoing": "S>N"}`), corner cards show "RISING / FALLING · n/5" (Malakal stage), "Best rising tide", badge "N/S direction not mapped yet"; log form at corners offers N → S (from north) / S → N (from south) / Slack / Turned. Map `flow_dir` once corner logs agree.

**Dive log**: Save freezes a snapshot (stage, range, ratio, rate; prediction with lag/factor; forecast weather at entry hour; rain; swell pump; confidence; `src` forecast or hindcast for past days) → "Copy" → `data/dive_log.csv` (header since 2026-11-16 has one column per context field; `source` = app / manual). Copy text is METAR-style, format in README "Log format", e.g. `20261116 1110 BLUE CUR N>S 3+ VIS20 PRED RISE1 LAG0 WRONG` / `MLK RISE 5.9H/L0515 TURN1.0H RNG0.94 NEAP0.67 RATE+6 | WS 07508G12KT SWL012/0.84M/7S WV0.96M FC1116-2120 | RA2.5 PMP0/0.99 CONF7`. Lowis may also send manual logs in her own format (e.g. `Blue corner_2026/11/16 Entry 11:11_ current From north to south_Strength 3+ _ Vis 20_Predict wrong`) — backfill context from the brief (brief-data branch). Logs can be backfilled up to 15 days with context. Strength scale: 0 none · 1 easy · 2 hard kicking / hook · 3 cannot swim against, plus optional grey toggle "− a bit weaker / + a bit stronger" saved as `1-`, `3+` (no `0-`). Entry time = two 24 h selects (hour, 5-min steps).
**Logs so far (4)**: Ulong 11-12 11:09 OUT 2 (pred OUT 3, matched); Ulong 11-15 10:51 OUT 2, 17 min before Malakal HW 11:08 (pred slack, wrong → hint Ulong turns earlier than Malakal; lag unchanged, one log); German 11-16 09:22 IN 1 (pred IN 2, matched); Blue Corner 11-16 11:11 N>S 3+ ~1 h before HW 12:08 at neap (pred rising 1/5 going slack 11:15, wrong — strength far too low). Most valuable next: corner direction vs Malakal stage; channel turn times near Malakal HW/LW; any neap + west swell day.

**UI decisions (Lowis)**: title "MM-DD HH:MM Brief"; wind & swell dials (no N), wind "21/27 kn"; moon icon; tide meter; swell-pump row; confidence score + level, chips one row; chart: plan-time line with tag, hourly ticks; high/low as 4 chronological tiles with HH/LL tags and small "tables" time; site cards: badges under Best incoming, grey notes grouped; sea-state table with arrows; coloured current-found buttons; corners in N/S; strength +/− in grey; 24 h log time; log button just "Copy", no "to Claude" wording; past days folded; small footer with disclaimer + © 2026 Lowis Wong. Lowis wants a full screenshot after each change.

**Analysis**: Step 1–2 (lagoon set-up), Project 1 (lagoon vs ocean), Project 2 (own constants), Project 3 (Sentinel-2 channel plumes — no signal at Ulong, weak signal at German, lags unchanged) done — see their docs. Project 10 (satellite bathymetry + moorings) in progress — `analysis/project10/README.md`. QGIS projects in ~/Backup/Palau_QGIS/ (EPSG:32653): Palau_Channels_P3.qgz, Palau_Bathymetry_P10.qgz. Roadmap: `notes/roadmap.md`.

**History**: 2026-10-19 v0. 2026-10-20 UI, log snapshots, confidence. 2026-10-22 schedule fix, Step 1–2. 2026-10-31 swell pump. 2026-11-10 Project 1 (lags 45→0, deep-neap penalty), Project 2 (own tide constants, monthly refit). 2026-11-16 Project 3 (satellite plumes; lags unchanged; channel coordinates corrected); strength +/−, 24 h log time, 15-day folded history; first 4 dive logs recorded; corners in N/S; METAR-style log copy. 2026-11-16/17 Project 10 stages A–E (bathymetry v3, gate 2 passed).
