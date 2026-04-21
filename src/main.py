#!/usr/bin/env python3
"""
main.py — Full commute-lens pipeline. Single entry point.

Runs the GPX parser incrementally, then enriches each trip with:
  - Sheet data (mileage, day type, notes) — fetched live from Google Sheets
  - Weather data (Open-Meteo API, cached in outputs/weather_cache.json)
  - Petrol price lookup (data/reference/petrol_prices.csv date ranges)
  - Derived fields (fuel cost, day of week, week number)

Usage:
  python main.py
"""

import csv
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
import yaml

# Allow importing sibling modules when run as a script
sys.path.insert(0, str(Path(__file__).parent))

from parser import (  # noqa: E402
    CSV_FIELDS,
    build_anchors,
    load_config,
    load_processed,
    parse_trips_incremental,
    print_summary,
    save_processed,
    write_csv_incremental,
)
from bluelink import fetch_bluelink_daily  # noqa: E402
from weather import get_weather_for_trip, load_cache, save_cache  # noqa: E402


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# Enrichment fields added by main.py on top of the parser's CSV_FIELDS
ENRICHMENT_FIELDS = [
    "mileage_kmpl",       # from sheet join
    "day_type",           # from sheet join
    "notes",              # from sheet join
    "petrol_price_rs",    # from petrol_prices.csv date range lookup
    "fuel_cost_rs",       # distance_km / mileage_kmpl * petrol_price_rs
    "day_of_week",        # derived from date (Monday, Tuesday, ...)
    "week_num",           # ISO week number derived from date
    "weather_condition",  # Open-Meteo: Clear / Cloudy / Rain / Heavy Rain
    "temp_c",             # Open-Meteo temperature at departure
    "precipitation_mm",   # Open-Meteo precipitation at departure hour
]

ALL_FIELDS = CSV_FIELDS + ENRICHMENT_FIELDS


# ---------------------------------------------------------------------------
# Sheet helpers
# ---------------------------------------------------------------------------

def load_sheet_csv(url: str) -> List[Dict]:
    """
    Fetch the sidecar Google Sheet CSV (Anyone with link can view).

    Expected columns: Date, Direction, Mileage (km/l), Day Type, Notes

    Handles sheets that have a title row above the actual headers: scans
    lines until it finds one containing "Date" and "Direction", then treats
    that line as the header row.
    """
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        print(f"  [sheet] WARNING: could not fetch sheet CSV: {exc}")
        return []

    lines = resp.text.splitlines()

    # Find the real header row (contains both "Date" and "Direction")
    header_idx = None
    for i, line in enumerate(lines):
        if "Date" in line and "Direction" in line:
            header_idx = i
            break

    if header_idx is None:
        print("  [sheet] WARNING: could not find header row (need 'Date' and 'Direction' columns).")
        return []

    reader = csv.DictReader(lines[header_idx:])
    return list(reader)


def build_sheet_index(rows: List[Dict]) -> Dict:
    """
    Build a lookup dict keyed by (date_iso, direction).

    Handles date formats: YYYY-MM-DD, DD/MM/YYYY, DD-Mon-YY (e.g. 14-Apr-26).
    """
    index: Dict = {}
    for row in rows:
        date_raw = row.get("Date", "").strip()
        direction = row.get("Direction", "").strip()
        if not date_raw or not direction:
            continue
        d = None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%b-%y", "%d-%b-%Y"):
            try:
                d = datetime.strptime(date_raw, fmt).date().isoformat()
                break
            except ValueError:
                continue
        if d is None:
            print(f"  [sheet] WARNING: unrecognised date format '{date_raw}' — skipping row.")
            continue
        index[(d, direction)] = row
    return index


# ---------------------------------------------------------------------------
# Petrol price helpers
# ---------------------------------------------------------------------------

def load_petrol_prices(csv_path: str) -> List[Dict]:
    """
    Load petrol_prices.csv.
    Columns: from_date, to_date (empty = current), price
    """
    p = Path(csv_path)
    if not p.exists():
        print(f"  [petrol] WARNING: {csv_path} not found.")
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def lookup_petrol_price(prices: List[Dict], trip_date_str: str) -> Optional[float]:
    """
    Return the petrol price (Rs/l) applicable on trip_date_str (YYYY-MM-DD).

    Matches the row where from_date <= trip_date and to_date is empty or
    trip_date <= to_date. The last matching row wins (most recent range first).
    """
    try:
        trip_d = date.fromisoformat(trip_date_str)
    except ValueError:
        return None

    for row in reversed(prices):
        try:
            from_d = date.fromisoformat(row["from_date"].strip())
        except (ValueError, KeyError):
            continue
        to_str = row.get("to_date", "").strip()
        if to_str:
            try:
                if not (from_d <= trip_d <= date.fromisoformat(to_str)):
                    continue
            except ValueError:
                continue
        else:
            if trip_d < from_d:
                continue
        return float(row["price"])

    return None


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def enrich_row(
    row: Dict,
    sheet_index: Dict,
    petrol_prices: List[Dict],
    weather_cache: Dict,
    weather_lat: float,
    weather_lon: float,
) -> Dict:
    """
    Fill enrichment fields for a single trip row.

    Already-populated fields are left untouched so repeat runs don't re-fetch.
    """
    trip_date = row.get("date", "")
    direction = row.get("direction", "")

    # --- Sheet join ---
    if not row.get("mileage_kmpl"):
        sheet_row = sheet_index.get((trip_date, direction), {})
        row["mileage_kmpl"] = (sheet_row.get("Mileage (km/l)")
                               or sheet_row.get("Mileage(km/l)", "")).strip()
        row["day_type"] = sheet_row.get("Day Type", "").strip()
        row["notes"] = sheet_row.get("Notes", "").strip()

    # --- Petrol price ---
    if not row.get("petrol_price_rs"):
        price = lookup_petrol_price(petrol_prices, trip_date)
        row["petrol_price_rs"] = price if price is not None else ""

    # --- Fuel cost ---
    if not row.get("fuel_cost_rs"):
        try:
            dist_km = float(row.get("distance_km") or 0)
            mileage = float(row.get("mileage_kmpl") or 0)
            price = float(row.get("petrol_price_rs") or 0)
            if mileage > 0 and price > 0 and dist_km > 0:
                row["fuel_cost_rs"] = round(dist_km / mileage * price, 2)
            else:
                row["fuel_cost_rs"] = ""
        except (ValueError, TypeError):
            row["fuel_cost_rs"] = ""

    # --- Date-derived fields ---
    if not row.get("day_of_week") and trip_date:
        try:
            d = date.fromisoformat(trip_date)
            row["day_of_week"] = d.strftime("%A")
            row["week_num"] = d.isocalendar()[1]
        except ValueError:
            row["day_of_week"] = ""
            row["week_num"] = ""

    # --- Weather (office/mall coordinates — commute destination area) ---
    if not row.get("weather_condition"):
        departure_time = row.get("departure_time", "")
        if departure_time:
            weather = get_weather_for_trip(
                weather_lat, weather_lon, departure_time, weather_cache
            )
            for field in ("weather_condition", "temp_c", "precipitation_mm"):
                row[field] = "" if weather[field] is None else weather[field]

    return row


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def read_master_csv(csv_path: str) -> List[Dict]:
    """Read existing master_trips.csv into a list of row dicts."""
    p = Path(csv_path)
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_master_csv(rows: List[Dict], csv_path: str) -> None:
    """Write all rows to master_trips.csv with the full enriched field set."""
    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ALL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            for field in ALL_FIELDS:
                row.setdefault(field, "")
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "config.yaml"

    if not config_path.exists():
        print(f"ERROR: config.yaml not found at {config_path}", file=sys.stderr)
        print("Copy config.example.yaml to config.yaml and fill in your values.")
        sys.exit(1)

    cfg = load_config(str(config_path))
    home, office, mall = build_anchors(cfg)

    paths = cfg["paths"]
    gpx_dir = repo_root / paths["gpx_dir"]
    outputs_dir = repo_root / paths["outputs"]
    output_csv = outputs_dir / "master_trips.csv"
    processed_json = outputs_dir / "processed.json"
    weather_cache_path = outputs_dir / "weather_cache.json"
    petrol_prices_path = repo_root / paths["petrol_prices"]

    thresholds = cfg.get("thresholds", {})
    min_points = thresholds.get("min_trip_points", 10)
    stop_min_minutes = float(thresholds.get("stop_min_minutes", 20.0))
    merge_gap = 30

    sheet_csv_url = cfg.get("sheet_csv_url", "")

    # ------------------------------------------------------------------
    # Step 1: Run parser incrementally
    # ------------------------------------------------------------------
    print("\n[1/5] Running GPX parser...")
    processed_files = load_processed(str(processed_json))
    all_gpx = {p.name for p in Path(gpx_dir).glob("*.gpx")}
    new_files = all_gpx - processed_files

    if new_files:
        print(f"  New files: {sorted(new_files)}")
        new_trips, discarded, touched_filesets = parse_trips_incremental(
            str(gpx_dir), home, office, mall,
            min_points, merge_gap, processed_files, stop_min_minutes,
        )
        print_summary(new_trips, discarded)
        write_csv_incremental(new_trips, str(output_csv), touched_filesets)
        newly_processed = {f for fs in touched_filesets for f in fs}
        save_processed(str(processed_json), processed_files | newly_processed)
        print(f"  {len(new_trips)} new trip(s) added.")
    else:
        print("  No new GPX files.")

    # ------------------------------------------------------------------
    # Step 2: Fetch Bluelink daily aggregates
    # ------------------------------------------------------------------
    print("\n[2/5] Fetching Bluelink daily aggregates...")
    bluelink_csv = outputs_dir / "bluelink_daily.csv"
    result = fetch_bluelink_daily(cfg, str(bluelink_csv))
    if result is None:
        print("  Bluelink unavailable — skipped.")
    elif result == 0:
        print("  No new Bluelink data.")
    else:
        print(f"  {result} day(s) in {bluelink_csv}")

    # ------------------------------------------------------------------
    # Step 3: Fetch sheet data
    # ------------------------------------------------------------------
    print("\n[3/5] Fetching sheet data...")
    if sheet_csv_url:
        sheet_rows = load_sheet_csv(sheet_csv_url)
        sheet_index = build_sheet_index(sheet_rows)
        print(f"  {len(sheet_rows)} sheet row(s) loaded.")
    else:
        sheet_index = {}
        print("  WARNING: sheet_csv_url not set in config.yaml — skipping sheet join.")

    # ------------------------------------------------------------------
    # Step 4: Load petrol prices
    # ------------------------------------------------------------------
    print("\n[4/5] Loading petrol prices...")
    petrol_prices = load_petrol_prices(str(petrol_prices_path))
    print(f"  {len(petrol_prices)} price period(s) loaded.")

    # ------------------------------------------------------------------
    # Step 5: Enrich all trips
    # ------------------------------------------------------------------
    print("\n[5/5] Enriching trips with weather, sheet, and petrol data...")
    weather_cache = load_cache(str(weather_cache_path))
    all_rows = read_master_csv(str(output_csv))

    if not all_rows:
        print("  No trips in master_trips.csv — nothing to enrich.")
        sys.exit(0)

    enriched = []
    for row in all_rows:
        enriched.append(
            enrich_row(row, sheet_index, petrol_prices, weather_cache,
                       office.lat, office.lon)
        )

    save_cache(str(weather_cache_path), weather_cache)
    write_master_csv(enriched, str(output_csv))

    print(f"  {len(enriched)} trip(s) written to {output_csv}")
    print("\nDone.")
