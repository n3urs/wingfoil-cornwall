"""Fire made-up conditions at the spot rules and see the whole board light up.

    .venv/bin/python tools/simulate.py                 # a tour of typical days
    .venv/bin/python tools/simulate.py 22 SW low       # one specific scenario
    .venv/bin/python tools/simulate.py 28 W mid --wave 1.2 --period 11

Useful for sanity-checking config/spots.yaml after you edit it, without waiting
for the weather to cooperate.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Plain Windows consoles default stdout to cp1252, which can't encode the
# check/cross marks and colour codes below — force UTF-8 so this runs the same
# on Windows as it does everywhere else.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from wingfoil.config import COMPASS, load_rider, load_spots  # noqa: E402
from wingfoil.scoring import score_hour  # noqa: E402
from wingfoil.tides import UK, TideState  # noqa: E402

C = {
    "go": "\033[1;92m",
    "good": "\033[92m",
    "marginal": "\033[93m",
    "no": "\033[90m",
    "off": "\033[0m",
    "hdr": "\033[1;96m",
    "warn": "\033[1;91m",
}


def deg(txt: str) -> float:
    txt = txt.upper()
    if txt.replace(".", "").isdigit():
        return float(txt)
    if txt not in COMPASS:
        raise SystemExit(f"Unknown direction {txt!r}. Use degrees or one of {', '.join(COMPASS)}")
    return COMPASS.index(txt) * 22.5


def make_tide(state: str) -> TideState:
    """Fabricate a plausible tide state: low / mid-rising / mid-falling / high."""
    presets = {
        "low": (0.05, True, 0.15, 5.5, -0.2),
        "rising": (0.5, True, 1.0, 3.0, -3.0),
        "mid": (0.5, True, 1.0, 3.0, -3.0),
        "falling": (0.5, False, 1.0, -3.0, 3.0),
        "ebb": (0.5, False, 1.0, -3.0, 3.0),
        "high": (0.95, False, 0.15, -0.2, 5.5),
    }
    if state not in presets:
        raise SystemExit(f"Unknown tide {state!r}. Use: low, rising, mid, falling, ebb, high")
    pct, rising, flow, to_hw, to_lw = presets[state]
    return TideState(
        height=round(1.0 + pct * 5.0, 2),
        pct=pct,
        rising=rising,
        flow=flow,
        hours_to_hw=to_hw,
        hours_to_lw=to_lw,
        next_hw=None,
        next_lw=None,
    )


def run(
    wind: float,
    direction: str,
    tide: str,
    wave: float,
    period: float,
    swell_dir: float | str | None = None,
    wind_wave_m: float | None = None,
) -> list[tuple]:
    spots = load_spots()
    rider = load_rider()
    d = deg(direction)
    sd = deg(swell_dir) if isinstance(swell_dir, str) else swell_dir
    ts = make_tide(tide)
    when = datetime(2026, 8, 7, 13, 0, tzinfo=UK)

    rows = []
    for spot in spots:
        r = score_hour(
            spot=spot,
            rider=rider,
            when=when,
            wind_kn=wind,
            gust_kn=wind * 1.25,
            wind_dir=d,
            wave_m=wave,
            period_s=period,
            tide=ts,
            swell_dir=sd,
            wind_wave_m=wind_wave_m,
        )
        rows.append((spot, r))
    rows.sort(key=lambda p: p[1]["score"], reverse=True)
    return rows


def show(wind: float, direction: str, tide: str, wave: float, period: float) -> None:
    print(
        f"\n{C['hdr']}{wind:.0f}kn {direction.upper()}  ·  {tide} tide  ·  "
        f"{wave:.1f}m @ {period:.0f}s{C['off']}"
    )
    print("─" * 78)
    for spot, r in run(wind, direction, tide, wave, period):
        col = C[r["band"]]
        wing = r["wing"]["size"] or "—"
        bar = "█" * int(r["score"] / 5)
        print(f"  {col}{spot.name:22} {r['score']:3d} {bar:<20}{C['off']} {wing:>3}  ", end="")
        note = (r["warnings"] + r["reasons"])[0] if (r["warnings"] or r["reasons"]) else ""
        if r["warnings"]:
            print(f"{C['warn']}{note}{C['off']}")
        else:
            print(note)


SCENARIOS = [
    (18, "SW", "mid", 1.2, 10, "Classic Cornish SW — Gwithian should top it"),
    (18, "NW", "low", 0.8, 9, "NW low tide — Bluff and Daymer flat-water day"),
    (20, "E", "low", 0.3, 6, "Easterly — north coast is offshore, go south"),
    (30, "W", "rising", 1.8, 12, "Strong W — 4m weather, Crantock only on the push"),
    (30, "W", "falling", 1.8, 12, "Same but ebbing — Crantock must collapse"),
    (16, "SE", "mid", 0.4, 7, "Light SE — Mylor and Pentewan"),
    (24, "N", "high", 2.5, 13, "N at high water with big swell — mostly a no"),
    (9, "SW", "mid", 1.0, 9, "Not enough wind for the 6m anywhere"),
]


def check() -> int:
    """Assertions that encode what the guides say. Fails loudly if a rule breaks."""
    fails = []
    total = 0

    def expect(cond, msg):
        nonlocal total
        total += 1
        if not cond:
            fails.append(msg)

    def score_of(rows, spot_id):
        return next(r["score"] for s, r in rows if s.id == spot_id)

    def field_of(rows, spot_id, key):
        return next(r[key] for s, r in rows if s.id == spot_id)

    sw = run(18, "SW", "mid", 1.2, 10)
    expect(score_of(sw, "gwithian") >= 60, "Gwithian should be strong in 18kn SW at mid tide")
    expect(score_of(sw, "bluff") == 0, "The Bluff should be dead in a SW (needs NW-N-NE)")
    expect(score_of(sw, "pentewan") < 30, "Pentewan is offshore in a SW")

    nw = run(18, "NW", "low", 0.8, 9)
    expect(score_of(nw, "bluff") >= 60, "The Bluff should be strong in 18kn NW at low tide")
    expect(score_of(nw, "daymer") >= 55, "Daymer should be good in a NW")

    east = run(20, "E", "low", 0.3, 6)
    expect(score_of(east, "pentewan") >= 60, "Pentewan should top an easterly")
    expect(score_of(east, "watergate") < 30, "Watergate is offshore in an E")

    rise = run(30, "W", "rising", 1.8, 12)
    fall = run(30, "W", "falling", 1.8, 12)
    expect(
        score_of(fall, "crantock") < score_of(rise, "crantock") * 0.4,
        "Crantock must collapse on an ebb tide (Gannel sweeps you out)",
    )

    hw = run(20, "SW", "high", 1.0, 10)
    lw = run(20, "SW", "low", 1.0, 10)
    expect(
        score_of(hw, "marazion") >= score_of(lw, "marazion") * 0.8,
        "Marazion should not collapse near high water — wingfoilkit.com calls it"
        " rideable at all states of the tide, and foil launching is actually easiest"
        " mid-to-high tide (steep shelf, deep quickly vs. a long low-tide wade)",
    )
    ghw = run(18, "W", "high", 1.0, 10)
    gmid = run(18, "W", "mid", 1.0, 10)
    expect(
        score_of(ghw, "gwithian") < score_of(gmid, "gwithian") * 0.6,
        "Gwithian must drop at high water (pinned against the cliff)",
    )
    whw = run(18, "NW", "high", 1.0, 10)
    wlw = run(18, "NW", "low", 1.0, 10)
    expect(
        score_of(whw, "watergate") < score_of(wlw, "watergate") * 0.6,
        "Watergate must drop at high water (no beach)",
    )

    light = run(9, "SW", "mid", 1.0, 9)
    expect(all(r["score"] == 0 for _, r in light), "9kn is unridable on a 6m at 80kg")

    windy = run(28, "SW", "mid", 1.0, 9)
    expect(
        all(r["wing"]["size"] == "4m" for _, r in windy if r["wing"]["size"]),
        "28kn should call for the 4m",
    )
    mid = run(16, "SW", "mid", 1.0, 9)
    expect(
        all(r["wing"]["size"] == "6m" for _, r in mid if r["wing"]["size"]),
        "16kn should call for the 6m",
    )

    # Directional swell exposure (Copernicus swell partition -> spot.faces).
    # Watergate faces 300 (WNW) — swell arriving square onto that should score
    # noticeably better than the same height/period arriving from behind the
    # headland, at otherwise-identical wind and tide.
    square = run(18, "NW", "low", 1.5, 12, swell_dir=300)
    behind = run(18, "NW", "low", 1.5, 12, swell_dir=120)
    # `sea` is a modulator (18% weight), not a gate — wing foiling doesn't need
    # good wave alignment the way surfing does, so misaligned swell should
    # nudge the score down, not sink it. The real signal is the sea component
    # and its direction factor, which should move a lot even if the blended
    # score only moves a little.
    expect(
        score_of(square, "watergate") > score_of(behind, "watergate"),
        "Swell arriving square at Watergate should still score at least as well as the same swell from behind the headland",
    )
    expect(
        field_of(behind, "watergate", "parts")["sea_direction"] < 0.5,
        "Watergate's sea-direction factor should be heavily discounted for a swell arriving from behind it",
    )
    expect(
        field_of(square, "watergate", "parts")["sea"] > field_of(behind, "watergate", "parts")["sea"] * 1.15,
        "The sea-state component itself should be meaningfully better for square-on swell",
    )
    # A spot with no `faces` set (Mylor) must not be penalised for something
    # we have no geometry to judge — direction factor stays neutral.
    expect(
        field_of(square, "mylor", "parts")["sea_direction"] == 1.0,
        "Mylor has no `faces` set — its sea-direction factor should stay neutral, not be penalised",
    )

    # Clean groundswell vs the same height/period ruined by local wind chop.
    clean = run(18, "NW", "low", 1.0, 10, swell_dir=300, wind_wave_m=0.1)
    choppy = run(18, "NW", "low", 1.0, 10, swell_dir=300, wind_wave_m=1.3)
    expect(
        field_of(clean, "watergate", "wave_school") is True,
        "1.0m @ 10s with negligible wind chop should be a wave-school day at Watergate",
    )
    expect(
        field_of(choppy, "watergate", "wave_school") is False,
        "The same 1.0m @ 10s swell with 1.3m of local wind chop on top should NOT be a wave-school day",
    )

    if fails:
        print(f"\n{C['warn']}{len(fails)} rule check(s) FAILED:{C['off']}")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print(f"\n{C['good']}✓ all {total} spot-rule checks passed{C['off']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("wind", nargs="?", type=float)
    p.add_argument("direction", nargs="?", default="SW")
    p.add_argument("tide", nargs="?", default="mid")
    p.add_argument("--wave", type=float, default=1.0)
    p.add_argument("--period", type=float, default=10)
    p.add_argument("--check", action="store_true", help="run the rule assertions")
    a = p.parse_args()

    if a.check:
        return check()
    if a.wind is not None:
        show(a.wind, a.direction, a.tide, a.wave, a.period)
        return 0
    for wind, d, t, wv, pd, blurb in SCENARIOS:
        print(f"\n\033[2m{blurb}\033[0m", end="")
        show(wind, d, t, wv, pd)
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
