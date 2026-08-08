# Cornwall Wing Foiling Dashboard

Glance at it, know where to go.

Eight Cornish spots scored 0–100 every hour for the next 7 days from live wind,
swell and tide, against each spot's own wind-direction and tide requirements.
Tells you which wing to rig for 80kg on a 900cm² foil.

```bash
./run.sh
```

Then open **http://localhost:8787**.

First run builds a virtualenv and installs dependencies (~30s). After that it
starts instantly.

Or skip running anything locally — it's published at
**https://n3urs.github.io/wingfoil-cornwall/**, rebuilt every 3 hours by a
GitHub Action. Bookmark that instead. See [Publishing it](#publishing-it).

---

## The tide API key

It works out of the box without a key, deriving tides from Open-Meteo's modelled
sea level. For official UKHO tide times:

1. Register at **https://developer.admiralty.co.uk** (not `*.portal.azure-api.net` —
   that's the old developer portal, retired, always 503s)
2. Sign in → **Products** → **UK Tidal API - Discovery** → Subscribe (free,
   10k calls/month — Foundation and Premium next to it are paid, skip those)
3. Your profile → copy the **Primary key**
4. `cp .env.example .env` and paste it after `ADMIRALTY_API_KEY=`

The dashboard picks the nearest of the 607 UK tidal stations to each spot
automatically. The station it used is shown under each spot's name.

---

## The swell model

By default swell comes from Open-Meteo's global wave model — fine, but at
roughly 9-25km resolution it can't tell that a headland is sheltering one bay
from a swell that's hammering the next one along. For that you want
**Copernicus Marine's Northwest European Shelf model**: 1.5km resolution,
hourly, updated daily, and it reports the sea state as three separate parts —
total, distant swell, and local wind-chop — rather than one blended number.

1. Register at **https://data.marine.copernicus.eu** (free)
2. In this project's venv, install the client and log in **once**:
   ```bash
   .venv/bin/pip install copernicusmarine
   .venv/bin/copernicusmarine login
   ```
   That prompts for your username and password right there in the terminal and
   stores them in `~/.copernicusmarine/` — nothing goes in this app's `.env`,
   and the app never sees your password.
3. Restart the dashboard. No further config — `wingfoil/swell.py` picks it up
   automatically once the credentials file exists.

Without it the dashboard still works, silently using Open-Meteo's swell data
instead, same posture as the tide key. The **status box** top-right of the
header shows which one you're actually getting for each source, live.

---

## Publishing it

**https://n3urs.github.io/wingfoil-cornwall/** — bookmark it, use it from any
device, nothing to start. A GitHub Action rebuilds it every 3 hours (`.github/
workflows/pages.yml`), on every push to `main`, and on demand from the
Actions tab's "Run workflow" button.

It's the same dashboard, computed by the same code (`tools/build_static.py`
just calls `build_dashboard()` and writes the result to a static
`dashboard.json` next to the page) — everything works identically **except**
`?demo=` conditions, which need a live server to compute on the spot.

For the published copy to use the good data sources rather than silently
falling back to Open-Meteo everywhere, add these as **repo secrets**
(Settings → Secrets and variables → Actions → New repository secret) —
optional, same fallback posture as running it locally without them:

| Secret | Value |
|---|---|
| `ADMIRALTY_API_KEY` | your Admiralty primary key |
| `COPERNICUSMARINE_SERVICE_USERNAME` | your Copernicus Marine username |
| `COPERNICUSMARINE_SERVICE_PASSWORD` | your Copernicus Marine password |

**GitHub Pages needs a public repo** on the free plan — private repos need
GitHub Pro or above. Nothing sensitive is tracked (`.env` is gitignored,
`.env.example` is blank), so this repo is public.

---

## Reading it

**The status box**, top-right of the header, is one row per data source —
Wind, Tide, Swell — with a dot: green means live from the best available
source, amber means it's degraded to a fallback (still real data, just a
coarser or cached one), red means that source has nothing. Hover a row for the
detail (which UKHO station, how many spots are on the regional wave model,
etc).

**The ring.** Spots are laid out in roughly their real geographic order — north
coast up top, south coast at the bottom. Each tile shows wind, score, and the
wing to rig. Green = go. Olive = worth it. Amber = marginal. Grey = don't.

**The dial.** A chart compass rose showing wind at the selected spot. The arrow
points the way the wind is blowing; the label is the direction it's coming
*from*.

**The map** on the left is the real Cornish coastline (OpenStreetMap data, baked
into `static/coast.js` — no tiles, no network, works offline). Each spot is a
dot in its true position, coloured by its score and glowing when it's on. The
selected spot gets a ring; click a dot to select it.

**The sea inside the dial** is the tide. The porthole fills and empties with the
water: brim full at high water, near empty at dead low. The caption under it
gives the direction and the time to the next turn (`▲ HW 4h02` = flooding, high
water in four hours). Hover the day timeline below to scrub it (see below).

**The day tabs.** One per day for the next week. Each shows the best score any
spot manages that day and which spot that is, so you can see what's worth
opening before you click. Click a day and the whole board switches to that
day's overview — every tile then reports that spot's *best* for the day, with
the window it's on (`6m · 10:00–18:59`) rather than one particular hour. Arrow
keys work too.

`TODAY` means the **rest of** today — a window that closed at lunchtime isn't a
recommendation, so past hours are excluded. Once daylight's gone it says so.

**The day timeline** in the detail panel answers *when* to go. For the open day
at the selected spot:

- **Bars** are the hourly rating, coloured by band. The dashed olive line across
  them is 55 — above it is worth going.
- **The dashed white line** is wind speed, with the gust range shaded behind it.
- **The water strip underneath** is the tide on the same time axis, with HW and
  LW marked. This is the point of the chart: you can see the score climb as the
  tide drops away from high water, so it's obvious *why* the good window is
  where it is.
- **The green band** is the recommended window, labelled with its times.
- **NOW** is marked when you're looking at today.

**Hover across the chart to scrub time.** The dial follows your cursor — the
water level, wind and tide readout all update to that hour, with a cursor line
marking where you are. Move off and it snaps back to the day's best hour. The
dial always labels which moment it's showing (`NOW`, `14:00`, or `17:00 · BEST`).

Click any bar to pin the whole board to that hour; click it again to release
back to the day view.

**Window chips** above the chart list the day's best runs — times, length, peak
score and wing. Click one to jump to its peak hour. If nothing clears 55 the
chips relax to the least-bad stretch rather than showing nothing, and say so.

**The week grid** is best score per spot per day, with the best window and wing.
Click any cell to jump to that day and spot.

**🌊 / WAVE DAY** means a wave spot where the swell is small and clean enough to
actually be a sensible day to go and learn — 0.6–1.6m at 8s+, and (with the
Copernicus source) not dominated by local wind-chop on top. The **swell line**
in the detail panel shows height, period and direction, plus how much of that
is wind-chop when it's non-trivial (`1.2m @ 10s WNW · 0.4m chop`).

Swell direction only affects the score at spots with a `faces` bearing set in
`spots.yaml` (the open-coast ones) — a swell arriving from behind a headland
scores worse there than the same swell arriving square-on, even at identical
height and period. It's a modulator, not a gate: wing foiling doesn't need
good wave alignment the way surfing does, so a misaligned swell nudges the
score down rather than zeroing it.

---

## Tuning it

Two config files. Nothing else needs touching.

### `config/spots.yaml`

The brain. Each spot's wind-direction arcs, tide rules, hazards and notes.
Direction arcs are `[from, to]` degrees clockwise and may wrap through 0.

```yaml
wind:
  ideal: [[225, 315]]     # SW-W-NW: full marks
  ok: [[315, 350]]        # N: works, scores 0.62
  gusty: [[250, 292]]     # over land: x0.85
  offshore: [[0, 200]]    # hard safety cap at 0.25 + a warning
tide:
  best: mid               # any | low | mid | high  — a preference
  avoid_hw_hours: 1.5     # a veto: no beach here near high water
  avoid_lw_hours: 1.5
  rising_only: false      # a veto: ebb is dangerous here
  flow_penalty: 0.0       # 0-1, how much strong tidal stream hurts
faces: 300                # optional — bearing the open water lies in, for
                           # swell-direction scoring. Omit at sheltered spots
                           # where a swell's direction doesn't matter.
```

The spot rules were built from published Cornish kite/wing guides (Kernow
Kitesurf Club, Pasty Adventures, South West Kitesurf, Poseidon, KitesurfKit,
Windsurf Magazine). **Treat them as a starting point** — tune them as you learn
each place, especially Mylor and Daymer where sources are thinnest. The
`faces` bearings are estimated from coastline geometry, not surveyed — same
deal, correct them as you learn how swell actually wraps into each bay.

### `config/rider.yaml`

Weight, foil, and the wind range of each wing you own. Also three ability dials:

- `high_wind_tolerance` — 1.0 means a 35kn day isn't penalised
- `wave_confidence` — low values push you toward flat water and only flag waves
  when they're small and clean
- `gust_tolerance` — how much a big gust spread hurts the score

---

## Checking your changes

After editing `spots.yaml`, fire made-up conditions at it:

```bash
.venv/bin/python tools/simulate.py 22 SW low
```

```bash
.venv/bin/python tools/simulate.py --check
```

`--check` runs assertions encoding what the guides say (Crantock must
collapse on an ebb, Marazion must drop near high water, Pentewan must top an
easterly, 28kn must call for the 4m, a swell arriving square at Watergate must
beat the same swell from behind the headland, wind-chop must knock out the
wave-school badge, and so on). Run it after any config edit.

With no arguments it walks through eight typical Cornish scenarios.

You can also preview conditions in the browser:
`http://localhost:8787/?demo=18,225,1.2,10` — 18kn from 225°, 1.2m at 10s.

---

## How the score works

```
score = 100 × direction × wind × (0.60 + 0.22×tide_pref + 0.18×sea) × gust × safety × tide_gate
```

`direction`, `wind` and `tide_gate` are **gates** — wind from a direction the
spot doesn't work in, wind your quiver can't cover, or a tide state that means
there's no beach, all drive the score to zero rather than merely lowering it.
`tide_pref` and `sea` then modulate a session that's already possible. `sea`
itself factors in swell direction against the spot's `faces` bearing where
that's configured — see [The swell model](#the-swell-model).

Bands: **72+** go · **55** good · **38** marginal · below that, no.

---

## Data

- Wind, gusts, temperature — [Open-Meteo Forecast API](https://open-meteo.com/)
  (UK Met Office model, best_match)
- Swell — [Copernicus Marine Northwest Shelf](https://data.marine.copernicus.eu/product/NWSHELF_ANALYSISFORECAST_WAV_004_014)
  (1.5km, hourly), falling back to [Open-Meteo Marine](https://marine-api.open-meteo.com/)
- Tides — [UKHO ADMIRALTY UK Tidal API](https://developer.admiralty.co.uk/),
  falling back to Open-Meteo's modelled sea level

No keys needed for a working dashboard — Admiralty and Copernicus are both
optional upgrades over the Open-Meteo defaults, see above. Responses are
cached in `cache/` (wind 30 min, marine 1 hr, tides 6 hrs, swell 6 hrs) so
refreshes are instant and it degrades to the last good data if you're offline.

---

## Safety

The dashboard is a filter, not a judgement. It doesn't know about the sandbar
that moved last winter, today's rip, who else is out, or whether the forecast is
lying. Offshore warnings and the ebb-tide vetoes at Crantock, Daymer and the
Bluff are there because those specific places have caught people out — respect
them. The Bluff needs a pass and insurance before you go at all.
