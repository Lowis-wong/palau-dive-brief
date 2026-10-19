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
  const w = brief.weather;
  const last = brief.days[brief.days.length - 1].date;
  $("#fresh").innerHTML =
    `Built <b>${esc(brief.generated_at)}</b> (${ago(brief.generated_at)}) · ` +
    (w ? `weather ${ago(w.generated_at)}` : "no weather") +
    ` · tides to ${last.slice(5)}`;
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
function dayWeather(day) {
  return brief.weather?.days?.[day.date] || null;
}
function renderSummary() {
  const d = brief.days[dayIdx];
  const ts = d.tide_strength;
  const wz = dayWeather(d);
  const west = wz?.find((z) => z.zone === "west-south") || wz?.[0];
  const flags = [...d.flags];
  if (wz) {
    const rain = Math.max(...wz.map((z) => z.rain_prev24_mm ?? 0));
    if (rain >= 20) flags.push(`${rain} mm rain in the last 24 h: outgoing water from the lagoon will be murkier than usual.`);
    const east = wz.find((z) => z.zone === "east");
    if (east?.wave_max_m >= 1.8) flags.push(`East side waves to ${east.wave_max_m} m: east reef exposed.`);
    if (west?.wave_max_m >= 1.5) flags.push(`West side waves to ${west.wave_max_m} m: check conditions on the outer reef.`);
    const wind = Math.max(...wz.map((z) => z.wind_max_kn ?? 0));
    if (wind >= 18) flags.push(`Wind up to ${wind} kn: surface conditions and pick-ups harder.`);
  }
  $("#summary").innerHTML = `
    <div class="stats">
      <div class="stat"><div class="k">Tide</div><div class="v">${esc(ts.label)}</div>
        <div class="s num">range ${ts.max_range_m ?? "–"} m · ×${ts.ratio}</div></div>
      <div class="stat"><div class="k">Flow</div><div class="v num">${d.flow_level ?? "–"}<small style="font-size:13px;color:var(--muted)">/5</small>${meter(d.flow_level || 0)}</div>
        <div class="s">peak at Malakal</div></div>
      <div class="stat"><div class="k">Moon</div><div class="v" style="font-size:16px">${esc(d.moon.phase)}</div>
        <div class="s num">day ${d.moon.age_days} · ${Math.round(d.moon.illumination * 100)}%</div></div>
    </div>
    ${west ? `<p class="note num">West reef: wind ${west.wind_dir ?? ""} ${west.wind_max_kn ?? "–"} kn (gust ${west.gust_max_kn ?? "–"}) · swell ${west.swell_dir ?? ""} ${west.swell_max_m ?? "–"} m @ ${west.swell_period_s ?? "–"} s</p>` : ""}
    ${flags.length ? `<ul class="flags">${flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}`;
}

/* ---------------------------------------------------------------- chart */
function renderChart() {
  const d = brief.days[dayIdx];
  const W = 360, H = 180, L = 30, R = 8, T = 22, B = 22;
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
  for (let h = 6; h <= 18; h += 3) s += `<g class="axis"><text x="${x(h * 60)}" y="${H - 6}" text-anchor="middle">${String(h).padStart(2, "0")}</text></g>`;
  s += `<line id="planLine" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--text)" stroke-width="2" opacity=".55"/>`;
  const path = pts.map((v, i) => (v == null ? "" : `${i === 0 ? "M" : "L"}${x(360 + i * 10).toFixed(1)},${y(v).toFixed(1)}`)).join("");
  s += `<path class="curve" d="${path}"/>`;
  for (const e of d.events) {
    const m = tToMin(e.time);
    if (m < 360 || m > 1080) continue;
    const ex = x(m), ey = y(e.h);
    s += `<circle class="ext" cx="${ex}" cy="${ey}" r="4"/><text class="extlabel" x="${Math.min(Math.max(ex, L + 24), W - R - 24)}" y="${e.kind === "H" ? ey - 9 : ey + 15}" text-anchor="middle">${e.kind} ${e.time} · ${e.h.toFixed(2)}</text>`;
  }
  const now = palauNow();
  if (now.date === d.date && now.mins >= 360 && now.mins <= 1080) s += `<line class="now" x1="${x(now.mins)}" x2="${x(now.mins)}" y1="${T}" y2="${H - B}"/>`;
  s += `<line id="cross" class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>`;
  s += `<rect id="hit" x="${L}" y="0" width="${pw}" height="${H}" fill="transparent"/></svg><div id="tip" class="tip" hidden></div>`;
  $("#chart").innerHTML = s;
  movePlanLine();

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
}

/* ---------------------------------------------------------------- sites */
function renderSites() {
  const d = brief.days[dayIdx];
  $("#planTime").textContent = kToTime(planK);
  const ev = d.events.map((e) => `<b>${e.kind === "H" ? "High" : "Low"}</b> ${e.time} (${e.h.toFixed(2)} m)`).join(" · ");
  $("#events").innerHTML = ev;
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
      <div class="bar-lab"><span>06</span><span>12</span><span>18</span></div>
      <p class="best">${best}</p>
      ${advice ? `<p class="entry">${esc(advice.entry)}</p>` : ""}
      ${stageNote ? `<p class="meta">${esc(stageNote)}</p>` : ""}
      <p class="meta"><span class="badge">lag ${meta.lag_min >= 0 ? "+" : ""}${meta.lag_min} min</span> <span class="badge">${conf}</span></p>
      <p class="meta">${esc(meta.hazards)}</p>
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
    <td class="num">${z.wind_dir ?? ""} ${z.wind_max_kn ?? "–"}<br><small>g${z.gust_max_kn ?? "–"}</small></td>
    <td class="num">${z.swell_dir ?? ""} ${z.swell_max_m ?? "–"}<br><small>${z.swell_period_s ?? "–"} s</small></td>
    <td class="num">${z.wave_max_m ?? "–"}</td></tr>`).join("");
  const rain = wz[0]?.rain_prev24_mm;
  $("#weather").innerHTML = `<div class="card-head"><h2>Sea state · 06–18 max</h2></div>
    <table><thead><tr><th>Area</th><th>Wind kn</th><th>Swell m</th><th>Waves m</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="note">Model forecast (Open-Meteo / DWD), updated ${esc(brief.weather.generated_at)}. ${rain != null ? `Rain previous 24 h: ${rain} mm.` : ""} Ocean-current model is too coarse for channels, so it is not used.</p>`;
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
function predictedAt(siteId, date, time) {
  const day = brief?.days.find((d) => d.date === date);
  const sd = day?.sites.find((s) => s.id === siteId);
  if (!sd || !time) return null;
  const k = Math.round((tToMin(time) - 360) / 10);
  const c = sd.states[k];
  return c ? `${DIR_SHORT[DIR[c[0]]]} ${c.slice(1)}/5` : null;
}
function logLine(l) {
  const name = brief?.sites.find((s) => s.id === l.site)?.name || l.site;
  return `${l.date} ${l.time} | ${name} | found ${l.dir || "?"} str ${l.str ?? "?"} | vis ${l.vis || "?"} m | pred ${l.pred || "n/a"} | ${l.match || "-"}${l.notes ? " | " + l.notes : ""}`;
}
function renderLogs() {
  const logs = store.get(LOG_KEY, []);
  if (!logs.length) { $("#logList").innerHTML = `<p class="empty">Saved logs stay on this phone until you copy them to Claude.</p>`; return; }
  $("#logList").innerHTML = `<div class="loghead"><b>${logs.length} unsent</b>
    <span><button class="ghost" id="copyLogs" type="button">Copy for Claude</button> <button class="ghost" id="clearLogs" type="button">Clear</button></span></div>` +
    logs.map((l, i) => `<div class="logitem"><span>${esc(logLine(l))}</span><button type="button" data-del="${i}" aria-label="Delete">×</button></div>`).join("");
  $("#copyLogs").onclick = async () => {
    const txt = "Dive log feedback:\n" + logs.map(logLine).join("\n");
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
    l.pred = predictedAt(l.site, l.date, l.time);
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
addEventListener("offline", net);
net(); initLogForm(); load();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
