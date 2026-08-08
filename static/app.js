const $ = (id) => document.getElementById(id);

const state = {
  data: null,
  day: 0,        // which day tab is open (0 = today)
  idx: 0,        // hour being shown — the day's peak unless you click the strip
  nowIdx: 0,
  pinnedHour: false,  // true once you pick an hour off the strip yourself
  hoverIdx: null,     // hour under the cursor while scrubbing the day chart
  selected: null,
};

// --------------------------------------------------------------------------
// data
// --------------------------------------------------------------------------

// Works two ways: against the live FastAPI server (./run.sh — full live
// data, ?demo= works) and as a published static site (GitHub Pages — reads
// the dashboard.json a scheduled Action last built, no demo mode since
// there's no server to compute one on demand). Relative paths throughout so
// this resolves correctly whether the site is served from a domain root or
// a Pages project subpath like /wingfoil-cornwall/.
async function fetchDashboard() {
  const demo = new URLSearchParams(location.search).get("demo");
  try {
    const r = await fetch("api/dashboard" + (demo ? `?demo=${encodeURIComponent(demo)}` : ""));
    if (r.ok) return await r.json();
  } catch (e) { /* no live server here — fall through to the static snapshot */ }

  if (demo) {
    throw new Error("Demo mode needs the local server (./run.sh) — not available on the published site.");
  }
  const r2 = await fetch("dashboard.json");
  if (!r2.ok) throw new Error(`Could not load dashboard.json (${r2.status})`);
  return await r2.json();
}

async function load() {
  $("verdict-head").textContent = "Loading…";
  let d;
  try {
    d = await fetchDashboard();
  } catch (e) {
    $("verdict-head").textContent = "Error";
    $("verdict-sub").textContent = e.message;
    return;
  }
  if (d.error) {
    $("verdict-head").textContent = "Error";
    $("verdict-sub").textContent = d.error;
    return;
  }
  state.data = d;

  const times = d.spots[0].hours.map((h) => h.time);
  state.times = times;

  const now = new Date(d.generated_at);
  let best = 0, bestDiff = Infinity;
  times.forEach((t, i) => {
    const diff = Math.abs(new Date(t) - now);
    if (diff < bestDiff) { bestDiff = diff; best = i; }
  });
  state.nowIdx = best;
  state.idx = best;
  state.day = 0;
  state.pinnedHour = false;
  if (!state.selected) state.selected = d.best_now;

  $("kit").innerHTML =
    `${d.rider.weight_kg}kg · ${d.rider.foil}cm² foil · ${d.rider.wings.join(" + ")}<br>` +
    `<span style="opacity:.7">updated ${d.generated_label}</span>`;

  drawSources();
  render();
}

function drawSources() {
  const el = $("sources");
  if (!el) return;
  const rows = state.data.sources || [];
  el.innerHTML = rows.map((s) => {
    const short = { "Wind & weather": "WIND", "Tides": "TIDE", "Swell": "SWELL" }[s.name] || s.name.toUpperCase();
    return `<div class="src-row ${s.status}" title="${esc(s.detail)}">
      <span class="src-dot"></span>
      <span class="src-name">${short}</span>
      <span class="src-provider">${esc(s.provider)}</span>
    </div>`;
  }).join("");
}

const dayOf = (spot) => spot.days[state.day] || spot.days[0];

// What each tile reports for the open day: its best hour, unless you've pinned
// a specific hour off the strip — then everything lines up on that hour.
function hourAt(spot) {
  if (state.pinnedHour) {
    const t = state.data.spots[0].hours[state.idx].time;
    return spot.hours.find((h) => h.time === t) || spot.hours[state.idx];
  }
  const d = dayOf(spot);
  return spot.hours.find((h) => h.time === d.peak_time) || spot.hours[0];
}

// Score to rank/colour a tile by: the day's best when browsing a whole day.
const scoreAt = (spot) => (state.pinnedHour ? hourAt(spot).score : dayOf(spot).best_score);

// The moment the dial is reading out. Scrubbing the day chart wins, then a
// pinned hour, then the day's peak. The dial always labels which it's showing.
function focusHour(spot) {
  if (state.hoverIdx != null) {
    const t = state.data.spots[0].hours[state.hoverIdx];
    if (t) return spot.hours.find((h) => h.time === t.time) || hourAt(spot);
  }
  return hourAt(spot);
}

// --------------------------------------------------------------------------
// render
// --------------------------------------------------------------------------

function render() {
  const d = state.data;
  if (!d) return;

  const spots = d.spots.slice().sort((a, b) => scoreAt(b) - scoreAt(a));
  const top = spots[0];

  const v = verdictFor(top);
  $("verdict-head").textContent = v.headline;
  $("verdict-head").className = "verdict-head " + v.band;
  $("verdict-sub").textContent = v.detail;

  drawDays();
  drawMap();
  drawRing();
  drawDial();
  drawDetail();
  drawWeek();

  $("warnings").innerHTML = (d.warnings || []).map((w) => `<div>${esc(w)}</div>`).join("");
  drawSeabed();
}

function verdictFor(top) {
  const h = hourAt(top);
  const day = dayOf(top);
  const s = scoreAt(top);
  const tab = state.data.day_tabs[state.day] || {};

  let when;
  if (state.pinnedHour) when = `${String(h.hour).padStart(2, "0")}:00`;
  else if (day.window) when = `${day.window.from}–${day.window.to}`;
  else when = `${String(day.peak_hour).padStart(2, "0")}:00`;

  const detail = `${when} · ${h.wind_kn}kn ${h.wind_dir_txt} · ${h.wing.text}`;
  const dayWord = tab.is_today ? (tab.partial ? "rest of today" : "today") : tab.label;

  if (tab.over) {
    return { headline: "Today's done", detail: "Daylight's gone — check tomorrow.", band: "no" };
  }
  if (s >= 72) return { headline: `GO — ${title(top.name)}`, detail, band: "go" };
  if (s >= 55) return { headline: `Worth it — ${title(top.name)}`, detail, band: "good" };
  if (s >= 38) {
    return { headline: `Marginal ${dayWord}`, detail: `Best is ${title(top.name)} at ${s}/100`, band: "marginal" };
  }
  return { headline: `Nothing on ${dayWord}`, detail: `Best is ${title(top.name)} at ${s}/100`, band: "no" };
}

function drawDays() {
  const tabs = state.data.day_tabs || [];
  $("daybar").innerHTML = tabs.map((t, i) => `
    <button class="daytab ${t.band}${i === state.day ? " sel" : ""}${t.over ? " over" : ""}" data-i="${i}">
      <div class="d1">${t.is_today ? "TODAY" : esc(t.short)}</div>
      <div class="d2">${esc(t.daynum)}</div>
      <div class="d3">${t.best_score}</div>
      <div class="d4">${t.over ? "over" : esc(title(t.best_spot || ""))}</div>
    </button>`).join("");

  $("daybar").querySelectorAll(".daytab").forEach((el) => {
    el.onclick = () => {
      const i = +el.dataset.i;
      state.day = i;
      state.pinnedHour = false;   // a new day starts on that day's best hour
      // Follow the day's best spot, otherwise you land on a detail panel for
      // somewhere that's a write-off that day.
      if (tabs[i] && tabs[i].best_spot_id) state.selected = tabs[i].best_spot_id;
      render();
    };
  });
}

// Place spots around the ring in their real geographic order, so the board
// reads a bit like a map: north-coast spots up top, south coast at the bottom.
function ringOrder(spots) {
  const clat = spots.reduce((a, s) => a + s.lat, 0) / spots.length;
  const clon = spots.reduce((a, s) => a + s.lon, 0) / spots.length;
  return spots.slice().sort((a, b) => bearing(a) - bearing(b));

  function bearing(s) {
    const dy = s.lat - clat;
    const dx = (s.lon - clon) * Math.cos(clat * Math.PI / 180);
    return (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360;
  }
}

function drawRing() {
  const ring = $("ring");
  const d = state.data;
  [...ring.querySelectorAll(".spot")].forEach((n) => n.remove());

  const W = ring.clientWidth, H = ring.clientHeight;
  const cx = W / 2, cy = H / 2;
  const rx = Math.min(W / 2 - 95, 310), ry = Math.min(H / 2 - 55, 285);
  const ordered = ringOrder(d.spots);
  const n = ordered.length;

  ordered.forEach((spot, i) => {
    const h = hourAt(spot);
    const day = dayOf(spot);
    const score = scoreAt(spot);
    const band = state.pinnedHour ? h.band : day.band;

    const a = (-90 + (360 / n) * i) * Math.PI / 180;
    const el = document.createElement("div");
    el.className = `spot ${band}` + (spot.id === state.selected ? " sel" : "");
    el.style.left = (cx + rx * Math.cos(a)) + "px";
    el.style.top = (cy + ry * Math.sin(a)) + "px";

    const wind = h.wind_kn == null ? "—" :
      `${Math.round(h.wind_kn)}<span style="opacity:.6">–${Math.round(h.gust_kn)}</span>kn ${h.wind_dir_txt}`;
    const badges =
      (day.wave_school && !state.pinnedHour ? `<span class="badge wave">wave day</span> ` : "") +
      (state.pinnedHour && h.wave_school ? `<span class="badge wave">wave day</span> ` : "") +
      (h.warnings && h.warnings.length ? `<span class="badge warn">${esc(shortWarn(h.warnings[0]))}</span>` : "");

    // Don't suggest a wing for a spot you shouldn't be at — it reads as a
    // recommendation even when the spot is a write-off.
    const usable = band !== "no" && h.wing.size;
    // When browsing a whole day, say when it's on rather than which hour won.
    const when = state.pinnedHour
      ? `${String(h.hour).padStart(2, "0")}:00`
      : (day.window ? `${day.window.from}–${day.window.to}` : `${String(day.peak_hour).padStart(2, "0")}:00`);
    const sub = usable ? `${esc(h.wing.size)} · ${when}` : "not today";

    el.innerHTML =
      `<div class="spot-name">${esc(spot.name)}</div>` +
      `<div class="spot-line">${wind}</div>` +
      `<div class="spot-score">${score}</div>` +
      `<div class="spot-wing">${sub}</div>` +
      (badges ? `<div>${badges}</div>` : "") +
      waveGlyph(h.wave_m, band);

    el.onclick = () => { state.selected = spot.id; render(); };
    ring.appendChild(el);
  });
}

function shortWarn(w) {
  if (w.startsWith("OFFSHORE")) return "offshore";
  if (w.includes("EBB")) return "ebb — no";
  if (w.includes("high water")) return "high water";
  if (w.includes("low water")) return "low water";
  if (w.includes("ebb")) return "current";
  return w.slice(0, 14);
}

const BAND_COL = { go: "#35d98a", good: "#a7d155", marginal: "#ecac3a", no: "#5d7d88" };

// Real OpenStreetMap coastline, projected the same way it was baked in coast.js.
const geo = (lat, lon) => [
  (lon * COAST.k - COAST.minx) * COAST.scale,
  (-lat - COAST.miny) * COAST.scale,
];

function drawMap() {
  const svg = $("map");
  if (!svg || typeof COAST === "undefined") return;

  const pts = state.data.spots.map((s) => ({ s, xy: geo(s.lat, s.lon) }));
  // Frame on the spots, not the whole county — they don't reach the far corners.
  const xs = pts.map((p) => p.xy[0]), ys = pts.map((p) => p.xy[1]);
  const pad = 105;
  const x0 = Math.max(0, Math.min(...xs) - pad), x1 = Math.min(COAST.w, Math.max(...xs) + pad);
  const y0 = Math.max(0, Math.min(...ys) - pad), y1 = Math.min(COAST.h, Math.max(...ys) + pad);
  svg.setAttribute("viewBox", `${x0} ${y0} ${x1 - x0} ${y1 - y0}`);

  // Land is filled from the closed path; the coastline is stroked separately so
  // the artificial closing edge across the frame corner never shows.
  const coast =
    `<path d="${COAST.land}" fill="#123642" stroke="none"/>` +
    COAST.paths.map((d) => `<path d="${d}" fill="none" stroke="#5aa8bd" stroke-width="1.5"
        stroke-linejoin="round" stroke-linecap="round"
        vector-effect="non-scaling-stroke" opacity=".95"/>`).join("");

  // Radii must be in viewBox units, but the card renders the viewBox at about a
  // third scale — so size them as a fraction of the frame, not in raw units,
  // or every dot comes out 2px wide.
  const U = (x1 - x0) / 100;   // 1% of frame width

  const dots = pts.map(({ s, xy }) => {
    const band = state.pinnedHour ? hourAt(s).band : dayOf(s).band;
    const score = scoreAt(s);
    const col = BAND_COL[band];
    const sel = s.id === state.selected;
    const lit = band === "go" || band === "good";
    const r = (sel ? 3.6 : lit ? 2.9 : 2.3) * U;
    return `<g class="map-dot" data-id="${s.id}">
        <circle class="map-hit" cx="${xy[0]}" cy="${xy[1]}" r="${6 * U}" fill="${col}" opacity="0"/>
        ${lit || sel ? `<circle cx="${xy[0]}" cy="${xy[1]}" r="${r * 2.3}" fill="${col}" opacity="${sel ? .26 : .16}"/>` : ""}
        ${sel ? `<circle cx="${xy[0]}" cy="${xy[1]}" r="${r + 2.2 * U}" fill="none" stroke="#d9c9a3"
                   stroke-width="2" vector-effect="non-scaling-stroke"/>` : ""}
        <circle cx="${xy[0]}" cy="${xy[1]}" r="${r}" fill="${col}"
                stroke="#04121a" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
        <title>${esc(s.name)} — ${score}/100</title>
      </g>`;
  }).join("");

  svg.innerHTML = coast + dots;
  svg.querySelectorAll(".map-dot").forEach((el) => {
    el.onclick = () => { state.selected = el.dataset.id; render(); };
  });

  const sel = state.data.spots.find((s) => s.id === state.selected);
  if (sel) {
    const band = state.pinnedHour ? hourAt(sel).band : dayOf(sel).band;
    $("map-caption").innerHTML =
      `<span style="color:${BAND_COL[band]};font-weight:600">${esc(sel.name)}</span> · ${scoreAt(sel)}/100`;
  }
}

// Bathymetry contours: wobbling concentric rings, like depth lines on a chart.
// Deterministic, drawn once, purely decorative.
function drawSeabed() {
  const svg = $("seabed");
  const W = window.innerWidth, H = document.body.scrollHeight || window.innerHeight;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");

  const cx = W * 0.5, cy = H * 0.34;
  let paths = "";
  for (let ring = 0; ring < 14; ring++) {
    const base = 120 + ring * 118;
    const pts = [];
    for (let a = 0; a <= 360; a += 6) {
      const rad = a * Math.PI / 180;
      // Layered sines give an organic, non-circular contour.
      const wob =
        Math.sin(rad * 3 + ring * 1.7) * 26 +
        Math.sin(rad * 5 - ring * 0.9) * 15 +
        Math.sin(rad * 2 + ring * 2.6) * 34;
      const r = base + wob;
      pts.push(`${(cx + r * Math.cos(rad)).toFixed(1)},${(cy + r * Math.sin(rad) * 0.62).toFixed(1)}`);
    }
    const fade = 0.30 - ring * 0.012;
    paths += `<polygon points="${pts.join(" ")}" fill="none" stroke="#3d9cb8" stroke-width="1" opacity="${Math.max(0.06, fade).toFixed(3)}"/>`;
  }
  svg.innerHTML = paths;
}

// A 16-point compass rose, the way it's drawn on a paper chart.
function compassRose(C, pt) {
  let out = "";
  for (let k = 0; k < 8; k++) {
    const a = k * 45;
    const cardinal = k % 2 === 0;
    const len = cardinal ? 104 : 68;
    const w = cardinal ? 13 : 9;
    const [tx, ty] = pt(a, len);
    const [lx, ly] = pt(a - 90, w);
    const [rx, ry] = pt(a + 90, w);
    const light = k === 0 ? "#d9c9a3" : "#2f6577";
    out +=
      `<polygon points="${C},${C} ${lx},${ly} ${tx},${ty}" fill="#14323d" stroke="#3a6b7a" stroke-width=".6"/>` +
      `<polygon points="${C},${C} ${tx},${ty} ${rx},${ry}" fill="${light}" stroke="#3a6b7a" stroke-width=".6" opacity="${k === 0 ? .85 : .5}"/>`;
  }
  return out;
}

function drawDial() {
  const spot = state.data.spots.find((s) => s.id === state.selected) || state.data.spots[0];
  const h = focusHour(spot);
  const svg = $("dial");
  const C = 160, RING = 126;

  // Say which moment this is a readout of — without it the water level looks
  // arbitrary, since it's one hour out of the day.
  const gi = spot.hours.indexOf(h);
  const stamp = gi === state.nowIdx ? "NOW"
    : `${String(h.hour).padStart(2, "0")}:00${state.hoverIdx == null && !state.pinnedHour ? " · BEST" : ""}`;

  if (h.wind_dir == null) { svg.innerHTML = ""; return; }

  const pt = (bearing, rad) => [
    C + rad * Math.sin(bearing * Math.PI / 180),
    C - rad * Math.cos(bearing * Math.PI / 180),
  ];

  // Degree ticks every 10, labelled every 30 — chart convention.
  let ticks = "";
  for (let b = 0; b < 360; b += 10) {
    const major = b % 30 === 0;
    const [x1, y1] = pt(b, RING);
    const [x2, y2] = pt(b, RING - (major ? 9 : 4));
    ticks += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${major ? "#5d8fa0" : "#2b5866"}" stroke-width="${major ? 1.3 : .8}"/>`;
  }
  const labels = [["N", 0], ["E", 90], ["S", 180], ["W", 270]].map(([t, b]) => {
    const [x, y] = pt(b, RING - 21);
    return `<text x="${x}" y="${y + 4}" text-anchor="middle" fill="${b === 0 ? "#d9c9a3" : "#7ea7b2"}" font-size="12" font-weight="600" letter-spacing="1">${t}</text>`;
  }).join("");

  // The tide, as sea filling the middle of the dial. Empty porthole = dead low,
  // brim full = high water. Scrub the slider and it rises and falls.
  const RIN = 78;
  let water = "";
  if (h.tide) {
    const lvl = Math.max(0, Math.min(1, h.tide.pct));
    const wy = C + RIN - 2 * RIN * lvl;
    const amp = 3.2, phase = state.idx * 0.45;
    let surface = `M ${C - RIN},${wy.toFixed(2)}`;
    for (let x = -RIN + 2; x <= RIN; x += 4) {
      surface += ` L ${C + x},${(wy + Math.sin(x / 13 + phase) * amp).toFixed(2)}`;
    }
    water =
      `<g clip-path="url(#port)">` +
        `<path d="${surface} L ${C + RIN},${C + RIN} L ${C - RIN},${C + RIN} Z" fill="url(#sea)"/>` +
        `<path d="${surface}" fill="none" stroke="#7fdcf2" stroke-width="1.5" opacity=".8"/>` +
      `</g>`;
  }
  // Scrim so the readout stays crisp whatever the water is doing behind it.
  const scrim = `<ellipse cx="${C}" cy="${C + 6}" rx="64" ry="46" fill="#061620" opacity=".5"/>`;

  const turn = h.tide && h.tide.next_turn;
  const tideCaption = turn
    ? `<text x="${C}" y="${C + 66}" text-anchor="middle" fill="#7fdcf2" font-size="9.5" letter-spacing="1.4" opacity=".9">` +
      `${h.tide.rising ? "▲" : "▼"} ${turn.type} ${fmtH(Math.max(0, turn.in_h))}` +
      `</text>`
    : "";

  // Swell readout, for the spots that actually get any.
  const swell = (h.wave_m != null && h.wave_m > 0.2 && spot.character !== "flatwater")
    ? `<text x="${C}" y="${C + 97}" text-anchor="middle" fill="#4fb8d8" font-size="9" letter-spacing="1.5" opacity=".75">SWELL ${h.wave_m}M${h.period_s ? " @ " + h.period_s + "S" : ""}${h.swell_dir_txt ? " " + h.swell_dir_txt : ""}</text>`
    : "";

  const to = (h.wind_dir + 180) % 360;
  const [tx, ty] = pt(to, 86);
  const [bx, by] = pt(h.wind_dir, 86);
  const col = BAND_COL[h.band];

  svg.innerHTML = `
    <defs>
      <radialGradient id="water" cx="50%" cy="45%">
        <stop offset="0%" stop-color="#0d2c38"/><stop offset="100%" stop-color="#061a24"/>
      </radialGradient>
      <marker id="arw" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto">
        <polygon points="0 0, 7 3.5, 0 7" fill="${col}"/>
      </marker>
      <linearGradient id="sea" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#3fa3c8" stop-opacity=".55"/>
        <stop offset="100%" stop-color="#06283c" stop-opacity=".9"/>
      </linearGradient>
      <clipPath id="port"><circle cx="${C}" cy="${C}" r="${RIN}"/></clipPath>
    </defs>
    <circle cx="${C}" cy="${C}" r="${RING}" fill="url(#water)" stroke="#1c414e"/>
    ${ticks}
    ${compassRose(C, pt)}
    ${labels}
    <circle cx="${C}" cy="${C}" r="${RIN}" fill="#061620" opacity=".9"/>
    ${water}
    ${scrim}
    <circle cx="${C}" cy="${C}" r="${RIN}" fill="none" stroke="#2f6a7d" stroke-width="1.2"/>
    <line x1="${bx}" y1="${by}" x2="${tx}" y2="${ty}" stroke="${col}" stroke-width="3.5" marker-end="url(#arw)" opacity=".95"/>
    <text x="${C}" y="${C - 46}" text-anchor="middle" fill="#d9c9a3" font-size="10" letter-spacing="2" opacity=".9">${esc(stamp)}</text>
    <text x="${C}" y="${C - 10}" text-anchor="middle" fill="#e9f4f5" font-size="40" font-weight="700">${Math.round(h.wind_kn)}</text>
    <text x="${C}" y="${C + 10}" text-anchor="middle" fill="#a8ccd6" font-size="11" letter-spacing="2">KN ${h.wind_dir_txt}</text>
    <text x="${C}" y="${C + 29}" text-anchor="middle" fill="#8fb6c2" font-size="10">gusts ${Math.round(h.gust_kn)}</text>
    <text x="${C}" y="${C + 48}" text-anchor="middle" fill="${col}" font-size="12" font-weight="600" letter-spacing="1">${esc(h.wing.size || "—")}</text>
    ${tideCaption}
    ${swell}
  `;
}

// Little wave along the bottom of a tile. Amplitude = swell height, so a
// glance tells you flat water from a lumpy day.
function waveGlyph(waveM, band) {
  const amp = Math.max(0.8, Math.min(5.5, (waveM ?? 0) * 2.4));
  const col = BAND_COL[band];
  const line = (phase, y, op, w) => {
    let d = `M0,${y}`;
    for (let x = 0; x <= 180; x += 6) {
      d += ` L${x},${(y + Math.sin((x / 22) + phase) * amp).toFixed(2)}`;
    }
    return `<path d="${d}" fill="none" stroke="${col}" stroke-width="${w}" opacity="${op}"/>`;
  };
  return `<svg class="spot-wave" viewBox="0 0 180 16" preserveAspectRatio="none">
    ${line(0, 8, .55, 1.6)}${line(2.1, 11.5, .28, 1.2)}</svg>`;
}

function drawDetail() {
  const spot = state.data.spots.find((s) => s.id === state.selected) || state.data.spots[0];
  const h = hourAt(spot);
  const dayRow = dayOf(spot);
  const dayHours = spot.hours.filter((x) => x.time.slice(0, 10) === dayRow.date);

  // Admiralty heights are metres above chart datum (always positive and
  // meaningful). Open-Meteo's are relative to mean sea level, so they go
  // negative — show those as a position through the range instead.
  let tide = "no tide data";
  if (h.tide) {
    const nt = h.tide.next_turn;
    const hw = nt
      ? `${nt.type === "HW" ? "high" : "low"} water in ${fmtH(Math.max(0, nt.in_h))}`
      : (h.tide.hours_to_hw >= 0 ? `HW in ${fmtH(h.tide.hours_to_hw)}` : `HW ${fmtH(-h.tide.hours_to_hw)} ago`);
    const level = spot.tide_source === "admiralty"
      ? `${h.tide.height.toFixed(1)}m`
      : `${Math.round(h.tide.pct * 100)}% of range`;
    tide = `${level} · ${h.tide.state} · ${hw}`;
  }

  const stats = [
    ["Score", `${h.score}<span style="font-size:12px;color:var(--dim)">/100</span>`],
    ["Wind", `${h.wind_kn} <span style="font-size:12px;color:var(--dim)">gust ${h.gust_kn}</span> kn ${h.wind_dir_txt}`],
    ["Wing", h.band !== "no" && h.wing.size
      ? h.wing.text + (h.wing.alt ? `<span style="font-size:12px;color:var(--dim)"> or ${h.wing.alt}</span>` : "")
      : `<span style="color:var(--dim)">${esc(h.wing.text)}</span>`],
    ["Tide", tide],
    ["Swell", swellStat(h)],
    ["Air", h.temp_c != null ? `${Math.round(h.temp_c)}°C` : "—"],
  ];

  const chart = dayChart(spot, dayHours, dayRow);

  const tab = state.data.day_tabs[state.day] || {};
  const windowTxt = dayRow.window
    ? `best window ${dayRow.window.from}–${dayRow.window.to} (${dayRow.window.hours}h, peaks ${dayRow.window.peak_score})`
    : `no window above 55 — peaks ${dayRow.best_score} at ${String(dayRow.peak_hour).padStart(2, "0")}:00`;

  $("detail").innerHTML =
    `<h3>${esc(spot.name)}</h3>` +
    `<div class="sub">${tab.is_today && tab.partial ? "Rest of today" : esc(dayRow.label)} · ${windowTxt}` +
    `${state.pinnedHour ? ` · showing ${String(h.hour).padStart(2, "0")}:00` : ""}</div>` +
    `<div class="sub" style="margin-top:-12px">${esc(spot.character.replace("_", " "))} · tides from ${esc(spot.tide_station || spot.tide_source || "—")} · ${esc(swellSourceLabel(spot))}</div>` +
    `<div class="stats">${stats.map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("")}</div>` +
    windowChips(dayRow) +
    chart +
    `<ul class="reasons">` +
      (h.warnings || []).map((w) => `<li class="warn">${esc(w)}</li>`).join("") +
      (h.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("") +
    `</ul>` +
    `<div class="notes"><ul class="reasons">${spot.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>`;

  // Clicking an hour pins the whole board to it; clicking it again releases
  // back to the day overview.
  $("detail").querySelectorAll(".daychart .bar").forEach((el) => {
    el.style.cursor = "pointer";
    el.onclick = () => {
      const i = +el.dataset.i;
      state.pinnedHour = !(state.pinnedHour && state.idx === i);
      state.idx = i;
      render();
    };
  });
  wireChartScrub(spot, dayHours);

  // A window chip jumps to its peak hour.
  $("detail").querySelectorAll(".chip[data-h]").forEach((el) => {
    el.onclick = () => {
      const hr = +el.dataset.h;
      const target = dayHours.find((x) => x.hour === hr);
      if (!target) return;
      state.idx = spot.hours.indexOf(target);
      state.pinnedHour = true;
      render();
    };
  });
}

// "When should I go" as chips. Falls back to a relaxed threshold so a mediocre
// day still tells you its least-bad stretch rather than showing nothing.
function windowChips(dayRow) {
  const strong = dayRow.windows || [];
  const list = strong.length ? strong : (dayRow.windows_any || []);
  if (!list.length) {
    return `<div class="chips"><span class="chip none">No rideable window this day</span></div>`;
  }
  const soft = !strong.length;
  return `<div class="chips">` +
    `<span class="chips-label">${soft ? "Least bad" : "Best windows"}</span>` +
    list.map((w, i) => `
      <span class="chip ${band(w.peak_score)}${i === 0 ? " top" : ""}" data-h="${w.peak_hour}">
        <b>${w.from}–${w.to}</b>
        <span class="chip-m">${w.hours}h · peaks ${w.peak_score} · ${esc(w.wing.size || "—")}</span>
      </span>`).join("") +
    `</div>`;
}

const band = (s) => (s >= 72 ? "go" : s >= 55 ? "good" : s >= 38 ? "marginal" : "no");

// The day as a chart: hourly rating bars with the wind over them, and the tide
// running underneath on the same time axis — so a score dip lines up visibly
// with the tide being wrong.
function dayChart(spot, hours, dayRow) {
  if (!hours.length) return "";

  const W = 960, H = 258;
  const L = 34, R = 20, TOP = 18;
  const PLOT_B = 168;                     // bottom of the score/wind plot
  const TIDE_T = 196, TIDE_B = 240;       // the water strip
  const cw = (W - L - R) / hours.length;  // column width per hour

  const x = (i) => L + i * cw;
  const xMid = (i) => L + (i + 0.5) * cw;
  const yScore = (s) => PLOT_B - (s / 100) * (PLOT_B - TOP);

  const winds = hours.map((h) => h.gust_kn ?? h.wind_kn ?? 0);
  const wMax = Math.max(28, Math.ceil(Math.max(...winds) / 5) * 5);
  const yWind = (v) => PLOT_B - (v / wMax) * (PLOT_B - TOP);

  // Shade the recommended window behind everything.
  const best = (dayRow.windows && dayRow.windows[0]) || (dayRow.windows_any && dayRow.windows_any[0]);
  let shade = "";
  if (best) {
    const a = hours.findIndex((h) => h.hour === best.from_hour);
    const b = hours.findIndex((h) => h.hour === best.to_hour);
    if (a >= 0 && b >= a) {
      shade = `<rect x="${x(a)}" y="${TOP}" width="${(b - a + 1) * cw}" height="${TIDE_B - TOP}" fill="#35d98a" opacity=".07"/>` +
        `<text x="${xMid((a + b) / 2)}" y="${TOP - 5}" text-anchor="middle" fill="#35d98a" font-size="10" letter-spacing="1.2">BEST ${best.from}–${best.to}</text>`;
    }
  }

  // Gridlines + score axis.
  let grid = "";
  for (const s of [0, 25, 50, 75, 100]) {
    grid += `<line x1="${L}" y1="${yScore(s)}" x2="${W - R}" y2="${yScore(s)}" stroke="#1c414e" stroke-width=".7" opacity="${s === 0 ? 1 : .5}"/>` +
      `<text x="${L - 6}" y="${yScore(s) + 3}" text-anchor="end" fill="#5d8fa0" font-size="9">${s}</text>`;
  }
  // The 55 "worth going" line.
  grid += `<line x1="${L}" y1="${yScore(55)}" x2="${W - R}" y2="${yScore(55)}" stroke="#a7d155" stroke-width=".8" stroke-dasharray="4 4" opacity=".45"/>`;

  // Score bars.
  const bars = hours.map((h, i) => {
    const y = yScore(h.score);
    const gi = spot.hours.indexOf(h);
    const sel = state.pinnedHour && gi === state.idx;
    return `<rect class="bar" data-i="${gi}" x="${x(i) + 1.2}" y="${y}" width="${cw - 2.4}" height="${Math.max(1.5, PLOT_B - y)}" rx="1.5"
      fill="${BAND_COL[h.band]}" opacity="${h.band === "no" ? .5 : .92}"
      stroke="${sel ? "#d9c9a3" : "none"}" stroke-width="1.4"><title>${String(h.hour).padStart(2, "0")}:00 — ${h.score}/100 · ${h.wind_kn}kn ${h.wind_dir_txt} · ${h.wing.size || "no wing"}</title></rect>`;
  }).join("");

  // Wind: mean line with the gust range behind it.
  const gustArea = hours.map((h, i) => `${xMid(i)},${yWind(h.gust_kn ?? h.wind_kn ?? 0)}`).join(" ")
    + " " + hours.map((h, i) => `${xMid(hours.length - 1 - i)},${yWind(hours[hours.length - 1 - i].wind_kn ?? 0)}`).join(" ");
  const windLine = hours.map((h, i) => `${xMid(i)},${yWind(h.wind_kn ?? 0)}`).join(" ");

  // Tide strip.
  let tide = "";
  const tided = hours.map((h, i) => ({ h, i })).filter((p) => p.h.tide);
  if (tided.length >= 4) {
    const yT = (pct) => TIDE_B - pct * (TIDE_B - TIDE_T);
    const line = tided.map(({ h, i }) => `${xMid(i)},${yT(h.tide.pct).toFixed(1)}`).join(" ");
    const first = tided[0], last = tided[tided.length - 1];
    tide =
      `<polygon points="${xMid(first.i)},${TIDE_B} ${line} ${xMid(last.i)},${TIDE_B}" fill="url(#tideFill)"/>` +
      `<polyline points="${line}" fill="none" stroke="#7fdcf2" stroke-width="1.6" opacity=".9"/>`;
    // Label the turns.
    tided.forEach(({ h, i }, k) => {
      if (k === 0 || k === tided.length - 1) return;
      const p = tided[k - 1].h.tide.pct, n = tided[k + 1].h.tide.pct, c = h.tide.pct;
      const isHW = c >= p && c >= n && c > 0.9;
      const isLW = c <= p && c <= n && c < 0.1;
      if (!isHW && !isLW) return;
      tide += `<circle cx="${xMid(i)}" cy="${yT(c)}" r="2.6" fill="#7fdcf2"/>` +
        `<text x="${xMid(i)}" y="${isHW ? yT(c) - 6 : yT(c) + 12}" text-anchor="middle" fill="#7fdcf2" font-size="8.5" letter-spacing=".8">${isHW ? "HW" : "LW"} ${String(h.hour).padStart(2, "0")}:00</text>`;
    });
  }

  // Hour axis + "now".
  const axis = hours.map((h, i) => h.hour % 3 === 0
    ? `<text x="${xMid(i)}" y="${PLOT_B + 15}" text-anchor="middle" fill="#5d8fa0" font-size="9.5">${String(h.hour).padStart(2, "0")}</text>` : "").join("");
  const nowI = hours.findIndex((h) => spot.hours.indexOf(h) === state.nowIdx);
  const nowLine = nowI >= 0
    ? `<line x1="${xMid(nowI)}" y1="${TOP}" x2="${xMid(nowI)}" y2="${TIDE_B}" stroke="#d9c9a3" stroke-width="1" stroke-dasharray="3 3" opacity=".8"/>
       <text x="${xMid(nowI)}" y="${TIDE_B + 12}" text-anchor="middle" fill="#d9c9a3" font-size="8.5" letter-spacing="1">NOW</text>` : "";

  return `<svg class="daychart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    <defs><linearGradient id="tideFill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#3fa3c8" stop-opacity=".5"/>
      <stop offset="100%" stop-color="#06283c" stop-opacity=".15"/>
    </linearGradient></defs>
    ${shade}${grid}${bars}
    <polygon points="${gustArea}" fill="#e9f4f5" opacity=".07"/>
    <polyline points="${windLine}" fill="none" stroke="#e9f4f5" stroke-width="1.3" opacity=".55" stroke-dasharray="5 3"/>
    <text x="${W - R}" y="${TOP + 2}" text-anchor="end" fill="#8fb6c2" font-size="9" opacity=".8">wind — dashed, to ${wMax}kn</text>
    ${axis}
    <text x="${L - 6}" y="${TIDE_T + 4}" text-anchor="end" fill="#5d8fa0" font-size="9">HW</text>
    <text x="${L - 6}" y="${TIDE_B + 3}" text-anchor="end" fill="#5d8fa0" font-size="9">LW</text>
    ${tide}${nowLine}
    <line id="chart-cursor" x1="0" y1="${TOP}" x2="0" y2="${TIDE_B}" stroke="#d9c9a3" stroke-width="1.2" opacity="0" pointer-events="none"/>
  </svg>`;
}

// Scrubbing the chart drives the dial — the chart is the time control now that
// the slider's gone. Only the dial redraws, so it stays smooth and the chart
// doesn't rebuild under the cursor.
function wireChartScrub(spot, dayHours) {
  const svg = $("detail").querySelector(".daychart");
  if (!svg) return;
  const cursor = svg.querySelector("#chart-cursor");
  const W = 960, L = 34, R = 20;
  const cw = (W - L - R) / dayHours.length;

  const hourFromEvent = (e) => {
    const r = svg.getBoundingClientRect();
    const vx = ((e.clientX - r.left) / r.width) * W;
    return Math.max(0, Math.min(dayHours.length - 1, Math.floor((vx - L) / cw)));
  };

  svg.style.cursor = "crosshair";
  svg.addEventListener("mousemove", (e) => {
    const i = hourFromEvent(e);
    state.hoverIdx = spot.hours.indexOf(dayHours[i]);
    cursor.setAttribute("x1", L + (i + 0.5) * cw);
    cursor.setAttribute("x2", L + (i + 0.5) * cw);
    cursor.setAttribute("opacity", ".85");
    drawDial();
  });
  svg.addEventListener("mouseleave", () => {
    state.hoverIdx = null;
    cursor.setAttribute("opacity", "0");
    drawDial();
  });
}

function drawWeek() {
  const d = state.data;
  const days = d.spots[0].days;
  let html = `<div class="hd"></div>` + days.map((x) => `<div class="hd">${esc(x.label)}</div>`).join("");

  d.spots.forEach((spot) => {
    html += `<div class="rowname">${esc(spot.name)}</div>`;
    spot.days.forEach((day) => {
      const w = day.window
        ? `${day.window.from}–${day.window.to}`
        : `${String(day.peak_hour).padStart(2, "0")}:00`;
      html += `<div class="cell ${day.band}" data-spot="${spot.id}" data-date="${day.date}" data-hour="${day.peak_hour}">
        <div class="s">${day.best_score}</div>
        <div class="m">${Math.round(day.wind_kn)}kn ${day.wind_dir_txt}</div>
        <div class="m">${w}</div>
        <div class="m">${esc(day.wing.size || "—")}${day.wave_school ? " 🌊" : ""}</div>
      </div>`;
    });
  });

  $("week-grid").innerHTML = html;
  $("week-grid").querySelectorAll(".cell").forEach((el) => {
    el.onclick = () => {
      const di = (state.data.day_tabs || []).findIndex((t) => t.date === el.dataset.date);
      if (di >= 0) state.day = di;
      state.pinnedHour = false;
      state.selected = el.dataset.spot;
      render();
      window.scrollTo({ top: 0, behavior: "smooth" });
    };
  });
}

// --------------------------------------------------------------------------

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const title = (s) => s.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
const fmtH = (h) => {
  const m = Math.round(h * 60);
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}`;
};

function swellStat(h) {
  if (h.wave_m == null) return "—";
  let s = `${h.wave_m}m${h.period_s ? " @ " + h.period_s + "s" : ""}${h.swell_dir_txt ? " " + h.swell_dir_txt : ""}`;
  if (h.wind_wave_m != null && h.wind_wave_m > 0.1) {
    s += ` <span style="font-size:12px;color:var(--dim)">· ${h.wind_wave_m}m chop</span>`;
  }
  return s;
}

function swellSourceLabel(spot) {
  if (spot.swell_source === "copernicus") {
    return `swell from Copernicus 1.5km${spot.swell_grid_km != null ? ` (${spot.swell_grid_km}km)` : ""}`;
  }
  if (spot.swell_source === "open-meteo") return "swell from Open-Meteo (global model)";
  return "swell data unavailable";
}

$("refresh").onclick = () => load();
document.addEventListener("keydown", (e) => {
  if (!state.data) return;
  const n = (state.data.day_tabs || []).length;
  if (e.key === "ArrowRight" && state.day < n - 1) { state.day++; state.pinnedHour = false; render(); }
  if (e.key === "ArrowLeft" && state.day > 0) { state.day--; state.pinnedHour = false; render(); }
});
window.addEventListener("resize", () => { if (state.data) { drawRing(); drawSeabed(); } });

load();
setInterval(load, 15 * 60 * 1000);
