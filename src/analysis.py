#!/usr/bin/env python3
"""
analysis.py — Heatmap and dashboard generation for commute-lens.

generate_heatmap():  Folium speed-coloured map of all trips (incl. partial)
generate_dashboard(): Plotly single-file HTML dashboard of commute analytics

Run directly:  python src/analysis.py
Called from:    main.py steps 6-7
"""

import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import folium
import plotly.graph_objects as go
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).parent))

from parser import haversine, load_config, parse_gpx, TrackPoint


ORANGE = "#e85d04"
DARK_BG = "#1a1a2e"

SPEED_COLORS = [
    (30, "#00aa44"),   # green: > 30 km/h
    (15, "#ffcc00"),   # yellow: 15-30 km/h
    (5,  "#ff6600"),   # orange: 5-15 km/h
    (0,  "#ff2222"),   # red: < 5 km/h
]


def _speed_color(speed_kmh: Optional[float]) -> str:
    if speed_kmh is None:
        return "#999999"
    for threshold, color in SPEED_COLORS:
        if speed_kmh >= threshold:
            return color
    return "#ff2222"


# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------

def _load_all_trip_points(csv_path: str, gpx_dir: str) -> List[List[TrackPoint]]:
    """Load GPS points for every trip in master_trips.csv (including partials)."""
    rows: List[Dict] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    all_tracks: List[List[TrackPoint]] = []
    for row in rows:
        filename = row.get("filename", "")
        parts = [p.strip() for p in filename.split(";")]
        points: List[TrackPoint] = []
        for part in parts:
            gpx_path = os.path.join(gpx_dir, part)
            if os.path.exists(gpx_path):
                try:
                    points.extend(parse_gpx(gpx_path))
                except Exception:
                    continue
        if points:
            points.sort(key=lambda p: p.time)
            all_tracks.append(points)

    return all_tracks


def _segment_coverage(all_tracks: List[List[TrackPoint]], grid_size: float = 50.0) -> Dict[Tuple[int, int], int]:
    """Count how many trips pass through each ~50m grid cell."""
    coverage: Dict[Tuple[int, int], int] = {}
    for points in all_tracks:
        seen_cells: set = set()
        for p in points:
            cell = (int(p.lat * 111000 / grid_size), int(p.lon * 111000 / grid_size))
            seen_cells.add(cell)
        for cell in seen_cells:
            coverage[cell] = coverage.get(cell, 0) + 1
    return coverage


def generate_heatmap(csv_path: str, gpx_dir: str, output_path: str) -> None:
    """Generate a Folium speed-coloured heatmap of all recorded trips."""
    all_tracks = _load_all_trip_points(csv_path, gpx_dir)
    if not all_tracks:
        print("  No tracks to plot.")
        return

    coverage = _segment_coverage(all_tracks)
    max_coverage = max(coverage.values()) if coverage else 1

    all_lats = [p.lat for track in all_tracks for p in track]
    all_lons = [p.lon for track in all_tracks for p in track]
    center_lat = sum(all_lats) / len(all_lats)
    center_lon = sum(all_lons) / len(all_lons)

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=12,
        tiles="https://cartodb-basemaps-{s}.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png",
        attr="&copy; OpenStreetMap contributors &copy; CartoDB",
    )

    for points in all_tracks:
        for i in range(1, len(points)):
            p1, p2 = points[i - 1], points[i]

            if haversine(p1.lat, p1.lon, p2.lat, p2.lon) > 500:
                continue

            speed = p1.speed_kmh
            color = _speed_color(speed)

            cell = (int(p1.lat * 111000 / 50.0), int(p1.lon * 111000 / 50.0))
            trips_here = coverage.get(cell, 1)
            weight = 2 + (trips_here / max_coverage) * 4

            folium.PolyLine(
                locations=[[p1.lat, p1.lon], [p2.lat, p2.lon]],
                color=color,
                weight=weight,
                opacity=0.7,
            ).add_to(m)

    legend_html = """
    <div style="position:fixed; bottom:30px; left:30px; z-index:1000;
                background:white; padding:12px 16px; border-radius:8px;
                box-shadow:0 2px 6px rgba(0,0,0,0.3); font-family:sans-serif; font-size:13px;">
        <b>Commute speed heatmap &mdash; all recorded trips</b><br><br>
        <span style="background:#00aa44;padding:2px 10px;color:white;border-radius:3px;">&gt;30 km/h</span><br>
        <span style="background:#ffcc00;padding:2px 10px;border-radius:3px;">15&ndash;30 km/h</span><br>
        <span style="background:#ff6600;padding:2px 10px;color:white;border-radius:3px;">5&ndash;15 km/h</span><br>
        <span style="background:#ff2222;padding:2px 10px;color:white;border-radius:3px;">&lt;5 km/h</span><br>
        <br><span style="font-size:11px;color:#666;">Line thickness = trip coverage</span>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    title_html = """
    <div style="position:fixed; top:10px; left:50%; transform:translateX(-50%); z-index:1000;
                background:white; padding:8px 20px; border-radius:8px;
                box-shadow:0 2px 6px rgba(0,0,0,0.3); font-family:sans-serif; font-size:16px; font-weight:bold;">
        Commute speed heatmap &mdash; all recorded trips
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    m.save(output_path)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def _parse_departure_hour(departure_time: str) -> Optional[float]:
    """Extract fractional hour (e.g. 9.5 for 9:30) from departure_time string."""
    try:
        parts = departure_time.split(" ")
        time_part = parts[1] if len(parts) >= 2 else parts[0]
        h, m, s = time_part.split(":")
        return int(h) + int(m) / 60.0
    except (ValueError, IndexError):
        return None


def generate_dashboard(csv_path: str, output_path: str) -> None:
    """Generate a Plotly dashboard HTML from master_trips.csv (full trips only for stats)."""
    rows: List[Dict] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    full = [r for r in rows if str(r.get("partial", "")).lower() != "true"]
    n = len(full)

    fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=[
            f"Departure time vs duration (n={n})",
            f"Day of week avg duration — outbound (n={len([r for r in full if r['direction']=='Home to Office'])})",
            f"Duration over time (n={n})",
            f"Mileage over time (n={len([r for r in full if r.get('mileage_kmpl')])})",
            f"Parking distribution (n={n})",
            "",
        ],
        specs=[
            [{"type": "scatter"}, {"type": "bar"}],
            [{"type": "scatter"}, {"type": "scatter"}],
            [{"type": "pie"}, {}],
        ],
        vertical_spacing=0.12,
        horizontal_spacing=0.1,
    )

    # --- Chart 1: Departure time vs adjusted duration scatter ---
    for direction, color, symbol in [
        ("Home to Office", ORANGE, "circle"),
        ("Office to Home", "#4ecdc4", "diamond"),
    ]:
        d_trips = [r for r in full if r["direction"] == direction]
        hours = []
        durations = []
        labels = []
        for r in d_trips:
            h = _parse_departure_hour(r.get("departure_time", ""))
            dur = r.get("adjusted_duration_mins") or r.get("duration_min")
            if h is not None and dur:
                hours.append(h)
                durations.append(float(dur))
                labels.append(r.get("date", ""))

        fig.add_trace(go.Scatter(
            x=hours, y=durations, mode="markers",
            marker=dict(color=color, size=10, symbol=symbol),
            name=direction, text=labels, hovertemplate="%{text}<br>Depart: %{x:.1f}h<br>Duration: %{y:.0f} min",
        ), row=1, col=1)

    fig.update_xaxes(title_text="Departure hour", row=1, col=1)
    fig.update_yaxes(title_text="Adjusted duration (min)", row=1, col=1)

    # --- Chart 2: Day of week avg duration bar (outbound only) ---
    outbound = [r for r in full if r["direction"] == "Home to Office"]
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_durations: Dict[str, List[float]] = {d: [] for d in day_order}
    for r in outbound:
        day = r.get("day_of_week", "")
        dur = r.get("adjusted_duration_mins") or r.get("duration_min")
        if day in day_durations and dur:
            day_durations[day].append(float(dur))

    days_present = [d for d in day_order if day_durations[d]]
    avgs = [sum(day_durations[d]) / len(day_durations[d]) for d in days_present]

    fig.add_trace(go.Bar(
        x=days_present, y=avgs,
        marker_color=ORANGE, name="Avg outbound duration",
        hovertemplate="%{x}: %{y:.0f} min",
        showlegend=False,
    ), row=1, col=2)

    fig.update_yaxes(title_text="Avg duration (min)", row=1, col=2)

    # --- Chart 3: Duration over time ---
    for direction, color in [("Home to Office", ORANGE), ("Office to Home", "#4ecdc4")]:
        d_trips = sorted(
            [r for r in full if r["direction"] == direction],
            key=lambda r: r.get("date", ""),
        )
        dates = [r.get("date", "") for r in d_trips]
        durs = []
        for r in d_trips:
            dur = r.get("adjusted_duration_mins") or r.get("duration_min")
            durs.append(float(dur) if dur else None)

        fig.add_trace(go.Scatter(
            x=dates, y=durs, mode="lines+markers",
            marker=dict(color=color, size=8),
            line=dict(color=color),
            name=direction, showlegend=False,
            hovertemplate="%{x}<br>%{y:.0f} min",
        ), row=2, col=1)

    fig.update_xaxes(title_text="Date", row=2, col=1)
    fig.update_yaxes(title_text="Adjusted duration (min)", row=2, col=1)

    # --- Chart 4: Mileage over time ---
    mileage_trips = sorted(
        [r for r in full if r.get("mileage_kmpl")],
        key=lambda r: r.get("date", ""),
    )
    m_dates = [r.get("date", "") for r in mileage_trips]
    m_vals = [float(r["mileage_kmpl"]) for r in mileage_trips]
    m_dirs = [r.get("direction", "") for r in mileage_trips]
    m_colors = [ORANGE if d == "Home to Office" else "#4ecdc4" for d in m_dirs]

    fig.add_trace(go.Scatter(
        x=m_dates, y=m_vals, mode="lines+markers",
        marker=dict(color=m_colors, size=8),
        line=dict(color=ORANGE),
        name="Mileage (km/l)", showlegend=False,
        hovertemplate="%{x}<br>%{y:.1f} km/l",
    ), row=2, col=2)

    fig.update_xaxes(title_text="Date", row=2, col=2)
    fig.update_yaxes(title_text="Mileage (km/l)", row=2, col=2)

    # --- Chart 5: Parking distribution pie ---
    parking_counts: Dict[str, int] = {}
    for r in full:
        p = r.get("parking", "Unknown") or "Unknown"
        parking_counts[p] = parking_counts.get(p, 0) + 1

    pie_labels = list(parking_counts.keys())
    pie_values = list(parking_counts.values())
    pie_colors = [ORANGE, "#4ecdc4", "#ffd166", "#999999", "#ef476f"]

    fig.add_trace(go.Pie(
        labels=pie_labels, values=pie_values,
        marker=dict(colors=pie_colors[:len(pie_labels)]),
        hole=0.4,
        hovertemplate="%{label}: %{value} trip(s)<extra></extra>",
    ), row=3, col=1)

    # --- Layout ---
    fig.update_layout(
        title=dict(
            text="commute-lens dashboard",
            font=dict(size=24, color=ORANGE),
            x=0.5,
        ),
        height=1100,
        template="plotly_dark",
        paper_bgcolor=DARK_BG,
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5,
            font=dict(size=12),
        ),
        margin=dict(t=100, b=40, l=60, r=40),
    )

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    fig.write_html(output_path, include_plotlyjs=True)


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
    outputs_dir = repo_root / Path(cfg["paths"]["outputs"])
    csv_path = str(outputs_dir / "master_trips.csv")

    print("Generating heatmap...")
    generate_heatmap(csv_path, gpx_dir, str(outputs_dir / "heatmap.html"))
    print("  Done.")

    print("Generating dashboard...")
    generate_dashboard(csv_path, str(outputs_dir / "dashboard.html"))
    print("  Done.")
