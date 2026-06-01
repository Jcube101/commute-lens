#!/usr/bin/env python3
"""
test_detectors.py — Run spatial dwell and tortuosity detectors against all
GPX files at multiple threshold combinations. Outputs a report for review
before any changes to master_trips.csv or the main pipeline.

Run from repo root:
    python src/tests/test_detectors.py
"""

import sys
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
from datetime import timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Add src/ to path so imports work when run from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser import (
    Anchor,
    TrackPoint,
    Waypoint,
    build_anchors,
    build_waypoints,
    classify_trip,
    haversine,
    load_and_sort_gpx_files,
    load_config,
    merge_consecutive_groups,
    PARTIAL_MIN_DISTANCE_KM,
    PARTIAL_MIN_DURATION_MIN,
)
from detectors import (
    DwellEvent,
    TortuosityEvent,
    detect_spatial_dwell,
    detect_tortuosity,
)

IST = timezone(timedelta(hours=5, minutes=30))

# Known true positives — file patterns -> expected stop type
KNOWN_TRUE_POSITIVES = {
    "Shooting Range": [
        "2026-05-05",  # May 5 return — known suspected unreported stop
        "2026-04-30",  # shooting range stops
        "2026-04-28",  # shooting range stops
        "2026-04-23",  # shooting range stops
        "2026-04-21",  # shooting range stops
        "2026-04-16",  # shooting range stops
        "2026-05-19",  # shooting range stops
        "2026-05-07",  # shooting range stops
    ],
}


def classify_all_trips(
    gpx_dir: str,
    home: Anchor,
    office: Anchor,
    mall: Anchor,
    waypoints: List[Waypoint],
) -> List[Dict]:
    """Parse and classify all GPX files, returning trip dicts with points."""
    files = load_and_sort_gpx_files(gpx_dir)
    groups = merge_consecutive_groups(files, merge_gap_minutes=30)

    all_groups = []
    for names, points in groups:
        result = classify_trip(points, home, office, mall, min_points=10,
                               stop_min_minutes=20.0, waypoints=waypoints)
        classification = "discarded"
        if result is not None:
            if result["partial"]:
                if (result["distance_km"] < PARTIAL_MIN_DISTANCE_KM
                        or result["duration_min"] < PARTIAL_MIN_DURATION_MIN):
                    classification = "discarded (sub-threshold partial)"
                else:
                    classification = "partial"
            else:
                classification = "commute"
            result["filename"] = "; ".join(names)
        all_groups.append({
            "filenames": names,
            "filename_str": "; ".join(names),
            "points": points,
            "classification": classification,
            "trip": result,
        })

    return all_groups


def is_known_tp(event_time: str, location: str) -> str:
    """Check if a detection is a known true positive."""
    for known_loc, dates in KNOWN_TRUE_POSITIVES.items():
        if known_loc.lower() in location.lower():
            for d in dates:
                if d in event_time:
                    return f"KNOWN TP ({known_loc})"
    # May 5 special case
    if "2026-05-05" in event_time:
        return "KNOWN TP (May 5 suspected unreported stop)"
    return "Review needed"


def fmt_time(dt) -> str:
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


def run_dwell_sweep(
    all_groups: List[Dict],
    home: Anchor,
    office: Anchor,
    mall: Anchor,
    waypoints: List[Waypoint],
    thresholds: List[Tuple[float, float]],
) -> None:
    """Run spatial dwell detection at multiple threshold combinations."""
    print("\n" + "=" * 100)
    print("  DETECTION SYSTEM 1 — SPATIAL DWELL")
    print("=" * 100)

    for radius_m, duration_min in thresholds:
        print(f"\n{'-' * 100}")
        print(f"  Threshold: radius={radius_m}m, min_duration={duration_min}min")
        print(f"{'-' * 100}")

        total_triggers = 0
        for group in all_groups:
            events = detect_spatial_dwell(
                group["points"], home, office, mall, waypoints,
                radius_m=radius_m, min_duration_mins=duration_min,
            )
            if not events:
                continue

            total_triggers += len(events)
            print(f"\n  File: {group['filename_str']}")
            print(f"  Classification: {group['classification']}")
            if group["trip"]:
                t = group["trip"]
                print(f"  Trip: {t['direction']}  |  {t['date']}  |  {t['distance_km']} km  |  {t['duration_min']} min")
                if t["stop_detected"]:
                    print(f"  Existing gap-stop: {t['stop_duration_mins']} min at {t['stop_location']}")

            for ev in events:
                verdict = is_known_tp(fmt_time(ev.start_time), ev.location)
                print(f"    → DWELL  {fmt_time(ev.start_time)} — {fmt_time(ev.end_time)}")
                print(f"             Duration: {ev.duration_mins} min  |  Radius: {ev.radius_m}m")
                print(f"             Centroid: ({ev.centroid_lat}, {ev.centroid_lon})  |  Location: {ev.location}")
                print(f"             Verdict: {verdict}")

        print(f"\n  Total triggers at this threshold: {total_triggers}")


def run_tortuosity_sweep(
    all_groups: List[Dict],
    home: Anchor,
    office: Anchor,
    mall: Anchor,
    waypoints: List[Waypoint],
    thresholds: List[Tuple[float, float]],
) -> None:
    """Run tortuosity detection at multiple threshold combinations."""
    print("\n" + "=" * 100)
    print("  DETECTION SYSTEM 2 — TORTUOSITY (ERRATIC MOVEMENT)")
    print("=" * 100)

    for tort_threshold, min_dur in thresholds:
        print(f"\n{'-' * 100}")
        print(f"  Threshold: tortuosity>{tort_threshold}, min_duration={min_dur}min")
        print(f"{'-' * 100}")

        total_triggers = 0
        for group in all_groups:
            events = detect_tortuosity(
                group["points"], home, office, mall, waypoints,
                tortuosity_threshold=tort_threshold,
                min_duration_mins=min_dur,
            )
            if not events:
                continue

            total_triggers += len(events)
            print(f"\n  File: {group['filename_str']}")
            print(f"  Classification: {group['classification']}")
            if group["trip"]:
                t = group["trip"]
                print(f"  Trip: {t['direction']}  |  {t['date']}  |  {t['distance_km']} km  |  {t['duration_min']} min")

            for ev in events:
                verdict = is_known_tp(fmt_time(ev.start_time), ev.location)
                print(f"    → ERRATIC  {fmt_time(ev.start_time)} — {fmt_time(ev.end_time)}")
                print(f"               Duration: {ev.duration_mins} min  |  Path: {ev.distance_m}m  |  Displacement: {ev.displacement_m}m")
                print(f"               Tortuosity: {ev.tortuosity}  |  Location: {ev.location}")
                print(f"               Verdict: {verdict}")

        print(f"\n  Total triggers at this threshold: {total_triggers}")


def main():
    repo_root = Path(__file__).resolve().parent.parent.parent
    config_path = repo_root / "config.yaml"

    if not config_path.exists():
        print("ERROR: config.yaml not found. Cannot run detector tests without real config.")
        sys.exit(1)

    cfg = load_config(str(config_path))
    home, office, mall = build_anchors(cfg)
    waypoints = build_waypoints(cfg)
    gpx_dir = str(repo_root / cfg["paths"]["gpx_dir"])

    print("=" * 100)
    print("  commute-lens — Detector Test Report")
    print(f"  GPX directory: {gpx_dir}")
    print(f"  Waypoints: {[w.name for w in waypoints]}")
    print("=" * 100)

    all_groups = classify_all_trips(gpx_dir, home, office, mall, waypoints)

    commute_count = sum(1 for g in all_groups if g["classification"] == "commute")
    partial_count = sum(1 for g in all_groups if g["classification"] == "partial")
    discarded_count = sum(1 for g in all_groups if "discarded" in g["classification"])

    print(f"\n  Total file groups: {len(all_groups)}")
    print(f"  Commute trips: {commute_count}")
    print(f"  Partial trips: {partial_count}")
    print(f"  Discarded: {discarded_count}")

    # Spatial dwell sweep
    dwell_thresholds = [
        (50.0, 10.0),
        (50.0, 15.0),
        (50.0, 20.0),
    ]
    run_dwell_sweep(all_groups, home, office, mall, waypoints, dwell_thresholds)

    # Tortuosity sweep
    tort_thresholds = [
        (2.0, 2.0),
        (2.5, 2.0),
        (3.0, 3.0),
    ]
    run_tortuosity_sweep(all_groups, home, office, mall, waypoints, tort_thresholds)

    # Summary
    print("\n" + "=" * 100)
    print("  END OF REPORT — Review triggers above before integrating into pipeline")
    print("=" * 100)


if __name__ == "__main__":
    main()
