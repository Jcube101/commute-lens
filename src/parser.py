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
    "near_office",
    "scenario_c",
    "stop_detected",
    "stop_duration_mins",
    "stop_location",
    "adjusted_duration_mins",
    "point_count",
    "walk_detected",
    "walk_duration_mins",
    "walk_origin",
    "dwell_stops",
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


class Waypoint:
    """A named waypoint for stop location enrichment (does not affect classification)."""

    def __init__(self, name: str, lat: float, lon: float, radius_m: float, wp_type: str = ""):
        self.name = name
        self.lat = lat
        self.lon = lon
        self.radius_m = radius_m
        self.wp_type = wp_type

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


def build_waypoints(cfg: dict) -> List['Waypoint']:
    wps = cfg.get("waypoints", {})
    result = []
    for key, val in wps.items():
        result.append(Waypoint(
            name=val.get("name", key),
            lat=val["lat"],
            lon=val["lon"],
            radius_m=val.get("radius_m", 200),
            wp_type=val.get("type", ""),
        ))
    return result


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
    malformed_files: Optional[Set[str]] = None,
) -> List[Tuple[str, List[TrackPoint]]]:
    """Parse all GPX files in gpx_dir and return them sorted by first timestamp.

    Any file that fails XML parsing is added to malformed_files (if provided)
    so the caller can mark it as processed and avoid retrying on future runs.
    """
    results: List[Tuple[str, List[TrackPoint]]] = []
    for gpx_file in sorted(Path(gpx_dir).glob("*.gpx")):
        try:
            points = parse_gpx(str(gpx_file))
        except ET.ParseError as exc:
            print(f"  WARNING: skipping malformed GPX {gpx_file.name} — {exc}")
            if malformed_files is not None:
                malformed_files.add(gpx_file.name)
            continue
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
    waypoints: Optional[List['Waypoint']] = None,
) -> Tuple[bool, float, str]:
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

    Returns (stop_detected, total_stop_duration_minutes, stop_location).
    """
    total_stop_mins = 0.0
    stop_locations: List[str] = []

    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        gap_min = (p2.time - p1.time).total_seconds() / 60.0

        if gap_min < stop_min_minutes:
            continue

        if p1.speed_kmh is not None and p1.speed_kmh > max_entry_speed_kmh:
            continue

        if haversine(p1.lat, p1.lon, p2.lat, p2.lon) > max_displacement_m:
            continue

        mid_lat = (p1.lat + p2.lat) / 2
        mid_lon = (p1.lon + p2.lon) / 2
        if (home.matches(mid_lat, mid_lon)
                or office.matches(mid_lat, mid_lon)
                or mall.matches(mid_lat, mid_lon)):
            continue

        total_stop_mins += gap_min

        location = _match_waypoint(mid_lat, mid_lon, waypoints)
        if location and location not in stop_locations:
            stop_locations.append(location)

    stop_detected = total_stop_mins > 0
    stop_location = "; ".join(stop_locations) if stop_locations else ("Unknown" if stop_detected else "")
    return stop_detected, round(total_stop_mins, 1), stop_location


def _match_waypoint(
    lat: float, lon: float, waypoints: Optional[List['Waypoint']] = None,
) -> Optional[str]:
    """Return the name of the nearest matching waypoint, or None."""
    if not waypoints:
        return None
    best_name = None
    best_dist = float("inf")
    for wp in waypoints:
        if wp.matches(lat, lon):
            d = wp.distance_to(lat, lon)
            if d < best_dist:
                best_name = wp.name
                best_dist = d
    return best_name


WAYPOINT_DWELL_MIN_MINUTES = 10.0


def _detect_waypoint_dwell(
    points: List[TrackPoint],
    waypoints: List['Waypoint'],
) -> Optional[str]:
    """Check if the trip spent significant time near any waypoint (>=10 min)."""
    wp_times: Dict[str, float] = {}
    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        mid_lat = (p1.lat + p2.lat) / 2
        mid_lon = (p1.lon + p2.lon) / 2
        name = _match_waypoint(mid_lat, mid_lon, waypoints)
        if name:
            seg_min = (p2.time - p1.time).total_seconds() / 60.0
            wp_times[name] = wp_times.get(name, 0.0) + seg_min
    matches = [n for n, t in wp_times.items() if t >= WAYPOINT_DWELL_MIN_MINUTES]
    return "; ".join(matches) if matches else None


# ---------------------------------------------------------------------------
# Spatial dwell detection
# ---------------------------------------------------------------------------

DWELL_RADIUS_M = 50.0
DWELL_MIN_DURATION_MINS = 15.0
DWELL_GAP_OVERLAP_TOLERANCE_MINS = 5.0


def _max_spread(points: List[TrackPoint], start: int, end: int) -> float:
    """2x max distance from centroid for points[start:end+1]. O(n)."""
    n = end - start + 1
    if n < 2:
        return 0.0
    clat = sum(points[k].lat for k in range(start, end + 1)) / n
    clon = sum(points[k].lon for k in range(start, end + 1)) / n
    max_dist = 0.0
    for k in range(start, end + 1):
        d = haversine(clat, clon, points[k].lat, points[k].lon)
        if d > max_dist:
            max_dist = d
    return max_dist * 2


def detect_spatial_dwell(
    points: List[TrackPoint],
    home: 'Anchor',
    office: 'Anchor',
    mall: 'Anchor',
    waypoints: Optional[List['Waypoint']] = None,
    radius_m: float = DWELL_RADIUS_M,
    min_duration_mins: float = DWELL_MIN_DURATION_MINS,
    gap_stop_intervals: Optional[List[Tuple[datetime, datetime]]] = None,
) -> List[Dict]:
    """
    Detect segments where GPS stays within a small radius for extended time.

    Catches parked stops where OsmAnd kept logging at low frequency — gap-based
    detection misses these because no large timestamp gap exists.

    Returns list of dwell event dicts. Skips dwells at anchors and dwells that
    overlap existing gap-based stops (within tolerance).
    """
    if len(points) < 2:
        return []

    min_duration_secs = min_duration_mins * 60.0
    overlap_secs = DWELL_GAP_OVERLAP_TOLERANCE_MINS * 60.0
    anchors = [home, office, mall]

    # Phase 1: sliding window to find qualifying dwell intervals
    dwell_intervals: List[Tuple[int, int]] = []
    i = 0
    while i < len(points) - 1:
        j = i + 1
        while j < len(points) and (points[j].time - points[i].time).total_seconds() < min_duration_secs:
            j += 1
        if j >= len(points):
            break

        spread = _max_spread(points, i, j)
        if spread < radius_m:
            best_j = j
            k = j + 1
            while k < len(points):
                if _max_spread(points, i, k) < radius_m:
                    best_j = k
                    k += 1
                else:
                    break
            dwell_intervals.append((i, best_j))
            i = best_j + 1
        else:
            i += 1

    if not dwell_intervals:
        return []

    # Phase 2: merge overlapping/adjacent intervals
    merged: List[Tuple[int, int]] = [dwell_intervals[0]]
    for start, end in dwell_intervals[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Phase 3: build events, filtering anchors and gap-stop overlaps
    events: List[Dict] = []
    for start_idx, end_idx in merged:
        segment = points[start_idx:end_idx + 1]
        duration = (segment[-1].time - segment[0].time).total_seconds() / 60.0
        if duration < min_duration_mins:
            continue

        n = len(segment)
        clat = sum(p.lat for p in segment) / n
        clon = sum(p.lon for p in segment) / n
        spread = _max_spread(points, start_idx, end_idx)

        if any(a.matches(clat, clon) for a in anchors):
            continue

        # Skip if this dwell overlaps an existing gap-based stop
        if gap_stop_intervals:
            dwell_start = segment[0].time
            dwell_end = segment[-1].time
            overlaps = False
            for gap_start, gap_end in gap_stop_intervals:
                if (dwell_start.timestamp() <= gap_end.timestamp() + overlap_secs
                        and dwell_end.timestamp() >= gap_start.timestamp() - overlap_secs):
                    overlaps = True
                    break
            if overlaps:
                continue

        location = _match_waypoint(clat, clon, waypoints) or "Unknown"
        events.append({
            "start_time": segment[0].time,
            "end_time": segment[-1].time,
            "duration_mins": round(duration, 1),
            "centroid_lat": round(clat, 6),
            "centroid_lon": round(clon, 6),
            "radius_m": round(spread, 1),
            "location": location,
        })

    return events


def _get_gap_stop_intervals(
    points: List[TrackPoint],
    stop_min_minutes: float,
    max_displacement_m: float = 150.0,
    max_entry_speed_kmh: float = 15.0,
) -> List[Tuple[datetime, datetime]]:
    """Return (start_time, end_time) for each gap-based stop, for overlap checking."""
    intervals = []
    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        gap_min = (p2.time - p1.time).total_seconds() / 60.0
        if gap_min < stop_min_minutes:
            continue
        if p1.speed_kmh is not None and p1.speed_kmh > max_entry_speed_kmh:
            continue
        if haversine(p1.lat, p1.lon, p2.lat, p2.lon) > max_displacement_m:
            continue
        intervals.append((p1.time, p2.time))
    return intervals


def _format_dwell_stops(events: List[Dict]) -> str:
    """Format dwell events into a human-readable string for the CSV field."""
    if not events:
        return ""
    parts = []
    for ev in events:
        start_ist = ev["start_time"].astimezone(IST).strftime("%H:%M")
        end_ist = ev["end_time"].astimezone(IST).strftime("%H:%M")
        parts.append(f"{ev['location']} {start_ist}-{end_ist} ({ev['duration_mins']}min)")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Walk detection and truncation
# ---------------------------------------------------------------------------

WALK_SPEED_THRESHOLD_KMH = 7.0
WALK_MIN_DURATION_MINS = 3.0
WALK_MAX_DISTANCE_M = 1000.0
WALK_ANCHOR_RADIUS_M = 150.0
WALK_AMBIGUOUS_THRESHOLD_M = 55.0

WALK_GUARD_MIN_DISTANCE_KM = 10.0
WALK_GUARD_MIN_DURATION_MIN = 20.0

NEAR_OFFICE_MIN_M = 150.0
NEAR_OFFICE_MAX_M = 800.0


def detect_and_truncate_walk(
    points: List[TrackPoint],
    home: Anchor,
    office: Anchor,
    mall: Anchor,
) -> Tuple[List[TrackPoint], bool, float, Optional[str]]:
    """
    Detect a trailing walk segment and truncate the trip at the parking point.

    Scans backwards from the GPX end for sustained walking speed (< 7 km/h
    for > 3 min, < 1 km). If found, checks where the walk started against
    all three anchors using a fixed 150m radius. If the walk origin is near
    an anchor, the trip is truncated there.

    Walk origin determines parking:
      - Near MALL  -> parked at mall, walked to office (Scenario A)
      - Near OFFICE -> parked at office (Scenario B, long walk variant)
      - Near HOME  -> return trip, walked inside after parking (Scenario D)

    Returns:
      truncated_points   — points up to the last vehicle-speed point
      walk_detected      — True if walk was found and truncated
      walk_duration_mins — duration of removed walk segment
      walk_origin        — anchor name where walk started, or None
    """
    if len(points) < 10:
        return points, False, 0.0, None

    duration_min = (points[-1].time - points[0].time).total_seconds() / 60.0
    if duration_min < WALK_GUARD_MIN_DURATION_MIN:
        return points, False, 0.0, None

    last_vehicle_idx = len(points) - 1
    for i in range(len(points) - 1, -1, -1):
        speed = points[i].speed_kmh
        if speed is not None and speed > WALK_SPEED_THRESHOLD_KMH:
            last_vehicle_idx = i
            break

    if last_vehicle_idx >= len(points) - 2:
        return points, False, 0.0, None

    walk_start_idx = last_vehicle_idx + 1
    walk_start = points[walk_start_idx]
    walk_duration = (points[-1].time - walk_start.time).total_seconds() / 60.0

    if walk_duration < WALK_MIN_DURATION_MINS:
        return points, False, 0.0, None

    walk_points = points[walk_start_idx:]
    walk_dist = sum(
        haversine(walk_points[i].lat, walk_points[i].lon,
                  walk_points[i + 1].lat, walk_points[i + 1].lon)
        for i in range(len(walk_points) - 1)
    )
    if walk_dist > WALK_MAX_DISTANCE_M:
        return points, False, 0.0, None

    walk_origin = None
    best_dist = float("inf")
    anchor_distances = {}
    for anchor in [mall, office, home]:
        dist = anchor.distance_to(walk_start.lat, walk_start.lon)
        if dist <= WALK_ANCHOR_RADIUS_M:
            anchor_distances[anchor.name] = dist
            if dist < best_dist:
                walk_origin = anchor.name
                best_dist = dist

    if walk_origin is None:
        return points, False, 0.0, None

    # When MALL and OFFICE are both in range and within 50m of each other,
    # prefer MALL — real-world parking prior is heavily MALL-biased
    if (mall.name in anchor_distances and "OFFICE" in anchor_distances
            and abs(anchor_distances[mall.name] - anchor_distances["OFFICE"])
            <= WALK_AMBIGUOUS_THRESHOLD_M):
        walk_origin = mall.name
        best_dist = anchor_distances[mall.name]

    truncated = points[: last_vehicle_idx + 1]
    if len(truncated) < 10:
        return points, False, 0.0, None

    return truncated, True, round(walk_duration, 1), walk_origin


# ---------------------------------------------------------------------------
# Trip classification
# ---------------------------------------------------------------------------

def _nearest_anchor(
    lat: float, lon: float, *anchors: Anchor,
) -> Optional[Anchor]:
    """Return the nearest matching anchor, or None. Breaks ties by distance."""
    candidates = []
    for a in anchors:
        if a.matches(lat, lon):
            candidates.append((a, a.distance_to(lat, lon)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


def classify_trip(
    points: List[TrackPoint],
    home: Anchor,
    office: Anchor,
    mall: Anchor,
    min_points: int = 10,
    stop_min_minutes: float = 20.0,
    waypoints: Optional[List['Waypoint']] = None,
) -> Optional[Dict]:
    """
    Classify a sequence of track points as a commute trip.

    Walk detection runs first: scans backwards for a trailing walk segment,
    checks where the walk started (which anchor at 150m), and truncates.
    The walk origin overrides the endpoint anchor for parking classification.

    Outbound scenarios (HOME -> OFFICE/MALL):
      A: walk starts near MALL  -> parking=Mall (or Sent to Mall if Scenario C)
      B: no walk or walk starts near OFFICE -> parking=Office
      C: OFFICE mid-route + end at MALL -> parking=Sent to Mall

    Return scenarios (OFFICE/MALL -> HOME):
      D: walk starts near HOME -> truncate at HOME, parking from start anchor

    When a point falls within multiple anchor radii, nearest wins.
    """
    if len(points) < min_points:
        return None

    points, walk_detected, walk_duration_mins, walk_origin = detect_and_truncate_walk(
        points, home, office, mall
    )

    start, end = points[0], points[-1]

    start_anchor = _nearest_anchor(start.lat, start.lon, home, office, mall)
    end_anchor = _nearest_anchor(end.lat, end.lon, home, office, mall)

    # Walk origin overrides the truncated endpoint anchor
    if walk_detected and walk_origin is not None:
        for a in [home, office, mall]:
            if a.name == walk_origin:
                end_anchor = a
                break

    direction: Optional[str] = None
    parking: Optional[str] = None
    partial = False
    near_office = False
    scenario_c = False

    def _is(anchor: Optional[Anchor], name: str) -> bool:
        return anchor is not None and anchor.name == name

    end_near_office = (
        end_anchor is None
        and NEAR_OFFICE_MIN_M < office.distance_to(end.lat, end.lon) <= NEAR_OFFICE_MAX_M
    )
    start_near_office = (
        start_anchor is None
        and NEAR_OFFICE_MIN_M < office.distance_to(start.lat, start.lon) <= NEAR_OFFICE_MAX_M
    )

    def _resolve_mall_parking() -> str:
        nonlocal scenario_c
        scenario_c = any(office.matches(p.lat, p.lon) for p in points[1:-1])
        return "Sent to Mall" if scenario_c else "Mall"

    # ---- OUTBOUND: start at HOME ----
    if _is(start_anchor, "HOME"):
        if _is(end_anchor, "OFFICE"):
            direction, parking = "Home to Office", "Office"
        elif _is(end_anchor, mall.name):
            direction = "Home to Office"
            parking = _resolve_mall_parking()
        elif end_near_office:
            near_office = True
            direction, parking = "Home to Office", "Near Office"

    # ---- RETURN: end at HOME ----
    elif _is(end_anchor, "HOME"):
        if _is(start_anchor, "OFFICE"):
            direction, parking = "Office to Home", "Office"
        elif _is(start_anchor, mall.name):
            direction, parking = "Office to Home", "Mall"
        elif start_near_office:
            near_office = True
            direction, parking = "Office to Home", "Near Office"
        elif start_anchor is None:
            partial = True
            direction, parking = "Office to Home", "Unknown"

    # ---- PARTIAL: end matches non-HOME anchor, start not HOME ----
    elif (end_anchor is not None and not _is(end_anchor, "HOME")
          and not _is(start_anchor, "HOME")):
        partial = True
        if _is(end_anchor, "OFFICE"):
            direction, parking = "Home to Office", "Office"
        elif _is(end_anchor, mall.name):
            direction, parking = "Home to Office", "Mall"

    elif end_near_office and not _is(start_anchor, "HOME"):
        partial = True
        near_office = True
        direction, parking = "Home to Office", "Near Office"

    if direction is None:
        return None

    departure_ist = points[0].time.astimezone(IST)
    arrival_ist = points[-1].time.astimezone(IST)
    duration_min = (points[-1].time - points[0].time).total_seconds() / 60.0

    # --- Gap-based stop detection (priority 1) ---
    stop_detected, stop_duration_mins, stop_location = detect_stops(
        points, home, office, mall, stop_min_minutes=stop_min_minutes,
        waypoints=waypoints,
    )
    adjusted_duration_mins = round(duration_min - stop_duration_mins, 1)

    # For trips without a gap-based stop, check if significant dwell time
    # was spent near any waypoint (catches suspected unreported stops)
    if not stop_location and waypoints:
        wp_dwell = _detect_waypoint_dwell(points, waypoints)
        if wp_dwell:
            stop_location = wp_dwell

    # --- Spatial dwell detection (priority 2) ---
    # Catches parked stops where OsmAnd kept logging at low frequency
    gap_intervals = _get_gap_stop_intervals(points, stop_min_minutes)
    dwell_events = detect_spatial_dwell(
        points, home, office, mall,
        waypoints=waypoints,
        gap_stop_intervals=gap_intervals,
    )

    dwell_stops_str = _format_dwell_stops(dwell_events)
    total_dwell_mins = sum(ev["duration_mins"] for ev in dwell_events)
    if total_dwell_mins > 0:
        adjusted_duration_mins = round(adjusted_duration_mins - total_dwell_mins, 1)
        stop_detected = True
        stop_duration_mins = round(stop_duration_mins + total_dwell_mins, 1)
        dwell_locations = []
        for ev in dwell_events:
            if ev["location"] not in dwell_locations:
                dwell_locations.append(ev["location"])
        if stop_location:
            for loc in dwell_locations:
                if loc not in stop_location:
                    stop_location = stop_location + " + " + loc
        else:
            stop_location = " + ".join(dwell_locations)

    return {
        "filename": None,
        "date": departure_ist.date().isoformat(),
        "direction": direction,
        "departure_time": departure_ist.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "arrival_time": arrival_ist.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "duration_min": round(duration_min, 1),
        "distance_km": round(trip_distance_km(points), 3),
        "avg_speed_kmh": round(calc_avg_speed_kmh(points), 1),
        "parking": parking,
        "partial": partial,
        "near_office": near_office,
        "scenario_c": scenario_c,
        "stop_detected": stop_detected,
        "stop_duration_mins": stop_duration_mins,
        "stop_location": stop_location,
        "adjusted_duration_mins": adjusted_duration_mins,
        "point_count": len(points),
        "walk_detected": walk_detected,
        "walk_duration_mins": walk_duration_mins,
        "walk_origin": walk_origin or "",
        "dwell_stops": dwell_stops_str,
        "points": points,
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
    waypoints: Optional[List['Waypoint']] = None,
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
        result = classify_trip(points, home, office, mall, min_points, stop_min_minutes, waypoints)
        if result is None:
            discarded.append((names, "unrelated - no anchor match"))
        else:
            if result["partial"]:
                if (result["distance_km"] < PARTIAL_MIN_DISTANCE_KM
                        or result["duration_min"] < PARTIAL_MIN_DURATION_MIN):
                    discarded.append((names, "partial below minimum threshold"))
                    continue
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


PARTIAL_MIN_DISTANCE_KM = 10.0
PARTIAL_MIN_DURATION_MIN = 20.0


def parse_trips_incremental(
    gpx_dir: str,
    home: Anchor,
    office: Anchor,
    mall: Anchor,
    min_points: int,
    merge_gap_minutes: int,
    processed_files: Set[str],
    stop_min_minutes: float = 20.0,
    waypoints: Optional[List['Waypoint']] = None,
) -> Tuple[List[Dict], List[Tuple[List[str], str]], List[Set[str]]]:
    """
    Like parse_trips but skips groups where every constituent file is already
    in processed_files.

    Returns:
      new_trips        — classified trips from new/changed groups
      discarded        — unrelated groups (also new/changed)
      touched_filesets — set-per-group, used to remove stale CSV rows on re-merge
    """
    malformed: Set[str] = set()
    files = load_and_sort_gpx_files(gpx_dir, malformed_files=malformed)
    groups = merge_consecutive_groups(files, merge_gap_minutes)

    new_trips: List[Dict] = []
    discarded: List[Tuple[List[str], str]] = []
    touched_filesets: List[Set[str]] = []

    # Malformed files are marked as processed so they aren't retried
    if malformed:
        touched_filesets.append(malformed)

    for names, points in groups:
        names_set = set(names)
        if names_set.issubset(processed_files):
            continue  # already processed — skip

        # At least one new file in this group
        touched_filesets.append(names_set)

        result = classify_trip(points, home, office, mall, min_points, stop_min_minutes, waypoints)
        if result is None:
            discarded.append((names, "unrelated - no anchor match"))
        else:
            # Filter: partial trips must meet minimum distance and duration
            if result["partial"]:
                if (result["distance_km"] < PARTIAL_MIN_DISTANCE_KM
                        or result["duration_min"] < PARTIAL_MIN_DURATION_MIN):
                    discarded.append((names, "partial below minimum threshold"))
                    continue
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
