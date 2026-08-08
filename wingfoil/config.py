"""Load and validate the spot / rider config."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

COMPASS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def compass(deg: float) -> str:
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def in_arc(deg: float, arc: list[float]) -> bool:
    """True if `deg` falls inside [from, to] measured clockwise (may wrap 0)."""
    start, end = arc[0] % 360, arc[1] % 360
    d = deg % 360
    if start <= end:
        return start <= d <= end
    return d >= start or d <= end


def arc_distance(deg: float, arcs: list[list[float]]) -> float:
    """Smallest angular distance from `deg` to the nearest edge of any arc.

    Returns 0.0 when inside an arc.
    """
    if not arcs:
        return 360.0
    best = 360.0
    for arc in arcs:
        if in_arc(deg, arc):
            return 0.0
        for edge in arc:
            diff = abs((deg - edge + 180) % 360 - 180)
            best = min(best, diff)
    return best


@dataclass
class TideRule:
    best: str = "any"
    avoid_hw_hours: float = 0.0
    avoid_lw_hours: float = 0.0
    rising_only: bool = False
    flow_penalty: float = 0.0


@dataclass
class WindRule:
    ideal: list[list[float]] = field(default_factory=list)
    ok: list[list[float]] = field(default_factory=list)
    gusty: list[list[float]] = field(default_factory=list)
    offshore: list[list[float]] = field(default_factory=list)


@dataclass
class Spot:
    id: str
    name: str
    lat: float
    lon: float
    character: str
    swell_exposure: float
    wave_school: bool
    wind: WindRule
    tide: TideRule
    notes: list[str] = field(default_factory=list)
    # Compass bearing the open water lies in — the direction a swell arrives
    # FROM to hit this beach square-on. None for spots sheltered enough that
    # swell direction doesn't matter (estuaries, near-zero swell_exposure).
    faces: float | None = None


@dataclass
class Wing:
    size: str
    min: float
    lo: float
    hi: float
    max: float


@dataclass
class Rider:
    weight_kg: float
    foil_label: str
    wings: list[Wing]
    high_wind_tolerance: float
    wave_confidence: float
    gust_tolerance: float
    school_min_wave: float
    school_max_wave: float
    school_min_period: float


def load_spots(path: Path | None = None) -> list[Spot]:
    raw = yaml.safe_load((path or CONFIG_DIR / "spots.yaml").read_text())
    spots = []
    for s in raw["spots"]:
        spots.append(
            Spot(
                id=s["id"],
                name=s["name"],
                lat=float(s["lat"]),
                lon=float(s["lon"]),
                character=s.get("character", "beach_break"),
                swell_exposure=float(s.get("swell_exposure", 1.0)),
                wave_school=bool(s.get("wave_school", False)),
                wind=WindRule(
                    ideal=s["wind"].get("ideal") or [],
                    ok=s["wind"].get("ok") or [],
                    gusty=s["wind"].get("gusty") or [],
                    offshore=s["wind"].get("offshore") or [],
                ),
                tide=TideRule(
                    best=s["tide"].get("best", "any"),
                    avoid_hw_hours=float(s["tide"].get("avoid_hw_hours", 0)),
                    avoid_lw_hours=float(s["tide"].get("avoid_lw_hours", 0)),
                    rising_only=bool(s["tide"].get("rising_only", False)),
                    flow_penalty=float(s["tide"].get("flow_penalty", 0)),
                ),
                notes=s.get("notes") or [],
                faces=float(s["faces"]) if s.get("faces") is not None else None,
            )
        )
    return spots


def load_rider(path: Path | None = None) -> Rider:
    raw = yaml.safe_load((path or CONFIG_DIR / "rider.yaml").read_text())
    ab = raw.get("ability", {})
    ws = raw.get("wave_school", {})
    return Rider(
        weight_kg=float(raw["weight_kg"]),
        foil_label=str(raw["foil"]["label"]),
        wings=[Wing(**w) for w in raw["wings"]],
        high_wind_tolerance=float(ab.get("high_wind_tolerance", 1.0)),
        wave_confidence=float(ab.get("wave_confidence", 0.5)),
        gust_tolerance=float(ab.get("gust_tolerance", 0.7)),
        school_min_wave=float(ws.get("min_wave_m", 0.6)),
        school_max_wave=float(ws.get("max_wave_m", 1.6)),
        school_min_period=float(ws.get("min_period_s", 8)),
    )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
