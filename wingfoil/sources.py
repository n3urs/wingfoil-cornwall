"""Open-Meteo weather + marine forecasts.

Both endpoints accept comma-separated coordinate lists, so all eight spots come
back in two HTTP calls regardless of how many spots you add.
"""
from __future__ import annotations

import httpx

from . import cache
from .config import Spot

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

WEATHER_VARS = [
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "temperature_2m",
    "precipitation",
    "weather_code",
]
MARINE_VARS = [
    "wave_height",
    "wave_period",
    "wave_direction",
    "swell_wave_height",
    "swell_wave_period",
    "swell_wave_direction",
    "sea_level_height_msl",
]

WEATHER_TTL = 30 * 60
MARINE_TTL = 60 * 60


def _as_list(payload):
    """Open-Meteo returns a bare object for one location, a list for many."""
    return payload if isinstance(payload, list) else [payload]


def _fetch(client: httpx.Client, url: str, params: dict, key: str, ttl: int):
    hit = cache.get(key, ttl)
    if hit is not None:
        return hit, None
    try:
        r = client.get(url, params=params, timeout=25)
        r.raise_for_status()
        data = _as_list(r.json())
        cache.put(key, data)
        return data, None
    except Exception as exc:  # noqa: BLE001 - degrade to stale cache, never crash
        stale, age = cache.get_stale(key)
        if stale is not None:
            return stale, f"{url.split('/')[2]}: using cached data {age / 60:.0f} min old ({exc})"
        return None, f"{url.split('/')[2]}: {exc}"


def fetch_forecasts(spots: list[Spot], days: int = 7) -> tuple[dict, dict, list[str], bool]:
    """Return (weather_by_spot_id, marine_by_spot_id, warnings, weather_live).

    `weather_live` is False when the weather call had to fall back to stale
    cache (or failed outright) — used to drive the status box on the
    dashboard, distinct from the free-text warnings banner.
    """
    lats = ",".join(f"{s.lat:.4f}" for s in spots)
    lons = ",".join(f"{s.lon:.4f}" for s in spots)
    warnings: list[str] = []

    weather_params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": ",".join(WEATHER_VARS),
        "wind_speed_unit": "kn",
        "timezone": "Europe/London",
        "forecast_days": days,
    }
    # One extra day of marine data. Tide turning points are derived by finding
    # local extrema, which can't resolve at the very end of a series — without
    # the overhang the final evening of the forecast has no tide.
    marine_params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": ",".join(MARINE_VARS),
        "timezone": "Europe/London",
        "forecast_days": min(days + 1, 16),
    }

    with httpx.Client(follow_redirects=True) as client:
        weather, w_err = _fetch(
            client, WEATHER_URL, weather_params, f"weather:{lats}:{days}", WEATHER_TTL
        )
        marine, m_err = _fetch(
            client, MARINE_URL, marine_params, f"marine:{lats}:{days + 1}", MARINE_TTL
        )
        if marine is None:
            # sea_level_height_msl isn't available for every grid point; retry without.
            marine_params["hourly"] = ",".join(v for v in MARINE_VARS if v != "sea_level_height_msl")
            marine, m_err = _fetch(
                client, MARINE_URL, marine_params, f"marine-nosl:{lats}:{days + 1}", MARINE_TTL
            )

    if w_err:
        warnings.append(w_err)
    if m_err:
        warnings.append(m_err)

    if weather is None:
        raise RuntimeError(f"Could not fetch wind forecast and no cache available. {w_err}")

    by_weather = {s.id: weather[i]["hourly"] for i, s in enumerate(spots) if i < len(weather)}
    by_marine = {}
    if marine:
        for i, s in enumerate(spots):
            if i < len(marine) and "hourly" in marine[i]:
                by_marine[s.id] = marine[i]["hourly"]

    return by_weather, by_marine, warnings, w_err is None
