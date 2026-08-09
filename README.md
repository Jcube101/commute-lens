# commute-lens

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

> Bengaluru traffic is unpredictable. I built a system to find patterns in it anyway.

commute-lens ingests GPS tracks from OsmAnd, enriches them with weather and fuel data, and surfaces patterns in a daily Bengaluru commute — departure time windows that beat traffic, routes that consistently underperform, stops that inflate your trip timer without you noticing.

It is designed around a real commute with unusual geometry: three anchor points (home, office, a nearby mall used as a parking proxy), two valid parking scenarios, mid-trip stops that appear as timestamp gaps rather than slow-speed sequences, and OsmAnd's displacement-based recording that stops logging entirely when the car is stationary.

---

## Why this exists

Google Maps (and every other live-routing app) answers one question well: **"how do I get there right now?"** It has no memory. Close the app after today's drive and every signal it just showed you — how long the Silk Board crawl actually cost you, whether last Tuesday's rain made things worse, whether leaving 15 minutes earlier is worth it on your specific route — is gone. There's no accumulation, no trend, no "compared to what."

commute-lens exists to answer a different question: **"given everything I know about this commute, what should I actually do?"** That requires data that survives past a single trip:

- **Distributional insight, not a single ETA.** Maps gives you one number for right now. This gives you the full spread of durations for a departure window, so you can see reliability (tight vs. wildly variable) instead of a single point estimate that might be the best or worst day you've ever had.
- **Attribution, not just outcome.** When a trip runs long, this pipeline can tell you *why* — a genuine traffic slowdown, an unplanned stop, a walk from parking that ate 10 minutes, or rain that historically adds X minutes on this route. Maps just shows you were late.
- **Personal geometry Maps doesn't model.** This commute has a parking quirk (a mall used as an overflow lot when office parking is full) and a mid-trip redirect scenario that no general-purpose routing app has any concept of. The classifier here is built around *this* commute's real shape, not a generic origin-destination pair.
- **A record that compounds.** Every GPX file adds to a personal dataset that gets more useful over time — 10 trips give you a heatmap, 40 give you patterns, 200 give you a predictive departure-time model. Google Maps can't build you a model of your own commute because it doesn't keep your history in a form you can analyze.
- **Fuel cost and mileage tracking**, tied to petrol price history and actual trip computer readings — something no routing app touches at all.

In short: Maps optimizes a single trip in the moment. This optimizes the *pattern* of a repeated commute over months, using only data you own.

---

## What it produces

| Output | Description |
|---|---|
| `master_trips.csv` | One row per trip — GPS-derived fields plus weather, mileage, fuel cost, stop locations, sheet notes |
| `heatmap.html` | Speed-coloured road segments, green to red, built from all classified trips |
| `dashboard.html` | Departure time buckets, route comparison, weekly trends, fuel cost breakdown |

---

## Quick start

```bash
git clone https://github.com/Jcube101/commute-lens.git
cd commute-lens

# Create and enter a virtual environment
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Windows (Git Bash)
source .venv/Scripts/activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt

cp config.example.yaml config.yaml
# edit config.yaml with your anchor coordinates, vehicle, and sheet URL

python src/main.py
```

Once you're done for the session, run `deactivate` to leave the virtual environment.

This gets the pipeline running against your own commute data. For the full walkthrough — OsmAnd recording settings, anchor coordinate setup, sidecar sheet structure, and troubleshooting — see [CONTRIBUTING.md](CONTRIBUTING.md).

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
  master_trips.csv   — enriched, one row per trip
      |
      v
  cluster.py         — DBSCAN route clustering, labels via Nominatim
      |
      v
  analysis.py        — speed heatmap (Folium) + dashboard (Plotly)
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

When the walk origin falls within range of both MALL and OFFICE (which are only ~218m apart), and the distances are within 55m of each other, MALL is preferred — reflecting the real-world parking prior (mall parking is 4x more frequent than office parking).

### Mid-trip stop detection

OsmAnd uses displacement-based recording: when the car is stationary, the GPS simply stops logging. A stop appears as a time gap between two points at nearly the same coordinates — not as a sequence of slow-speed readings.

A gap is flagged as a stop when all four conditions hold:

- Gap > 20 minutes
- Spatial displacement < 150 m
- Speed at entry < 15 km/h
- Midpoint is not within any anchor radius

On real trips this strips a long mid-trip stop out of the raw duration, so the adjusted figure is driving time only.

### Stop location enrichment

Detected stops are matched against named waypoints defined in `config.yaml`. If a stop falls within a waypoint's radius, the `stop_location` field is populated with the waypoint name (e.g. "Shooting Range"). Waypoints also detect dwell time for suspected unreported stops — trips where the user forgot to pause recording. Waypoints enrich stop analysis without affecting trip classification.

---

## Demo — synthetic commuter profiles

Don't have your own commute data yet? The repo includes a synthetic demo mode with 4 fictional Bengaluru commuter profiles built on real road geometry (via OSRM):

| Corridor | Known bottlenecks |
|---|---|
| Whitefield → JP Nagar | Marathahalli bridge, Silk Board |
| Marathahalli → HSR Layout | Iblur junction, Silk Board |
| Hebbal → Rajajinagar | Hebbal flyover, Dairy Circle |
| Electronic City → Jayanagar | Silk Board |

```bash
python src/generate_demo.py
```

This generates synthetic GPX files with realistic speed profiles — time-of-day variation, weather impact, and bottleneck slowdowns. Output lands in `data/demo/` (already committed, so you can browse without running anything).

The interactive explorer lets you select a profile, toggle departure window and weather, and see how the bottleneck heatmap and reliability scores change. This is distributional intelligence over time — what Google Maps cannot tell you.

> The project gets more useful the longer it runs. Real data improves the analysis over time — 10 trips give you a heatmap, 40 give you patterns, 200 give you predictions. Rare for a portfolio project.

---

## Data privacy

`config.yaml`, `data/gpx/`, and `outputs/` are all gitignored. Your coordinates, routes, waypoints, and sheet URL never leave your machine. The portfolio demo uses only synthetic data on public road geometry — no personal locations are ever published.

---

## Roadmap

- **Phase 1** (complete): GPX parser, incremental processing, stop detection
- **Phase 2** (complete): Weather enrichment, sheet join, petrol price, Bluelink daily aggregates, walk detection, full pipeline
- **Phase 3** (complete): Route clustering (DBSCAN), speed heatmap (Folium + CartoDB Positron), analytics dashboard (Plotly)
- **Infrastructure — Track A** (complete): real pipeline migrated to jobpi (Raspberry Pi), manual SSH trigger, nothing exposed publicly
- **Phase 4 — Track B**: synthetic demo mode (OSRM + MapLibre GL JS), interactive portfolio frontend, hosted publicly at `commute.job-joseph.com`
- **Phase 5**: Junction bottleneck ranking, day-of-week variance, seasonal patterns
- **Phase 6**: Predictive departure model, commute cost calculator, city-level traffic intelligence

See [ROADMAP.md](ROADMAP.md) for detail.

---

## Contributing / forking

Want to run this for your own commute? See [CONTRIBUTING.md](CONTRIBUTING.md) — it walks through OsmAnd setup, anchor configuration, sheet structure, and first run.
