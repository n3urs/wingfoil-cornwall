"""Copernicus Marine Service — Northwest European Shelf wave model.

1.5km resolution, hourly, updated daily — a proper regional wave model, not
the ~9-25km global blend Open-Meteo's marine API gives you. Crucially it
separates the wave field into partitions:

  total   VHM0 / VMDR / VTPK        — combined sea state
  swell   VHM0_SW1 / VMDR_SW1 / VTM01_SW1  — the primary distant groundswell
  wind    VHM0_WW / VMDR_WW / VTM01_WW     — locally wind-generated chop

That split is what lets scoring.py tell "small clean groundswell, good day to
learn" apart from "today's wind has kicked the sea up", which a single blended
wave-height number can't.

Requires a free Copernicus Marine account and `copernicusmarine login` run
once in a terminal (stores credentials in ~/.copernicusmarine/, never touches
this app's config). If that hasn't been done, every call here fails fast and
the engine falls back to Open-Meteo marine data with a warning — same
degrade-gracefully posture as the tides module without an Admiralty key.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

from . import cache
from .config import Spot, haversine_km

DATASET_ID = "cmems_mod_nws_wav_anfc_1.5km_PT1H-i"
VARIABLES = [
    "VHM0", "VMDR", "VTPK",
    "VHM0_SW1", "VMDR_SW1", "VTM01_SW1",
    "VHM0_WW", "VMDR_WW", "VTM01_WW",
]
TTL = 6 * 3600  # the source updates once a day; this just avoids re-downloading


def _bbox(spots: list[Spot], margin: float = 0.08) -> tuple[float, float, float, float]:
    lats = [s.lat for s in spots]
    lons = [s.lon for s in spots]
    return (min(lons) - margin, max(lons) + margin, min(lats) - margin, max(lats) + margin)


def _download(spots: list[Spot], days: int) -> dict | None:
    """One subset call covering every spot, parsed into plain JSON-able data.

    Downloads a scratch NetCDF, reads it with xarray, then discards it — only
    the parsed series get cached, so the cache stays plain JSON like every
    other source in this app.
    """
    try:
        import copernicusmarine as cm
        import numpy as np
        import xarray as xr
    except ImportError:
        return None

    from datetime import datetime, timedelta, timezone

    from .tides import UK

    min_lon, max_lon, min_lat, max_lat = _bbox(spots)
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)

    with tempfile.TemporaryDirectory() as tmp:
        try:
            cm.subset(
                dataset_id=DATASET_ID,
                variables=VARIABLES,
                minimum_longitude=min_lon, maximum_longitude=max_lon,
                minimum_latitude=min_lat, maximum_latitude=max_lat,
                start_datetime=start.strftime("%Y-%m-%dT%H:%M:%S"),
                end_datetime=end.strftime("%Y-%m-%dT%H:%M:%S"),
                output_directory=tmp,
                output_filename="swell.nc",
                disable_progress_bar=True,
            )
        except Exception:  # noqa: BLE001 — no creds, no quota, no network: all fall back
            return None

        ds = xr.open_dataset(Path(tmp) / "swell.nc")

        lats = ds["latitude"].values
        lons = ds["longitude"].values
        # Copernicus times are UTC. Convert to Europe/London wall-clock and
        # format exactly like Open-Meteo's naive local strings ("...T20:00",
        # no seconds, no offset) so the two sources merge by matching string
        # keys rather than silently drifting an hour apart during BST.
        raw_utc = ds["time"].values.astype("datetime64[s]").tolist()
        times = [
            t.replace(tzinfo=timezone.utc).astimezone(UK).strftime("%Y-%m-%dT%H:%M")
            for t in raw_utc
        ]
        land = np.isnan(ds["VHM0"].isel(time=0).values)  # (lat, lon) mask, reused per spot

        out: dict[str, dict] = {}
        for spot in spots:
            # 1.5km grid cells right at the coast are often land; walk out to
            # the nearest wet cell rather than the geometrically nearest one —
            # the same "nearest usable station" problem tides.py solves.
            best_i = best_j = None
            best_d = float("inf")
            for i, la in enumerate(lats):
                for j, lo in enumerate(lons):
                    if land[i, j]:
                        continue
                    d = haversine_km(spot.lat, spot.lon, float(la), float(lo))
                    if d < best_d:
                        best_d, best_i, best_j = d, i, j
            if best_i is None:
                continue

            def series(name: str) -> list[float | None]:
                vals = ds[name].isel(latitude=best_i, longitude=best_j).values
                return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 3) for v in vals]

            out[spot.id] = {
                "time": times,
                "grid_km": round(best_d, 1),
                "wave_height": series("VHM0"),
                "wave_direction": series("VMDR"),
                "wave_period": series("VTPK"),
                "swell_wave_height": series("VHM0_SW1"),
                "swell_wave_direction": series("VMDR_SW1"),
                "swell_wave_period": series("VTM01_SW1"),
                "wind_wave_height": series("VHM0_WW"),
                "wind_wave_direction": series("VMDR_WW"),
                "wind_wave_period": series("VTM01_WW"),
            }
        return out


def fetch_swell(spots: list[Spot], days: int = 7) -> tuple[dict[str, dict], list[str]]:
    """Return (hourly_by_spot_id, warnings). Empty dict on any failure."""
    key = f"copernicus:{DATASET_ID}:{','.join(s.id for s in spots)}:{days}"
    hit = cache.get(key, TTL)
    if hit is not None:
        return hit, []

    data = _download(spots, days)
    if data:
        cache.put(key, data)
        return data, []

    stale, age = cache.get_stale(key)
    if stale is not None:
        return stale, [f"Copernicus Marine unavailable, using cached swell data {age / 3600:.0f}h old"]

    return {}, [
        "Copernicus Marine not available — using Open-Meteo swell instead. "
        "Run `copernicusmarine login` (see README) to enable the 1.5km regional wave model."
    ]
