#!/usr/bin/env python3
"""
bluelink.py — Fetch Bluelink daily trip aggregates and upsert to CSV.

Uses the raw _get_trip_info() method since update_day_trip_info() crashes
on India region data (library bug: missing tripTime field).

Fetches current month + previous 3 months, upserts into
outputs/bluelink_daily.csv keyed on date.

Fails silently — the pipeline must never break because Bluelink is down.
"""

import csv
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml

BLUELINK_CSV_FIELDS = [
    "date",
    "total_distance_km",
    "drive_time_mins",
    "idle_time_mins",
    "avg_speed_kmh",
    "max_speed_kmh",
    "trip_count",
]


def _month_strings(today: date, lookback: int = 4) -> List[str]:
    """Return YYYYMM strings for the current month and previous (lookback-1) months."""
    months = []
    y, m = today.year, today.month
    for _ in range(lookback):
        months.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months


def _fetch_daily_aggregates(cfg: dict) -> List[Dict]:
    """Connect to Bluelink and return daily aggregate rows."""
    from hyundai_kia_connect_api import VehicleManager

    bl = cfg.get("bluelink")
    if not bl:
        return []

    vm = VehicleManager(
        region=6,
        brand=2,
        username=bl["username"],
        password=bl["password"],
        pin=str(bl["pin"]),
    )
    vm.check_and_refresh_token()

    vid = list(vm.vehicles.keys())[0]
    v = vm.vehicles[vid]
    api = vm.api

    rows: List[Dict] = []
    today = date.today()

    for ym in _month_strings(today):
        try:
            raw = api._get_trip_info(vm.token, v, ym, 0)
        except Exception:
            continue

        msg = raw.get("resMsg", {})
        day_list = msg.get("tripDayList", [])
        if not day_list:
            continue

        for day_entry in day_list:
            day_str = day_entry.get("tripDayInMonth", "")
            if len(day_str) != 8:
                continue

            iso_date = f"{day_str[:4]}-{day_str[4:6]}-{day_str[6:8]}"

            try:
                day_raw = api._get_trip_info(vm.token, v, day_str, 1)
            except Exception:
                continue

            day_trips = day_raw.get("resMsg", {}).get("dayTripList", [])
            if not day_trips:
                continue

            agg = day_trips[0]
            rows.append({
                "date": iso_date,
                "total_distance_km": agg.get("tripDist", ""),
                "drive_time_mins": agg.get("tripDrvTime", ""),
                "idle_time_mins": agg.get("tripIdleTime", ""),
                "avg_speed_kmh": agg.get("tripAvgSpeed", ""),
                "max_speed_kmh": agg.get("tripMaxSpeed", ""),
                "trip_count": agg.get("dayTripCnt", ""),
            })

    return rows


def _load_existing(csv_path: str) -> Dict[str, Dict]:
    """Load existing bluelink_daily.csv into a dict keyed by date."""
    p = Path(csv_path)
    if not p.exists():
        return {}
    with open(p, newline="", encoding="utf-8") as f:
        return {row["date"]: row for row in csv.DictReader(f)}


def _write_csv(all_rows: Dict[str, Dict], csv_path: str) -> None:
    """Write all rows sorted by date."""
    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    sorted_rows = sorted(all_rows.values(), key=lambda r: r["date"])
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BLUELINK_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(sorted_rows)


def fetch_bluelink_daily(cfg: dict, output_path: str) -> Optional[int]:
    """
    Main entry point. Fetches Bluelink daily aggregates and upserts to CSV.

    Returns the number of rows written, or None if Bluelink is unavailable.
    """
    try:
        new_rows = _fetch_daily_aggregates(cfg)
    except Exception as exc:
        print(f"  [bluelink] WARNING: fetch failed — {exc}")
        return None

    if not new_rows:
        return 0

    existing = _load_existing(output_path)
    for row in new_rows:
        existing[row["date"]] = row

    _write_csv(existing, output_path)
    return len(existing)
