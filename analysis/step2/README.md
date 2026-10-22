# Step 2 — what drives lagoon set-up at Malakal (run locally)

Inputs: `step1/wl_hourly.csv.gz` and `step1/forcing/*` from the `analysis-data` branch, merged into
`forcing_all.csv.gz`. `prep.py` builds hourly series (Godin low-pass, 30-day anomalies); `build_daily.py`
writes `daily.csv.gz`. Results (2026-10-22, 1980–2026, 16,890 days):

- Inverse barometer: −0.91 cm/hPa (theory −0.99). Explains ~17% of day-to-day set-up variance.
- Waves add ~4%, wind ~1%, rain 0 (5-year block cross-validation). ~79% unexplained = mostly ocean-wide.
- After removing pressure, mean set-up by daily west Hs (ERA5 7.5N 134.0E): <2 m ≈ 0 cm; 2–2.5 m +0.7;
  2.5–3 m +1.7; >3 m +2.8 cm. P(set-up > 3 cm): 7–9% below 2 m, 16% / 28% / 39% above.
- Composite of 16 big west-swell onsets: peak ≈ +1.8 cm on day 0–1, back to zero by day 4.
- East waves do not raise Malakal (slightly negative when west waves held fixed).
