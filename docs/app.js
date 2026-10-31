"use strict";
/* Palau Dive Brief — renders docs/data/brief.json. Works offline via sw.js. */

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const DIR = { I: "in", O: "out", S: "slack" };
const DIR_LABEL = { in: "Incoming", out: "Outgoing", slack: "Slack" };
const DIR_SHORT = { in: "IN", out: "OUT", slack: "SLACK" };
const segDir = (d) => (d === "incoming" ? "in" : d === "outgoing" ? "out" : "slack");
const LOG_KEY = "pdb.logs.v1";

let brief = null;
let dayIdx = 0;
let planK = 15; // 10-min steps after 06:00 (15 => 08:30)

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

function palauNow() {
  const d = new Date(Date.now() + 9 * 3600e3);
  return { date: d.toISOString().slice(0, 10), mins: d.getUTCHours() * 60 + d.getUTCMinutes() };
}
const kToTime = (k) => { const m = 360 + k * 10; return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };
const tToMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
function ago(stamp) {
  const t = Date.parse(stamp.replace(" ", "T") + ":00+09:00");
  const h = (Date.now() - t) / 3600e3;
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min ago`;
  if (h < 48) return `${Math.round(h)} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

/* ---------------------------------------------------------------- load */
async function load() {
  try {
    const r = await fetch("data/brief.json", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    brief = await r.json();
  } catch (e) {
    $("#fresh").textContent = "No data yet. Open once with signal to download the brief.";
    return;
  }
  const today = palauNow().date;
  const i = brief.days.findIndex((d) => d.date === today);
  dayIdx = i >= 0 ? i : 0;
  if (i >= 0) {
    const m = palauNow().mins;
    if (m > 360 && m < 1080) planK = Math.round((m - 360) / 10);
  }
  $("#plan").value = planK;
  renderAll();
}

function renderAll() {
  renderFresh(); renderDays(); renderSummary(); renderChart(); renderSites(); renderWeather(); renderNWS(); renderLogSites(); renderLogs();
}

function renderFresh() {
  const g = brief.generated_at; // "YYYY-MM-DD HH:MM"
  const last = brief.days[brief.days.length - 1].date;
  $("#title").innerHTML = `<b class="num">${esc(g.slice(5, 10))} ${esc(g.slice(11, 16))}</b> <span>Brief</span>`;
  $("#fresh").innerHTML = `Updated ${ago(g)} · tides to ${last.slice(8)}/${last.slice(5, 7)}` +
    (brief.weather ? "" : " · no weather");
  const stale = Object.entries(brief.status || {}).filter(([, v]) => String(v).startsWith("stale"));
  if (stale.length) $("#fresh").innerHTML += ` · <b>${stale.map(([k]) => k).join(", ")} stale</b>`;
}

function renderDays() {
  const nav = $("#days");
  nav.innerHTML = brief.days.map((d, i) =>
    `<button type="button" data-i="${i}" aria-pressed="${i === dayIdx}">${esc(d.weekday)}<small>${d.date.slice(8)}/${d.date.slice(5, 7)}</small></button>`).join("");
  nav.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    dayIdx = +b.dataset.i; renderAll();
  }));
  nav.querySelector('[aria-pressed="true"]')?.scrollIntoView({ inline: "center", block: "nearest" });
}

/* ---------------------------------------------------------------- summary */
function meter(n) {
  return `<span class="meter" aria-hidden="true">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</span>`;
}
/* Tide strength 1-5 from today's largest range relative to the 5-week mean (neap ... spring). */
function tideLevel(ratio) {
  return ratio < 0.7 ? 1 : ratio < 0.85 ? 2 : ratio < 1.15 ? 3 : ratio < 1.3 ? 4 : 5;
}
/* Moon as seen from Palau (northern tropics): waxing lit on the right. */
function moonIcon(ageDays) {
  const r = 14, c = 16;
  const theta = (2 * Math.PI * ageDays) / 29.530588853;
  const waxing = ageDays < 29.530588853 / 2;
  const rx = Math.abs(Math.cos(theta)) * r;
  const gibbous = Math.cos(theta) < 0; // more than half lit
  const lit = `M${c} ${c - r} A${r} ${r} 0 0 1 ${c} ${c + r} A${rx.toFixed(2)} ${r} 0 0 ${gibbous ? 1 : 0} ${c} ${c - r} Z`;
  return `<svg class="moon" viewBox="0 0 32 32" aria-hidden="true">
    <circle cx="${c}" cy="${c}" r="${r}" class="moon-dark"/>
    <path d="${lit}" class="moon-lit" ${waxing ? "" : `transform="translate(${2 * c} 0) scale(-1 1)"`}/>
    <circle cx="${c}" cy="${c}" r="${r}" class="moon-ring"/></svg>`;
}
function dayWeather(day) {
  return brief.weather?.days?.[day.date] || null;
}
const fmt0 = (v) => (v == null ? "–" : Math.round(v));
/* Small arrow pointing where the wind/swell is going (from + 180). */
function arrow(fromDeg) {
  if (fromDeg == null) return "";
  return `<svg class="arr" viewBox="0 0 16 16" aria-hidden="true"><g transform="rotate(${(fromDeg + 180) % 360} 8 8)">
    <line x1="8" y1="14" x2="8" y2="4"/><path d="M8 1.5 L11.5 6.5 L8 5.2 L4.5 6.5 Z"/></g></svg>`;
}
const PTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const toDeg = (deg, name) => (deg != null ? deg : name ? PTS.indexOf(name) * 22.5 : null);

/* Compass dial: arrow shows where the wind/swell is GOING; it enters from the side it comes FROM. */
function dial(fromDeg, label) {
  if (fromDeg == null) return "";
  const rot = (fromDeg + 180) % 360;
  return `<svg class="dial" viewBox="0 0 64 64" role="img" aria-label="${esc(label)}">
    <circle cx="32" cy="32" r="27" class="ring"/>
    ${[0, 90, 180, 270].map((a) => `<line class="tick" x1="32" y1="5" x2="32" y2="10" transform="rotate(${a} 32 32)"/>`).join("")}
    <text x="32" y="17" class="n" text-anchor="middle">N</text>
    <g transform="rotate(${rot} 32 32)">
      <line x1="32" y1="52" x2="32" y2="18" class="shaft"/>
      <path d="M32 12 L38.5 22 L32 19.5 L25.5 22 Z" class="head"/>
    </g></svg>`;
}

function renderSummary() {
  const d = brief.days[dayIdx];
  const ts = d.tide_strength;
  const wz = dayWeather(d);
  const westZones = (wz || []).filter((z) => z.zone.startsWith("west"));
  const pick = (k) => westZones.reduce((m, z) => ((z[k] ?? -1) > (m?.[k] ?? -1) ? z : m), null);
  const windZ = pick("wind_max_kn"), swellZ = pick("swell_max_m");
  const east = wz?.find((z) => z.zone === "east");
  const flags = [...d.flags];
  if (east?.wave_max_m >= 1.8) flags.push(`East reef waves to ${east.wave_max_m} m.`);
  const rain24 = Math.max(0, ...(wz || []).map((z) => z.rain_prev24_mm ?? 0));
  if (rain24 >= 20) flags.push(`Rain ${Math.round(rain24)} mm in 24 h: murkier outgoing water.`);
  const pump = d.swell_pump;
  const pumpRow = pump ? `<div class="pump">
      <span class="pump-k">West swell pump</span>
      <span class="pump-meter" aria-hidden="true">${[1, 2, 3].map((i) => `<i class="${i <= pump.level ? "on" : ""}"></i>`).join("")}</span>
      <span class="pump-v">${esc(pump.label[0].toUpperCase() + pump.label.slice(1))}</span>
      <span class="pump-s num">${pump.level ? `lagoon ≈ +${pump.typical_lift_cm} cm · ` : ""}W waves ${pump.west_wave_mean_m.toFixed(1)} m avg</span>
    </div>` : "";

  // Only warnings stronger than the near-permanent small craft advisory.
  const nwsFresh = brief.nws && (Date.parse(d.date) - Date.parse(brief.nws.fetched_at.slice(0, 10))) / 864e5 <= 1;
  const alerts = nwsFresh ? (brief.nws.headlines || []).filter((h) => !/small craft advisory/i.test(h)) : [];

  const conf = d.confidence;
  const factors = conf?.factors || [];
  const confRow = conf ? `<div class="conf ${conf.level}">
      <div class="conf-top"><span class="conf-k">Confidence</span>
        ${conf.score != null ? `<span class="conf-score num"><b>${conf.score}</b>/10</span>` : ""}
        <span class="conf-v">${esc(conf.level === "unknown" ? "tide only" : conf.level[0].toUpperCase() + conf.level.slice(1))}</span></div>
      ${factors.length ? `<div class="chips">${factors.map((f) => `<span class="chip">${esc(f)}</span>`).join("")}</div>` : ""}
    </div>` : "";

  const windBox = windZ ? `<div class="cond">${dial(toDeg(windZ.wind_dir_deg, windZ.wind_dir), `Wind from ${windZ.wind_dir}`)}
      <div><div class="k">Wind</div><div class="cv num">${fmt0(windZ.wind_max_kn)}<span class="gust">/${fmt0(windZ.gust_max_kn)}</span><small> kn</small></div>
      <div class="s num">from ${esc(windZ.wind_dir ?? "–")}</div></div></div>` : "";
  const swellBox = swellZ ? `<div class="cond">${dial(toDeg(swellZ.swell_dir_deg, swellZ.swell_dir), `Swell from ${swellZ.swell_dir}`)}
      <div><div class="k">Swell</div><div class="cv num">${swellZ.swell_max_m ?? "–"}<small> m</small></div>
      <div class="s num">from ${esc(swellZ.swell_dir ?? "–")} · ${swellZ.swell_period_s != null ? Math.round(swellZ.swell_period_s) : "–"} s</div></div></div>` : "";

  $("#summary").innerHTML = `
    ${alerts.map((h) => `<p class="alert">NWS: ${esc(h)}</p>`).join("")}
    <div class="stats">
      <div class="stat"><div class="k">Tide</div><div class="v">${esc(ts.label)}${meter(tideLevel(ts.ratio))}</div>
        <div class="s num">range ${ts.max_range_m ?? "–"} m</div></div>
      <div class="stat"><div class="k">Flow</div><div class="v num">${d.flow_level ?? "–"}<small style="font-size:13px;color:var(--muted)">/5</small>${meter(d.flow_level || 0)}</div>
        <div class="s">peak at Malakal</div></div>
      <div class="stat"><div class="k">Moon</div>
        <div class="moonrow">${moonIcon(d.moon.age_days)}<div class="v moon-name">${esc(d.moon.phase)}</div></div>
        <div class="s num">day ${d.moon.age_days} · ${Math.round(d.moon.illumination * 100)}%</div></div>
    </div>
    ${windBox || swellBox ? `<div class="conds">${windBox}${swellBox}</div>` : ""}
    ${pumpRow}
    ${confRow}
    ${flags.length ? `<ul class="flags">${flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}`;
}

/* ---------------------------------------------------------------- chart */
function renderChart() {
  const d = brief.days[dayIdx];
  const W = 360, H = 190, L = 30, R = 8, T = 30, B = 22;
  const k0 = 36, k1 = 108; // 06:00 .. 18:00 in 10-min steps
  const pts = d.curve_10min.slice(k0, k1 + 1);
  const vals = pts.filter((v) => v != null);
  if (!vals.length) { $("#chart").textContent = "No tide data for this day."; return; }
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max(0.1, (hi - lo) * 0.15); lo -= pad; hi += pad;
  const x = (min) => L + ((min - 360) / 720) * (W - L - R);
  const y = (h) => T + (1 - (h - lo) / (hi - lo)) * (H - T - B);
  const pw = W - L - R;

  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Tide curve 06:00 to 18:00 with incoming and outgoing periods">`;
  for (const g of d.reference_segments) {
    const a = x(tToMin(g.start)), b = x(Math.min(tToMin(g.end) + 5, 1080));
    s += `<rect x="${a}" y="${T}" width="${Math.max(0, b - a)}" height="${H - T - B}" fill="var(--${segDir(g.direction)}-soft)"/>`;
  }
  const step = (hi - lo) > 1.2 ? 0.5 : 0.25;
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    s += `<line class="gridline" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><g class="axis"><text x="${L - 4}" y="${y(v) + 3}" text-anchor="end">${v.toFixed(step < 0.5 ? 2 : 1)}</text></g>`;
  }
  for (let h = 6; h <= 18; h += 1) s += `<line class="hourtick" x1="${x(h * 60)}" x2="${x(h * 60)}" y1="${H - B}" y2="${H - B + 3}"/><g class="axis"><text x="${x(h * 60)}" y="${H - 6}" text-anchor="middle">${String(h).padStart(2, "0")}</text></g>`;
  // planned entry time (moves with the slider and when the chart is tapped)
  s += `<line id="planLine" x1="0" x2="0" y1="${T}" y2="${H - B}" class="planline"/>
    <g id="planTag"><rect x="-19" y="${T - 17}" width="38" height="14" rx="7" class="plantag"/><text x="0" y="${T - 6.5}" text-anchor="middle" class="plantext"></text></g>`;
  const path = pts.map((v, i) => (v == null ? "" : `${i === 0 ? "M" : "L"}${x(360 + i * 10).toFixed(1)},${y(v).toFixed(1)}`)).join("");
  s += `<path class="curve" d="${path}"/>`;
  const marks = extremesMarked(d);
  for (const e of marks) {
    const m = tToMin(e.time);
    if (m < 360 || m > 1080) continue;
    const ex = x(m), ey = y(e.h);
    s += `<circle class="ext${e.tag ? " extreme" : ""}" cx="${ex}" cy="${ey}" r="4"/><text class="extlabel" x="${Math.min(Math.max(ex, L + 20), W - R - 20)}" y="${e.kind === "H" ? ey - 9 : ey + 15}" text-anchor="middle">${e.time} · ${e.h.toFixed(2)}</text>`;
  }
  s += `<line id="cross" class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>`;
  s += `<rect id="hit" x="${L}" y="0" width="${pw}" height="${H}" fill="transparent"/></svg><div id="tip" class="tip" hidden></div>`;
  $("#chart").innerHTML = s;
  movePlanLine();
  renderEvents(marks);

  const svg = $("#chart svg"), hit = $("#hit"), cross = $("#cross"), tip = $("#tip");
  const show = (ev) => {
    const r = svg.getBoundingClientRect();
    const sx = ((ev.clientX - r.left) / r.width) * W;
    const k = Math.max(0, Math.min(72, Math.round(((sx - L) / pw) * 72)));
    const v = pts[k];
    if (v == null) return;
    const m = 360 + k * 10, px = x(m);
    const seg = d.reference_segments.find((g) => tToMin(g.start) <= m && m <= tToMin(g.end) + 5);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.style.left = `${(px / W) * 100}%`;
    tip.textContent = `${kToTime(k)} · ${v.toFixed(2)} m · ${seg ? DIR_LABEL[segDir(seg.direction)] : ""}`;
  };
  const hide = () => { cross.setAttribute("visibility", "hidden"); tip.hidden = true; };
  hit.addEventListener("pointermove", show);
  hit.addEventListener("pointerdown", show);
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("click", (ev) => { // tap the chart to set the plan time
    const r = svg.getBoundingClientRect();
    planK = Math.max(0, Math.min(72, Math.round(((((ev.clientX - r.left) / r.width) * W - L) / pw) * 72)));
    $("#plan").value = planK; renderSites(); movePlanLine();
  });
}
function movePlanLine() {
  const l = $("#planLine"); if (!l) return;
  const W = 360, L = 30, R = 8;
  const px = L + (planK / 72) * (W - L - R);
  l.setAttribute("x1", px); l.setAttribute("x2", px);
  const tag = $("#planTag");
  tag.setAttribute("transform", `translate(${Math.min(Math.max(px, L + 19), W - R - 19)} 0)`);
  tag.querySelector("text").textContent = kToTime(planK);
}
/* Mixed semidiurnal tide: flag the higher high (HH) and lower low (LL) of the day. */
function extremesMarked(d) {
  const ev = d.events.map((e) => ({ ...e }));
  const hs = ev.filter((e) => e.kind === "H"), ls = ev.filter((e) => e.kind === "L");
  if (hs.length > 1) hs.reduce((a, b) => (b.h > a.h ? b : a)).tag = "HH";
  if (ls.length > 1) ls.reduce((a, b) => (b.h < a.h ? b : a)).tag = "LL";
  return ev;
}
function renderEvents(marks) {
  const row = (kind, label) => {
    const es = marks.filter((e) => e.kind === kind);
    if (!es.length) return "";
    return `<div class="hl"><span class="hl-k">${label}</span>${es.map((e) =>
      `<span class="hl-e${e.tag ? " tagged" : ""}"><span class="num">${e.time}</span> <span class="num hl-h">${e.h.toFixed(2)} m</span>${e.tag ? `<i class="tag ${e.tag}">${e.tag}</i>` : ""}</span>`).join("")}</div>`;
  };
  $("#events").innerHTML = row("H", "High") + row("L", "Low");
}

/* ---------------------------------------------------------------- sites */
function renderSites() {
  const d = brief.days[dayIdx];
  $("#planTime").textContent = kToTime(planK);
  $("#sites").innerHTML = brief.sites.map((meta) => {
    const sd = d.sites.find((x) => x.id === meta.id);
    if (!sd) return "";
    const code = sd.states[planK];
    const dir = code ? DIR[code[0]] : null;
    const lvl = code ? code.slice(1) : "–";
    const advice = dir === "in" ? meta.incoming : dir === "out" ? meta.outgoing : null;
    const stageNote = dir === "slack" ? meta.slack : advice?.notes;
    const bar = sd.segments.map((g) => {
      const span = tToMin(g.end) + 5 - tToMin(g.start);
      return `<span class="${segDir(g.direction)}" style="flex:${span}" title="${DIR_LABEL[segDir(g.direction)]} ${g.start}–${g.end}, peak ${g.peak_level}/5"></span>`;
    }).join("");
    const markLeft = (planK / 72) * 100;
    const best = sd.best ? `Best incoming: <b class="num">${sd.best.start}–${sd.best.end}</b>, peak ${sd.best.peak_level}/5 at ${sd.best.peak_time}` : "No incoming window between 06:00 and 18:00";
    const conf = meta.confidence === "unverified" ? "estimate, not yet calibrated" : `${esc(meta.confidence)} · ${meta.n_logs} logs`;
    return `<article class="site">
      <div class="site-head"><span class="site-name">${esc(meta.name)}</span>
        ${dir ? `<span class="pill ${dir}">${DIR_SHORT[dir]} · ${lvl}/5</span>` : ""}</div>
      <div class="bar">${bar}<i class="mark" style="left:calc(${markLeft}% - 1px)"></i></div>
      <div class="bar-ticks" aria-hidden="true">${Array.from({ length: 13 }, (_, i) => `<i style="left:${(i / 12) * 100}%"></i>`).join("")}</div>
      <div class="bar-lab">${[6, 9, 12, 15, 18].map((h) => `<span style="left:${((h - 6) / 12) * 100}%">${String(h).padStart(2, "0")}</span>`).join("")}</div>
      <p class="best">${best}</p>
      <p class="badges"><span class="badge">lag ${meta.lag_min >= 0 ? "+" : ""}${meta.lag_min} min</span><span class="badge">${conf}</span></p>
      ${advice ? `<p class="entry">${esc(advice.entry)}</p>` : ""}
      <div class="notes">${stageNote ? `<p>${esc(stageNote)}</p>` : ""}<p>${esc(meta.hazards)}</p></div>
    </article>`;
  }).join("");
}

/* ---------------------------------------------------------------- weather */
function renderWeather() {
  const d = brief.days[dayIdx];
  const wz = dayWeather(d);
  if (!wz) {
    $("#weather").innerHTML = `<div class="card-head"><h2>Sea state</h2></div><p class="empty">${brief.weather ? "Wind and wave forecast covers the next 7 days only. Tides above are still valid." : "No weather data downloaded yet."}</p>`;
    return;
  }
  const rows = wz.map((z) => `<tr><td>${esc(z.name)}</td>
    <td class="num"><span class="wx">${arrow(toDeg(z.wind_dir_deg, z.wind_dir))}${fmt0(z.wind_max_kn)}<span class="gust">/${fmt0(z.gust_max_kn)}</span></span><small>${esc(z.wind_dir ?? "")}</small></td>
    <td class="num"><span class="wx">${arrow(toDeg(z.swell_dir_deg, z.swell_dir))}${z.swell_max_m ?? "–"}</span><small>${esc(z.swell_dir ?? "")} · ${z.swell_period_s != null ? Math.round(z.swell_period_s) : "–"} s</small></td>
    <td class="num"><span class="wx">${z.wave_max_m ?? "–"}</span></td></tr>`).join("");
  const rain = wz[0]?.rain_prev24_mm;
  $("#weather").innerHTML = `<div class="card-head"><h2>Sea state · 06–18 max</h2></div>
    <table class="wxt"><thead><tr><th>Area</th><th>Wind</th><th>Swell</th><th>Waves</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="note">Wind kn (mean/gust), swell and waves m; arrows point where it is going. Model forecast (Open-Meteo / DWD), updated ${esc(brief.weather.generated_at)}. ${rain != null ? `Rain previous 24 h: ${rain} mm.` : ""} Ocean-current model is too coarse for channels, so it is not used.</p>`;
}
function renderNWS() {
  const n = brief.nws;
  $("#nwsCard").hidden = !n;
  if (n) $("#nws").textContent = `Fetched ${n.fetched_at}\n\n${n.text}`;
}

/* ---------------------------------------------------------------- dive log */
function renderLogSites() {
  const sel = $("#logForm select[name=site]");
  if (sel.options.length) return;
  sel.innerHTML = brief.sites.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("") + `<option value="other">Other (put name in notes)</option>`;
  const f = $("#logForm");
  if (!f.date.value) f.date.value = palauNow().date;
}
/* Freeze the conditions at the moment a dive is saved. The brief only holds yesterday onward,
   so this is the only chance to keep the forecast that was in hand for that dive. */
function snapshot(siteId, date, time) {
  if (!brief || !date || !time) return null;
  const day = brief.days.find((d) => d.date === date);
  if (!day) return null;
  const meta = brief.sites.find((s) => s.id === siteId);
  const mins = tToMin(time);
  const abs = (dt, hm) => Date.parse(`${dt}T${hm}:00+09:00`);
  const t = abs(date, time);
  const ctx = { v: 1, src: "forecast" };

  // Malakal tide stage
  const ext = brief.days.flatMap((d) => d.events.map((e) => ({ ...e, ts: abs(d.date, e.time) }))).sort((a, b) => a.ts - b.ts);
  const i = ext.findIndex((e) => e.ts > t);
  if (i > 0) {
    const a = ext[i - 1], b = ext[i];
    const k10 = Math.round(mins / 10);
    const cv = day.curve_10min;
    const rate = cv[k10 - 1] != null && cv[k10 + 1] != null ? Math.round(((cv[k10 + 1] - cv[k10 - 1]) / (20 / 60)) * 100) : null;
    ctx.tide = {
      stage: a.kind === "L" ? "incoming" : "outgoing",
      since: `${a.kind} ${a.time}`, h_since: +((t - a.ts) / 3600e3).toFixed(1), h_to_next: +((b.ts - t) / 3600e3).toFixed(1),
      range_m: +Math.abs(b.h - a.h).toFixed(2), ratio: day.tide_strength.ratio, label: day.tide_strength.label, rate_cm_h: rate,
    };
  }
  // Prediction for this site with the parameters in force today
  const sd = day.sites.find((s) => s.id === siteId);
  const code = sd?.states[Math.round((mins - 360) / 10)];
  if (code) ctx.pred = { dir: DIR_SHORT[DIR[code[0]]], level: +code.slice(1), lag_min: meta?.lag_min, factor: meta?.strength_factor };
  // Weather at the entry hour for the site's zone
  const zone = meta?.zone || "west-south";
  const hr = brief.weather?.hourly?.[date]?.[zone]?.[Math.floor(mins / 60)];
  if (hr) {
    const f = brief.weather.hourly.fields;
    ctx.wx = Object.fromEntries(f.map((k, j) => [k, hr[j]]));
    ctx.wx.zone = zone;
    ctx.wx.issued = brief.weather.generated_at;
  }
  const dz = dayWeather(day)?.find((z) => z.zone === zone);
  if (dz) ctx.rain24_mm = dz.rain_prev24_mm;
  if (day.confidence) ctx.conf = { level: day.confidence.level, score: day.confidence.score, factors: day.confidence.factors || [] };
  if (day.swell_pump) ctx.pump = { level: day.swell_pump.level, west_mean_m: day.swell_pump.west_wave_mean_m };
  return ctx;
}
const cmp = (deg) => (deg == null ? "?" : PTS[Math.round((deg % 360) / 22.5) % 16]);
function logLine(l) {
  const name = brief?.sites.find((s) => s.id === l.site)?.name || l.site;
  const p = l.ctx?.pred;
  return `${l.date} ${l.time} | ${name} | found ${l.dir || "?"} str ${l.str ?? "?"} | vis ${l.vis || "?"} m | pred ${p ? `${p.dir} ${p.level}/5 (lag ${p.lag_min})` : l.pred || "n/a"} | ${l.match || "-"}${l.notes ? " | " + l.notes : ""}`;
}
function ctxLine(c) {
  if (!c) return "   (no context saved: Claude to backfill)";
  const parts = [];
  if (c.tide) parts.push(`Malakal ${c.tide.stage} ${c.tide.h_since} h after ${c.tide.since}, ${c.tide.h_to_next} h to turn, range ${c.tide.range_m} m, ${c.tide.label} x${c.tide.ratio}, rate ${c.tide.rate_cm_h ?? "?"} cm/h`);
  if (c.wx) parts.push(`${c.wx.zone} fcst ${c.wx.issued}: swell ${c.wx.swell_m ?? "?"} m ${cmp(c.wx.swell_dir)} ${c.wx.swell_s ?? "?"} s, waves ${c.wx.wave_m ?? "?"} m, wind ${cmp(c.wx.wind_dir)} ${c.wx.wind_kn ?? "?"} kn g${c.wx.gust_kn ?? "?"}`);
  if (c.rain24_mm != null) parts.push(`rain24 ${c.rain24_mm} mm`);
  if (c.pump) parts.push(`pump ${c.pump.level}/3 (W mean ${c.pump.west_mean_m} m)`);
  if (c.conf) parts.push(`conf ${c.conf.score ?? ""}/10 ${c.conf.level}${c.conf.factors.length ? ` (${c.conf.factors.join(", ")})` : ""}`);
  return "   " + parts.join(" | ");
}
function renderLogs() {
  const logs = store.get(LOG_KEY, []);
  if (!logs.length) { $("#logList").innerHTML = `<p class="empty">Saved logs stay on this phone until you copy them to Claude.</p>`; return; }
  $("#logList").innerHTML = `<div class="loghead"><b>${logs.length} unsent</b>
    <span><button class="ghost" id="copyLogs" type="button">Copy for Claude</button> <button class="ghost" id="clearLogs" type="button">Clear</button></span></div>` +
    logs.map((l, i) => `<div class="logitem"><span>${esc(logLine(l))}</span><button type="button" data-del="${i}" aria-label="Delete">×</button></div>`).join("");
  $("#copyLogs").onclick = async () => {
    const txt = "Dive log feedback:\n" + logs.map((l) => `${logLine(l)}\n${ctxLine(l.ctx)}`).join("\n");
    try { await navigator.clipboard.writeText(txt); $("#copyLogs").textContent = "Copied ✓"; }
    catch { prompt("Copy this:", txt); }
  };
  $("#clearLogs").onclick = () => { if (confirm("Clear all saved logs? Only do this after sending them.")) { store.set(LOG_KEY, []); renderLogs(); } };
  $("#logList").querySelectorAll("[data-del]").forEach((b) => b.onclick = () => {
    const ls = store.get(LOG_KEY, []); ls.splice(+b.dataset.del, 1); store.set(LOG_KEY, ls); renderLogs();
  });
}
function initLogForm() {
  const f = $("#logForm");
  const picked = {};
  f.querySelectorAll(".seg").forEach((g) => g.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", "false"));
    b.setAttribute("aria-pressed", "true"); picked[g.dataset.name] = b.dataset.v;
  })));
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    const l = { site: f.site.value, date: f.date.value, time: f.time.value, dir: picked.dir, str: picked.str,
      vis: f.vis.value, match: f.match.value, notes: f.notes.value.trim() };
    l.ctx = snapshot(l.site, l.date, l.time);
    const ls = store.get(LOG_KEY, []); ls.push(l); store.set(LOG_KEY, ls);
    f.time.value = ""; f.vis.value = ""; f.notes.value = ""; f.match.value = "";
    f.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", "false"));
    for (const k in picked) delete picked[k];
    renderLogs();
  });
}

/* ---------------------------------------------------------------- boot */
$("#plan").addEventListener("input", (e) => { planK = +e.target.value; if (brief) { renderSites(); movePlanLine(); } });
function net() { $("#net").hidden = navigator.onLine; }
addEventListener("online", () => { net(); load(); });
// iOS keeps home-screen apps alive in the background: refetch when brought back to the front.
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && navigator.onLine) load(); });
addEventListener("offline", net);
net(); initLogForm(); load();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
