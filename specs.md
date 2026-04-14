# commute-lens — Technical Specification

---

## Data sources

| Source | What it provides | How accessed |
|---|---|---|
| OsmAnd GPX files | GPS track points (lat/lon/time/speed/ele/hdop) | Manual copy to `data/gpx/` before pipeline run |
| Google Sheets sidecar | Mileage, day type, notes | Fetched live via CSV export URL in config.yaml |
| Open-Meteo API | Hourly weather (temp, humidity, rain, wind, code) | HTTP GET, cached in `outputs/weather_cache.json` |
| `petrol_prices.csv` | Petrol price by date range | Read from `data/reference/petrol_prices.csv` |

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
| `point_count` | int | GPX parse | Number of track points in the trip |

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
| `temp_c` | float | Open-Meteo | Temperature at departure hour |
| `humidity_pct` | float | Open-Meteo | Relative humidity at departure hour |
| `rain_mm` | float | Open-Meteo | Rainfall at departure hour |
| `wind_kmh` | float | Open-Meteo | Wind speed at departure hour |
| `weather_code` | int | Open-Meteo | WMO weather interpretation code |

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
```

`config.yaml` is gitignored. `config.example.yaml` (committed) contains only placeholder values.

---

## Pipeline steps (main.py)

1. **Load config** — read `config.yaml`, build Anchor objects for HOME, OFFICE, MALL
2. **Incremental GPX parse** — compare `data/gpx/*.gpx` against `outputs/processed.json`; parse only new files
3. **Merge consecutive groups** — files with inter-file gap < 30 min are merged before classification
4. **Classify trips** — each group classified against anchor pairs; see classification rules below
5. **Detect stops** — gap-based stop detector run on each classified trip
6. **Write parser output** — append new rows to `master_trips.csv`; remove superseded rows on re-merge
7. **Fetch sheet CSV** — `requests.get(sheet_csv_url)` on every run; parse and index by (date, direction)
8. **Load petrol prices** — read date-range table from `petrol_prices.csv`
9. **Load weather cache** — read `outputs/weather_cache.json`
10. **Enrich all rows** — for each row in `master_trips.csv`, fill missing enrichment fields
11. **Save weather cache** — write updated cache back to disk
12. **Write master_trips.csv** — overwrite with full enriched field set

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

Open-Meteo forecast API (free, no API key required):

```
GET https://api.open-meteo.com/v1/forecast
    ?latitude={lat}&longitude={lon}
    &hourly=temperature_2m,relative_humidity_2m,rain,windspeed_10m,weathercode
    &start_date={date}&end_date={date}
    &timezone=Asia/Kolkata
```

HOME coordinates are used as the weather location proxy (departure point). The hour index matching `departure_time` is selected from the hourly array. Results are cached by `{rounded_lat}_{rounded_lon}_{date}` key.

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
| `data/reference/petrol_prices.csv` | Yes | Fuel price reference (no personal data) |
| `config.example.yaml` | Yes | Placeholder template |
