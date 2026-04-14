#!/usr/bin/env python3
"""
weather.py — Fetch hourly weather from Open-Meteo for commute trips.

Uses the Open-Meteo forecast API (free, no API key required).
Supports up to 92 days of historical data via the past_days parameter.
Results cached in outputs/weather_cache.json to avoid re-fetching.

Cache key: "{rounded_lat}_{rounded_lon}_{date}"
Each entry stores the raw hourly payload for the day.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import urllib.request
import urllib.parse

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = (
    "temperature_2m,relative_humidity_2m,rain,windspeed_10m,weathercode"
)


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def load_cache(cache_path: str) -> Dict:
    """Load the local weather cache from disk. Returns empty dict if absent."""
    p = Path(cache_path)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache_path: str, cache: Dict) -> None:
    """Persist the weather cache to disk."""
    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def _cache_key(lat: float, lon: float, date: str) -> str:
    """Cache key rounded to 2 decimal places so nearby points share an entry."""
    return f"{round(lat, 2)}_{round(lon, 2)}_{date}"


# ---------------------------------------------------------------------------
# API fetch
# ---------------------------------------------------------------------------

def _fetch_hourly(lat: float, lon: float, date: str) -> Optional[Dict]:
    """
    Fetch hourly weather for a single date from Open-Meteo.

    Returns the 'hourly' sub-dict from the API response, or None on failure.
    """
    params = urllib.parse.urlencode({
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "hourly": HOURLY_VARS,
        "start_date": date,
        "end_date": date,
        "timezone": "Asia/Kolkata",
    })
    url = f"{FORECAST_URL}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("hourly")
    except Exception as exc:
        print(
            f"  [weather] WARNING: could not fetch {date} "
            f"at ({lat:.2f}, {lon:.2f}): {exc}"
        )
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_weather_for_trip(
    lat: float,
    lon: float,
    departure_time_str: str,
    cache: Dict,
) -> Dict[str, Optional[float]]:
    """
    Return weather conditions at departure time for a trip.

    Parameters
    ----------
    lat, lon           : trip departure coordinates (HOME lat/lon used)
    departure_time_str : formatted string from parser, e.g.
                         "2026-04-14 19:00:37 UTC+05:30"
    cache              : mutable dict loaded by load_cache(); updated in-place

    Returns
    -------
    dict with keys: temp_c, humidity_pct, rain_mm, wind_kmh, weather_code
    All values are None if data is unavailable.
    """
    empty: Dict[str, Optional[float]] = {
        "temp_c": None,
        "humidity_pct": None,
        "rain_mm": None,
        "wind_kmh": None,
        "weather_code": None,
    }

    # Parse departure time
    try:
        # "2026-04-14 19:00:37 UTC+05:30" -> "2026-04-14 19:00:37 +05:30"
        dt_str = departure_time_str.replace("UTC+05:30", "+05:30").replace("UTC+5:30", "+05:30")
        departure_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S %z")
    except Exception:
        return empty

    date_str = departure_dt.strftime("%Y-%m-%d")
    hour = departure_dt.hour

    # Fetch or retrieve from cache
    key = _cache_key(lat, lon, date_str)
    if key not in cache:
        hourly = _fetch_hourly(lat, lon, date_str)
        if hourly is None:
            return empty
        cache[key] = hourly

    hourly = cache[key]
    times = hourly.get("time", [])
    if not times:
        return empty

    # Find index for the departure hour
    # Open-Meteo time format: "2026-04-14T19:00"
    idx = None
    for i, t in enumerate(times):
        if len(t) >= 13 and t[11:13] == f"{hour:02d}":
            idx = i
            break

    if idx is None:
        # Fall back to closest hour
        min_diff = float("inf")
        idx = 0
        for i, t in enumerate(times):
            try:
                t_hour = int(t[11:13])
                diff = abs(t_hour - hour)
                if diff < min_diff:
                    min_diff = diff
                    idx = i
            except (ValueError, IndexError):
                pass

    def _get(field: str) -> Optional[float]:
        vals = hourly.get(field, [])
        val = vals[idx] if idx < len(vals) else None
        return val  # may be None if API returned null

    return {
        "temp_c": _get("temperature_2m"),
        "humidity_pct": _get("relative_humidity_2m"),
        "rain_mm": _get("rain"),
        "wind_kmh": _get("windspeed_10m"),
        "weather_code": _get("weathercode"),
    }
