# Running commute-lens for your own commute

commute-lens is designed around a specific Bengaluru commute but the core logic — three anchors, incremental GPX ingestion, gap-based stop detection — applies to any regular commute with a similar structure. This guide walks through adapting it to yours.

---

## Prerequisites

- Python 3.10+
- OsmAnd installed on your Android phone
- A Google account (for the sidecar sheet)

```bash
git clone https://github.com/Jcube101/commute-lens.git
cd commute-lens
pip install -r requirements.txt
```

---

## Step 1: Configure OsmAnd

In OsmAnd, set up auto-recording so it captures commute trips automatically:

1. **Menu → Plugins → Trip Recording → Settings**
2. Auto-record movement: ON
3. Speed threshold: 7 km/h (starts when you move, stops when you slow down)
4. Minimum displacement: 10 m
5. Auto-split recording after gap: 10 min
6. Logging interval: 3s (during navigation) / 5s (otherwise)

OsmAnd will create a new GPX file when you start moving and auto-split if you stop for over 10 minutes. The parser merges files with gaps under 30 minutes (e.g. petrol bunk stops).

---

## Step 2: Define your anchor coordinates

Your commute needs three anchor points:
- **HOME** — where you start and end each day
- **OFFICE** — your workplace
- **MALL / parking proxy** — an alternative parking point if you have one (or use a copy of OFFICE coordinates if not applicable)

Get coordinates from Google Maps: long-press a location, tap the coordinate display to copy.

Set `radius_m` to cover the natural GPS drift at each location (300m is a good starting point).

---

## Step 3: Create config.yaml

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:

```yaml
sheet_csv_url: <your sheet CSV URL — see Step 5>

anchors:
  home:
    lat: <your home latitude>
    lon: <your home longitude>
    radius_m: 300
  office:
    lat: <your office latitude>
    lon: <your office longitude>
    radius_m: 300
  mall:
    name: <parking location name, or copy office>
    lat: <lat>
    lon: <lon>
    radius_m: 300

vehicle:
  name: <your car>
  arai_kmpl: <official ARAI fuel economy>

thresholds:
  min_trip_points: 10
  gap_split_minutes: 10
  slow_speed_kmh: 15
  stop_min_minutes: 20

paths:
  gpx_dir: data/gpx/
  sheet_log: data/reference/sheet_log.csv
  petrol_prices: data/reference/petrol_prices.csv
  outputs: outputs/
```

`config.yaml` is gitignored — your personal data stays local.

---

## Step 4: Set up the sidecar sheet

Create a new Google Sheet with these columns in row 1:

| A | B | C | D | E |
|---|---|---|---|---|
| Date | Direction | Mileage (km/l) | Day Type | Notes |

**Direction** should be a dropdown with exactly two values: `Home to Office` and `Office to Home`. Set this up via Data → Data validation.

**Day Type** dropdown values: `Normal`, `Post-Holiday`, `Pre-Holiday`, `WFH`, `Detour`, `Other`.

After each commute leg, add one row with the date, direction, your actual fuel economy (from the car's trip computer), day type, and any notes.

### Publish the sheet as CSV

1. File → Share → Publish to web
2. Select the sheet tab containing your trip log
3. Format: Comma-separated values (.csv)
4. Click Publish and copy the URL
5. Paste that URL into `config.yaml` as `sheet_csv_url`

The sheet only needs to be readable — the pipeline never writes to it.

---

## Step 5: Seed petrol prices

Edit `data/reference/petrol_prices.csv`:

```csv
from_date,to_date,price
2026-01-01,,105.0
```

- `from_date`: when this price came into effect (YYYY-MM-DD)
- `to_date`: leave blank for the current price; fill in when the price changes
- `price`: Rs/l (or your local currency per litre)

Add a new row each time the fuel price changes. Do not edit existing rows.

---

## Step 6: Transfer GPX files

After each week of commuting:

1. In OsmAnd: My Places → Tracks → select files → long press → Share → Google Drive
2. Download from Google Drive to your PC
3. Copy files into `data/gpx/`

The pipeline processes only new files on each run — already-processed files are skipped.

---

## Step 7: First run

```bash
python main.py
```

This will:
1. Parse all GPX files in `data/gpx/`
2. Classify and filter trips against your anchors
3. Fetch weather for each trip from Open-Meteo (cached locally after first fetch)
4. Join with your sheet data on date + direction
5. Look up petrol price by date
6. Write `outputs/master_trips.csv` with all fields

Check the console output to verify trip classifications look correct. If trips are being discarded as "unrelated", check that your anchor coordinates and radius values are right.

---

## Demo mode — explore without personal data

If you want to see the pipeline output without setting up your own commute data, use the synthetic demo mode:

```bash
python src/generate_demo.py
```

This generates synthetic GPX files for 4 fictional Bengaluru commuter profiles using OSRM road geometry:
- Whitefield → JP Nagar
- Marathahalli → HSR Layout
- Hebbal → Koramangala
- Electronic City → Indiranagar

Output is written to `data/demo/` (committed to GitHub). You can also browse this folder directly to see what the pipeline produces — it contains pre-computed `master_trips.csv`, heatmap, and dashboard outputs for the synthetic profiles.

The synthetic data includes realistic speed profiles with time-of-day variation, weather impact, and known Bengaluru bottlenecks (Silk Board, Iblur, Marathahalli bridge, Hebbal flyover).

---

## Troubleshooting

**Trips classified as unrelated:** Check that your anchor coordinates actually match where you start and end. Increase `radius_m` if GPS drift at the anchor location is large.

**OFFICE and MALL both matching the start point:** If your office and parking are very close (< 300m), the parser uses distance tie-breaking to pick the closer anchor. Reduce one radius if the wrong one keeps winning.

**Stop detection flagging normal driving:** The 150m displacement guard usually prevents false positives, but on a very slow road (< 15 km/h) a traffic jam pause could get flagged. Increase `stop_min_minutes` in config.yaml if this happens.

**Weather fetch returning None:** Open-Meteo's forecast API covers up to 92 days in the past. Trips older than ~3 months will return empty weather fields. The archive API would be needed for older data — not currently implemented.
