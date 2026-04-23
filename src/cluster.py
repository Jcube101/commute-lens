#!/usr/bin/env python3
"""
cluster.py — Route clustering by path similarity for commute-lens.

Groups commute trips by geographic similarity using DBSCAN, separately for
outbound and return directions. Labels each cluster by its most distinctive
road segment via Nominatim reverse geocoding.

Run directly:  python src/cluster.py
Called from:    main.py step 9
"""

import csv
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

import numpy as np
from sklearn.cluster import DBSCAN

sys.path.insert(0, str(Path(__file__).parent))

from parser import (
    build_anchors,
    haversine,
    load_config,
    parse_gpx,
    TrackPoint,
)


MIN_TRIPS_FOR_CLUSTERING = 5

DBSCAN_EPS_METRES = 500
DBSCAN_MIN_SAMPLES = 2


# ---------------------------------------------------------------------------
# GPX loading for clustering
# ---------------------------------------------------------------------------

def load_trip_points(gpx_dir: str, filename: str) -> Optional[List[TrackPoint]]:
    """Load GPS points for a trip, handling merged filenames (semicolon-separated)."""
    parts = [f.strip() for f in filename.split(";")]
    all_points: List[TrackPoint] = []
    for part in parts:
        gpx_path = os.path.join(gpx_dir, part)
        if not os.path.exists(gpx_path):
            return None
        try:
            all_points.extend(parse_gpx(gpx_path))
        except Exception:
            return None
    all_points.sort(key=lambda p: p.time)
    return all_points if all_points else None


def subsample_points(points: List[TrackPoint], target: int = 100) -> List[Tuple[float, float]]:
    """Subsample track to ~target evenly-spaced (lat, lon) pairs for fast comparison."""
    if len(points) <= target:
        return [(p.lat, p.lon) for p in points]
    step = len(points) / target
    return [(points[int(i * step)].lat, points[int(i * step)].lon) for i in range(target)]


# ---------------------------------------------------------------------------
# Distance metric
# ---------------------------------------------------------------------------

def directed_point_distance(track_a: List[Tuple[float, float]],
                            track_b: List[Tuple[float, float]]) -> float:
    """Average nearest-point distance (metres) from each point in A to track B."""
    total = 0.0
    for lat_a, lon_a in track_a:
        min_dist = min(haversine(lat_a, lon_a, lat_b, lon_b) for lat_b, lon_b in track_b)
        total += min_dist
    return total / len(track_a)


def symmetric_track_distance(track_a: List[Tuple[float, float]],
                             track_b: List[Tuple[float, float]]) -> float:
    """Symmetric average point-to-point distance between two tracks."""
    return (directed_point_distance(track_a, track_b) +
            directed_point_distance(track_b, track_a)) / 2.0


def build_distance_matrix(tracks: List[List[Tuple[float, float]]]) -> np.ndarray:
    """Build a symmetric pairwise distance matrix (metres) for all tracks."""
    n = len(tracks)
    dist = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            d = symmetric_track_distance(tracks[i], tracks[j])
            dist[i, j] = d
            dist[j, i] = d
    return dist


# ---------------------------------------------------------------------------
# Label generation
# ---------------------------------------------------------------------------

def find_distinctive_point(cluster_tracks: List[List[Tuple[float, float]]],
                           other_tracks: List[List[Tuple[float, float]]]) -> Tuple[float, float]:
    """
    Find the point in cluster_tracks most distant from all other_tracks.
    This identifies the geographic segment that makes this cluster unique.
    """
    if not other_tracks:
        all_pts = [pt for track in cluster_tracks for pt in track]
        mid_idx = len(all_pts) // 2
        return all_pts[mid_idx] if all_pts else (0.0, 0.0)

    best_pt = (0.0, 0.0)
    best_min_dist = -1.0

    for track in cluster_tracks:
        step = max(1, len(track) // 20)
        for idx in range(0, len(track), step):
            lat, lon = track[idx]
            min_to_others = float("inf")
            for other in other_tracks:
                d = min(haversine(lat, lon, olat, olon) for olat, olon in other)
                min_to_others = min(min_to_others, d)
            if min_to_others > best_min_dist:
                best_min_dist = min_to_others
                best_pt = (lat, lon)

    return best_pt


def reverse_geocode(lat: float, lon: float) -> str:
    """Reverse geocode a point via Nominatim to get a road/area name."""
    url = (
        f"https://nominatim.openstreetmap.org/reverse"
        f"?lat={lat}&lon={lon}&format=json&zoom=16&addressdetails=1"
    )
    req = Request(url, headers={"User-Agent": "commute-lens/1.0 (personal project)"})
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        addr = data.get("address", {})
        road = addr.get("road") or addr.get("pedestrian") or addr.get("suburb") or ""
        return road
    except (URLError, json.JSONDecodeError, KeyError, OSError):
        return ""


def generate_cluster_label(cluster_tracks: List[List[Tuple[float, float]]],
                           other_tracks: List[List[Tuple[float, float]]]) -> str:
    """Generate a descriptive label like 'Via Outer Ring Rd' for a cluster."""
    lat, lon = find_distinctive_point(cluster_tracks, other_tracks)
    time.sleep(1.1)  # Nominatim rate limit: 1 req/sec
    road_name = reverse_geocode(lat, lon)
    if road_name:
        return f"Via {road_name}"
    return f"Via ({lat:.4f}, {lon:.4f})"


# ---------------------------------------------------------------------------
# Core clustering
# ---------------------------------------------------------------------------

def cluster_direction(
    trips: List[Dict],
    gpx_dir: str,
    direction: str,
) -> Tuple[List[Dict], str]:
    """
    Cluster trips for a single direction.

    Returns:
      trips       — same list with 'route_cluster' populated
      summary_msg — human-readable cluster summary
    """
    dir_trips = [t for t in trips if t["direction"] == direction]
    full_trips = [t for t in dir_trips if str(t.get("partial", "")).lower() != "true"]

    if len(full_trips) < MIN_TRIPS_FOR_CLUSTERING:
        for t in dir_trips:
            t["route_cluster"] = "Unclustered — insufficient data"
        needed = MIN_TRIPS_FOR_CLUSTERING - len(full_trips)
        msg = (
            f"  {direction}: {len(full_trips)} full trip(s), need {MIN_TRIPS_FOR_CLUSTERING}. "
            f"Skipped — {needed} more full trip(s) needed."
        )
        return dir_trips, msg

    # Load GPS tracks for full trips
    indexed: List[Tuple[int, List[Tuple[float, float]]]] = []
    for i, t in enumerate(full_trips):
        pts = load_trip_points(gpx_dir, t["filename"])
        if pts is None:
            continue
        indexed.append((i, subsample_points(pts)))

    if len(indexed) < MIN_TRIPS_FOR_CLUSTERING:
        for t in dir_trips:
            t["route_cluster"] = "Unclustered — insufficient data"
        return dir_trips, f"  {direction}: could not load enough GPX tracks for clustering."

    tracks = [tr for _, tr in indexed]
    indices = [idx for idx, _ in indexed]

    dist_matrix = build_distance_matrix(tracks)

    db = DBSCAN(eps=DBSCAN_EPS_METRES, min_samples=DBSCAN_MIN_SAMPLES, metric="precomputed")
    labels = db.fit_predict(dist_matrix)

    unique_labels = set(labels)
    cluster_ids = sorted(l for l in unique_labels if l >= 0)

    # Generate labels for each cluster
    cluster_label_map: Dict[int, str] = {}
    for cid in cluster_ids:
        c_tracks = [tracks[j] for j, l in enumerate(labels) if l == cid]
        o_tracks = [tracks[j] for j, l in enumerate(labels) if l != cid and l >= 0]
        cluster_label_map[cid] = generate_cluster_label(c_tracks, o_tracks)

    # Assign labels to full trips
    full_trip_labels: Dict[int, str] = {}
    for j, (orig_idx, _) in enumerate(indexed):
        lbl = labels[j]
        if lbl == -1:
            full_trip_labels[orig_idx] = "Outlier — unique route"
        else:
            full_trip_labels[orig_idx] = cluster_label_map[lbl]

    for i, t in enumerate(full_trips):
        t["route_cluster"] = full_trip_labels.get(i, "Unclustered")

    # Partial trips: assign to nearest full-trip cluster
    partial_trips = [t for t in dir_trips if str(t.get("partial", "")).lower() == "true"]
    for t in partial_trips:
        pts = load_trip_points(gpx_dir, t["filename"])
        if pts is None:
            t["route_cluster"] = "Unclustered — no GPX"
            continue
        sub = subsample_points(pts)
        best_dist = float("inf")
        best_label = "Unclustered"
        for j, track in enumerate(tracks):
            d = symmetric_track_distance(sub, track)
            if d < best_dist:
                best_dist = d
                orig_idx = indices[j]
                best_label = full_trip_labels.get(orig_idx, "Unclustered")
        if best_dist < DBSCAN_EPS_METRES * 2:
            t["route_cluster"] = best_label
        else:
            t["route_cluster"] = "Outlier — unique route"

    # Build summary
    label_counts: Dict[str, int] = {}
    for t in dir_trips:
        lbl = t.get("route_cluster", "Unclustered")
        label_counts[lbl] = label_counts.get(lbl, 0) + 1

    lines = [f"  {direction}: {len(cluster_ids)} cluster(s) from {len(full_trips)} full trip(s)"]
    for lbl, count in sorted(label_counts.items(), key=lambda x: -x[1]):
        lines.append(f"    {lbl}: {count} trip(s)")

    return dir_trips, "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_clustering(csv_path: str, gpx_dir: str) -> str:
    """
    Run route clustering on master_trips.csv and update it with route_cluster column.

    Returns a human-readable summary string.
    """
    rows: List[Dict] = []
    if Path(csv_path).exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

    if not rows:
        return "No trips in master_trips.csv — nothing to cluster."

    summaries = []
    for direction in ("Home to Office", "Office to Home"):
        dir_rows = [r for r in rows if r["direction"] == direction]
        if not dir_rows:
            summaries.append(f"  {direction}: no trips.")
            continue
        _, summary = cluster_direction(rows, gpx_dir, direction)
        summaries.append(summary)

    # Write back with route_cluster column
    fieldnames = list(rows[0].keys())
    if "route_cluster" not in fieldnames:
        fieldnames.append("route_cluster")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            row.setdefault("route_cluster", "")
            writer.writerow(row)

    bar = "=" * 64
    header = f"\n{bar}\n  commute-lens cluster — route summary\n{bar}"
    return header + "\n" + "\n".join(summaries) + f"\n{bar}\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "config.yaml"

    if not config_path.exists():
        print(f"ERROR: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config(str(config_path))
    gpx_dir = str(repo_root / cfg["paths"]["gpx_dir"])
    csv_path = str(repo_root / Path(cfg["paths"]["outputs"]) / "master_trips.csv")

    print(run_clustering(csv_path, gpx_dir))
