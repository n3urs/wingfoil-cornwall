"""The algorithm.

Score for one spot at one hour:

    score = 100 * direction * wind * (0.60 + wind*(0.22*tide + 0.18*sea)) * gust * safety * tide_gate

`direction` and `wind` are multiplicative gates: no usable wind, or wind from a
direction the spot simply doesn't work in, means zero — not "a bit lower".
Tide and sea state modulate a session that's already possible, but that bonus
is itself scaled by wind quality — a perfect tide and clean swell shouldn't
buy back a score on a day the wind is only marginal. Wind is the thing that
decides whether you're going at all; everything else decides how good it is
once you're out there.
"""
from __future__ import annotations

import math
from datetime import datetime

from .config import Rider, Spot, arc_distance, compass, in_arc
from .tides import TideSeries, TideState
from .wing import recommend, wind_quality

TAPER_DEG = 15.0  # how far outside an arc the score bleeds to zero

BANDS = [(72, "go"), (55, "good"), (38, "marginal"), (0, "no")]


def band(score: float) -> str:
    for threshold, name in BANDS:
        if score >= threshold:
            return name
    return "no"


# --------------------------------------------------------------------------- #

def direction_score(spot: Spot, wind_dir: float) -> tuple[float, str]:
    """0-1 for how well the wind direction suits the spot, plus a one-liner."""
    w = spot.wind
    label = compass(wind_dir)

    if in_arc_any(wind_dir, w.ideal):
        base, why = 1.0, f"{label} is the direction this spot wants"
    elif in_arc_any(wind_dir, w.ok):
        base, why = 0.62, f"{label} works here but isn't the best"
    else:
        d = min(arc_distance(wind_dir, w.ideal), arc_distance(wind_dir, w.ok))
        if d >= TAPER_DEG:
            return 0.0, f"{label} — wrong direction for this spot"
        base = 0.62 * (1 - d / TAPER_DEG)
        why = f"{label} is on the edge of what works"

    if in_arc_any(wind_dir, w.gusty):
        base *= 0.85
        why += ", expect it gusty"

    return base, why


def in_arc_any(deg: float, arcs: list[list[float]]) -> bool:
    return any(in_arc(deg, a) for a in arcs)


def safety_factor(spot: Spot, wind_dir: float) -> tuple[float, list[str]]:
    """Hard cap for genuinely dangerous setups."""
    warns: list[str] = []
    factor = 1.0
    if in_arc_any(wind_dir, spot.wind.offshore):
        factor = 0.25
        warns.append(f"OFFSHORE ({compass(wind_dir)}) — blows you out to sea")
    return factor, warns


def tide_score(spot: Spot, ts: TideState | None) -> tuple[float, float, list[str], list[str]]:
    """Split the tide into a preference and a veto.

    Returns (preference, gate, notes, warnings).

    preference  0-1, "this spot is nicer at mid tide" — nudges the score.
    gate        0-1, "there is no beach here at high water" / "the ebb will
                sweep you out to sea" — multiplies the whole score, because
                these are reasons the session cannot happen at all.
    """
    if ts is None:
        return 0.6, 1.0, [], []

    rule = spot.tide
    notes: list[str] = []
    warns: list[str] = []

    if rule.best == "mid":
        pref = 1.0 - 1.6 * abs(ts.pct - 0.5)
    elif rule.best == "low":
        pref = 1.0 - 1.1 * max(0.0, ts.pct - 0.15)
    elif rule.best == "high":
        pref = 1.0 - 1.1 * max(0.0, 0.85 - ts.pct)
    else:
        pref = 0.9
    pref = max(0.0, min(1.0, pref))

    gate = 1.0

    if rule.avoid_hw_hours and abs(ts.hours_to_hw) < rule.avoid_hw_hours:
        closeness = 1 - abs(ts.hours_to_hw) / rule.avoid_hw_hours
        gate *= max(0.05, (1 - closeness) ** 1.5)
        warns.append(f"{abs(ts.hours_to_hw):.1f}h from high water — no beach here")

    if rule.avoid_lw_hours and abs(ts.hours_to_lw) < rule.avoid_lw_hours:
        closeness = 1 - abs(ts.hours_to_lw) / rule.avoid_lw_hours
        gate *= max(0.05, (1 - closeness) ** 1.5)
        warns.append(f"{abs(ts.hours_to_lw):.1f}h from low water — it closes out")

    if rule.rising_only and not ts.rising:
        gate *= 0.08
        warns.append("EBB TIDE — this spot is rising-tide only")

    if rule.flow_penalty:
        gate *= 1 - rule.flow_penalty * ts.flow
        if ts.flow > 0.7 and not ts.rising:
            warns.append("Peak ebb — strong current running out")

    return pref, max(0.0, min(1.0, gate)), notes, warns


DIR_FULL_DEG = 50.0   # swell within this of `faces` counts at full strength
DIR_ZERO_DEG = 120.0  # beyond this it's arriving from behind the headland
DIR_FLOOR = 0.20       # real swell still wraps in via diffraction — never fully zero


def swell_direction_factor(spot: Spot, swell_dir: float | None) -> tuple[float, str | None]:
    """0-1: does this swell actually point at the bay, or is the coastline in the way.

    Neutral (1.0) whenever we don't have the geometry or the direction to judge —
    a spot with no `faces` set, or data with no direction, isn't penalised for
    something we can't check.
    """
    if spot.faces is None or swell_dir is None:
        return 1.0, None
    diff = abs((swell_dir - spot.faces + 180) % 360 - 180)
    if diff <= DIR_FULL_DEG:
        return 1.0, None
    if diff >= DIR_ZERO_DEG:
        return DIR_FLOOR, f"{compass(swell_dir)} swell is arriving from behind the headland here"
    factor = 1.0 - (1.0 - DIR_FLOOR) * (diff - DIR_FULL_DEG) / (DIR_ZERO_DEG - DIR_FULL_DEG)
    note = f"{compass(swell_dir)} swell is oblique here, not square onto the beach" if factor < 0.6 else None
    return factor, note


def sea_score(
    spot: Spot,
    rider: Rider,
    wave_m: float | None,
    period_s: float | None,
    swell_dir: float | None = None,
    wind_wave_m: float | None = None,
) -> tuple[float, list[str], bool, float]:
    """0-1 for sea state, notes, whether it's a wave-learning day, and the
    directional factor (returned separately so score_hour can show it)."""
    if wave_m is None or spot.swell_exposure < 0.1:
        return 0.85, [], False, 1.0

    dir_factor, dir_note = swell_direction_factor(spot, swell_dir)
    felt = wave_m * spot.swell_exposure * dir_factor
    notes: list[str] = [dir_note] if dir_note else []
    school = False

    if spot.character == "beach_break":
        # Comfortable size scales with how confident the rider is in waves.
        comfy = 0.9 + 2.4 * rider.wave_confidence
        if felt <= comfy:
            s = 0.75 + 0.25 * min(1.0, felt / max(comfy, 0.1))
        else:
            s = max(0.1, 1.0 - (felt - comfy) / (comfy + 1.0))
            notes.append(f"{wave_m:.1f}m swell is big for your wave experience")

        # A wind-sea-dominated day isn't "clean" no matter how good the height
        # and period look — the local wind has churned it up on top.
        choppy = wind_wave_m is not None and wind_wave_m > max(0.35, wave_m * 0.4)
        if (
            spot.wave_school
            and rider.school_min_wave <= wave_m <= rider.school_max_wave
            and (period_s or 0) >= rider.school_min_period
            and dir_factor >= 0.6
        ):
            if choppy:
                notes.append(f"{wave_m:.1f}m @ {period_s:.0f}s would be clean, but local wind chop is spoiling it")
            else:
                school = True
                notes.append(f"Clean {wave_m:.1f}m @ {period_s:.0f}s — good day to learn waves")
    else:
        # Flat-water spot: swell is pure nuisance.
        s = max(0.15, 1.0 - felt / 1.5)
        if felt > 0.8:
            notes.append(f"{wave_m:.1f}m swell is spoiling the flat water")

    return max(0.0, min(1.0, s)), notes, school, dir_factor


def gust_factor(rider: Rider, mean_kn: float, gust_kn: float | None) -> tuple[float, list[str]]:
    if not gust_kn or mean_kn < 1:
        return 1.0, []
    ratio = gust_kn / mean_kn
    if ratio <= 1.3:
        return 1.0, []
    excess = min(ratio - 1.3, 0.7)
    penalty = excess * (1.0 - rider.gust_tolerance) * 0.9 + excess * 0.15
    notes = []
    if ratio > 1.5:
        notes.append(f"Gusty — {mean_kn:.0f}kn averaging, {gust_kn:.0f}kn gusts")
    return max(0.3, 1.0 - penalty), notes


# --------------------------------------------------------------------------- #

def score_hour(
    spot: Spot,
    rider: Rider,
    when: datetime,
    wind_kn: float | None,
    gust_kn: float | None,
    wind_dir: float | None,
    wave_m: float | None,
    period_s: float | None,
    tide: TideState | None,
    swell_dir: float | None = None,
    wind_wave_m: float | None = None,
    swell_source: str | None = None,
) -> dict:
    """Score one spot at one hour. Returns everything the UI needs."""
    if wind_kn is None or wind_dir is None:
        return {"time": when.isoformat(), "score": 0, "band": "no", "reasons": ["No forecast data"]}

    reasons: list[str] = []
    warns: list[str] = []

    dir_s, dir_why = direction_score(spot, wind_dir)
    wind_s = wind_quality(rider, wind_kn, gust_kn)
    tide_pref, tide_gate, tide_notes, tide_warns = tide_score(spot, tide)
    sea_s, sea_notes, school, sea_dir_factor = sea_score(
        spot, rider, wave_m, period_s, swell_dir=swell_dir, wind_wave_m=wind_wave_m
    )
    gust_s, gust_notes = gust_factor(rider, wind_kn, gust_kn)
    safe_s, safe_warns = safety_factor(spot, wind_dir)

    # The tide/sea bonus is gated by wind quality, not just added on top of it —
    # a perfect tide and clean swell shouldn't buy back a score on a day the
    # wind itself is marginal. At wind_s=1 this is unchanged from a flat
    # 0.60 + 0.22*tide + 0.18*sea; as wind_s drops toward the wing's floor,
    # the bonus shrinks with it, because swell and tide don't matter if you
    # can't actually get powered up.
    quality = 0.60 + wind_s * (0.22 * tide_pref + 0.18 * sea_s)
    raw = 100.0 * dir_s * wind_s * quality * gust_s * safe_s * tide_gate
    score = int(round(max(0.0, min(100.0, raw))))

    rec = recommend(rider, wind_kn, gust_kn)

    if wind_s <= 0:
        reasons.append(rec["text"])
    else:
        reasons.append(dir_why)
    reasons += tide_notes + sea_notes + gust_notes
    warns += safe_warns + tide_warns

    return {
        "time": when.isoformat(),
        "hour": when.hour,
        "score": score,
        "band": band(score),
        "wind_kn": round(wind_kn, 1),
        "gust_kn": round(gust_kn, 1) if gust_kn is not None else None,
        "wind_dir": round(wind_dir),
        "wind_dir_txt": compass(wind_dir),
        "wave_m": round(wave_m, 1) if wave_m is not None else None,
        "period_s": round(period_s) if period_s is not None else None,
        "swell_dir": round(swell_dir) if swell_dir is not None else None,
        "swell_dir_txt": compass(swell_dir) if swell_dir is not None else None,
        "wind_wave_m": round(wind_wave_m, 1) if wind_wave_m is not None else None,
        "swell_source": swell_source,
        "tide": None
        if tide is None
        else {
            "height": tide.height,
            "pct": tide.pct,
            "rising": tide.rising,
            # Signed distance to the NEAREST HW/LW — this is what the
            # avoid_hw_hours / avoid_lw_hours rules key off.
            "hours_to_hw": tide.hours_to_hw,
            "hours_to_lw": tide.hours_to_lw,
            # The turn that's actually coming up, for display.
            "next_turn": _next_turn(when, tide),
            "state": _tide_word(tide),
        },
        "wing": rec,
        "wave_school": school,
        "reasons": reasons,
        "warnings": warns,
        "parts": {
            "direction": round(dir_s, 2),
            "wind": round(wind_s, 2),
            "tide": round(tide_pref, 2),
            "tide_gate": round(tide_gate, 2),
            "sea": round(sea_s, 2),
            "sea_direction": round(sea_dir_factor, 2),
            "gust": round(gust_s, 2),
            "safety": round(safe_s, 2),
        },
    }


def _next_turn(when: datetime, ts: TideState) -> dict | None:
    """The next turn of the tide: HW if it's flooding, LW if it's ebbing.

    Distinct from `hours_to_hw`, which is the nearest HW in either direction.
    """
    target = ts.next_hw if ts.rising else ts.next_lw
    if target is None:
        return None
    return {
        "type": "HW" if ts.rising else "LW",
        "in_h": round((target.when - when).total_seconds() / 3600, 2),
    }


def _tide_word(ts: TideState) -> str:
    if ts.pct > 0.85:
        return "high"
    if ts.pct < 0.15:
        return "low"
    return "mid, pushing" if ts.rising else "mid, dropping"


def find_windows(hours: list[dict], min_score: int = 55) -> list[dict]:
    """Every run of consecutive hours at or above `min_score`, best first.

    Ranked by peak score, then by length — a 4-hour window peaking at 80 beats
    a 6-hour one peaking at 60, but between equals you want the longer session.
    """
    runs, current = [], None
    for h in hours:
        if h["score"] >= min_score:
            if current is None:
                current = {"start": h, "end": h, "peak": h}
            else:
                current["end"] = h
                if h["score"] > current["peak"]["score"]:
                    current["peak"] = h
        else:
            if current:
                runs.append(current)
            current = None
    if current:
        runs.append(current)

    out = []
    for r in runs:
        start = datetime.fromisoformat(r["start"]["time"])
        end = datetime.fromisoformat(r["end"]["time"])
        out.append({
            "from": start.strftime("%H:%M"),
            "to": end.replace(minute=59).strftime("%H:%M"),
            "from_hour": start.hour,
            "to_hour": end.hour,
            "hours": _length(r),
            "peak_score": r["peak"]["score"],
            "peak_hour": r["peak"]["hour"],
            "wing": r["peak"]["wing"],
        })
    out.sort(key=lambda w: (-w["peak_score"], -w["hours"]))
    return out


def best_window(hours: list[dict], min_score: int = 55) -> dict | None:
    windows = find_windows(hours, min_score)
    return windows[0] if windows else None


def _length(run: dict) -> int:
    a = datetime.fromisoformat(run["start"]["time"])
    b = datetime.fromisoformat(run["end"]["time"])
    return int((b - a).total_seconds() / 3600) + 1
