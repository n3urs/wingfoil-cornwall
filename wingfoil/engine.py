"""Pulls the sources together and builds the dashboard payload."""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime

from .config import Rider, Spot, load_rider, load_spots
from .scoring import best_window, find_windows, score_hour
from .sources import fetch_forecasts
from .swell import fetch_swell
from .tides import UK, build_tides


def _series(hourly: dict, name: str) -> list:
    return hourly.get(name) or []


def _at(seq: list, i: int):
    if i < len(seq):
        v = seq[i]
        return None if v is None else float(v)
    return None


def parse_demo(spec: str | None) -> dict | None:
    """`?demo=18,225,1.2,10` -> pretend it's 18kn from 225 deg, 1.2m at 10s.

    Handy for seeing what a real day looks like when the forecast is flat calm,
    and for checking spot rules after editing spots.yaml. Direction rotates
    slowly across the week so the 7-day grid stays interesting.
    """
    if not spec:
        return None
    try:
        parts = [float(p) for p in spec.split(",")]
    except ValueError:
        return None
    keys = ["wind_kn", "wind_dir", "wave_m", "period_s"]
    return dict(zip(keys, parts))


def build_dashboard(days: int = 7, demo: dict | None = None) -> dict:
    spots: list[Spot] = load_spots()
    rider: Rider = load_rider()

    weather, marine, warnings, weather_live = fetch_forecasts(spots, days=days)
    tide_series, tide_warnings = build_tides(
        spots, marine, os.environ.get("ADMIRALTY_API_KEY"), days=days
    )
    warnings += tide_warnings

    # Copernicus Marine's 1.5km regional wave model, when available — falls
    # back to the Open-Meteo marine data already fetched above per hour.
    swell_data, swell_warnings = fetch_swell(spots, days=days)
    warnings += swell_warnings

    now = datetime.now(UK)
    out_spots = []

    for spot in spots:
        w = weather.get(spot.id)
        if not w:
            continue
        m = marine.get(spot.id) or {}
        times = _series(w, "time")
        speeds = _series(w, "wind_speed_10m")
        dirs = _series(w, "wind_direction_10m")
        gusts = _series(w, "wind_gusts_10m")
        temps = _series(w, "temperature_2m")
        rain = _series(w, "precipitation")

        waves = _series(m, "wave_height")
        periods = _series(m, "wave_period")
        swell_h = _series(m, "swell_wave_height")
        swell_p = _series(m, "swell_wave_period")
        swell_dirs = _series(m, "swell_wave_direction")
        # Marine runs a day longer than the weather forecast, so line the two
        # up by timestamp rather than by index.
        m_at = {t: j for j, t in enumerate(_series(m, "time"))}

        sw = swell_data.get(spot.id)
        sw_at = {t: j for j, t in enumerate(_series(sw or {}, "time"))} if sw else {}

        ts = tide_series.get(spot.id)
        hours = []

        for i, t in enumerate(times):
            when = datetime.fromisoformat(t).replace(tzinfo=UK)
            tide_state = ts.at(when) if ts else None

            # Copernicus for this hour if it covers it, else Open-Meteo — taken
            # as a whole per hour rather than mixed field-by-field, so a wave
            # height and its direction always come from the same model.
            wave = period = swell_dir = wind_wave_m = None
            swell_source = None
            sj = sw_at.get(t)
            if sw and sj is not None:
                wave = _at(sw["wave_height"], sj)
                if wave is not None:
                    period = _at(sw["wave_period"], sj)
                    swell_dir = _at(sw["swell_wave_direction"], sj)
                    wind_wave_m = _at(sw["wind_wave_height"], sj)
                    swell_source = "copernicus"

            if wave is None:
                j = m_at.get(t)
                if j is not None:
                    wave = _at(waves, j)
                    if wave is None:
                        wave = _at(swell_h, j)
                    period = _at(periods, j)
                    if period is None:
                        period = _at(swell_p, j)
                    swell_dir = _at(swell_dirs, j)
                    if wave is not None:
                        swell_source = "open-meteo"

            wind_kn, gust_kn, wind_dir = _at(speeds, i), _at(gusts, i), _at(dirs, i)
            if demo:
                wind_kn = demo.get("wind_kn", wind_kn)
                gust_kn = wind_kn * 1.25 if wind_kn else gust_kn
                # Rotate 90 degrees across the week so every spot gets its turn.
                wind_dir = (demo.get("wind_dir", wind_dir or 0) + i * (90 / max(len(times), 1))) % 360
                wave = demo.get("wave_m", wave)
                period = demo.get("period_s", period)
                swell_dir = wind_dir  # demo swell tracks demo wind so faces-aligned spots light up

            h = score_hour(
                spot=spot,
                rider=rider,
                when=when,
                wind_kn=wind_kn,
                gust_kn=gust_kn,
                wind_dir=wind_dir,
                wave_m=wave,
                period_s=period,
                tide=tide_state,
                swell_dir=swell_dir,
                wind_wave_m=wind_wave_m,
                swell_source=swell_source,
            )
            h["temp_c"] = _at(temps, i)
            h["rain_mm"] = _at(rain, i)
            hours.append(h)

        if not hours:
            continue

        # Nearest hour to now (clamped to the forecast range).
        now_idx = min(
            range(len(hours)),
            key=lambda i: abs(
                (datetime.fromisoformat(hours[i]["time"]).replace(tzinfo=UK) - now).total_seconds()
            ),
        )

        by_day = defaultdict(list)
        for h in hours:
            by_day[h["time"][:10]].append(h)

        today = now.date().isoformat()
        this_hour = now.replace(minute=0, second=0, microsecond=0)

        day_rows = []
        for date in sorted(by_day):
            dh = by_day[date]
            # Daylight only — no point recommending 3am.
            daylight = [x for x in dh if 6 <= x["hour"] <= 21] or dh
            partial = over = False
            if date == today:
                # "Today" means the rest of today. A window that closed at
                # lunchtime is not a recommendation.
                ahead = [
                    x for x in daylight
                    if datetime.fromisoformat(x["time"]) >= this_hour
                ]
                if ahead:
                    partial = len(ahead) < len(daylight)
                    daylight = ahead
                else:
                    over = True
            peak = max(daylight, key=lambda x: x["score"])
            d = datetime.fromisoformat(date)
            day_rows.append(
                {
                    "date": date,
                    "label": f"{d.strftime('%a')} {d.day} {d.strftime('%b')}",
                    "short": d.strftime("%a"),
                    "daynum": f"{d.day} {d.strftime('%b')}",
                    "is_today": date == today,
                    "partial": partial,
                    "over": over,
                    "hours_scored": len(daylight),
                    "best_score": peak["score"],
                    "band": peak["band"],
                    "peak_hour": peak["hour"],
                    "peak_time": peak["time"],
                    "wind_kn": peak["wind_kn"],
                    "gust_kn": peak["gust_kn"],
                    "wind_dir": peak["wind_dir"],
                    "wind_dir_txt": peak["wind_dir_txt"],
                    "wave_m": peak["wave_m"],
                    "wing": peak["wing"],
                    "wave_school": any(x["wave_school"] and x["score"] >= 55 for x in daylight),
                    "window": best_window(daylight),
                    "windows": find_windows(daylight)[:3],
                    # Relaxed threshold so a mediocre day still suggests when to go.
                    "windows_any": find_windows(daylight, 38)[:3],
                }
            )

        out_spots.append(
            {
                "id": spot.id,
                "name": spot.name,
                "lat": spot.lat,
                "lon": spot.lon,
                "character": spot.character,
                "notes": spot.notes,
                "guide": spot.guide,
                # The actual scoring inputs, for the Guide panel's compass/tide-bar —
                # drawn from what the engine really uses, not re-parsed guide prose.
                "wind_rule": {
                    "ideal": spot.wind.ideal,
                    "ok": spot.wind.ok,
                    "gusty": spot.wind.gusty,
                    "offshore": spot.wind.offshore,
                },
                "tide_rule": {
                    "best": spot.tide.best,
                    "avoid_hw_hours": spot.tide.avoid_hw_hours,
                    "avoid_lw_hours": spot.tide.avoid_lw_hours,
                    "rising_only": spot.tide.rising_only,
                },
                "tide_source": ts.source if ts else None,
                "tide_station": ts.station if ts else None,
                "swell_source": "copernicus" if sw else ("open-meteo" if m else None),
                "swell_grid_km": sw.get("grid_km") if sw else None,
                "now": hours[now_idx],
                "hours": hours,
                "days": day_rows,
            }
        )

    out_spots.sort(key=lambda s: s["now"]["score"], reverse=True)

    # One row per day for the tab bar: the best any spot manages that day, so
    # you can see which days are worth opening before you click them.
    day_tabs = []
    if out_spots:
        for i, d0 in enumerate(out_spots[0]["days"]):
            best = max(
                (s["days"][i] for s in out_spots if i < len(s["days"])),
                key=lambda d: d["best_score"],
                default=None,
            )
            winner = max(
                (s for s in out_spots if i < len(s["days"])),
                key=lambda s: s["days"][i]["best_score"],
                default=None,
            )
            day_tabs.append({
                "date": d0["date"],
                "label": d0["label"],
                "short": d0["short"],
                "daynum": d0["daynum"],
                "is_today": d0["is_today"],
                "partial": d0["partial"],
                "over": d0["over"],
                "best_score": best["best_score"] if best else 0,
                "band": best["band"] if best else "no",
                "best_spot": winner["name"] if winner else None,
                "best_spot_id": winner["id"] if winner else None,
            })

    best = out_spots[0] if out_spots else None
    verdict = _verdict(out_spots)
    sources = _source_status(out_spots, weather_live)

    # Next genuinely good session anywhere, if right now is a write-off.
    upcoming = []
    for s in out_spots:
        for d in s["days"]:
            if d["best_score"] >= 60:
                upcoming.append({"spot": s["name"], "spot_id": s["id"], **d})
    upcoming.sort(key=lambda d: (d["date"], -d["best_score"]))

    return {
        "generated_at": now.isoformat(),
        "generated_label": f"{now.strftime('%a')} {now.day} {now.strftime('%b')}, {now.strftime('%H:%M')}",
        "rider": {
            "weight_kg": rider.weight_kg,
            "foil": rider.foil_label,
            "wings": [w.size for w in rider.wings],
        },
        "warnings": warnings,
        "verdict": verdict,
        "best_now": best["id"] if best else None,
        "sources": sources,
        "day_tabs": day_tabs,
        "spots": out_spots,
        "upcoming": upcoming[:12],
    }


def _source_status(out_spots: list[dict], weather_live: bool) -> list[dict]:
    """One row per data source for the status box: name, status, detail.

    status is "live" (best source, fresh), "fallback" (degraded but still
    giving real data), or "down" (nothing usable at all).
    """

    def spot_ratio(key: str, best_value: str) -> tuple[int, int]:
        vals = [s.get(key) for s in out_spots]
        have = sum(1 for v in vals if v is not None)
        good = sum(1 for v in vals if v == best_value)
        return good, have

    tide_good, tide_have = spot_ratio("tide_source", "admiralty")
    swell_good, swell_have = spot_ratio("swell_source", "copernicus")
    n = len(out_spots) or 1

    def status_of(good: int, have: int) -> str:
        if have == 0:
            return "down"
        if good == n:
            return "live"
        return "fallback"

    return [
        {
            "id": "wind",
            "name": "Wind & weather",
            "provider": "Open-Meteo (UK Met Office model)",
            "status": "live" if weather_live else "fallback",
            "detail": "hourly, updated live" if weather_live else "using cached data",
        },
        {
            "id": "tide",
            "name": "Tides",
            "provider": "UKHO Admiralty" if tide_good else "Open-Meteo (modelled)",
            "status": status_of(tide_good, tide_have),
            "detail": f"{tide_good}/{n} spots on official stations" if tide_have else "no tide data",
        },
        {
            "id": "swell",
            "name": "Swell",
            "provider": "Copernicus Marine 1.5km" if swell_good else "Open-Meteo (global)",
            "status": status_of(swell_good, swell_have),
            "detail": f"{swell_good}/{n} spots on the regional model" if swell_have else "no swell data",
        },
    ]


def _verdict(spots: list[dict]) -> dict:
    if not spots:
        return {"headline": "No data", "detail": "", "band": "no"}
    top = spots[0]
    s = top["now"]["score"]
    if s >= 72:
        return {
            "headline": f"GO — {top['name'].title()}",
            "detail": f"{top['now']['wind_kn']:.0f}kn {top['now']['wind_dir_txt']}"
            f" · {top['now']['wing']['text']}",
            "band": "go",
        }
    if s >= 55:
        return {
            "headline": f"Worth it — {top['name'].title()}",
            "detail": f"{top['now']['wind_kn']:.0f}kn {top['now']['wind_dir_txt']}"
            f" · {top['now']['wing']['text']}",
            "band": "good",
        }
    if s >= 38:
        return {
            "headline": "Marginal everywhere",
            "detail": f"Best is {top['name'].title()} at {s}/100. Check the week below.",
            "band": "marginal",
        }
    return {
        "headline": "Nowhere is on right now",
        "detail": f"Best is {top['name'].title()} at {s}/100. Check the week below.",
        "band": "no",
    }
