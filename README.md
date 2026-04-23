# commute-lens

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

> Bengaluru traffic is unpredictable. I built a system to find patterns in it anyway.

commute-lens ingests GPS tracks from OsmAnd, enriches them with weather and fuel data, and surfaces patterns in a daily Bengaluru commute — departure time windows that beat traffic, routes that consistently underperform, stops that inflate your trip timer without you noticing.

It is designed around a real commute with unusual geometry: three anchor points (home, office, a nearby mall used as a parking proxy), two valid parking scenarios, mid-trip stops that appear as timestamp gaps rather than slow-speed sequences, and OsmAnd's displacement-based recording that stops logging entirely when the car is stationary.

---

## What it produces

| Output | Description |
|---|---|
| `master_trips.csv` | One row per trip — GPS-derived fields plus weather, mileage, fuel cost, sheet notes |
| `heatmap.html` | Speed-coloured road segments, green to red, built from all classified trips |
| `dashboard.html` | Departure time buckets, route comparison, weekly trends, fuel cost breakdown |

---

## How it works

```
OsmAnd GPX files
      |
      v
  parser.py          — classify trips, detect stops & walks, extract GPS metrics
      |
      v
  bluelink.py        — Hyundai Bluelink daily trip aggregates (optional)
      |
      v
  weather.py         — Open-Meteo weather at departure hour, cached locally
      |
      v
  Google Sheet CSV   — mileage, day type, notes (fetched live)
      |
      v
  petrol_prices.csv  — fuel price by date range
      |
      v
  master_trips.csv   ��� enriched, one row per trip
      |
      v
  cluster.py         — DBSCAN route clustering, labels via Nominatim
      |
      v
  analysis.py        — speed heatmap (Folium) + dashboard (Plotly)
```

Run the whole pipeline with one command:

```
python main.py
```

---

## Trip classification

The parser classifies each GPX file (or merged group) against three anchor coordinates defined in `config.yaml`:

| Pattern | Classification |
|---|---|
| HOME → OFFICE | Valid outbound, parking = Office |
| HOME → MALL | Valid outbound, parking = Mall |
| HOME → OFFICE (mid-route) → MALL | Scenario C — sent to mall after arriving at office |
| OFFICE/MALL → HOME | Valid return |
| End at anchor, start elsewhere | Partial trip — kept for heatmap, flagged `partial=True` |
| No anchor match | Discarded silently |

Consecutive GPX files with a gap under 30 minutes are merged before classification, handling OsmAnd auto-splits at petrol bunks.

### Walk detection

When a trip's recorded endpoint is near OFFICE or HOME and the final segment shows sustained walking speed (< 7 km/h for > 3 minutes over < 1 km), the parser truncates the trip at the last point where vehicle speed exceeded 7 km/h. This handles the common case of parking at the mall and walking to the office with OsmAnd still recording. The truncated endpoint is used for all classification, distance, and duration calculations. The 1 km distance cap filters out slow traffic crawl that would otherwise trigger false positives.

### Mid-trip stop detection

OsmAnd uses displacement-based recording: when the car is stationary, the GPS simply stops logging. A stop appears as a time gap between two points at nearly the same coordinates — not as a sequence of slow-speed readings.

A gap is flagged as a stop when all four conditions hold:

- Gap > 20 minutes
- Spatial displacement < 150 m
- Speed at entry < 15 km/h
- Midpoint is not within any anchor radius

This correctly strips a 65-minute shooting range stop from the raw 177-minute trip duration, producing a clean 112-minute adjusted figure.

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/Jcube101/commute-lens.git
cd commute-lens
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` and fill in:
- Your home, office, and mall coordinates
- Your vehicle name and ARAI fuel economy baseline
- Your Google Sheet CSV export URL (`sheet_csv_url`)
- (Optional) Hyundai Bluelink credentials — fetches daily trip aggregates automatically

### 3. Add GPX files

Drop OsmAnd GPX files into `data/gpx/`. The parser handles new files incrementally — already-processed files are skipped on repeat runs.

### 4. Seed petrol prices

Edit `data/reference/petrol_prices.csv`:

```
from_date,to_date,price
2026-04-13,,103.0
```

Leave `to_date` blank for the current price. Add a new row when the pump price changes.

### 5. Run

```bash
python main.py
```

---

## Demo — synthetic commuter profiles

Don't have your own commute data yet? The repo includes a synthetic demo mode with 4 fictional Bengaluru commuter profiles built on real road geometry (via OSRM):

| Corridor | Known bottlenecks |
|---|---|
| Whitefield → JP Nagar | Marathahalli bridge, Silk Board |
| Marathahalli → HSR Layout | Iblur junction, Silk Board |
| Hebbal → Koramangala | Hebbal flyover, Dairy Circle |
| Electronic City → Indiranagar | Silk Board, Koramangala |

```bash
python src/generate_demo.py
```

This generates synthetic GPX files with realistic speed profiles — time-of-day variation, weather impact, and bottleneck slowdowns. Output lands in `data/demo/` (already committed, so you can browse without running anything).

The interactive explorer lets you select a profile, toggle departure window and weather, and see how the bottleneck heatmap and reliability scores change. This is distributional intelligence over time — what Google Maps cannot tell you.

> The project gets more useful the longer it runs. Real data improves the analysis over time — 10 trips give you a heatmap, 40 give you patterns, 200 give you predictions. Rare for a portfolio project.

---

## Data privacy

`config.yaml`, `data/gpx/`, and `outputs/` are all gitignored. Your coordinates, routes, and sheet URL never leave your machine. The portfolio demo uses only synthetic data on public road geometry — no personal locations are ever published.

---

## Roadmap

- **Phase 1** (complete): GPX parser, incremental processing, stop detection
- **Phase 2** (complete): Weather enrichment, sheet join, petrol price, Bluelink daily aggregates, walk detection, full pipeline
- **Phase 3** (complete): Route clustering (DBSCAN), speed heatmap (Folium + CartoDB Positron), analytics dashboard (Plotly)
- **Phase 4**: Synthetic demo mode (OSRM + MapLibre GL JS), interactive portfolio frontend
- **Phase 5**: Junction bottleneck ranking, day-of-week variance, seasonal patterns
- **Phase 6**: Predictive departure model, commute cost calculator, city-level traffic intelligence

See [roadmap.md](roadmap.md) for detail.

---

## Contributing / forking

Want to run this for your own commute? See [CONTRIBUTING.md](CONTRIBUTING.md) — it walks through OsmAnd setup, anchor configuration, sheet structure, and first run.
