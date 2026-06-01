"""
detectors.py — Spatial dwell and tortuosity detection for commute-lens.

Two independent detection systems that supplement the existing gap-based
stop detection in parser.py:

1. Spatial dwell: finds segments where GPS stays within a 50m radius for
   15+ minutes — catches parked stops where OsmAnd kept logging at low freq.

2. Tortuosity: finds erratic low-speed segments (petrol bunks, snack stops,
   wandering around a forecourt) via path tortuosity ratio.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from parser import (
    Anchor,
    TrackPoint,
    Waypoint,
    haversine,
    _match_waypoint,
)


# ---------------------------------------------------------------------------
# Shared data classes
# ---------------------------------------------------------------------------

@dataclass
class DwellEvent:
    start_time: datetime
    end_time: datetime
    duration_mins: float
    centroid_lat: float
    centroid_lon: float
    radius_m: float
    location: str  # waypoint name or "Unknown location"


@dataclass
class TortuosityEvent:
    start_time: datetime
    end_time: datetime
    duration_mins: float
    distance_m: float  # wandering distance (path length)
    displacement_m: float  # straight-line displacement
    tortuosity: float  # path_length / displacement
    location: str


# ---------------------------------------------------------------------------
# Detection System 1 — Spatial dwell
# ---------------------------------------------------------------------------

def _max_spread(points: List[TrackPoint], start: int, end: int) -> float:
    """Max distance from centroid for points[start:end+1]. O(n)."""
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
    # Diameter is at most 2x centroid distance; use it as spread metric
    return max_dist * 2


def _centroid(points: List[TrackPoint]) -> Tuple[float, float]:
    lat = sum(p.lat for p in points) / len(points)
    lon = sum(p.lon for p in points) / len(points)
    return lat, lon


def detect_spatial_dwell(
    points: List[TrackPoint],
    home: Anchor,
    office: Anchor,
    mall: Anchor,
    waypoints: Optional[List[Waypoint]] = None,
    radius_m: float = 50.0,
    min_duration_mins: float = 15.0,
) -> List[DwellEvent]:
    """
    Slide a time-based window across GPS points and flag windows where the
    spatial spread stays below threshold for the full duration.

    Uses 2x max-centroid-distance as spread metric (O(n) per window check).
    Adjacent qualifying windows are merged into a single dwell event.
    Dwell events at HOME, OFFICE, or MALL anchors are suppressed.
    """
    if len(points) < 2:
        return []

    min_duration_secs = min_duration_mins * 60.0
    anchors = [home, office, mall]

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

    # Phase 3: build DwellEvent objects, filtering out anchor locations
    events: List[DwellEvent] = []
    for start_idx, end_idx in merged:
        segment = points[start_idx:end_idx + 1]
        duration = (segment[-1].time - segment[0].time).total_seconds() / 60.0
        if duration < min_duration_mins:
            continue

        clat, clon = _centroid(segment)
        hull_r = _max_spread(points, start_idx, end_idx)

        # Suppress if centroid is at any anchor
        at_anchor = False
        for a in anchors:
            if a.matches(clat, clon):
                at_anchor = True
                break
        if at_anchor:
            continue

        location = _match_waypoint(clat, clon, waypoints) or "Unknown location"
        events.append(DwellEvent(
            start_time=segment[0].time,
            end_time=segment[-1].time,
            duration_mins=round(duration, 1),
            centroid_lat=round(clat, 6),
            centroid_lon=round(clon, 6),
            radius_m=round(hull_r, 1),
            location=location,
        ))

    return events


# ---------------------------------------------------------------------------
# Detection System 2 — Tortuosity (erratic movement)
# ---------------------------------------------------------------------------

def detect_tortuosity(
    points: List[TrackPoint],
    home: Anchor,
    office: Anchor,
    mall: Anchor,
    waypoints: Optional[List[Waypoint]] = None,
    speed_threshold_kmh: float = 7.0,
    min_duration_mins: float = 2.0,
    tortuosity_threshold: float = 2.5,
    max_displacement_m: float = 300.0,
) -> List[TortuosityEvent]:
    """
    Scan for continuous low-speed segments and check for erratic movement
    via the tortuosity ratio (path_length / straight_line_displacement).
    """
    if len(points) < 3:
        return []

    min_duration_secs = min_duration_mins * 60.0
    anchors = [home, office, mall]

    # Phase 1: identify contiguous slow-speed runs
    slow_runs: List[Tuple[int, int]] = []  # (start_idx, end_idx) inclusive
    run_start = None

    for i, p in enumerate(points):
        is_slow = p.speed_kmh is not None and p.speed_kmh < speed_threshold_kmh
        if is_slow:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                slow_runs.append((run_start, i - 1))
                run_start = None
    if run_start is not None:
        slow_runs.append((run_start, len(points) - 1))

    # Phase 2: evaluate each slow run for tortuosity
    events: List[TortuosityEvent] = []
    for start_idx, end_idx in slow_runs:
        if end_idx - start_idx < 2:
            continue

        segment = points[start_idx:end_idx + 1]
        duration_secs = (segment[-1].time - segment[0].time).total_seconds()
        if duration_secs < min_duration_secs:
            continue

        # Path length (cumulative haversine)
        path_length = 0.0
        for k in range(1, len(segment)):
            path_length += haversine(
                segment[k - 1].lat, segment[k - 1].lon,
                segment[k].lat, segment[k].lon,
            )

        # Straight-line displacement
        displacement = haversine(
            segment[0].lat, segment[0].lon,
            segment[-1].lat, segment[-1].lon,
        )

        if displacement < 1.0:
            # Near-zero displacement — this is a dwell, not tortuosity
            continue

        if displacement > max_displacement_m:
            continue

        tortuosity = path_length / displacement
        if tortuosity < tortuosity_threshold:
            continue

        # Suppress if centroid is at any anchor
        clat, clon = _centroid(segment)
        at_anchor = False
        for a in anchors:
            if a.matches(clat, clon):
                at_anchor = True
                break
        if at_anchor:
            continue

        location = _match_waypoint(clat, clon, waypoints) or "Unknown location"
        events.append(TortuosityEvent(
            start_time=segment[0].time,
            end_time=segment[-1].time,
            duration_mins=round(duration_secs / 60.0, 1),
            distance_m=round(path_length, 1),
            displacement_m=round(displacement, 1),
            tortuosity=round(tortuosity, 2),
            location=location,
        ))

    return events
