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

### Commute structure
- User parks at a **nearby mall** most days — avoids traffic U-turn, saves ~15 mins
- Sometimes drives directly to **office** when parking is available
- Sometimes **sent to mall mid-trip** after arriving at office to find no parking (Scenario C)
- Return: office -> walk -> mall -> home  OR  office -> home directly
- Three anchor points defined: HOME, OFFICE, MALL — coordinates in config.yaml only

### GPX recording rules (user habit)
- **Under 30 min stops** (petrol bunk, chai, short wait): keep recording straight through
- **Over 30 min stops** (shooting range, football): stop recording, restart when leaving
- **Forgot to start at home**: still record — parser flags partial=True, useful for heatmap
- **Detour trips**: parser filters automatically as they don't match anchor pairs. Note in sheet if no return GPX that day

### GPX data confirmed working
- Tested with a road trip GPX file (~65 km, ~115 mins)
- OsmAnd logs: lat/lon, timestamp, elevation, speed (m/s), HDOP at every point
- Speed stored under OsmAnd namespace: https://osmand.net/docs/technical/osmand-file-formats/osmand-gpx
- Point interval: ~5-6 seconds — sufficient for junction-level bottleneck detection
- Parser must handle OsmAnd namespace explicitly when extracting speed

### GPX file transfer method
- Android 13+ blocks access to Android/data/ from Files app
- Workaround: OsmAnd -> My Places -> Tracks -> long press -> Share -> Google Drive
- Weekly habit: download from Drive -> paste into data/gpx/

---

## Where personal data lives
All of the following are in config.yaml which is gitignored and never leaves your machine:
- HOME coordinates
- OFFICE coordinates
- MALL name and coordinates

config.example.yaml (committed to GitHub) contains only placeholder values:
```yaml
anchors:
  home:
    lat: YOUR_HOME_LAT
    lon: YOUR_HOME_LON
    radius_m: 300
  office:
    lat: YOUR_OFFICE_LAT
    lon: YOUR_OFFICE_LON
    radius_m: 300
  mall:
    name: YOUR_MALL_NAME
    lat: YOUR_MALL_LAT
    lon: YOUR_MALL_LON
    radius_m: 300
```

---

## Project Structure

```
commute-lens/
  data/
    gpx/                     <- weekly OsmAnd GPX drops (gitignored)
    reference/
      Commute_Sidecar.xlsx   <- minimal manual log (upload to Google Sheets)
      petrol_prices.csv      <- date-range fuel price reference
      sheet_log.csv          <- CSV export from Google Sheets for parser
  outputs/                   <- all generated files (gitignored)
    master_trips.csv         <- one row per trip, all fields merged
    heatmap.html             <- speed-coloured map of road segments
    dashboard.html           <- summary charts and trends
  src/
    parser.py                <- GPX ingestion, trip filter, haversine, parking detection
    weather.py               <- Open-Meteo API fetch by lat/lon/datetime
    cluster.py               <- route clustering by path similarity, descriptive labels
    analysis.py              <- heatmap and dashboard generation
    main.py                  <- entry point, runs full pipeline
  config.example.yaml        <- placeholder template (committed to GitHub)
  config.yaml                <- real coordinates and settings (gitignored, never committed)
  requirements.txt
  CLAUDE.md                  <- this file
  README.md                  <- portfolio-grade, personality-first
```

---

## Minimal Sidecar Sheet

5 columns, two rows per day (one per leg), direction explicit to catch missing entries:

| Field | Source | Notes |
|---|---|---|
| Date | Manual | Join key for parser |
| Direction | Manual dropdown | Home to Office / Office to Home |
| Mileage (km/l) | Manual | From car trip computer after trip |
| Day Type | Manual dropdown | Normal / Post-Holiday / Pre-Holiday / WFH / Other |
| Notes | Manual optional | Detours, anomalies, missing GPX reason |

Petrol Prices tab — update only when pump price changes:

| From Date | To Date | Petrol Price (Rs/l) |
|---|---|---|
| 2026-04-13 | - | 103.0 |

Everything else auto-derived by parser:
- Departure/arrival time, duration, distance, avg speed -> GPX timestamps + haversine
- Parking (Office / Mall / Sent to Mall) -> GPX endpoint vs anchor coordinates in config.yaml
- Scenario C -> office coordinates appear mid-route before mall endpoint
- Partial trip -> end matches anchor but start does not — flagged partial=True, not discarded
- Weather -> Open-Meteo API by date/time/location
- Route cluster label -> path similarity clustering
- Fuel cost -> distance / mileage x petrol price lookup by date
- Week number, day of week, trip number -> computed

---

## Parser Logic (parser.py)

### Trip classification
- Valid outbound  : start ~= HOME  and end ~= OFFICE or MALL
- Valid return    : start ~= OFFICE or MALL  and end ~= HOME
- Scenario C      : start ~= HOME, OFFICE coords mid-route, end ~= MALL
- Partial trip    : end matches anchor, start does not -> partial=True, keep for heatmap
- Unrelated trip  : no anchor match -> discard silently

### Short-stop merge logic
If two consecutive GPX files are <30 mins apart and together form a valid anchor pair,
merge them into one trip. Handles petrol bunk auto-splits cleanly.

### Speed extraction
OsmAnd speed is in m/s under the osmand.net namespace. Multiply by 3.6 for km/h.

---

## Full Pipeline (main.py)

1. Read all GPX files from data/gpx/
2. Classify each as valid commute / partial / unrelated
3. Merge consecutive files separated by <30 min gaps if they form valid anchor pair
4. Extract per-trip: departure, arrival, duration, distance, speed profile, parking, Scenario C flag
5. Fetch weather from Open-Meteo for each trip date/time
6. Join with sheet_log.csv on date + direction
7. Look up petrol price from petrol_prices.csv by date range
8. Calculate fuel cost (distance / mileage x price)
9. Cluster trips by path similarity -> assign descriptive route labels
10. Output master_trips.csv
11. Generate heatmap.html — speed coloured green->red per segment
12. Generate dashboard.html — departure bucket analysis, route comparison, weekly trends

---

## Build Order

### Done
- [x] OsmAnd auto-record configured (7 km/h trigger, 10m displacement, 10min gap split, 3s/5s interval)
- [x] GPX data quality confirmed with test file
- [x] OsmAnd speed namespace confirmed for parser
- [x] Anchor coordinates in config.yaml (gitignored)
- [x] Minimal sidecar sheet built (Commute_Sidecar.xlsx) and uploaded to Google Sheets
- [x] Petrol price confirmed: Rs 103/l as of 2026-04-13
- [x] GPX transfer method confirmed: OsmAnd share -> Google Drive -> PC download
- [x] 4 real commute GPX files in data/gpx/ ready to test parser
- [x] GitHub repo created: github.com/Jcube101/commute-lens
- [x] Folder structure created locally
- [x] config.yaml written locally (gitignored)
- [x] config.example.yaml committed with placeholder values only
- [x] .gitignore covers config.yaml, data/gpx/, outputs/
- [x] Initial commit pushed
- [x] Recording rules defined (30 min threshold, partial trip handling, detour filtering)
- [x] Short-stop merge logic defined for petrol bunk splits

### To Do

#### Phase 1 — Foundation (Claude Code, start here)
- [ ] parser.py — GPX reader, trip classifier, merger, haversine, speed extraction, parking detection, partial flag
- [ ] Test parser against 4 real commute GPX files in data/gpx/ — verify output CSV
- [ ] sheet_log.csv — export from Google Sheets, confirm join works on date + direction

#### Phase 2 — Enrichment (after parser verified)
- [ ] weather.py — Open-Meteo fetch by lat/lon/datetime, cache results locally
- [ ] cluster.py — path similarity clustering, descriptive label generation
- [ ] main.py — join pipeline: GPX output + sheet + weather + petrol price -> master_trips.csv

#### Phase 3 — Output (needs ~10+ real commute trips)
- [ ] heatmap.html — folium map, speed coloured green->red per segment
- [ ] dashboard.html — departure bucket analysis, route comparison, weekly trends, fuel cost

#### Phase 4 — Portfolio (after ~40 trips and meaningful data)
- [ ] README.md — bold, personality-first, orange accent #e85d04, shields.io badges, heatmap as hero visual
- [ ] Portfolio page on job-joseph.com (Lovable prompt)
- [ ] Add to CV alongside other projects

---

## Open Items
- [ ] Confirm Google Drive folder name used for GPX sync (for README documentation)

---

## Reference
- OsmAnd GPX speed namespace: https://osmand.net/docs/technical/osmand-file-formats/osmand-gpx
- ARAI baseline: 19.2 km/l (Exter AMT petrol)
- Real-world city mileage expected: 13-15 km/l (Bengaluru stop-start)
- Minimum trips before analysis meaningful: 40
- Open-Meteo: free, no API key, historical hourly weather by lat/lon
- Folium: Python library for interactive Leaflet maps
- 4 real commute GPX files in data/gpx/ ready for parser testing