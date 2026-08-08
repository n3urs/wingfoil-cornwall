"""Tide predictions.

Primary source is the UKHO ADMIRALTY UK Tidal API (free "Discovery" tier:
register at https://admiraltyapi.portal.azure-api.net for a key, then put it in
.env as ADMIRALTY_API_KEY). It gives authoritative HW/LW events for 607 UK
stations, today plus six days.

If no key is set, or a station lookup fails, we fall back to deriving HW/LW from
Open-Meteo's hourly `sea_level_height_msl`. Less precise, zero setup.

Everything downstream consumes the same `TideSeries`, so the two paths are
interchangeable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from . import cache
from .config import Spot, haversine_km

UK = ZoneInfo("Europe/London")
ADMIRALTY_BASE = "https://admiraltyapi.azure-api.net/uktidalapi/api/V1"
STATIONS_TTL = 30 * 24 * 3600
EVENTS_TTL = 6 * 3600


@dataclass
class TideEvent:
    when: datetime  # tz-aware, Europe/London
    height: float  # metres
    is_high: bool


@dataclass
class TideState:
    height: float
    pct: float  # 0.0 at LW, 1.0 at HW
    rising: bool
    flow: float  # 0 at slack, 1 at peak mid-tide stream
    hours_to_hw: float  # signed distance to the NEAREST HW; negative = passed
    hours_to_lw: float
    next_hw: "TideEvent | None"  # the next HW/LW ahead, not the nearest
    next_lw: "TideEvent | None"


class TideSeries:
    """HW/LW events plus cosine interpolation between them."""

    def __init__(self, events: list[TideEvent], source: str, station: str = ""):
        self.events = sorted(events, key=lambda e: e.when)
        self.source = source
        self.station = station

    def __bool__(self) -> bool:
        return len(self.events) >= 2

    def at(self, t: datetime) -> TideState | None:
        if len(self.events) < 2:
            return None
        prev = nxt = None
        for a, b in zip(self.events, self.events[1:]):
            if a.when <= t <= b.when:
                prev, nxt = a, b
                break
        if prev is None:
            return None

        span = (nxt.when - prev.when).total_seconds()
        phase = 0.0 if span <= 0 else (t - prev.when).total_seconds() / span
        # Tide height between two turning points follows a half-cosine closely.
        height = prev.height + (nxt.height - prev.height) * (1 - math.cos(math.pi * phase)) / 2
        flow = math.sin(math.pi * phase)  # rate of change -> tidal stream strength

        lo, hi = min(prev.height, nxt.height), max(prev.height, nxt.height)
        pct = 0.5 if hi - lo < 1e-6 else (height - lo) / (hi - lo)
        rising = nxt.height > prev.height

        def _delta(is_high: bool) -> tuple[float, datetime | None]:
            future = [e for e in self.events if e.is_high == is_high and e.when >= t]
            past = [e for e in self.events if e.is_high == is_high and e.when < t]
            nxt_e = future[0] if future else None
            if nxt_e and past:
                fwd = (nxt_e.when - t).total_seconds() / 3600
                back = (t - past[-1].when).total_seconds() / 3600
                signed = fwd if fwd <= back else -back
            elif nxt_e:
                signed = (nxt_e.when - t).total_seconds() / 3600
            elif past:
                signed = -(t - past[-1].when).total_seconds() / 3600
            else:
                signed = 99.0
            return signed, nxt_e

        hw_delta, next_hw = _delta(True)
        lw_delta, next_lw = _delta(False)

        return TideState(
            height=round(height, 2),
            pct=round(pct, 3),
            rising=rising,
            flow=round(flow, 3),
            hours_to_hw=round(hw_delta, 2),
            hours_to_lw=round(lw_delta, 2),
            next_hw=next_hw,
            next_lw=next_lw,
        )


# --------------------------------------------------------------------------- #
# ADMIRALTY
# --------------------------------------------------------------------------- #

def _admiralty_stations(client: httpx.Client, key: str) -> list[dict]:
    hit = cache.get("admiralty:stations", STATIONS_TTL)
    if hit is not None:
        return hit
    r = client.get(
        f"{ADMIRALTY_BASE}/Stations",
        headers={"Ocp-Apim-Subscription-Key": key},
        timeout=25,
    )
    r.raise_for_status()
    feats = r.json().get("features", [])
    stations = []
    for f in feats:
        coords = (f.get("geometry") or {}).get("coordinates") or []
        props = f.get("properties") or {}
        if len(coords) >= 2 and props.get("Id"):
            stations.append(
                {"id": props["Id"], "name": props.get("Name", ""), "lat": coords[1], "lon": coords[0]}
            )
    cache.put("admiralty:stations", stations)
    return stations


def _admiralty_events(client: httpx.Client, key: str, station_id: str, days: int) -> list[TideEvent]:
    ck = f"admiralty:events:{station_id}:{days}"
    raw = cache.get(ck, EVENTS_TTL)
    if raw is None:
        r = client.get(
            f"{ADMIRALTY_BASE}/Stations/{station_id}/TidalEvents",
            params={"duration": days},
            headers={"Ocp-Apim-Subscription-Key": key},
            timeout=25,
        )
        r.raise_for_status()
        raw = r.json()
        cache.put(ck, raw)

    events = []
    for e in raw:
        dt_txt = e.get("DateTime")
        if not dt_txt or e.get("Height") is None:
            continue
        dt = datetime.fromisoformat(dt_txt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # Admiralty publishes UTC
        events.append(
            TideEvent(
                when=dt.astimezone(UK),
                height=float(e["Height"]),
                is_high=str(e.get("EventType", "")).lower().startswith("high"),
            )
        )
    return events


# --------------------------------------------------------------------------- #
# OPEN-METEO FALLBACK
# --------------------------------------------------------------------------- #

def _events_from_sea_level(times: list[str], heights: list[float | None]) -> list[TideEvent]:
    """Find HW/LW turning points in an hourly sea-level series.

    Refines each turning point with a quadratic fit through its three samples so
    the times land within a few minutes rather than on the hour.
    """
    pts = [
        (i, h) for i, h in enumerate(heights) if h is not None
    ]
    if len(pts) < 5:
        return []
    idx = {i: h for i, h in pts}
    events: list[TideEvent] = []
    for i in range(1, len(times) - 1):
        a, b, c = idx.get(i - 1), idx.get(i), idx.get(i + 1)
        if a is None or b is None or c is None:
            continue
        is_high = b >= a and b >= c and (b > a or b > c)
        is_low = b <= a and b <= c and (b < a or b < c)
        if not (is_high or is_low):
            continue
        denom = a - 2 * b + c
        offset = 0.0 if abs(denom) < 1e-9 else 0.5 * (a - c) / denom
        offset = max(-0.5, min(0.5, offset))
        peak = b - 0.25 * (a - c) * offset
        when = datetime.fromisoformat(times[i]).replace(tzinfo=UK) + timedelta(hours=offset)
        events.append(TideEvent(when=when, height=round(peak, 2), is_high=is_high))
    return events


# --------------------------------------------------------------------------- #
# ENTRY POINT
# --------------------------------------------------------------------------- #

def build_tides(
    spots: list[Spot],
    marine: dict,
    api_key: str | None,
    days: int = 7,
) -> tuple[dict[str, TideSeries], list[str]]:
    """Return (tide_series_by_spot_id, warnings)."""
    warnings: list[str] = []
    series: dict[str, TideSeries] = {}

    if api_key:
        try:
            with httpx.Client(follow_redirects=True) as client:
                stations = _admiralty_stations(client, api_key)
                if not stations:
                    raise RuntimeError("station list was empty")
                for spot in spots:
                    st = min(
                        stations,
                        key=lambda s: haversine_km(spot.lat, spot.lon, s["lat"], s["lon"]),
                    )
                    dist = haversine_km(spot.lat, spot.lon, st["lat"], st["lon"])
                    try:
                        events = _admiralty_events(client, api_key, st["id"], days)
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"Admiralty {spot.name}: {exc}")
                        continue
                    if events:
                        series[spot.id] = TideSeries(
                            events, "admiralty", f"{st['name']} ({dist:.0f} km)"
                        )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Admiralty API unavailable, falling back to Open-Meteo sea level: {exc}")
    else:
        warnings.append(
            "No ADMIRALTY_API_KEY set — using Open-Meteo sea level for tides. "
            "Register free at admiraltyapi.portal.azure-api.net for official UKHO tide times."
        )

    for spot in spots:
        if spot.id in series:
            continue
        hourly = marine.get(spot.id) or {}
        heights = hourly.get("sea_level_height_msl")
        times = hourly.get("time")
        if heights and times:
            events = _events_from_sea_level(times, heights)
            if len(events) >= 2:
                series[spot.id] = TideSeries(events, "open-meteo", "modelled sea level")

    missing = [s.name for s in spots if s.id not in series]
    if missing:
        warnings.append(
            "No tide data for " + ", ".join(missing) + " — scored on wind and swell only."
        )
    return series, warnings
