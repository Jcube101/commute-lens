#!/usr/bin/env python3
"""Reprocess all GPX files with new spatial dwell detection and report results."""
import sys
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from datetime import timedelta, timezone
from parser import (
    build_anchors, build_waypoints, load_config,
    load_and_sort_gpx_files, merge_consecutive_groups,
    classify_trip, PARTIAL_MIN_DISTANCE_KM, PARTIAL_MIN_DURATION_MIN,
)

IST = timezone(timedelta(hours=5, minutes=30))

cfg = load_config("config.yaml")
home, office, mall = build_anchors(cfg)
waypoints = build_waypoints(cfg)
gpx_dir = cfg["paths"]["gpx_dir"]

files = load_and_sort_gpx_files(gpx_dir)
groups = merge_consecutive_groups(files, 30)

full_trips = []
partial_trips = []
discarded = 0
dwell_found = []

for names, points in groups:
    result = classify_trip(points, home, office, mall, 10, 20.0, waypoints)
    if result is None:
        discarded += 1
        continue
    result["filename"] = "; ".join(names)
    if result["partial"]:
        if result["distance_km"] < PARTIAL_MIN_DISTANCE_KM or result["duration_min"] < PARTIAL_MIN_DURATION_MIN:
            discarded += 1
            continue
        partial_trips.append(result)
    else:
        full_trips.append(result)

    if result.get("dwell_stops"):
        dwell_found.append(result)

all_trips = sorted(full_trips + partial_trips, key=lambda x: x["date"])

print(f"Full trips: {len(full_trips)}")
print(f"Partial trips: {len(partial_trips)}")
print(f"Discarded: {discarded}")
print()

print("=" * 100)
print("  NEW DWELL DETECTIONS (trips that gained dwell_stops)")
print("=" * 100)
print()
for t in dwell_found:
    print(f"  {t['date']}  {t['direction']}")
    print(f"    Duration: {t['duration_min']} min raw -> {t['adjusted_duration_mins']} min adjusted")
    print(f"    Stop duration: {t['stop_duration_mins']} min")
    print(f"    Stop location: {t['stop_location']}")
    print(f"    Dwell stops: {t['dwell_stops']}")
    has_gap = any(
        "gap" not in t.get("dwell_stops", "").lower()
        and t["stop_duration_mins"] > 0
        for _ in [1]
    )
    print()

print("=" * 100)
print("  FULL STOP SUMMARY — ALL TRIPS")
print("=" * 100)
print()

hdr = f"{'Date':<14}{'Direction':<20}{'Raw':>8}{'Adj':>8}{'Stop':>8}  {'Location':<30}  {'Dwell Stops'}"
print(hdr)
print("-" * len(hdr) + "-" * 40)

for t in all_trips:
    p = " (P)" if t["partial"] else ""
    date_str = t["date"] + p
    loc = t["stop_location"] or "-"
    dwell = t["dwell_stops"] or "-"
    print(
        f"{date_str:<14}{t['direction']:<20}"
        f"{t['duration_min']:>8.1f}{t['adjusted_duration_mins']:>8.1f}"
        f"{t['stop_duration_mins']:>8.1f}  {loc:<30}  {dwell}"
    )

print()
print("=" * 100)
print("  KEY CHANGES — May 5, May 26, Jun 1")
print("=" * 100)
for t in all_trips:
    if t["date"] in ("2026-05-05", "2026-05-26", "2026-06-01") and "Home" not in t["direction"][:4]:
        print(f"\n  {t['date']} {t['direction']}")
        print(f"    Raw duration:      {t['duration_min']} min")
        print(f"    Adjusted duration: {t['adjusted_duration_mins']} min")
        print(f"    Stop duration:     {t['stop_duration_mins']} min")
        print(f"    Stop location:     {t['stop_location']}")
        print(f"    Dwell stops:       {t['dwell_stops']}")
