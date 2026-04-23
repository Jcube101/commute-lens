# commute-lens — Technical Specification

---

## Data sources

| Source | What it provides | How accessed |
|---|---|---|
| OsmAnd GPX files | GPS track points (lat/lon/time/speed/ele/hdop) | Manual copy to `data/gpx/` before pipeline run |
| Google Sheets sidecar | Mileage, day type, notes | Fetched live via CSV export URL in config.yaml |
| Open-Meteo API | Hourly weather (condition, temp, precipitation) | HTTP GET, forecast + archive APIs, cached in `outputs/weather_cache.json` |
| Hyundai Bluelink API | Daily trip aggregates (distance, drive/idle time, speed) | `hyundai_kia_connect_api` v4.10.3, region=6 (India), cached in `outputs/bluelink_daily.csv` |
| `petrol_prices.csv` | Petrol price by date range | Read from `data/reference/petrol_prices.csv` |
| Nominatim (OpenStreetMap) | Reverse geocoding for cluster labels | HTTP GET, free, 1 req/sec rate limit. Used by `cluster.py` |
| CartoDB Positron tiles | Map tiles for heatmap | Tile URL in `analysis.py`. Free, no key, no referer required |
| OSRM (router.project-osrm.org) | Road geometry for synthetic demo routes | HTTP GET, free, no API key. Used only by `generate_demo.py` |
| OpenFreeMap tiles | Map tiles for portfolio frontend | Tile URL in frontend config. Free, no key |

---

## master_trips.csv schema

One row per classified trip. All fields are strings in the CSV.

### Parser-derived fields

| Field | Type | Source | Notes |
|---|---|---|---|
| `filename` | str | GPX filename(s) | Semicolon-joined if merged from multiple files |
| `date` | YYYY-MM-DD | GPX first point timestamp | Converted to IST before extracting date |
| `direction` | str | Classification | "Home to Office" or "Office to Home" |
| `departure_time` | str | GPX first point | "YYYY-MM-DD HH:MM:SS UTC+05:30" |
| `arrival_time` | str | GPX last point | "YYYY-MM-DD HH:MM:SS UTC+05:30" |
| `duration_min` | float | arrival - departure | Raw wall-clock duration including stops |
| `distance_km` | float | Haversine cumsum | Cumulative great-circle distance over all points |
| `avg_speed_kmh` | float | OsmAnd speed avg | Average of per-point OsmAnd speeds; falls back to distance/time |
| `parking` | str | Endpoint anchor match | "Office", "Mall", "Sent to Mall", or "Unknown" |
| `partial` | bool | Classification | True if recording started mid-route (not at an anchor) |
| `scenario_c` | bool | Mid-route OFFICE match | True if OFFICE coordinates appear mid-route before MALL endpoint |
| `stop_detected` | bool | Gap detector | True if at least one mid-trip stop was flagged |
| `stop_duration_mins` | float | Gap detector | Sum of all detected stop durations |
| `adjusted_duration_mins` | float | duration - stop | Driving time net of detected stops |
| `point_count` | int | GPX parse | Number of track points in the trip (after walk truncation if applicable) |
| `walk_detected` | bool | Walk detector | True if a trailing walk segment was truncated |
| `walk_duration_mins` | float | Walk detector | Duration of the removed walk segment |

### Enrichment fields (added by main.py)

| Field | Type | Source | Notes |
|---|---|---|---|
| `mileage_kmpl` | float | Google Sheet | Entered manually from car trip computer after trip |
| `day_type` | str | Google Sheet | Normal / Post-Holiday / Pre-Holiday / WFH / Detour / Other |
| `notes` | str | Google Sheet | Free text anomalies |
| `petrol_price_rs` | float | petrol_prices.csv | Lookup by trip date against date ranges |
| `fuel_cost_rs` | float | Derived | distance_km / mileage_kmpl * petrol_price_rs |
| `day_of_week` | str | Derived from date | "Monday", "Tuesday", etc. |
| `week_num` | int | Derived from date | ISO week number |
| `weather_condition` | str | Open-Meteo | Clear / Cloudy / Rain / Heavy Rain (from WMO code) |
| `temp_c` | float | Open-Meteo | Temperature at departure hour (°C) |
| `precipitation_mm` | float | Open-Meteo | Precipitation at departure hour (mm) |
| `route_cluster` | str | cluster.py | DBSCAN route label (e.g. "Via Outer Ring Rd") or "Unclustered — insufficient data" |

---

## config.yaml structure

```yaml
sheet_csv_url: <Google Sheets CSV export URL>

anchors:
  home:
    lat: <float>
    lon: <float>
    radius_m: 300           # match radius — points within this are "at HOME"
  office:
    lat: <float>
    lon: <float>
    radius_m: 300
  mall:
    name: <string>
    lat: <float>
    lon: <float>
    radius_m: 300

vehicle:
  name: <string>
  arai_kmpl: 19.2

thresholds:
  min_trip_points: 10       # fewer points -> discard (too short / GPS glitch)
  gap_split_minutes: 10     # OsmAnd auto-split gap setting (informational)
  slow_speed_kmh: 15        # reserved for future heatmap colouring
  stop_min_minutes: 20      # gap longer than this -> candidate mid-trip stop

paths:
  gpx_dir: data/gpx/
  sheet_log: data/reference/sheet_log.csv    # legacy path, kept for reference
  petrol_prices: data/reference/petrol_prices.csv
  outputs: outputs/

bluelink:
  username: <Bluelink email>
  password: <Bluelink password>
  pin: <Bluelink PIN>
```

`config.yaml` is gitignored. `config.example.yaml` (committed) contains only placeholder values. Bluelink credentials are optional — pipeline runs without them.

---

## Pipeline steps (main.py)

1. **Load config** — read `config.yaml`, build Anchor objects for HOME, OFFICE, MALL
2. **Incremental GPX parse** — compare `data/gpx/*.gpx` against `outputs/processed.json`; parse only new files. Malformed GPX files are skipped with a warning
3. **Merge consecutive groups** — files with inter-file gap < 30 min are merged before classification
4. **Walk detection** — if raw endpoint is near OFFICE or HOME, scan for trailing walk segment (< 7 km/h, > 3 min, < 1 km). Truncate before classification
5. **Classify trips** — each group classified against anchor pairs; see classification rules below
6. **Detect stops** — gap-based stop detector run on each classified trip
6. **Write parser output** — append new rows to `master_trips.csv`; remove superseded rows on re-merge
7. **Fetch Bluelink daily aggregates** — last 4 months of daily trip stats via `_get_trip_info()`, upserted to `outputs/bluelink_daily.csv`. Silent on failure
8. **Fetch sheet CSV** — `requests.get(sheet_csv_url)` on every run; parse and index by (date, direction)
9. **Load petrol prices** — read date-range table from `petrol_prices.csv`
10. **Load weather cache** — read `outputs/weather_cache.json`
11. **Enrich all rows** — for each row in `master_trips.csv`, fill missing enrichment fields. Weather fetched from OFFICE coordinates using forecast API (recent) or archive API (>90 days)
12. **Save weather cache** — write updated cache back to disk
13. **Write master_trips.csv** — overwrite with full enriched field set
14. **Route clustering** — DBSCAN on point-to-point track distances, separate for outbound/return. Labels via Nominatim reverse geocoding. Adds `route_cluster` column to `master_trips.csv`. Skips directions with <5 full trips
15. **Generate heatmap** — Folium speed-coloured map of all trips (including partials). Output: `outputs/heatmap.html`
16. **Generate dashboard** — Plotly charts (departure vs duration, day-of-week, trends, mileage, parking). Output: `outputs/dashboard.html`

---

## Anchor matching logic

```python
class Anchor:
    def matches(self, lat, lon) -> bool:
        return haversine(self.lat, self.lon, lat, lon) <= self.radius_m

    def distance_to(self, lat, lon) -> float:
        return haversine(self.lat, self.lon, lat, lon)
```

Haversine formula used throughout for great-circle distance in metres.

**Tie-breaking:** When a start/end point falls within the radius of both OFFICE and MALL (they are geographically close), the anchor with the smaller `distance_to` value wins.

---

## Trip classification rules (in priority order)

```
start ~= HOME  and  end ~= OFFICE              -> "Home to Office",  parking="Office"
start ~= HOME  and  end ~= MALL                -> "Home to Office",  parking="Mall"
  (with OFFICE coords appearing mid-route)     -> Scenario C,        parking="Sent to Mall"
start ~= OFFICE or MALL  and  end ~= HOME      -> "Office to Home",  parking=start anchor
end ~= anchor  and  start not ~= any anchor    -> partial=True,      direction inferred from end
no anchor match at either end                  -> discard (None returned)
```

---

## Stop detection parameters

| Parameter | Default | Meaning |
|---|---|---|
| `stop_min_minutes` | 20.0 | Minimum gap duration to consider as a stop |
| `max_displacement_m` | 150.0 | Maximum movement during gap (car must be parked) |
| `max_entry_speed_kmh` | 15.0 | Maximum speed at the point before the gap |
| anchor exclusion | — | Midpoint must not be within any anchor radius |

All four conditions must hold simultaneously. Configurable via `thresholds.stop_min_minutes` in config.yaml.

---

## Walk detection parameters

Runs **before** trip classification. Detects trailing walk segments on trips whose raw endpoint is near OFFICE or HOME — handles the case where the user parks and walks the last stretch with OsmAnd still running.

| Parameter | Default | Meaning |
|---|---|---|
| `WALK_SPEED_THRESHOLD_KMH` | 7.0 | Maximum speed to classify as walking |
| `WALK_MIN_DURATION_MINS` | 3.0 | Minimum walk duration to trigger truncation |
| `WALK_MAX_DISTANCE_M` | 1000.0 | Maximum walk distance — filters out slow traffic crawl |

All three conditions must hold: speed below threshold, duration above minimum, distance below maximum. The endpoint must be near OFFICE or HOME for detection to trigger. When triggered, the trip is truncated at the last point where vehicle speed exceeded 7 km/h. The truncated endpoint is used for classification (parking label), distance, and duration. Fields `walk_detected` and `walk_duration_mins` are recorded.

---

## Short-stop merge logic (petrol bunk splits)

OsmAnd auto-splits recordings after a configurable inactivity gap (set to 10 minutes). A petrol bunk stop under 10 minutes might not trigger a split; one slightly over 10 minutes will produce two separate GPX files that together form a single valid trip.

`merge_consecutive_groups()` processes files sorted by first timestamp. Any two consecutive files with an inter-file gap under `merge_gap_minutes` (30 minutes, hardcoded) are merged into a single group before classification. The merged group is classified as one trip.

---

## OsmAnd GPX format notes

- Namespace: `http://www.topografix.com/GPX/1/1` (standard GPX)
- Speed extension: `<extensions><osmand:speed>` under namespace `https://osmand.net/docs/technical/osmand-file-formats/osmand-gpx`
- Speed unit: metres per second — multiply by 3.6 for km/h
- Recording interval: 3s during active navigation, 5s otherwise
- Displacement threshold: 10m — GPS stops logging when stationary
- Auto-split gap: 10 minutes (configurable in OsmAnd)

---

## Weather API

Open-Meteo (free, no API key required). Two endpoints:

- **Forecast API** — dates within 90 days: `https://api.open-meteo.com/v1/forecast`
- **Archive API** — older dates: `https://archive-api.open-meteo.com/v1/archive`

```
GET {base_url}
    ?latitude={lat}&longitude={lon}
    &hourly=temperature_2m,precipitation,weather_code
    &start_date={date}&end_date={date}
    &timezone=Asia/Kolkata
```

OFFICE coordinates are used as the weather fetch location (commute destination area). The hour index matching `departure_time` is selected from the hourly array. WMO weather codes are mapped to human-readable conditions: Clear, Cloudy, Rain, Heavy Rain. Results are cached by `{rounded_lat}_{rounded_lon}_{date}` key.

---

## Bluelink daily aggregates

`bluelink.py` fetches daily trip aggregates from the Hyundai Bluelink API (India, region=6) on every pipeline run. Uses the raw `_get_trip_info()` method since the library's `update_day_trip_info()` crashes on India data.

**Output:** `outputs/bluelink_daily.csv` — one row per driving day, upserted by date.

| Field | Type | Notes |
|---|---|---|
| `date` | YYYY-MM-DD | Driving day |
| `total_distance_km` | int | Total distance across all trips that day |
| `drive_time_mins` | int | Total driving time |
| `idle_time_mins` | int | Total idle time |
| `avg_speed_kmh` | float | Average speed |
| `max_speed_kmh` | int | Max speed reached |
| `trip_count` | int | Number of individual trips |

**Lookback:** current month + previous 3 months. History available from Jan 2026.

**Failure mode:** if Bluelink login or fetch fails for any reason, the error is logged and the pipeline continues. Bluelink is supplementary data — never blocks the pipeline.

---

## petrol_prices.csv format

```csv
from_date,to_date,price
2026-04-13,,103.0
```

- `from_date`: date from which this price applies (inclusive)
- `to_date`: date until which this price applies (inclusive); leave blank for current price
- `price`: Rs/l

Add a new row each time the pump price changes. Do not modify existing rows.

---

## File locations

| Path | Tracked in git | Purpose |
|---|---|---|
| `config.yaml` | No | Personal coordinates, sheet URL |
| `data/gpx/*.gpx` | No | OsmAnd GPS tracks |
| `outputs/master_trips.csv` | No | Enriched trip data |
| `outputs/processed.json` | No | Incremental processing state |
| `outputs/weather_cache.json` | No | Cached Open-Meteo responses |
| `outputs/bluelink_daily.csv` | No | Bluelink daily trip aggregates |
| `outputs/heatmap.html` | No | Folium speed-coloured commute map |
| `outputs/dashboard.html` | No | Plotly analytics dashboard |
| `data/reference/petrol_prices.csv` | Yes | Fuel price reference (no personal data) |
| `data/demo/` | Yes | Synthetic commuter data for portfolio demo |
| `config.example.yaml` | Yes | Placeholder template |

---

## Portfolio demo mode architecture

### Purpose

Real heatmaps and dashboards reveal personal home and office locations. The portfolio uses synthetic commuter profiles on real Bengaluru road geometry instead — no personal data exposed.

### How it works

1. `generate_demo.py` requests routes from OSRM for 4 fictional Bengaluru corridors:
   - Whitefield → JP Nagar
   - Marathahalli → HSR Layout
   - Hebbal → Koramangala
   - Electronic City → Indiranagar
2. OSRM returns real road geometry (from OpenStreetMap) as GeoJSON coordinates
3. The script generates synthetic GPX files with realistic speed profiles:
   - Time-of-day variation (peak vs off-peak)
   - Weather impact (rain slows traffic)
   - Known bottleneck slowdowns (Silk Board, Iblur, Marathahalli bridge, Hebbal flyover)
4. Output written to `data/demo/` — committed to GitHub so anyone can explore without personal data
5. Pre-computed outputs (heatmap, dashboard) also stored in `data/demo/`

### OSRM API usage

```
GET http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson
```

- Free, no API key required
- Returns road-snapped geometry following actual streets
- Rate limit: be polite (1 req/sec) — geometry is cached after first fetch
