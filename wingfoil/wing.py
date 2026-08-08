"""Wing size recommendation for the rider's actual quiver."""
from __future__ import annotations

from .config import Rider, Wing


def effective_wind(mean_kn: float, gust_kn: float | None) -> float:
    """Gusts do the sizing as much as the average does — you rig for the top end.

    Weighted toward the mean, but a big gust spread pulls the number up.
    """
    if gust_kn is None or gust_kn <= mean_kn:
        return mean_kn
    return 0.65 * mean_kn + 0.35 * gust_kn


def _fit(wing: Wing, wind: float) -> float:
    """0-1 how well this wing suits that wind. 1.0 across the sweet spot.

    Outside the sweet spot the score falls to 0.5 at the absolute edge rather
    than to 0 — owning a wing that covers the wind at all means the session
    happens, it just isn't perfect. Past min/max is a genuine 0.
    """
    if wind < wing.min or wind > wing.max:
        return 0.0
    if wing.lo <= wind <= wing.hi:
        return 1.0
    if wind < wing.lo:
        return 0.5 + 0.5 * (wind - wing.min) / max(wing.lo - wing.min, 1e-6)
    return 0.5 + 0.5 * (wing.max - wind) / max(wing.max - wing.hi, 1e-6)


def recommend(rider: Rider, mean_kn: float, gust_kn: float | None) -> dict:
    """Pick a wing and describe how it'll feel."""
    eff = effective_wind(mean_kn, gust_kn)
    scored = sorted(
        ((_fit(w, eff), w) for w in rider.wings), key=lambda p: p[0], reverse=True
    )
    best_fit, best = scored[0]

    smallest = min(rider.wings, key=lambda w: w.min)
    biggest = max(rider.wings, key=lambda w: w.max)

    if best_fit <= 0:
        if eff < smallest.min:
            return {
                "size": None,
                "fit": 0.0,
                "text": f"Not enough wind — {eff:.0f}kn needs a 7m+",
                "state": "too_light",
            }
        return {
            "size": None,
            "fit": 0.0,
            "text": f"Too much — {eff:.0f}kn is over the {biggest.size if biggest.max > smallest.max else smallest.size}",
            "state": "too_strong",
        }

    if best_fit >= 1.0:
        state, blurb = "ideal", "nicely powered"
    elif eff < best.lo:
        state, blurb = "under", "marginal, keep it moving"
    else:
        state, blurb = "over", "lit up, hang on"

    alt = None
    if len(scored) > 1 and scored[1][0] > 0.35:
        alt = scored[1][1].size

    return {
        "size": best.size,
        "fit": round(best_fit, 2),
        "alt": alt,
        "text": f"{best.size} — {blurb}",
        "state": state,
        "effective_kn": round(eff, 1),
    }


def wind_quality(rider: Rider, mean_kn: float, gust_kn: float | None) -> float:
    """0-1 score for "is there a usable amount of wind for my quiver"."""
    eff = effective_wind(mean_kn, gust_kn)
    best = max(_fit(w, eff) for w in rider.wings)
    if best <= 0:
        return 0.0
    # Being overpowered matters less to a rider happy in strong wind.
    top = max(w.hi for w in rider.wings)
    if eff > top:
        over = (eff - top) / 10.0
        best = max(best, 1.0 - over * (1.0 - rider.high_wind_tolerance) - over * 0.15)
    return max(0.0, min(1.0, best))
