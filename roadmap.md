# commute-lens — Roadmap

---

## Phase 1 — Foundation (complete)

**Goal:** Get real commute data into a structured CSV.

- [x] OsmAnd auto-record configured: 7 km/h trigger, 10m displacement, 10min auto-split gap, 3s/5s interval
- [x] GPX data quality confirmed with real files
- [x] OsmAnd speed namespace confirmed and handled in parser
- [x] Anchor coordinates set up in `config.yaml` (gitignored)
- [x] Minimal sidecar sheet built (`Commute_Sidecar.xlsx`) and published to Google Sheets
- [x] Petrol price reference seeded (`data/reference/petrol_prices.csv`)
- [x] GPX transfer method confirmed: OsmAnd share → Google Drive → manual copy to `data/gpx/`
- [x] `parser.py` — GPX reader, trip classifier, merger, haversine, speed extraction, parking detection, partial flag, mid-trip stop detection, incremental processing
- [x] Parser tested against 5 real GPX files — all classifications verified
- [x] Stop detection confirmed: 65.2 min shooting range stop correctly stripped on April 14 trip

---

## Phase 2 — Enrichment (complete)

**Goal:** Produce a complete `master_trips.csv` with all fields populated.

- [x] `weather.py` — Open-Meteo forecast + archive APIs, fetches weather condition (Clear/Cloudy/Rain/Heavy Rain), temperature, precipitation. Uses OFFICE coordinates. Cached in `outputs/weather_cache.json`
- [x] `bluelink.py` — Bluelink daily aggregate fetcher. Runs on every pipeline execution, fetches last 4 months of daily stats (distance, drive/idle time, avg/max speed, trip count), upserts to `outputs/bluelink_daily.csv`. Silent on failure — pipeline continues gracefully
- [x] `main.py` — single entry point: runs parser incrementally, fetches Bluelink daily aggregates, fetches sheet CSV live, looks up petrol price, enriches all rows with weather and derived fields, writes master_trips.csv
- [x] `petrol_prices.csv` seeded
- [x] `sheet_csv_url` added to `config.yaml` — sheet fetched fresh on every run
- [x] Parser resilient to malformed GPX files (skips with warning instead of crashing)
- [x] Verify end-to-end: `python main.py` tested with 17 GPX files (12 classified trips, 3 discarded, 1 malformed skipped), 10 sheet rows, weather for all dates, Bluelink 87 daily records
- [x] 12 classified trips (4 full + 8 partial) with full data — ready for Phase 3

### Bluelink API findings (2026-04-21)

- Login works (region=6, brand=2, `hyundai_kia_connect_api` v4.10.3). Vehicle discovered, monthly and daily aggregates returned
- **Per-trip mileage (km/l) is not available** from the India API — cannot replace manual sheet entry
- Per-trip data limited to: start/end timestamps and start/end coordinates. Per-trip drive time, distance, speed exist only as daily aggregates
- History depth: Jan 2026 – present (~4 months). Dec 2025 and earlier returns empty
- Library bug: `update_day_trip_info()` crashes on India data (missing `tripTime` field). Raw JSON via `_get_trip_info()` works — used in `bluelink.py`

---

## Phase 3 — Outputs (complete)

**Goal:** Turn structured data into useful visuals.

- [x] `cluster.py` — DBSCAN path similarity clustering (separate for outbound/return), descriptive route labels via Nominatim reverse geocoding (e.g. "Via Outer Ring Rd"). Requires 5+ full trips per direction; currently labelling all trips as "Unclustered — insufficient data" (2 full outbound, 3 full return). `route_cluster` column added to `master_trips.csv`
- [x] `heatmap.html` — Folium map with OpenStreetMap tiles. All trips plotted (including partials). Speed-coloured segments: green >30 km/h, yellow 15–30, orange 5–15, red <5. Line thickness scaled by trip coverage. No home/office markers. Legend and title overlay
- [x] `dashboard.html` — Plotly self-contained HTML. Full trips only for stats (sample size noted on each chart). Charts: departure time vs duration scatter (outbound/return series), day-of-week avg duration bar (outbound), duration over time line, mileage over time line, parking distribution pie. #e85d04 orange accent, dark theme
- [x] `analysis.py` — generates both `heatmap.html` and `dashboard.html` from `master_trips.csv` and GPX files. Called by `main.py` as pipeline steps 6–7
- [x] Pipeline updated to 7 steps: parse → Bluelink → sheet → petrol → enrich → cluster → visualise

---

## Phase 4 — Demo mode and portfolio (after ~40 real trips)

**Goal:** Make this presentable as a portfolio project without exposing personal location data.

### Synthetic demo mode (`generate_demo.py`)

- [ ] `generate_demo.py` — synthetic GPX generator using OSRM road geometry for 4 fictional Bengaluru commuters:
  - Whitefield → JP Nagar
  - Marathahalli → HSR Layout
  - Hebbal → Koramangala
  - Electronic City → Indiranagar
- [ ] Realistic speed profiles per segment: time-of-day and weather as inputs
- [ ] Known Bengaluru bottlenecks baked in: Silk Board, Iblur, Marathahalli bridge, Hebbal flyover
- [ ] `data/demo/` folder with pre-computed synthetic outputs (committed to GitHub)
- [ ] Demo config: `config.demo.yaml` with synthetic anchor coordinates (no real locations)

### Portfolio frontend

- [ ] MapLibre GL JS + OpenFreeMap tiles — interactive commuter profile explorer
  - Select commuter profile (4 corridors)
  - Toggle departure time window (before 8am / 8-9am / after 9am)
  - Toggle weather (clear / rain)
  - See bottleneck heatmap update per selection
  - See reliability score (variance, not just average) per route
- [ ] Key narrative: distributional patterns over time, not real-time routing — what Google Maps cannot tell you

### Portfolio polish

- [ ] `README.md` — bold, personality-first, #e85d04 orange, shields.io badges, heatmap as hero visual
- [ ] Portfolio page on job-joseph.com (Lovable prompt)
- [ ] Add to CV alongside other projects
- [ ] Clearly labelled "illustrative" on portfolio — real analysis runs locally

### Narrative arc

- Launch: synthetic demo + open-sourced pipeline
- 3 months: 60+ real trips, anonymised aggregate insights published
- 6 months: departure time prediction model — novel output beyond what Maps provides
- The project gets more useful the longer it runs — rare for a portfolio project

---

## Phase 5 — Commute Depth (same project, post-40 trips)

**Goal:** Squeeze more insight out of accumulated commute data.

- [ ] **Junction bottleneck ranking** — rank every junction on the route by average time cost across all trips. Needs speed + location only. Output: ranked list of "this signal costs you X minutes per commute on average"
- [ ] **Day-of-week consistency scoring** — not just average duration per day but variance. Which days are predictable vs wildly variable. More useful for planning than averages alone
- [ ] **Seasonal traffic patterns** — 6–12 months of data reveals whether certain months are structurally worse. School terms, monsoon, festival seasons all show up in the data
- [ ] **Fuel efficiency vs road type** — correlate mileage (from sheet) with elevation profile and stop-start density from GPX. Understand whether a longer, smoother route actually saves fuel

---

## Phase 6 — Standalone Projects (new repos, built on same pipeline)

**Goal:** Extend the recording habit into broader personal and community tools.

- [ ] **Predictive departure model** — train a simple model (decision tree) on historical trips: date, day of week, time, weather, duration. Output: "given it is a Tuesday in October and raining, leave by 8:10 for 80% chance of arriving under 35 minutes." Needs ~200 commute trips for reasonable accuracy
- [ ] **Commute cost of living calculator** — annual summary of hours and rupees spent commuting. "Your commute cost you 312 hours and Rs 47,000 last year." Useful for WFH negotiation, relocation decisions, car upgrade decisions. Needs commute GPX + sheet data only
- [ ] **Personal movement archive** — the same pipeline processes any trip recorded in OsmAnd, not just commutes. Over years this becomes a personal geography dataset — every road driven, every city visited, total distance covered. Storage is trivial (~50 MB per year of GPX). No sheet entry needed for non-commute trips, just keep OsmAnd running
- [ ] **City-level traffic intelligence** — if multiple contributors share anonymised GPX exports from the same city, the speed-per-segment data aggregates into crowd-sourced road speed intelligence. Same infrastructure as commute-lens, just aggregated. Privacy-respecting alternative to Waze built on OsmAnd. Long-term stretch goal

### Recording habit note

The 40-trip threshold applies to commute optimisation specifically. The GPX recording habit is worth maintaining indefinitely regardless. For non-commute trips — road trips, weekend drives, intercity travel — just keep OsmAnd running. No sheet entry needed. The parser classifies non-commute GPX as `unrelated` and sets it aside cleanly, but the data is retained for future use.

---

## Deferred / won't do (for now)

- **OBD-II integration** — Hyundai Exter AMT does not expose standard OBD-II easily. Manual mileage from trip computer is sufficient.
- **Automated GPX sync from Google Drive** — adds OAuth dependency for a one-minute manual step. Not worth it. (Future option: FolderSync app on Android for automatic phone→Drive sync)
- **Scheduled runs** — `python main.py` is the trigger. No cron, no daemon, no Task Scheduler.
- **Real-time traffic overlay** — would require a paid maps API. Out of scope for personal project.
