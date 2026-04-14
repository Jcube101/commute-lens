#!/usr/bin/env python3
"""
parser.py — GPX ingestion, trip classification, and extraction for commute-lens.

Reads all GPX files from data/gpx/, merges consecutive files separated by
<30 minutes, classifies each trip against anchor coordinates, and writes a
summary CSV to outputs/master_trips.csv.

Run directly:  python src/parser.py
"""

import csv
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml
from xml.etree import ElementTree as ET

# Namespace constants from OsmAnd GPX files
GPX_NS = "http://www.topografix.com/GPX/1/1"
OSMAND_NS = "https://osmand.net/docs/technical/osmand-file-formats/osmand-gpx"

IST = timezone(timedelta(hours=5, minutes=30))

CSV_FIELDS = [
    "filename",
    "date",
    "direction",
    "departure_time",
    "arrival_time",
    "duration_min",
    "distance_km",
    "avg_speed_kmh",
    "parking",
    "partial",
    "scenario_c",
    "stop_detected",
    "stop_duration_mins",
    "adjusted_duration_mins",
    "point_count",
]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres between two lat/lon points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class Anchor:
    """A named geographic anchor point (HOME / OFFICE / MALL)."""

    def __init__(self, name: str, lat: float, lon: float, radius_m: float):
        self.name = name
        self.lat = lat
        self.lon = lon
        self.radius_m = radius_m

    def matches(self, lat: float, lon: float) -> bool:
        return haversine(self.lat, self.lon, lat, lon) <= self.radius_m

    def distance_to(self, lat: float, lon: float) -> float:
        return haversine(self.lat, self.lon, lat, lon)


class TrackPoint:
    __slots__ = ("lat", "lon", "time", "speed_kmh", "ele", "hdop")

    def __init__(
        self,
        lat: float,
        lon: float,
        time: datetime,
        speed_kmh: Optional[float] = None,
        ele: Optional[float] = None,
        hdop: Optional[float] = None,
    ):
        self.lat = lat
        self.lon = lon
        self.time = time
        self.speed_kmh = speed_kmh
        self.ele = ele
        self.hdop = hdop


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def build_anchors(cfg: dict) -> Tuple[Anchor, Anchor, Anchor]:
    a = cfg["anchors"]
    home = Anchor(
        "HOME", a["home"]["lat"], a["home"]["lon"], a["home"]["radius_m"]
    )
    office = Anchor(
        "OFFICE", a["office"]["lat"], a["office"]["lon"], a["office"]["radius_m"]
    )
    mall = Anchor(
        a["mall"]["name"], a["mall"]["lat"], a["mall"]["lon"], a["mall"]["radius_m"]
    )
    return home, office, mall


# ---------------------------------------------------------------------------
# GPX parsing
# ---------------------------------------------------------------------------

def parse_gpx(filepath: str) -> List[TrackPoint]:
    """
    Parse a single GPX file and return track points sorted by timestamp.

    OsmAnd speed is stored in m/s under the osmand namespace inside
    <extensions>. We multiply by 3.6 to convert to km/h.
    """
    tree = ET.parse(filepath)
    root = tree.getroot()

    points: List[TrackPoint] = []
    for trkpt in root.findall(f".//{{{GPX_NS}}}trkpt"):
        lat = float(trkpt.get("lat"))
        lon = float(trkpt.get("lon"))

        time_el = trkpt.find(f"{{{GPX_NS}}}time")
        if time_el is None:
            continue
        time = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))

        ele_el = trkpt.find(f"{{{GPX_NS}}}ele")
        ele = float(ele_el.text) if ele_el is not None else None

        hdop_el = trkpt.find(f"{{{GPX_NS}}}hdop")
        hdop = float(hdop_el.text) if hdop_el is not None else None

        speed_el = trkpt.find(f"{{{GPX_NS}}}extensions/{{{OSMAND_NS}}}speed")
        speed_kmh = float(speed_el.text) * 3.6 if speed_el is not None else None

        points.append(TrackPoint(lat, lon, time, speed_kmh, ele, hdop))

    points.sort(key=lambda p: p.time)
    return points


def load_and_sort_gpx_files(
    gpx_dir: str,
) -> List[Tuple[str, List[TrackPoint]]]:
    """Parse all GPX files in gpx_dir and return them sorted by first timestamp."""
    results: List[Tuple[str, List[TrackPoint]]] = []
    for gpx_file in sorted(Path(gpx_dir).glob("*.gpx")):
        points = parse_gpx(str(gpx_file))
        if points:
            results.append((gpx_file.name, points))
    results.sort(key=lambda x: x[1][0].time)
    return results


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def merge_consecutive_groups(
    files: List[Tuple[str, List[TrackPoint]]],
    merge_gap_minutes: int = 30,
) -> List[Tuple[List[str], List[TrackPoint]]]:
    """
    Group consecutive GPX files whose inter-file gap is < merge_gap_minutes.

    This handles OsmAnd auto-splits caused by petrol bunk or short stops.
    Returns a list of (filenames, merged_track_points).
    """
    if not files:
        return []

    groups: List[Tuple[List[str], List[TrackPoint]]] = []
    current_names = [files[0][0]]
    current_points = list(files[0][1])

    for name, points in files[1:]:
        gap_min = (points[0].time - current_points[-1].time).total_seconds() / 60.0
        if gap_min < merge_gap_minutes:
            current_names.append(name)
            current_points.extend(points)
        else:
            groups.append((list(current_names), current_points))
            current_names = [name]
            current_points = list(points)

    groups.append((current_names, current_points))
    return groups


# ---------------------------------------------------------------------------
# Trip metrics
# ---------------------------------------------------------------------------

def trip_distance_km(points: List[TrackPoint]) -> float:
    """Cumulative haversine distance over all track points, in km."""
    total = 0.0
    for i in range(1, len(points)):
        total += haversine(
            points[i - 1].lat, points[i - 1].lon,
            points[i].lat, points[i].lon,
        )
    return total / 1000.0


def calc_avg_speed_kmh(points: List[TrackPoint]) -> float:
    """
    Average speed in km/h.

    Prefers OsmAnd speed values (already in m/s, converted to km/h).
    Falls back to distance / elapsed time if no speed data is available.
    """
    speeds = [p.speed_kmh for p in points if p.speed_kmh is not None]
    if speeds:
        return sum(speeds) / len(speeds)
    if len(points) >= 2:
        dist_km = trip_distance_km(points)
        elapsed_h = (points[-1].time - points[0].time).total_seconds() / 3600.0
        return dist_km / elapsed_h if elapsed_h > 0 else 0.0
    return 0.0


# ---------------------------------------------------------------------------
# Stop detection
# ---------------------------------------------------------------------------

def detect_stops(
    points: List[TrackPoint],
    home: Anchor,
    office: Anchor,
    mall: Anchor,
    stop_min_minutes: float = 20.0,
    max_displacement_m: float = 150.0,
    max_entry_speed_kmh: float = 15.0,
) -> Tuple[bool, float]:
    """
    Detect mid-trip stationary stops in OsmAnd GPX data.

    OsmAnd uses displacement-based recording (10 m threshold), so when the car
    is parked the GPS simply stops logging. A stop therefore appears as a large
    time gap between two consecutive points at nearly the same coordinates,
    not as a sequence of slow-speed points.

    A gap is flagged as a stop when ALL of these hold:
      1. Time gap         > stop_min_minutes   (20 min default)
      2. Displacement     < max_displacement_m  (150 m default — parked, not driving)
      3. Entry speed      < max_entry_speed_kmh (15 km/h — car was slowing, not cruising)
      4. Midpoint location is not within any anchor radius

    Returns (stop_detected, total_stop_duration_minutes).
    """
    total_stop_mins = 0.0

    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        gap_min = (p2.time - p1.time).total_seconds() / 60.0

        if gap_min < stop_min_minutes:
            continue

        # Guard: if the car was clearly moving before the gap it's a recording outage
        if p1.speed_kmh is not None and p1.speed_kmh > max_entry_speed_kmh:
            continue

        # Car must not have moved significantly during the gap
        if haversine(p1.lat, p1.lon, p2.lat, p2.lon) > max_displacement_m:
            continue

        # Stop must be away from all anchor points
        mid_lat = (p1.lat + p2.lat) / 2
        mid_lon = (p1.lon + p2.lon) / 2
        if (home.matches(mid_lat, mid_lon)
                or office.matches(mid_lat, mid_lon)
                or mall.matches(mid_lat, mid_lon)):
            continue

        total_stop_mins += gap_min

    stop_detected = total_stop_mins > 0
    return stop_detected, round(total_stop_mins, 1)


# ---------------------------------------------------------------------------
# Trip classification
# ---------------------------------------------------------------------------

def classify_trip(
    points: List[TrackPoint],
    home: Anchor,
    office: Anchor,
    mall: Anchor,
    min_points: int = 10,
    stop_min_minutes: float = 20.0,
) -> Optional[Dict]:
    """
    Classify a sequence of track points as a commute trip.

    Classification rules (in priority order):
      HOME → OFFICE          : valid outbound, parking = Office
      HOME → MALL            : valid outbound, parking = Mall
        (with OFFICE mid-route: Scenario C, parking = Sent to Mall)
      (OFFICE|MALL) → HOME   : valid return
      end ∈ anchor, start ∉  : partial trip (recording started late)
      otherwise              : unrelated — return None

    Returns a dict of extracted fields, or None if unrelated.
    """
    if len(points) < min_points:
        return None

    start, end = points[0], points[-1]

    start_home = home.matches(start.lat, start.lon)
    start_office = office.matches(start.lat, start.lon)
    start_mall = mall.matches(start.lat, start.lon)
    end_home = home.matches(end.lat, end.lon)
    end_office = office.matches(end.lat, end.lon)
    end_mall = mall.matches(end.lat, end.lon)

    start_anchor = start_home or start_office or start_mall
    end_anchor = end_home or end_office or end_mall

    direction: Optional[str] = None
    parking: Optional[str] = None
    partial = False
    scenario_c = False

    if start_home and end_office:
        direction, parking = "Home to Office", "Office"

    elif start_home and end_mall:
        # Scenario C: did the route pass through OFFICE mid-trip?
        scenario_c = any(office.matches(p.lat, p.lon) for p in points[1:-1])
        direction = "Home to Office"
        parking = "Sent to Mall" if scenario_c else "Mall"

    elif (start_office or start_mall) and end_home:
        direction = "Office to Home"
        if start_office and start_mall:
            # Both anchors within radius — pick the closer one
            parking = (
                "Office"
                if office.distance_to(start.lat, start.lon) < mall.distance_to(start.lat, start.lon)
                else "Mall"
            )
        else:
            parking = "Office" if start_office else "Mall"

    elif end_anchor and not start_anchor:
        # Partial: recording didn't begin at an anchor (e.g. forgot to start at home)
        partial = True
        if end_home:
            direction, parking = "Office to Home", "Unknown"
        elif end_office:
            direction, parking = "Home to Office", "Office"
        else:  # end_mall
            direction, parking = "Home to Office", "Mall"

    else:
        return None  # unrelated

    departure_ist = points[0].time.astimezone(IST)
    arrival_ist = points[-1].time.astimezone(IST)
    duration_min = (points[-1].time - points[0].time).total_seconds() / 60.0

    stop_detected, stop_duration_mins = detect_stops(
        points, home, office, mall, stop_min_minutes=stop_min_minutes
    )
    adjusted_duration_mins = round(duration_min - stop_duration_mins, 1)

    return {
        "filename": None,  # filled by caller
        "date": departure_ist.date().isoformat(),
        "direction": direction,
        "departure_time": departure_ist.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "arrival_time": arrival_ist.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "duration_min": round(duration_min, 1),
        "distance_km": round(trip_distance_km(points), 3),
        "avg_speed_kmh": round(calc_avg_speed_kmh(points), 1),
        "parking": parking,
        "partial": partial,
        "scenario_c": scenario_c,
        "stop_detected": stop_detected,
        "stop_duration_mins": stop_duration_mins,
        "adjusted_duration_mins": adjusted_duration_mins,
        "point_count": len(points),
        "points": points,  # retained in memory for heatmap; excluded from CSV
    }


# ---------------------------------------------------------------------------
# Main parse pipeline
# ---------------------------------------------------------------------------

def parse_trips(
    gpx_dir: str,
    home: Anchor,
    office: Anchor,
    mall: Anchor,
    min_points: int = 10,
    merge_gap_minutes: int = 30,
    stop_min_minutes: float = 20.0,
) -> Tuple[List[Dict], List[Tuple[List[str], str]]]:
    """
    Full parse pipeline: read → sort → merge → classify.

    Returns:
      trips     — list of classified trip dicts (including .points for heatmap)
      discarded — list of (filenames, reason) for unrelated groups
    """
    files = load_and_sort_gpx_files(gpx_dir)
    groups = merge_consecutive_groups(files, merge_gap_minutes)

    trips: List[Dict] = []
    discarded: List[Tuple[List[str], str]] = []

    for names, points in groups:
        result = classify_trip(points, home, office, mall, min_points, stop_min_minutes)
        if result is None:
            discarded.append((names, "unrelated - no anchor match"))
        else:
            result["filename"] = "; ".join(names)
            trips.append(result)

    return trips, discarded


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_csv(trips: List[Dict], output_path: str) -> None:
    """Write classified trips to CSV, excluding track point data."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for trip in trips:
            writer.writerow({k: trip[k] for k in CSV_FIELDS})


def print_summary(
    trips: List[Dict],
    discarded: List[Tuple[List[str], str]],
) -> None:
    """Print a human-readable classification summary to stdout."""
    bar = "=" * 64
    print(f"\n{bar}")
    print("  commute-lens parser — classification summary")
    print(bar)

    total_groups = len(trips) + len(discarded)
    print(f"  File groups processed : {total_groups}")
    print(f"  Trips classified      : {len(trips)}")
    print(f"  Discarded (unrelated) : {len(discarded)}")

    if trips:
        print()
        print("  TRIPS")
        print("  " + "-" * 62)
        for t in trips:
            flags = []
            if t["partial"]:
                flags.append("PARTIAL")
            if t["scenario_c"]:
                flags.append("Scenario C")
            flag_str = f"  [{', '.join(flags)}]" if flags else ""
            print(f"  {t['filename']}{flag_str}")
            print(f"    {t['date']}  {t['direction']}  ->  parking: {t['parking']}")
            dep = t["departure_time"][11:16]
            arr = t["arrival_time"][11:16]
            dur_str = f"{t['duration_min']} min"
            if t.get("stop_detected"):
                dur_str = (
                    f"{t['duration_min']} min raw  "
                    f"/ {t['adjusted_duration_mins']} min adj"
                    f"  (stop: {t['stop_duration_mins']} min)"
                )
            print(
                f"    {dep} -> {arr}  |  {t['distance_km']} km  "
                f"|  {dur_str}  |  {t['avg_speed_kmh']} km/h avg  "
                f"|  {t['point_count']} pts"
            )

    if discarded:
        print()
        print("  DISCARDED")
        print("  " + "-" * 62)
        for names, reason in discarded:
            print(f"  {'; '.join(names)}")
            print(f"    -> {reason}")

    print(f"{bar}\n")


# ---------------------------------------------------------------------------
# Incremental processing
# ---------------------------------------------------------------------------

def load_processed(processed_path: str) -> Set[str]:
    """Return the set of GPX filenames already committed to master_trips.csv."""
    p = Path(processed_path)
    if not p.exists():
        return set()
    with open(p, encoding="utf-8") as f:
        return set(json.load(f).get("files", []))


def save_processed(processed_path: str, processed_files: Set[str]) -> None:
    """Persist the processed filenames set to disk."""
    os.makedirs(os.path.dirname(os.path.abspath(processed_path)), exist_ok=True)
    with open(processed_path, "w", encoding="utf-8") as f:
        json.dump({"files": sorted(processed_files)}, f, indent=2)


def parse_trips_incremental(
    gpx_dir: str,
    home: Anchor,
    office: Anchor,
    mall: Anchor,
    min_points: int,
    merge_gap_minutes: int,
    processed_files: Set[str],
    stop_min_minutes: float = 20.0,
) -> Tuple[List[Dict], List[Tuple[List[str], str]], List[Set[str]]]:
    """
    Like parse_trips but skips groups where every constituent file is already
    in processed_files.

    Returns:
      new_trips        — classified trips from new/changed groups
      discarded        — unrelated groups (also new/changed)
      touched_filesets — set-per-group, used to remove stale CSV rows on re-merge
    """
    files = load_and_sort_gpx_files(gpx_dir)
    groups = merge_consecutive_groups(files, merge_gap_minutes)

    new_trips: List[Dict] = []
    discarded: List[Tuple[List[str], str]] = []
    touched_filesets: List[Set[str]] = []

    for names, points in groups:
        names_set = set(names)
        if names_set.issubset(processed_files):
            continue  # already processed — skip

        # At least one new file in this group
        touched_filesets.append(names_set)

        result = classify_trip(points, home, office, mall, min_points, stop_min_minutes)
        if result is None:
            discarded.append((names, "unrelated - no anchor match"))
        else:
            result["filename"] = "; ".join(names)
            new_trips.append(result)

    return new_trips, discarded, touched_filesets


def write_csv_incremental(
    new_trips: List[Dict],
    csv_path: str,
    touched_filesets: List[Set[str]],
) -> None:
    """
    Append new trips to master_trips.csv.

    If a previously-processed file now appears in a larger merged group
    (re-merge due to a late-arriving adjacent file), its old CSV row is
    removed first so the new merged row replaces it cleanly.
    """
    existing_rows: List[Dict] = []
    if Path(csv_path).exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row_files = set(row["filename"].split("; "))
                # Drop any row whose files overlap with a group we're re-processing
                if any(row_files & touched for touched in touched_filesets):
                    continue
                # Backfill any new fields added since this row was written
                for field in CSV_FIELDS:
                    row.setdefault(field, "")
                existing_rows.append(row)

    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing_rows)
        for trip in new_trips:
            writer.writerow({k: trip[k] for k in CSV_FIELDS})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "config.yaml"

    if not config_path.exists():
        print(f"ERROR: config.yaml not found at {config_path}", file=sys.stderr)
        print("Copy config.example.yaml to config.yaml and fill in your coordinates.")
        sys.exit(1)

    cfg = load_config(str(config_path))
    home, office, mall = build_anchors(cfg)

    gpx_dir = repo_root / cfg["paths"]["gpx_dir"]
    outputs_dir = repo_root / cfg["paths"]["outputs"]
    output_csv = outputs_dir / "master_trips.csv"
    processed_json = outputs_dir / "processed.json"

    thresholds = cfg.get("thresholds", {})
    min_points = thresholds.get("min_trip_points", 10)
    stop_min_minutes = float(thresholds.get("stop_min_minutes", 20.0))
    merge_gap = 30  # minutes — per spec

    processed_files = load_processed(str(processed_json))

    all_gpx = {p.name for p in Path(gpx_dir).glob("*.gpx")}
    new_files = all_gpx - processed_files

    if not new_files:
        print("\nNo new GPX files — nothing to process.")
        sys.exit(0)

    print(f"\nNew GPX files detected ({len(new_files)}): {sorted(new_files)}")

    new_trips, discarded, touched_filesets = parse_trips_incremental(
        str(gpx_dir), home, office, mall, min_points, merge_gap,
        processed_files, stop_min_minutes
    )

    print_summary(new_trips, discarded)

    write_csv_incremental(new_trips, str(output_csv), touched_filesets)

    # Mark every file in every touched group as processed (including discarded)
    newly_processed = {f for fs in touched_filesets for f in fs}
    save_processed(str(processed_json), processed_files | newly_processed)

    total_processed = len(processed_files | newly_processed)
    print(f"  CSV updated  -> {output_csv}  ({len(new_trips)} new row(s))")
    print(f"  processed.json -> {total_processed} file(s) total\n")
