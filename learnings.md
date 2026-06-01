# commute-lens — Key Technical Decisions

This document records the non-obvious design choices made during this project and the reasoning behind them. Decisions that seemed obvious are not here. Things that required investigation, debugging, or deliberate tradeoffs are.

---

## Gap-based stop detection, not speed-sequence detection

**What I built:** Stop detection checks for large time gaps between consecutive points at nearly the same coordinates, not for runs of low-speed readings.

**Why:** OsmAnd uses displacement-based recording (minimum 10m displacement threshold). When the car is stationary, OsmAnd stops logging entirely — there is no GPS point recorded while parked. This means a 65-minute stop at a shooting range produces exactly zero logged points during the stop. The stop is visible only as a jump in timestamp between the last point before parking and the first point after leaving.

A speed-sequence detector (e.g. "flag if speed < 5 km/h for 20+ consecutive minutes") would find nothing, because there are no points to form the sequence. When I analysed the April 14 GPX file, the shooting range stop had only three points in the relevant speed range, spanning under 30 seconds — not 65 minutes.

**Detection criteria (all four must hold):**
- Time gap > 20 minutes
- Spatial displacement < 150 m (car stayed put)
- Speed at entry point < 15 km/h (car was slowing, not cruising through a coverage dead zone)
- Midpoint not within any anchor radius (not arriving home or at work)

The entry-speed guard matters: a GPS coverage outage on a highway can also produce a large time gap, but the entry speed will be high. Without this guard, a tunnel or dead zone on the outer ring road would look like a stop.

---

## OsmAnd namespace for speed extraction

**What I built:** Speed is extracted from `<extensions><osmand:speed>` using the full namespace URI `https://osmand.net/docs/technical/osmand-file-formats/osmand-gpx`.

**Why:** OsmAnd logs speed in metres per second under its own XML namespace, not in the standard GPX `<speed>` element. A naive GPX parser looking for `<speed>` in the GPX namespace finds nothing and falls back to deriving speed from distance/time, losing the per-point precision OsmAnd actually records.

The speed value must be multiplied by 3.6 to convert m/s to km/h. Not all points have speed extensions — the namespace element is present only when OsmAnd logs it. The extractor returns `None` for missing points and the speed average skips them.

---

## Incremental processing design

**What I built:** `outputs/processed.json` stores the set of GPX filenames already committed to `master_trips.csv`. Each run parses only new files and appends to the CSV.

**Why:** Without this, every run would re-parse all GPX files and rewrite the CSV from scratch, discarding enrichment fields (weather, sheet data) that `main.py` wrote in a previous run. The parser only knows about its own output columns; it cannot reconstruct enrichment data.

**The re-merge edge case:** If a new GPX file arrives adjacent (< 30 min gap) to an already-processed file, the two form a merged group that supersedes the old single-file CSV row. `write_csv_incremental()` handles this by checking for filename overlap and removing stale rows before appending the new merged row.

**The extrasaction guard:** The parser's `DictWriter` uses `extrasaction="ignore"` so that when `main.py` has already written enrichment fields to `master_trips.csv`, the parser's incremental write does not raise a ValueError on the extra columns it does not own.

---

## Why Google Timeline was abandoned

Google Timeline used to export a complete location history as a JSON file. The new on-device format (post-2023) stores data on the device itself and has no bulk export path. The data is also increasingly sparse due to battery optimisation — it does not record continuous track points the way OsmAnd does.

OsmAnd was chosen because it records at a configurable interval (3s during navigation, 5s otherwise), uses displacement-based triggers to avoid logging while truly stationary, and exports clean GPX files that can be shared via Google Drive.

---

## Why a minimal manual sheet instead of fully automated logging

The only fields that genuinely cannot be extracted from GPS data are:

- **Mileage (km/l)** — must be read from the car's trip computer. No OBD-II access without additional hardware. The ARAI baseline of 19.2 km/l is not useful for per-trip cost analysis.
- **Day type** — Normal / Post-Holiday / Pre-Holiday / WFH. Context that affects departure time patterns (post-holiday Mondays are reliably worse) but cannot be inferred from GPS.
- **Notes** — Detours, missing GPX reason, unusual events.

Everything else is derived automatically. The sheet is five columns wide and two rows per commute day. It is published as a CSV endpoint and fetched live on every pipeline run — no manual export step.

---

## Anchor tie-breaking

OFFICE and MALL are geographically close (approximately 250 metres apart). When a trip starts within the radius of both anchors simultaneously, the parser picks the closer anchor rather than the first match. Without this, trips starting near the mall would always be classified as starting at OFFICE because OFFICE was checked first, producing wrong parking labels.

---

## GPX sync is a manual copy step by design

Android 13+ restricts access to `Android/data/` from the Files app, where OsmAnd stores track files. The workaround is to share through OsmAnd itself: My Places → Tracks → long press → Share → Google Drive.

Files are manually copied from Google Drive to `data/gpx/` before running the pipeline. Automating the Drive download would add OAuth credential management for a step that takes under a minute and happens at most weekly. Not worth it.

---

## Google Sheets CSV export quirks

**What broke:** The sheet join silently produced no matches on the first end-to-end run. All enrichment fields were blank even though 6 sheet rows were fetched.

**Root cause 1 — title row above headers:** Google Sheets CSV export includes every row starting from row 1. The sheet had a merged title row ("Commute Sidecar Log") in row 1, followed by the real column headers (Date, Direction, Mileage…) in row 2. `csv.DictReader` used the title row as the header, so all column names were wrong.

**Fix:** `load_sheet_csv()` now scans lines until it finds one containing both "Date" and "Direction", then treats that line as the header. This is robust to sheets with any number of title/description rows above the real headers.

**Root cause 2 — column name whitespace:** The sheet column header was `Mileage(km/l)` (no space before the parenthesis). The code looked for `Mileage (km/l)` (with a space). One character difference, silent miss.

**Fix:** `enrich_row()` now tries both variants with `get("Mileage (km/l)") or get("Mileage(km/l)")`.

**Root cause 3 — date format:** Google Sheets renders dates as `14-Apr-26` in its CSV export when the cell format is set to "Date". The original parser only handled `DD/MM/YYYY` and `YYYY-MM-DD`.

**Fix:** `build_sheet_index()` now tries four formats in order: `%d/%m/%Y`, `%Y-%m-%d`, `%d-%b-%y`, `%d-%b-%Y`. Unrecognised formats print a warning and skip the row rather than silently dropping it.

**Lesson:** Sheet CSV joins fail silently by default. Always add a diagnostic that shows how many rows matched vs how many were in the sheet, and print unrecognised date formats explicitly so the failure is visible.

---

## Recording rule: PAUSE, not stop

For stops longer than 30 minutes (shooting range, football), the correct OsmAnd action is to **pause** recording, not stop it. A paused recording resumes the same file when you restart; stopping and restarting creates two separate files.

With a paused recording, the gap between the last pre-stop point and the first post-stop point is exactly the stop duration. The gap-based detector handles this correctly. If two separate files were created instead, they would be merged by `merge_consecutive_groups()` only if the gap is under 30 minutes — which it is not for a 60-minute stop. So the two files would be classified separately, potentially misclassified.

---

## Gap-based stop detection is the only correct approach for OsmAnd

**What might seem obvious but isn't:** Most GPS stop detection looks for a run of speed=0 or speed<threshold readings. With OsmAnd's displacement-based recording, this approach fundamentally cannot work.

**Why:** OsmAnd has a 10m minimum displacement threshold. When the car is parked, there is no displacement, so OsmAnd logs nothing. A 65-minute stop produces zero GPS points during the stop. There is no speed=0 run to detect — only a clean timestamp gap between the last point before parking and the first point after leaving.

**Implication:** Any stop detection algorithm for OsmAnd data must be gap-based (look for time jumps between consecutive points at similar coordinates), not sequence-based (look for runs of low-speed readings). This is a fundamental property of the data source, not a design preference. The April 14 shooting range stop confirmed this: only 3 points below 15 km/h spanning <30 seconds, but a 65-minute timestamp gap.

---

## The portfolio privacy problem — solved by synthetic commuter profiles

**The problem:** A speed heatmap of real commute data reveals your home and office locations with high precision. The start cluster is home; the end cluster is your workplace. Publishing this on a portfolio site is a non-starter for privacy.

**Options considered:**
1. Blur endpoints (add noise to first/last N points) — still reveals the corridor
2. Show only mid-route segments — loses the context that makes it interesting
3. Anonymise by aggregating across many users — don't have many users
4. Synthetic commuter profiles on real road geometry — looks real, reveals nothing personal

**Solution:** Use OSRM (free routing on OpenStreetMap) to get real Bengaluru road geometry for 4 fictional commuter corridors (Whitefield→JP Nagar, Marathahalli→HSR Layout, Hebbal→Koramangala, Electronic City→Indiranagar). Generate synthetic GPX files with realistic speed profiles that include known bottlenecks (Silk Board, Iblur, Marathahalli bridge, Hebbal flyover), time-of-day variation, and weather impact.

**Why this works:** The portfolio demonstrates the analytical pipeline and visualisation quality without any personal data. The synthetic profiles use real road networks so the output looks genuine. The portfolio page is clearly labelled as illustrative. Real analysis stays entirely local.

---

## Weather fetched at OFFICE coordinates, not HOME

**What I changed:** Weather is fetched using the OFFICE/MALL area coordinates rather than HOME.

**Why:** The commute corridor's weather conditions matter more at the destination end, where Bengaluru's microclimate variation is most relevant to the trip. The office area (Koramangala) and home area are ~18 km apart — weather can differ meaningfully, especially during monsoon.

**API choice:** Open-Meteo's forecast API only covers ~92 days of history. For older trip dates, `weather.py` automatically switches to the archive API (`archive-api.open-meteo.com`). The field set was simplified from the original 5 fields (temp, humidity, rain, wind, WMO code) to 3: `weather_condition` (Clear/Cloudy/Rain/Heavy Rain mapped from WMO codes), `temp_c`, `precipitation_mm`. The human-readable condition is more useful for analysis than a raw WMO integer.

---

## Route clustering needs a minimum trip count to be meaningful

**What I built:** DBSCAN clustering on symmetric point-to-point track distances, with a minimum threshold of 5 full (non-partial) trips per direction before clustering runs.

**Why the threshold:** With fewer than 5 trips, DBSCAN either puts everything in one cluster (useless) or marks everything as noise. The distance metric — average nearest-point distance between subsampled tracks — is sensitive to GPS drift on similar routes, so a small sample size produces unreliable clusters. With 5+ trips, repeated patterns emerge and DBSCAN's density-based grouping works correctly.

**Why separate outbound and return:** The same commuter may take different routes in different directions (e.g. outbound via ORR to avoid a U-turn, return via a direct road). Mixing directions would create false clusters based on direction rather than route choice.

**Label generation:** Each cluster's distinctive segment (the point most distant from all other clusters) is reverse geocoded via Nominatim to produce labels like "Via Outer Ring Rd". This is more useful than "Cluster 0" but depends on Nominatim returning a meaningful road name — coordinates without nearby named roads fall back to lat/lon display.

---

## Folium segment rendering for speed heatmaps

**What I built:** Each consecutive pair of GPS points is rendered as a coloured polyline segment on a Folium map. Colour encodes speed; line weight encodes how many trips covered that road segment.

**Why per-segment, not per-point:** A point-based heatmap (Folium's HeatMap plugin) shows density, not speed. For commute analysis, the useful information is *where* the car is slow, not *where* GPS points are dense. Per-segment colouring with speed thresholds (green >30, yellow 15-30, orange 5-15, red <5 km/h) directly shows bottleneck locations.

**Coverage-weighted thickness:** A road segment driven once has low confidence — the speed might be an outlier. A segment driven 10 times is a reliable signal. Line thickness scaled by trip coverage communicates this visually without requiring the user to think about sample sizes.

**Gap filtering:** Segments where consecutive points are >500m apart are skipped. These occur at recording gaps (pause/resume, OsmAnd auto-split boundaries) and would draw misleading straight lines across the map.

---

## Walk detection needs a distance cap to avoid false positives from slow traffic

**What I built:** Walk detection scans backwards from the trip endpoint for sustained speed below 7 km/h. When found (> 3 minutes), it truncates the trip at the last point where vehicle speed exceeded 7 km/h.

**What broke:** The April 23 outbound trip was flagged with a 34-minute "walk" — clearly not walking. Bengaluru traffic regularly crawls below 7 km/h for extended periods near the office, and the speed-only check could not distinguish this from an actual walk.

**Fix:** Added a 1 km maximum walk distance cap. A mall-to-office walk is ~250 m; anything over 1 km is traffic crawl. After adding the guard: 3 legitimate walks remain (4–8 min each, 200–750 m), the 34-minute false positive is correctly rejected.

**Why the distance cap works better than a duration cap:** Duration alone would require tuning — a 10-minute walk is plausible at a large campus but 34 minutes is not. Distance is a stronger signal because a walk between two nearby locations has a hard physical upper bound regardless of walking speed or pauses. The mall and office are ~250 m apart, so 1 km is generous enough for GPS drift without accepting multi-km traffic crawl.

---

## OpenStreetMap tiles require a referer header — use CartoDB Positron for local files

**What broke:** `heatmap.html` showed "Access blocked — Referer required" when opened as a local `file://` URL. OpenStreetMap's tile servers require a valid HTTP Referer header, which browsers do not send for `file://` origins.

**Fix:** Switched from `tiles="OpenStreetMap"` to CartoDB Positron tiles (`https://cartodb-basemaps-{s}.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png`), which serve tiles without a referer requirement. The light/neutral style also works better as a background for speed-coloured overlays.

---

## Return route clusters reflect parking choice, not genuinely different routes

**What the clustering showed:** Office-to-Home trips split into two clusters: "Via Swamy Vivekananda Road" (7 trips, avg 28.2 km, 103 min) and "Via Residency Road" (3 trips, avg 22.2 km, 76 min).

**What it actually means:** The two clusters aren't alternative routes to the same destination — they're the same route with different starting points. Vivekananda Road trips go via the mall first (parking there on busy office-parking days, or Scenario C detours), adding ~6 km. Residency Road trips go directly home. Average speeds are identical (~21 km/h), confirming the time difference (28 min) is explained entirely by extra distance, not traffic conditions.

**Key pattern:** All 3 Residency Road trips departed after 20:15. All 7 Vivekananda trips departed before 20:20 (most before 20:00). Late departures correlate with direct-home drives — no mall stop needed.

**Actionable insight:** On days with no mall stop, departing after 20:15 via Residency Road saves 28 minutes and 6 km compared to the mall-route average. This is the single biggest time-saving variable in the return commute.

---

## Effective speed vs OsmAnd per-point average for anomaly detection

**The problem:** OsmAnd's average speed (mean of per-point `<osmand:speed>` values) is useless for detecting unreported stops. A trip where someone drove 30 km in 251 minutes (effective speed 7.3 km/h) shows an OsmAnd average of 20.9 km/h — because OsmAnd only logs points when the car is moving above its displacement threshold (10m). Stationary time produces zero points, making the per-point average completely blind to stops.

**The fix:** Use effective speed = distance_km / duration_min × 60. This is the ground truth for how fast you actually got from A to B. When effective speed is anomalously low (<15 km/h) combined with duration being >2.5 SD above the direction mean and no clean stop gap detected, the trip likely had an unreported activity period (shooting range, football) where the user forgot to pause recording.

**Why this matters beyond this project:** Any GPS analysis using OsmAnd (or similar displacement-gated loggers) that computes speed from per-point values will systematically overestimate speed and miss stationary periods. The per-point average only tells you "how fast were you when you were moving" — not "how efficiently did you cover this route."

---

## Ambiguous walk origins need a parking prior, not just nearest-anchor

**What broke:** The April 21 outbound trip was classified as `parking=Office` when the user actually parked at the mall. Walk detection correctly identified a trailing walk segment, but the walk origin point was 97.1m from OFFICE and 149.0m from MALL — both within the 150m walk anchor radius. Nearest-anchor tiebreaking picked OFFICE.

**Why nearest-anchor was wrong:** OFFICE and MALL are only ~218m apart. Any walk origin in the overlap zone between their 150m radii is genuinely ambiguous from GPS data alone. But the user parks at the mall ~80% of the time (12 out of 15 trips) — the prior is heavily MALL-biased.

**Fix:** When MALL and OFFICE walk-origin distances are within 55m of each other, prefer MALL. The 55m threshold (slightly above the observed 51.9m difference) accounts for GPS coordinate jitter at the boundary. This is a real-world prior encoded as a tiebreaker, not a hack — it reflects the actual parking distribution.

**Why this matters:** When two anchors are very close, pure distance-based tiebreaking produces classification noise that worsens as more trips land in the overlap zone. A prior based on observed behaviour makes the correct call for the vast majority of ambiguous cases. The fix is specific to the MALL-OFFICE pair because they have the unusual geometry (218m separation with 150m match radii).

---

## Named waypoints turn anonymous stops into actionable data

**What I built:** A `waypoints` section in `config.yaml` that defines named locations (shooting range, gym, etc.) with coordinates and a match radius. The stop detector checks whether each detected stop falls within a waypoint's radius and populates a `stop_location` field in `master_trips.csv`.

**Why:** Gap-based stop detection correctly identified stops, but the output was just `stop_detected=True` with a duration — no location context. Looking at `master_trips.csv`, you could see "this trip had a 65-minute stop" but not *where*. For commute analysis, knowing the stop was at the shooting range vs an unexpected traffic jam vs a friend's place matters.

**Design choice — waypoints are separate from anchors:** Anchors (HOME, OFFICE, MALL) affect trip classification — they determine direction, parking, and whether a trip is valid. Waypoints only enrich stop metadata. A shooting range waypoint doesn't change whether a trip is "Home to Office" — it just adds context to the stop. This separation is important because adding a waypoint should never change existing trip classifications.

**Dwell-time detection for unreported stops:** Beyond gap-based stops, the parser also checks whether the trip spent >=10 minutes near any waypoint. This catches "suspected unreported stops" where the user forgot to pause OsmAnd — no clean gap exists, but the trip clearly passed through a waypoint area with significant dwell time.

**Results:** 7 stops across return trips now show `stop_location=Shooting Range`. Zero "Unknown" stops — every detected gap-based stop matched a waypoint.

---

## Spatial dwell detection complements gap-based stops

**What I built:** A sliding-window detector that finds segments where GPS position stays within a 50m radius for 15+ consecutive minutes. Runs after gap-based stop detection; skips dwells that overlap existing gap-stops (within 5 minutes) to avoid double-counting.

**Why gap-based detection alone is insufficient:** OsmAnd's displacement-based recording (10m threshold) means that when parked, no points are logged — producing clean timestamp gaps that the gap detector catches. But when the car is parked and the phone has enough GPS drift to occasionally exceed the 10m threshold, OsmAnd keeps logging sporadically. These low-frequency points fill in the gap, making it too small for gap-based detection. The car is clearly stationary (all points within a 30-50m radius for an hour), but no single timestamp jump exceeds the 20-minute gap threshold.

**What it caught:** Three trips that gap detection missed:
- May 5 return: 174.9 min of dwell at an unknown location (friend visit). Previously only caught by the statistical suspected-unreported-stop heuristic. Now has a clean spatial signal
- May 26 return: 99.4 min at the Shooting Range. OsmAnd logged consistently throughout — no gap exceeded 20 minutes
- Jun 1 return: 52.5 min across two stops (20 min unknown snack stop + 32 min Shooting Range)

**Algorithm:** 2x max-centroid-distance as the spread metric (O(n) per window check, not O(n^2) pairwise). Sliding window expands until the 15-minute minimum is met, then greedily extends while the spread stays under 50m. Adjacent qualifying windows are merged. Centroid is checked against anchors (HOME/OFFICE/MALL) — dwells at trip endpoints are suppressed.

**Priority order:** Gap-based first (catches clean pauses), then spatial dwell (catches sporadic logging during stops). Both contribute to `adjusted_duration_mins`. The `dwell_stops` CSV field records each dwell with time, duration, and location.

---

## Tortuosity detection is unreliable at OsmAnd's logging resolution

**What I tested:** A tortuosity detector that scans for erratic low-speed segments (path_length / straight_line_displacement > threshold) to catch wandering at petrol bunks, snack stops, and forecourts.

**What went wrong:** At OsmAnd's 5-6 second logging interval, GPS jitter at traffic signals is indistinguishable from slow walking. A car stopped at a red light for 3 minutes produces 30-40 points with 2-5m GPS drift between each — the same pattern as someone walking around a petrol bunk forecourt. Tortuosity scores of 5-15 appeared on both genuine erratic movement (shooting range parking lot) and normal traffic stops.

**Threshold sweep results:**
- tortuosity > 2.0, min 2 min: 46 triggers, mostly GPS noise at traffic signals
- tortuosity > 2.5, min 2 min: 38 triggers, still too noisy
- tortuosity > 3.0, min 3 min: 23 triggers, cleaner but still ~50% false positives on commute trips

**Why this is fundamentally hard:** The issue is spatial resolution, not algorithm design. At 5m GPS accuracy and 5-second intervals, a stationary car and a slowly walking person produce overlapping movement signatures. Higher resolution GPS (1-second logging) or ground truth calibration data (manual stop labels for 50+ trips) would be needed to separate the distributions. Filed for Phase 5 revisit.

---

## Bluelink API does not provide per-trip mileage for India

**What I tested:** `hyundai_kia_connect_api` v4.10.3 with region=6 (India), brand=2 (Hyundai), against a Hyundai Exter AMT registered Dec 2024.

**What works:** Login, vehicle discovery, monthly aggregates (total distance, drive time, idle time, avg/max speed, list of driving days with trip counts). Per-trip data includes start/end timestamps and start/end lat/lon coordinates.

**What does not work:** Per-trip mileage (km/l) — the one field that would have eliminated the manual sheet entry. India API returns no fuel efficiency data at any granularity. Per-trip drive time, distance, and speed are also absent — only daily aggregates exist.

**Library bug encountered:** `update_day_trip_info()` crashes with `AttributeError: 'NoneType' object has no attribute 'hhmmss'`. The India API returns trip entries without the `tripTime` field that the library unconditionally accesses. The raw JSON via `_get_trip_info(token, vehicle, date_string, 1)` works fine and is how I extracted the per-trip fields.

**History depth:** Only Jan 2026 – present (~4 months back from April 2026). Dec 2025 and earlier returns empty responses despite the car being registered Dec 2024. Either the India server has a short retention window or Bluelink was not activated until Jan 2026.

**Decision:** Cannot replace manual mileage entry, but daily aggregates are useful supplementary data. `bluelink.py` fetches last 4 months of daily aggregates on every pipeline run and upserts to `outputs/bluelink_daily.csv`. Uses the raw `_get_trip_info()` method to avoid the library crash. Pipeline continues gracefully if Bluelink is unavailable — fetch errors are logged and skipped.
