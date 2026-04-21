#!/usr/bin/env python3
"""
weather.py — Fetch historical hourly weather from Open-Meteo.

Uses the forecast API for dates within 92 days, archive API for older dates.
Results cached in outputs/weather_cache.json keyed by date + rounded location.
"""

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional
import urllib.request
import urllib.parse

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARS = "temperature_2m,precipitation,weather_code"

WMO_CONDITIONS = {
    0: "Clear",
    1: "Clear", 2: "Cloudy", 3: "Cloudy",
    45: "Cloudy", 48: "Cloudy",
    51: "Rain", 53: "Rain", 55: "Rain",
    56: "Rain", 57: "Rain",
    61: "Rain", 63: "Rain", 65: "Heavy Rain",
    66: "Rain", 67: "Heavy Rain",
    71: "Rain", 73: "Rain", 75: "Heavy Rain",
    77: "Rain",
    80: "Rain", 81: "Rain", 82: "Heavy Rain",
    85: "Rain", 86: "Heavy Rain",
    95: "Heavy Rain", 96: "Heavy Rain", 99: "Heavy Rain",
}


def load_cache(cache_path: str) -> Dict:
    p = Path(cache_path)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache_path: str, cache: Dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def _cache_key(lat: float, lon: float, date_str: str) -> str:
    return f"{round(lat, 2)}_{round(lon, 2)}_{date_str}"


def _fetch_hourly(lat: float, lon: float, date_str: str) -> Optional[Dict]:
    days_ago = (date.today() - date.fromisoformat(date_str)).days
    base_url = FORECAST_URL if days_ago <= 90 else ARCHIVE_URL

    params = urllib.parse.urlencode({
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "hourly": HOURLY_VARS,
        "start_date": date_str,
        "end_date": date_str,
        "timezone": "Asia/Kolkata",
    })
    url = f"{base_url}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("hourly")
    except Exception as exc:
        print(f"  [weather] WARNING: fetch failed for {date_str}: {exc}")
        return None


def get_weather_for_trip(
    lat: float,
    lon: float,
    departure_time_str: str,
    cache: Dict,
) -> Dict:
    empty = {
        "weather_condition": None,
        "temp_c": None,
        "precipitation_mm": None,
    }

    try:
        dt_str = departure_time_str.replace("UTC+05:30", "+05:30").replace("UTC+5:30", "+05:30")
        departure_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S %z")
    except Exception:
        return empty

    date_str = departure_dt.strftime("%Y-%m-%d")
    hour = departure_dt.hour

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

    idx = None
    for i, t in enumerate(times):
        if len(t) >= 13 and t[11:13] == f"{hour:02d}":
            idx = i
            break
    if idx is None:
        idx = 0

    def _get(field: str):
        vals = hourly.get(field, [])
        return vals[idx] if idx < len(vals) else None

    wmo_code = _get("weather_code")
    condition = WMO_CONDITIONS.get(wmo_code, "Unknown") if wmo_code is not None else None

    return {
        "weather_condition": condition,
        "temp_c": _get("temperature_2m"),
        "precipitation_mm": _get("precipitation"),
    }
