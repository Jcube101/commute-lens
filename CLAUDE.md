# commute-lens — Project Tracker

## What we're building
A personal commute intelligence pipeline for a Bengaluru daily commute.
Ingests OsmAnd GPX files, enriches with weather and fuel data, clusters routes
by path similarity, and produces speed heatmaps and departure time optimisation.
Designed as a public GitHub project and portfolio piece.

**Repo**: github.com/Jcube101/commute-lens
**Local path**: C:\Users\jobjo\Github\commute-lens

---

## Context & Decisions Log

### User
- Daily Bengaluru commuter
- Car: Hyundai Exter AMT (petrol), ARAI baseline: **19.2 km/l**
- GitHub: Jcube101
- Primary machine: Windows (Git Bash / Windows Terminal)
- Portfolio site: job-joseph.com

### Key decisions made
- **Data source**: OsmAnd GPX files — auto-record ON, speed threshold 7 km/h, min displacement 10m, auto-split gap 10 mins, logging interval 3s during nav / 5s otherwise
- **No OBD-II**: Mileage entered manually from car's trip computer after each trip
- **Google Timeline abandoned**: New on-device format has no bulk export — data too sparse
- **Sheet is a minimal sidecar log** — not the primary data store. GPX is the source of truth
- **Route labelling is automatic** — parser clusters trips by path similarity. No manual labels
- **Parking auto-detected** from GPX endpoint coordinates (home vs office vs mall)
- **Weather auto-fetched** from Open-Meteo API (free, no key) using trip date/time
- **Petrol price** stored in a reference table with date ranges — not a per-trip manual field
- **Route labels** will be descriptive (e.g. "Via ORR") not arbitrary letters
- **config.yaml in .gitignore** — all personal coordinates and location data lives here only
- **data/gpx/ and outputs/ in .gitignore** — personal data stays local
- **No Windows Task Scheduler** — pipeline runs manually via python main.py as a deliberate "Go" trigger
- **Google Sheet fetched as CSV** — sheet published as "Anyone with link can view", URL in config.yaml, fetched fresh on every run
- **GPX sync is manual** — user copies from G:\My Drive\Miscellaneous\GPX to data/gpx/ before running pipeline
- **Sheet is read-only** — pipeline never writes to it. View-only link is correct and intentional

### Commute structure
- User parks at a **nearby mall** most days — avoids traffic U-turn, saves ~15 mins
- Sometimes drives directly to **office** when parking is available
- Sometimes **sent to mall mid-trip** after arriving at office to find no parking (Scenario C)
- Return: office -> walk -> mall -> home  OR  office -> home directly
- Three anchor points defined: HOME, OFFICE, MALL — coordinates in config.yaml only

### GPX recording rules (user habit)
- **Under 30 min stops** (petrol bunk, chai, short wait): keep recording straight through
- **Over 30 min stops** (shooting range ~2hrs twice/week, football ~1hr once/month): PAUSE OsmAnd (not stop), resume when leaving. Creates a timestamp gap in one file — gap-based stop detector handles it correctly
- **Do not stop and restart for detours** — pause/resume keeps it as one file
- **Forgot to start at home**: still record — parser flags partial=True, useful for heatmap
- **Kept recording through a long stop by mistake**: parser detects via gap analysis and subtracts stop duration. Both raw and adjusted duration recorded

### GPX data confirmed working
- Tested with a road trip GPX file (~65 km, ~115 mins)
- OsmAnd logs: lat/lon, timestamp, elevation, speed (m/s), HDOP at every point
- Speed stored under OsmAnd namespace: https://osmand.net/docs/technical/osmand-file-formats/osmand-gpx
- Point interval: ~5-6 seconds — sufficient for junction-level bottleneck detection
- Parser must handle OsmAnd namespace explicitly when extracting speed
- Gap-based stop detection is correct approach — OsmAnd stops logging when parked so there is no speed=0 run to detect, only a clean timestamp gap

### GPX file transfer method (manual, weekly)
- Android 13+ blocks access to Android/data/ from Files app
- Workaround: OsmAnd -> My Places -> Tracks -> long press -> Share -> Google Drive
- Google Drive mounted locally as G: drive
- Source path: G:\My Drive\Miscellaneous\GPX
- Destination: C:\Users\jobjo\Github\commute-lens\data\gpx\
- Future option: FolderSync app on Android for automatic phone->Drive sync (not set up yet)

### Portfolio strategy — privacy-first
- Real dashboard and analysis runs entirely locally — never hosted publicly
- Portfolio page uses synthetic commuter profiles built on OSRM road geometry
- Synthetic commuters: Whitefield->JP Nagar, Marathahalli->HSR Layout, Hebbal->Koramangala, Electronic City->Indiranagar
- No personal coordinates, location names, or real trip data ever appears on the website
- Demo mode clearly labelled as illustrative on the portfolio page
- Distinction from Google Maps: pattern intelligence over time (distributional insights, variance, reliability scores) — not real-time routing

### Portfolio narrative arc
- Launch (Phase 4): synthetic demo, pipeline open-sourced, clearly labelled illustrative
- 3 months: 60+ real trips, anonymised aggregate insights published ("across 60 commutes on this corridor...")
- 6 months: departure time prediction model trained on real data — novel output beyond what Maps provides
- The project gets more useful the longer it runs — rare for a portfolio project, worth saying in README

---

## Where personal data lives
All of the following are in config.yaml — gitignored, never leaves local machine:
- HOME coordinates
- OFFICE coordinates
- MALL name and coordinates
- Google Sheet CSV URL (sheet_csv_url)

config.example.yaml (committed to GitHub) contains only placeholder values.

---

## Project Structure

```
commute-lens/
  data/
    gpx/                     <- weekly OsmAnd GPX drops (gitignored)
    reference/
      Commute_Sidecar.xlsx   <- minimal manual log (uploaded to Google Sheets)
      petrol_prices.csv      <- date-range fuel price reference (seeded: Rs 103/l from 2026-04-13)
  outputs/                   <- all generated files (gitignored)
    master_trips.csv         <- one row per trip, all fields merged
    processed.json           <- tracks which GPX files have been processed (incremental)
    weather_cache.json       <- cached Open-Meteo responses to avoid re-fetching
    heatmap.html             <- speed-coloured map of road segments
    dashboard.html           <- summary charts and trends
  src/
    parser.py                <- GPX ingestion, trip classifier, merger, stop detection, haversine
    weather.py               <- Open-Meteo fetch by lat/lon/datetime with local cache
    cluster.py               <- route clustering by path similarity, descriptive labels
    analysis.py              <- heatmap and dashboard generation
    generate_demo.py         <- generates synthetic GPX files for portfolio demo mode
    main.py                  <- "Go" button. Run: python main.py
  data/demo/                 <- synthetic commuter data for portfolio (committed to GitHub)
  config.example.yaml        <- placeholder template (committed)
  config.yaml                <- real coordinates and settings (gitignored)
  requirements.txt
  CLAUDE.md                  <- this file
  README.md
  learnings.md
  specs.md
  roadmap.md
  CONTRIBUTING.md
```

---

## Minimal Sidecar Sheet

5 columns, two rows per day (one per leg):

| Field | Source | Notes |
|---|---|---|
| Date | Manual | Join key for parser |
| Direction | Manual dropdown | Home to Office / Office to Home |
| Mileage (km/l) | Manual | From car trip computer after trip |
| Day Type | Manual dropdown | Normal / Post-Holiday / Pre-Holiday / WFH / Detour / Other |
| Notes | Manual optional | Anomalies, missing GPX reason |

Petrol Prices tab: update only when pump price changes. Parser looks up by date range.

Sheet CSV URL: stored in config.yaml only. Fetched fresh on every pipeline run via requests.get().
Outbound and return legs are fully independent — missing one does not affect analysis of the other.

---

## Scenario Handling Reference

| Scenario | OsmAnd action | Sheet entry | Parser behaviour |
|---|---|---|---|
| Normal commute | Keep recording | 2 rows | Full trip extracted |
| Petrol bunk stop (<10 min) | Keep recording | Normal | Auto-split files merged |
| Forgot to start at home | Record from wherever | Normal | Flagged partial=True |
| Sent to mall mid-trip (Scenario C) | Keep recording | 1 row Home->Office | Auto-detected from GPX |
| Shooting range / football (>30 min) | PAUSE, resume after | Normal | Stop detected, duration adjusted |
| Forgot to pause (kept recording) | — | Normal | Gap analysis detects stop, adjusts |
| Only one leg recorded | Record that leg | 1 row | That leg processed independently |
| WFH day | No recording | No entry | Nothing to process |

---

## Parser Logic (parser.py)

### Trip classification
- Valid outbound  : start ~= HOME and end ~= OFFICE or MALL
- Valid return    : start ~= OFFICE or MALL and end ~= HOME
- Scenario C      : start ~= HOME, OFFICE coords mid-route, end ~= MALL
- Partial trip    : end matches anchor, start does not -> partial=True, kept for heatmap
- Unrelated trip  : no anchor match -> discarded silently

### Short-stop merge logic
Two consecutive GPX files <30 mins apart that together form valid anchor pair -> merged.
Re-merge: if new file is adjacent to already-processed file, old CSV row replaced with merged result.

### Mid-trip stop detection
Speed < 5 km/h or timestamp gap > 20 min at non-anchor coordinates:
- stop_detected = True, stop_duration_mins, adjusted_duration_mins recorded
- Gap-based detection is correct — OsmAnd stops logging when parked (no speed=0 run)

### Incremental processing
outputs/processed.json tracks processed filenames. Each run only processes new files.

---

## Full Pipeline — python main.py

1. Check for new GPX files not in processed.json
2. Parse new files: classify, merge, extract, detect stops
3. Fetch weather from Open-Meteo (use cache if already fetched for that date/location)
4. Fetch sidecar sheet CSV fresh from sheet_csv_url in config.yaml
5. Join on date + direction
6. Look up petrol price by date range from petrol_prices.csv
7. Calculate fuel cost
8. Cluster all trips by path similarity -> descriptive route labels
9. Append to master_trips.csv
10. Regenerate heatmap.html and dashboard.html

---

## Build Order

### Done
- [x] OsmAnd auto-record configured
- [x] GPX data quality confirmed with test file
- [x] OsmAnd speed namespace confirmed
- [x] Anchor coordinates in config.yaml (gitignored)
- [x] Minimal sidecar sheet built and uploaded to Google Sheets
- [x] Petrol price seeded: Rs 103/l from 2026-04-13
- [x] GPX transfer method confirmed (manual weekly)
- [x] parser.py — classifier, merger, haversine, speed extraction, parking, partial flag
- [x] Mid-trip stop detection (gap-based)
- [x] Incremental processing with processed.json
- [x] weather.py with local cache
- [x] Google Sheet CSV fetch in main.py
- [x] Petrol price lookup from petrol_prices.csv
- [x] Join pipeline verified end-to-end
- [x] requirements.txt created
- [x] All markdown files created: README, CLAUDE, learnings, specs, roadmap, CONTRIBUTING
- [x] All changes committed and pushed to GitHub

### To Do

#### Phase 3 — Output (needs ~10+ real commute trips)
- [ ] cluster.py — path similarity clustering, descriptive label generation
- [ ] heatmap.html — folium map, speed coloured green->red per segment, anonymised (no home/office markers)
- [ ] dashboard.html — departure bucket analysis, route comparison, weekly trends, fuel cost

#### Phase 4 — Demo mode and portfolio (after ~40 real trips)
- [ ] generate_demo.py — synthetic GPX generator using OSRM road geometry for 4 fictional Bengaluru commuters:
  - Whitefield -> JP Nagar
  - Marathahalli -> HSR Layout
  - Hebbal -> Koramangala
  - Electronic City -> Indiranagar
- [ ] Realistic speed profiles per segment: time-of-day and weather as inputs, known Bengaluru bottlenecks baked in (Silk Board, Iblur, Marathahalli bridge, Hebbal flyover)
- [ ] data/demo/ folder with pre-computed synthetic outputs (committed to GitHub)
- [ ] Portfolio frontend — MapLibre GL JS + OpenFreeMap tiles, interactive commuter profile explorer:
  - Select commuter profile
  - Toggle departure time window (before 8am / 8-9am / after 9am)
  - Toggle weather (clear / rain)
  - See bottleneck heatmap update
  - See reliability score (variance, not just average) per route
  - Key insight: distributional patterns over time, not real-time routing
- [ ] README.md — bold, personality-first, #e85d04 orange, shields.io badges, heatmap as hero visual
- [ ] Portfolio page on job-joseph.com (Lovable prompt)
- [ ] Add to CV

#### Phase 5 — Commute depth (post-40 trips, same repo)
- [ ] Junction bottleneck ranking — rank every junction by average time cost across all trips
- [ ] Day-of-week consistency scoring — variance per day, not just average duration
- [ ] Seasonal traffic patterns — 6-12 months reveals structural differences by month
- [ ] Fuel efficiency vs road type — correlate mileage with elevation and stop-start density

#### Phase 6 — Standalone projects (new repos)
- [ ] Predictive departure model — decision tree on 200+ trips: date, day, time, weather -> duration prediction with confidence interval
- [ ] Commute cost of living calculator — annual hours and rupees summary
- [ ] Personal movement archive — all OsmAnd recordings (not just commutes) as a personal geography dataset
- [ ] City-level traffic intelligence — aggregate anonymised GPX from multiple contributors, crowd-sourced road speed data for Bengaluru

---

## Open Items
- [ ] Confirm Google Drive GPX folder path is G:\My Drive\Miscellaneous\GPX (add to config.example.yaml)

---

## Data Sources
- OsmAnd GPX: personal GPS recordings
- Open-Meteo: free, no API key, historical + forecast weather by lat/lon
- OSRM (router.project-osrm.org): free routing API on OpenStreetMap for demo road geometry
- OpenFreeMap tiles: free map tiles for portfolio frontend
- Google Sheet CSV: personal sidecar log, fetched fresh each pipeline run

## Reference
- OsmAnd GPX speed namespace: https://osmand.net/docs/technical/osmand-file-formats/osmand-gpx
- ARAI baseline: 19.2 km/l (Exter AMT petrol)
- Real-world city mileage expected: 13-15 km/l (Bengaluru stop-start)
- Minimum trips before analysis meaningful: 40
- Google Sheet CSV format: https://docs.google.com/spreadsheets/d/SHEET_ID/export?format=csv&gid=TAB_GID
- OSRM routing: http://router.project-osrm.org/route/v1/driving/lon1,lat1;lon2,lat2?overview=full&geometries=geojson
