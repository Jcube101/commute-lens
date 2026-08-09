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
- **Weather auto-fetched** from Open-Meteo API (free, no key) using OFFICE coordinates and trip departure hour. Forecast API for recent dates, archive API for >90 days. Fields: weather condition (Clear/Cloudy/Rain/Heavy Rain), temperature, precipitation
- **Petrol price** stored in a reference table with date ranges — not a per-trip manual field
- **Route labels** will be descriptive (e.g. "Via ORR") not arbitrary letters
- **config.yaml in .gitignore** — all personal coordinates and location data lives here only
- **data/gpx/ and outputs/ in .gitignore** — personal data stays local
- **No Windows Task Scheduler** — pipeline runs manually via python main.py as a deliberate "Go" trigger
- **Google Sheet fetched as CSV** — sheet published as "Anyone with link can view", URL in config.yaml, fetched fresh on every run
- **GPX sync is manual** — user copies from G:\My Drive\Miscellaneous\GPX to data/gpx/ before running pipeline
- **Sheet is read-only** — pipeline never writes to it. View-only link is correct and intentional
- **Bluelink API**: tested 2026-04-21. Per-trip mileage NOT available from India API — cannot replace manual sheet entry. But daily aggregates (distance, drive time, idle time, avg/max speed, trip count) work and are fetched on every pipeline run via `bluelink.py`. See findings below
- **Bluelink login broke 2026-08-09** — Hyundai's India signin endpoint now returns `{"step": 5, "upgrade": false}` instead of a `redirectUrl`. Confirmed on both the pinned library version (4.10.3) and the latest (4.23.0) — not a library-version issue, root cause is server-side. Pipeline degrades gracefully (Bluelink step just gets skipped). GitHub issue drafted at `data/issues/bluelink_github_issue.md` (gitignored) for the user to file upstream
- **`data/issues/` added and gitignored** — scratch folder for draft notes (e.g. upstream bug reports) that shouldn't be committed to the repo
- **Pipeline migrated to jobpi (2026-08-09)** — the real pipeline (parser, weather, Bluelink, clustering, heatmap/dashboard generation) now runs on the Raspberry Pi at `~/projects/commute-lens` instead of Windows. Trigger stays manual: SSH into the Pi and run `python src/main.py` — no systemd timer, no cron, preserving the original "deliberate Go trigger" decision, just on different hardware. GPX sync stays the same manual OsmAnd -> Google Drive -> local step, except the local machine is now the Pi: `config.yaml` and `data/gpx/` are pushed to the Pi via `scp` from Windows (not committed — both remain gitignored on the Pi copy too). Nothing about this migration exposes anything publicly — no Cloudflare Tunnel entry, no subdomain, reachable only via SSH on the LAN/Tailscale, consistent with the privacy-first portfolio strategy below. The public `commute.job-joseph.com` subdomain (when built) will serve only the synthetic demo (Phase 4), never this real pipeline's output
- **jobpi's LAN IP is not static** — it moved from 192.168.1.199 to 192.168.1.63 between sessions (DHCP). If SSH/scp to the old IP times out, check the current IP or use jobpi's Tailscale address instead

### Bluelink API experiment results (2026-04-21)

**What works (India, Hyundai Exter):**
- Login and vehicle discovery: VIN, model, registration date confirmed
- Monthly aggregates: total drive time, idle time, distance, avg speed, max speed, plus list of days with trip counts
- Per-trip data: start/end timestamps (YYYYMMDDHHmmSS format), start/end lat/lon coordinates
- History depth: Jan 2026 – present (4 months). Dec 2025 and earlier returns empty — either server retention limit or Bluelink activation date

**What does NOT work:**
- **No per-trip mileage (km/l)** — the field we needed to replace manual sheet entry. India API does not return fuel efficiency data at any granularity
- **No per-trip drive time, idle time, distance, avg/max speed** — these exist only as daily aggregates, not per individual trip
- `update_day_trip_info()` crashes with `AttributeError: 'NoneType' object has no attribute 'hhmmss'` — library bug. India API returns trips without `tripTime` field; library assumes it exists. Raw JSON via `_get_trip_info()` works fine

**Available per-trip fields (India):**
```
serviceTID, tripStartTime, tripStartCoord {lat, lon}, tripEndTime, tripEndCoord {lat, lon}
```

**Available per-day aggregate fields:**
```
tripDrvTime (min), tripIdleTime (min), tripDist (km), tripAvgSpeed (km/h), tripMaxSpeed (km/h)
```

**Monthly data sample (Apr 2026):** 811 km across 16 driving days, 2042 min drive time, 685 min idle time, avg 24.2 km/h, max 132 km/h

**Verdict:** Cannot replace manual mileage column, but daily aggregates are useful supplementary data. `bluelink.py` runs on every `python src/main.py` execution, fetches last 4 months of daily aggregates via raw `_get_trip_info()`, and upserts to `outputs/bluelink_daily.csv` keyed on date. Pipeline continues gracefully if Bluelink is unavailable — fetch errors are logged and skipped

### Bluelink login failure (2026-08-09)

Login started failing with a generic `AuthenticationError("Login Failed")`. Debug logging traced it to `KiaUvoApiIN._get_authorization_code_with_redirect_url`: the `/api/v1/user/signin` POST now returns `{"step": 5, "upgrade": false}` instead of the `redirectUrl` the library expects — a `KeyError` gets swallowed by a broad `except Exception` and re-raised as the generic message.

Tested against both the pinned version (4.10.3) and the latest release (4.23.0) in an isolated venv — identical failure on both, so upgrading does not fix it. This looks like Hyundai's India Bluelink backend now requiring an extra verification step (OTP, forced T&C re-acceptance, or similar) that neither library version implements for the India region. Not fixable in our own code — needs to be cleared via the official Bluelink app/website, or a library fix from upstream once someone figures out what step 5 expects. Issue draft ready at `data/issues/bluelink_github_issue.md`.

### Commute structure
- User parks at a **nearby mall** most days — avoids traffic U-turn, saves ~15 mins
- Sometimes drives directly to **office** when parking is available
- Sometimes **sent to mall mid-trip** after arriving at office to find no parking (Scenario C)
- Return: office -> walk -> mall -> home  OR  office -> home directly
- Three anchor points defined: HOME, OFFICE, MALL — coordinates in config.yaml only

### GPX recording rules (user habit)
- **Under 30 min stops** (petrol bunk, chai, short wait): keep recording straight through
- **Over 30 min stops** (e.g. shooting range, football): PAUSE OsmAnd (not stop), resume when leaving. Creates a timestamp gap in one file — gap-based stop detector handles it correctly
- **Do not stop and restart for detours** — pause/resume keeps it as one file
- **Forgot to start at home**: still record — parser flags partial=True, useful for heatmap
- **Kept recording through a long stop by mistake**: parser detects via gap analysis and subtracts stop duration. Both raw and adjusted duration recorded

### GPX data confirmed working
- Tested with 19 real GPX files (14 classified trips, 3 discarded non-commute, 1 malformed skipped, 1 merged pair)
- OsmAnd logs: lat/lon, timestamp, elevation, speed (m/s), HDOP at every point
- Speed stored under OsmAnd namespace: https://osmand.net/docs/technical/osmand-file-formats/osmand-gpx
- Point interval: ~5-6 seconds — sufficient for junction-level bottleneck detection
- Parser handles malformed GPX files gracefully (skips with warning). Malformed filenames ARE added to processed.json so they're never reprocessed as data — but `load_and_sort_gpx_files()` re-scans every file in `data/gpx/` on every run (not just new ones) and unconditionally prints the warning again, so the console message repeats every run even after the file is "processed". Cosmetic only, no data impact
- Gap-based stop detection confirmed: 8 stops correctly detected across 41 trips (longest: 95.3 min)
- Walk detection: trailing walk segments (< 7 km/h for > 3 min, < 1 km) auto-truncated from trips ending near OFFICE or HOME. Endpoint reclassified after truncation. Fields: walk_detected, walk_duration_mins
- **Walk detection drift bug fixed (2026-08-09):** trailing GPS points logged at low frequency after the phone goes stationary (still <7 km/h, since speed reads ~0) were getting folded into the walk segment, inflating `walk_duration_mins` well past the real walk. Fix: `WALK_DRIFT_GAP_SECONDS = 30.0` in `parser.py` — the walk segment stops extending once the logging gap between consecutive points exceeds 30s (OsmAnd logs every 3-5s while genuinely moving). Confirmed on the 2026-06-25 trip: `walk_duration_mins` corrected from 43.5 → 10.7 min, matching the actual continuous-walking-speed window in the raw GPX
- Compound cases confirmed working: Scenario C + walk + long stop all detected independently on same trip (two real trips)
- As of 2026-06-01: 77 GPX files, 39 classified trips (27 full, 12 partial), 35 discarded (includes sub-threshold partials), 1 malformed
- As of 2026-08-09: 133 GPX files (57 new files processed this session → 38 new trips, 19 discarded), 77 classified trips total (59 full, 18 partial), 3 flagged (outlier/suspected stop). The one malformed file (`2026-04-18_20-52_Sat.gpx`) was deleted — it was a truncated duplicate of `2026-04-18_20-52_Sat_2.gpx`, which already covered its full time range and more, so deletion cost zero data

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
- Synthetic commuters: Whitefield->JP Nagar, Marathahalli->HSR Layout, Hebbal->Rajajinagar, Electronic City->Jayanagar
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
- Waypoints (e.g. shooting range) — enrich stop analysis without affecting trip classification
- Google Sheet CSV URL (sheet_csv_url)
- Bluelink credentials (username, password, pin) — optional

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
    issues/                  <- scratch notes, e.g. draft upstream bug reports (gitignored)
  outputs/                   <- all generated files (gitignored)
    master_trips.csv         <- one row per trip, all fields merged
    processed.json           <- tracks which GPX files have been processed (incremental)
    weather_cache.json       <- cached Open-Meteo responses to avoid re-fetching
    bluelink_daily.csv       <- Bluelink daily aggregates (date, distance, drive/idle time, speed, trip count)
    heatmap.html             <- speed-coloured map of road segments
    dashboard.html           <- summary charts and trends
  src/
    parser.py                <- GPX ingestion, trip classifier, merger, stop detection (gap + spatial dwell), walk detection, haversine
    detectors.py             <- Experimental detection modules (spatial dwell, tortuosity) — test harness only
    bluelink.py              <- Bluelink daily aggregate fetcher, upserts to outputs/bluelink_daily.csv
    weather.py               <- Open-Meteo fetch by lat/lon/datetime with local cache
    cluster.py               <- DBSCAN route clustering by path similarity, Nominatim labels
    analysis.py              <- generate_heatmap() and generate_dashboard()
    generate_demo.py         <- generates synthetic GPX files for portfolio demo mode
    main.py                  <- "Go" button. Run: python src/main.py
  data/demo/                 <- synthetic commuter data for portfolio (committed to GitHub)
  config.example.yaml        <- placeholder template (committed)
  config.yaml                <- real coordinates and settings (gitignored)
  requirements.txt
  CLAUDE.md                  <- this file
  README.md
  LEARNINGS.md
  SPECS.md
  ROADMAP.md
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

### Outbound scenarios (Home → Office)

| Scenario | GPX pattern | Walk origin | Parking | Notes |
|---|---|---|---|---|
| A — Parks at mall, walks to office | HOME → OFFICE (recording continued) | Walk starts near MALL (150m) | Mall | Walk truncated at mall. Most common outbound pattern |
| B — Parks at office directly | HOME → OFFICE | No walk (or short walk <3min near OFFICE) | Office | Rare — only when office parking available |
| C — Redirected to mall mid-trip | HOME → passes OFFICE → MALL (or OFFICE if walked) | Walk starts near MALL if walked | Sent to Mall | OFFICE mid-route detected. Walk detection same as A |

**Distinguishing A from B:** where the walking-speed segment begins. Within 150m of MALL → Scenario A (parking=Mall). Within 150m of OFFICE → Scenario B (parking=Office). This 150m check is hardcoded and independent of config anchor radii.

### Return scenarios (Office/Mall → Home)

| Scenario | GPX pattern | Walk origin | Parking | Notes |
|---|---|---|---|---|
| D — Direct home, walks inside | OFFICE/MALL → beyond HOME | Walk starts near HOME (150m) | Based on start anchor | Walk truncated at HOME |
| E — Via shooting range, forgot to pause | OFFICE/MALL → long slow period → HOME | N/A | Based on start anchor | Gap-based stop detection handles this |
| F — Via shooting range, paused correctly | Two GPX files with gap | N/A | Based on start anchor | Merge logic or separate partials |

### General scenario table

| Scenario | OsmAnd action | Sheet entry | Parser behaviour |
|---|---|---|---|
| Normal commute | Keep recording | 2 rows | Full trip extracted |
| Petrol bunk stop (<10 min) | Keep recording | Normal | Auto-split files merged |
| Forgot to start at home | Record from wherever | Normal | Flagged partial=True |
| Sent to mall mid-trip (Scenario C) | Keep recording | 1 row Home->Office | Auto-detected from GPX |
| Shooting range / football (>30 min) | PAUSE, resume after | Normal | Stop detected, duration adjusted |
| Forgot to pause (kept recording) | — | Normal | Gap analysis detects stop, adjusts |
| Only one leg recorded | Record that leg | 1 row | That leg processed independently |
| Walked to office after parking at mall | Keep recording | Normal | Walk detected, truncated at mall, parking=Mall |
| WFH day | No recording | No entry | Nothing to process |

---

## Parser Logic (parser.py)

### Anchor classification rules
- OFFICE radius: 150m, MALL radius: 150m, HOME radius: 300m (no nearby conflicting anchor)
- OFFICE-MALL separation: ~218m — radii must stay well below half this distance to avoid overlap
- When a point falls within multiple anchor radii simultaneously, nearest anchor wins (tie-break by haversine distance)
- Walk detection always uses a hardcoded 150m radius regardless of config anchor radius

### Walk detection and truncation (runs before classification)
Scans backwards from GPX end for sustained walking speed (< 7 km/h for > 3 min, < 1 km distance). If found, checks where the walk started against all three anchors at a fixed 150m radius. If the walk origin is near an anchor, the trip is truncated at the last vehicle-speed point.

Walk origin determines parking:
- Near MALL → parked at mall, walked to office (Scenario A). Checks for Scenario C (OFFICE mid-route)
- Near OFFICE → parked at office (Scenario B, long walk variant)
- Near HOME → return trip, walked inside after parking (Scenario D)
- **Ambiguity rule:** when MALL and OFFICE distances are within 55m of each other, MALL is preferred (parking prior: 12:3 ratio). Fixes misclassification for walk origins in the overlap zone between the two close anchors

Fields recorded: walk_detected, walk_duration_mins, walk_origin (anchor name)

### Trip classification
After walk detection, the walk origin overrides the truncated endpoint anchor for classification:
- Valid outbound  : start ~= HOME and end ~= OFFICE or MALL (or walk_origin at OFFICE/MALL)
- Valid return    : start ~= OFFICE or MALL and end ~= HOME (or walk_origin at HOME)
- Scenario C      : outbound with OFFICE coords mid-route, end at MALL → parking = "Sent to Mall"
- Partial trip    : end matches anchor, start does not → partial=True, kept for heatmap
- Unrelated trip  : no anchor match → discarded silently

### Short-stop merge logic
Two consecutive GPX files <30 mins apart that together form valid anchor pair -> merged.
Re-merge: if new file is adjacent to already-processed file, old CSV row replaced with merged result.

### Mid-trip stop detection (two-layer system)

**Layer 1 — Gap-based detection (priority):**
Timestamp gap > 20 min + displacement < 150m + entry speed < 15 km/h + not at any anchor:
- stop_detected = True, stop_duration_mins, stop_location, adjusted_duration_mins recorded
- Gap-based detection is correct — OsmAnd stops logging when parked (no speed=0 run)
- **Waypoint matching:** detected stops are checked against waypoints in config.yaml. If stop midpoint falls within a waypoint radius, `stop_location` = waypoint name; otherwise "Unknown". Waypoints also checked via dwell-time analysis (>=10 min near waypoint) to catch unreported stops

**Layer 2 — Spatial dwell detection (catches what gaps miss):**
Sliding window: GPS stays within 50m radius for 15+ consecutive minutes, not at any anchor:
- Runs after gap-based detection; skips dwells that overlap gap-stops (within 5 min tolerance)
- Catches stops where OsmAnd kept logging at low frequency (GPS drift above 10m threshold)
- Dwell durations subtracted from adjusted_duration_mins; locations appended to stop_location with " + "
- New CSV field: `dwell_stops` — each event with time, duration, and location
- As of 2026-06-01: 3 new stops caught that gap-based detection missed (one at an unknown location, the others at a waypoint)
- Total across all trips: 7 gap-based stops + 3 dwell-based stops = 10 detected stops

### Incremental processing
outputs/processed.json tracks processed filenames. Each run only processes new files.

### Known parser limitations (as of 2026-05-25) — all resolved
1. ~~Malformed files not added to processed.json~~ — **FIXED** (filenames are added; the per-run warning print is still cosmetic-repeated regardless, see GPX data notes above — not a data bug)
2. ~~No minimum distance/duration filter on partials~~ — **FIXED**: ≥10 km AND ≥20 min threshold
3. ~~Walk detection fires on ultra-short trips~~ — **FIXED**: guard on trips <10 km or <20 min
4. ~~Long detours without gaps escape stop detection~~ — **RESOLVED**: spatial dwell detection catches parked stops where OsmAnd kept logging; suspected unreported stop detection catches remaining edge cases via effective speed + duration anomaly
5. **Near-office zone (150m–800m) catches no current trips** — all borderline trips end >1 km from office. Feature ready for future trips that land in range
6. ~~Walk detection counted stationary GPS drift after arrival as part of the walk~~ — **FIXED (2026-08-09)**: see `WALK_DRIFT_GAP_SECONDS` in GPX data notes above

### Distance outlier detection
Trips whose distance exceeds 2.5 SD from mean distance for their direction are flagged `outlier=True` with reason string. Requires ≥5 full trips per direction. Outliers excluded from clustering, dashboard, and departure time analysis but kept in heatmap and master_trips.csv. Current status (2026-05-25): 0 outliers flagged — return direction has high natural variance (SD 3.13 km) due to mall-detour vs direct routes.

### Suspected unreported stop detection
Compound rule: adjusted duration >2.5 SD from direction mean AND effective speed (distance/duration) <15 km/h AND stop_detected=False. Uses effective speed because OsmAnd's per-point avg ignores stationary time. Catches recordings left running through activities (shooting range, football) with intermittent movement. Flagged trips: excluded from departure time analysis and duration averages, kept in heatmap. Pipeline prints a warning for each. Current: 1 trip flagged.

### Near-office classification
Trips ending 150m–800m from OFFICE (outside anchor radius but plausibly office-area) get `near_office=True`. Included in heatmap and CSV but excluded from parking analysis, departure time curves, and clustering. Bidirectional: also catches trips starting in the near-office zone heading home.

---

## Full Pipeline — python src/main.py

1. Check for new GPX files not in processed.json
2. Parse new files: classify (incl. near-office detection), merge, extract, detect stops (gap-based + spatial dwell)
3. Fetch Bluelink daily aggregates (last 4 months) → upsert to outputs/bluelink_daily.csv (silent on failure)
4. Fetch sidecar sheet CSV fresh from sheet_csv_url in config.yaml
5. Look up petrol price by date range from petrol_prices.csv
6. Enrich all trips: weather, sheet join, petrol price, fuel cost, derived fields + distance outlier detection (2.5 SD per direction)
7. Write enriched master_trips.csv
8. Cluster all trips by path similarity (DBSCAN, per direction) → add route_cluster column
9. Generate heatmap.html — Folium speed-coloured map of all trips
10. Generate dashboard.html — Plotly charts (departure vs duration, trends, mileage, parking)

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
- [x] weather.py — forecast + archive API, OFFICE coordinates, condition/temp/precipitation, cached
- [x] bluelink.py — daily aggregate fetcher, runs on every pipeline execution, 4-month lookback
- [x] Google Sheet CSV fetch in main.py
- [x] Petrol price lookup from petrol_prices.csv
- [x] Parser resilient to malformed GPX files (skips with warning)
- [x] Join pipeline verified end-to-end — 67 GPX files, 34 trips (22 full + 12 partial), 25 sheet rows, weather + Bluelink all populated
- [x] requirements.txt created
- [x] All markdown files created: README, CLAUDE, LEARNINGS, SPECS, ROADMAP, CONTRIBUTING
- [x] All changes committed and pushed to GitHub
- [x] Doc cleanup (2026-08-09): learnings.md/roadmap.md/specs.md renamed to LEARNINGS.md/ROADMAP.md/SPECS.md; README restructured with a "Why this exists" section (contrast with live-routing apps) and venv setup instructions, setup steps that duplicated CONTRIBUTING.md trimmed; `.claude/` untracked and gitignored so local permission settings stop showing as modified/untracked
- [x] Walk detection: trailing walk segments auto-truncated from trips ending near OFFICE or HOME (< 7 km/h, > 3 min, < 1 km). 12 walks detected across 41 trips (mostly office-end outbound, 2 return-end)
- [x] Compound detection confirmed: Scenario C + walk + long stop all fire independently on same trip

### Done (Phase 3)
- [x] cluster.py — DBSCAN path similarity clustering (outbound/return separate), Nominatim labels, 5-trip minimum per direction, route_cluster column in master_trips.csv
- [x] analysis.py — generate_heatmap() and generate_dashboard(), called by main.py steps 6-7
- [x] heatmap.html — Folium + CartoDB Positron tiles (file:// compatible), speed-coloured segments (green/yellow/orange/red), coverage-weighted thickness, no anchor markers, legend, LayerControl with 4 toggleable layers (Commute/Partial/Non-commute/Flagged)
- [x] dashboard.html — Plotly self-contained HTML, 5 charts (departure vs duration, day-of-week, duration trend, mileage trend, parking pie), #e85d04 orange + dark theme
- [x] Pipeline expanded to 7 steps: parse → Bluelink → sheet → petrol → enrich → cluster → visualise
- [x] Dependencies: numpy, scikit-learn, folium, plotly added to requirements.txt
- [x] Spatial dwell detection: 50m radius, 15 min minimum, integrated as priority-2 after gap-based stops. Catches 3 new stops that gap-based missed
- [x] Tortuosity detection: tested and parked — GPS jitter at traffic signals indistinguishable from walking at OsmAnd's 5-6s logging interval. Revisit in Phase 5
- [x] Pipeline migrated to jobpi (2026-08-09): repo cloned, venv + requirements installed, config.yaml + 133 GPX files copied via scp, full 7-step pipeline verified end-to-end (77 trips, weather/sheet/petrol/clustering all working, Bluelink degraded gracefully as expected). Manual SSH trigger preserved, nothing exposed publicly. Windows copy left in place for now (retire once Pi run is confirmed stable over a few weeks)

### To Do

#### Phase 4 — Demo mode and portfolio (after ~40 real trips)
- [ ] generate_demo.py — synthetic GPX generator using OSRM road geometry for 4 fictional Bengaluru commuters:
  - Whitefield -> JP Nagar
  - Marathahalli -> HSR Layout
  - Hebbal -> Rajajinagar
  - Electronic City -> Jayanagar
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
- Open-Meteo: free, no API key, forecast API (<90 days) + archive API (older) for weather by lat/lon
- Hyundai Bluelink: daily trip aggregates via `hyundai_kia_connect_api` (region=6 India). Optional — pipeline runs without it. **Login broken as of 2026-08-09** — see Bluelink login failure notes above
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
