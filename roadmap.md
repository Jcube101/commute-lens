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

## Phase 2 — Enrichment (in progress)

**Goal:** Produce a complete `master_trips.csv` with all fields populated.

- [x] `weather.py` — Open-Meteo fetch by lat/lon/datetime, local cache in `outputs/weather_cache.json`
- [x] `main.py` — single entry point: runs parser incrementally, fetches sheet CSV live, looks up petrol price, enriches all rows with weather and derived fields, writes master_trips.csv
- [x] `petrol_prices.csv` seeded
- [x] `sheet_csv_url` added to `config.yaml` — sheet fetched fresh on every run
- [x] Verify end-to-end: run `python main.py` with real sheet data and confirm all fields populated
- [ ] Collect ~10 classified commute trips with full data before moving to Phase 3

---

## Phase 3 — Outputs (needs ~10+ real commute trips)

**Goal:** Turn structured data into useful visuals.

- [ ] `heatmap.html` — Folium map, road segments coloured green to red by speed
- [ ] `dashboard.html` — departure time bucket analysis, route comparison, weekly fuel trends
- [ ] `analysis.py` — script that reads `master_trips.csv` and generates both HTML outputs
- [ ] `cluster.py` — path similarity clustering, assign descriptive route labels (e.g. "Via ORR")
  - Route labels added to `master_trips.csv` once clustering is stable

---

## Phase 4 — Portfolio (after ~40 trips and meaningful data)

**Goal:** Make this presentable as a portfolio project.

- [ ] Local web frontend to browse `master_trips.csv`, heatmap, and dashboard in one place
  - Stack TBD: plain HTML/JS or minimal React. Lightweight, no build step preferred.
- [ ] `README.md` — finalise with real heatmap screenshot as hero visual
- [ ] Portfolio page on job-joseph.com
- [ ] Add to CV alongside other projects

---

## Deferred / won't do (for now)

- **OBD-II integration** — Hyundai Exter AMT does not expose standard OBD-II easily. Manual mileage from trip computer is sufficient.
- **Automated GPX sync from Google Drive** — adds OAuth dependency for a one-minute manual step. Not worth it.
- **Scheduled runs** — `python main.py` is the trigger. No cron, no daemon.
- **Real-time traffic overlay** — would require a paid maps API. Out of scope for personal project.
