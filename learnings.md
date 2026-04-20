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
