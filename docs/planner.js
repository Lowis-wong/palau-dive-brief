"use strict";
/* Palau Dive Brief: 3-dive day planner (Project 4).
   Pure functions on brief.json data; runs in the app (offline) and in node for tests.

   A day's sites carry `states`: every 10 min from 06:00 to 18:00, "I3" = incoming level 3,
   "O2" = outgoing level 2, "S1" = slack. Each state gets a score from plan.json; a dive is scored
   over its first `score_window_min` minutes (entry weighted most), minus a penalty if the current
   reverses during the dive and when the site's weather zone has big waves. The plan maximises the
   mean dive score over every order of sites and every entry time from "as soon as we can get
   there" up to `max_wait_min` later, given travel times, dive length and surface intervals. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Planner = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const T0 = 360; // states[0] = 06:00
  const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
  const toHM = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const ceil10 = (m) => Math.ceil(m / 10) * 10;
  const WEIGHTS = [2, 1.5, 1, 1, 0.5, 0.5, 0.5];

  function travel(cfg, a, b) {
    if (a === b) return 0;
    const tm = cfg.travel_min || {};
    const v = (tm[a] && tm[a][b]) ?? (tm[b] && tm[b][a]);
    return v == null ? 40 : v;
  }

  function stateAt(siteDay, m) {
    const k = (m - T0) / 10;
    if (!siteDay || k < 0 || k >= siteDay.states.length || k !== Math.floor(k)) return null;
    const c = siteDay.states[k];
    return c ? { dir: { I: "in", O: "out", S: "slack" }[c[0]], level: +c.slice(1) } : null;
  }

  function stateScore(sc, st) {
    if (!st) return null;
    if (st.dir === "slack") return sc.slack;
    const arr = sc[st.dir];
    return arr[Math.max(0, Math.min(arr.length - 1, st.level - 1))];
  }

  /* Score one dive (0-10) and describe what the current does during it. */
  function scoreDive(cfg, siteDay, siteId, entry, waves) {
    const sc = cfg.scores[siteId];
    if (!sc) return null;
    const n = Math.floor((cfg.score_window_min ?? 40) / 10) + 1;
    let sum = 0, wsum = 0;
    const seen = [];
    for (let i = 0; i < n; i++) {
      const st = stateAt(siteDay, entry + i * 10);
      const s = stateScore(sc, st);
      if (s == null) return null;
      const w = WEIGHTS[i] ?? 0.5;
      sum += s * w; wsum += w;
      if (st.dir !== "slack") seen.push(st.dir);
    }
    let score = sum / wsum;
    const notes = [];
    if (seen.includes("in") && seen.includes("out")) { score -= cfg.turn_penalty ?? 1.5; notes.push("turns during dive"); }
    const wv = waves?.[siteId];
    if (wv != null) {
      for (const [lim, pen] of cfg.wave_penalty || []) {
        if (wv >= lim) { score -= pen; notes.push(`waves ${wv.toFixed(1)} m`); break; }
      }
    }
    // first change of phase within the dive (entry .. entry + dive_min)
    const first = stateAt(siteDay, entry);
    let change = null;
    for (let m = entry + 10; m <= entry + (cfg.dive_min ?? 50); m += 10) {
      const st = stateAt(siteDay, m);
      if (st && st.dir !== first.dir) { change = { time: toHM(m), dir: st.dir, level: st.level }; break; }
    }
    return { score: Math.max(0, Math.min(10, score)), entryState: first, change, notes };
  }

  function waveMap(sites, zones) {
    if (!zones) return null;
    const byZone = Object.fromEntries(zones.map((z) => [z.zone, z.wave_max_m]));
    return Object.fromEntries(sites.map((s) => [s.id, byZone[s.zone] ?? null]));
  }

  /* All plans for a day, best first. sites = brief.sites (meta), day = brief.days[i]. */
  function planDay(day, sites, cfg, opts = {}) {
    const depart = toMin(opts.depart || cfg.depart || "08:30");
    const nDives = cfg.dives ?? 3;
    const lastEntry = toMin(cfg.last_entry || "15:00");
    const maxWait = cfg.max_wait_min ?? 60;
    const buf = cfg.entry_buffer_min ?? 5;
    const diveMin = cfg.dive_min ?? 50;
    const waves = waveMap(sites, opts.zones);
    const ids = sites.map((s) => s.id).filter((id) => cfg.scores[id] && day.sites.some((x) => x.id === id));
    const sd = Object.fromEntries(day.sites.map((x) => [x.id, x]));
    const cache = new Map();
    const dive = (id, m) => {
      const k = id + m;
      if (!cache.has(k)) cache.set(k, scoreDive(cfg, sd[id], id, m, waves));
      return cache.get(k);
    };
    const plans = [];
    const walk = (seq, prevId, prevEntry, travelTot, waitTot) => {
      const i = seq.length;
      if (i === nDives) {
        const mean = seq.reduce((a, d) => a + d.score, 0) / nDives;
        const total = mean - (cfg.travel_penalty_per_min ?? 0.01) * travelTot - (cfg.wait_penalty_per_min ?? 0.01) * waitTot;
        const last = seq[seq.length - 1];
        plans.push({ dives: seq, score: mean, rank: total, travel_min: travelTot, wait_min: waitTot,
          back: toHM(toMin(last.entry) + diveMin + travel(cfg, last.site, "harbor")) });
        return;
      }
      for (const id of ids) {
        if (!cfg.allow_repeat && seq.some((d) => d.site === id)) continue;
        const run = travel(cfg, prevId, id);
        let earliest;
        if (i === 0) earliest = depart + run + buf;
        else {
          const si = i === (cfg.lunch_after_dive ?? 2) ? (cfg.lunch_surface_min ?? 90) : (cfg.surface_min ?? 60);
          earliest = prevEntry + diveMin + Math.max(si, run + buf);
        }
        earliest = ceil10(earliest);
        for (let m = earliest; m <= Math.min(earliest + maxWait, lastEntry); m += 10) {
          const r = dive(id, m);
          if (!r) continue;
          walk([...seq, { site: id, entry: toHM(m), run_min: run, wait_min: m - earliest, ...r }],
            id, m, travelTot + run, waitTot + (m - earliest));
        }
      }
    };
    walk([], "harbor", null, 0, 0);
    plans.sort((a, b) => b.rank - a.rank);
    return plans;
  }

  /* Best plan plus the best one with a different order of sites. */
  function recommend(day, sites, cfg, opts = {}) {
    const all = planDay(day, sites, cfg, opts);
    if (!all.length) return null;
    const best = all[0];
    const key = (p) => p.dives.map((d) => d.site).join(">");
    const alt = all.find((p) => key(p) !== key(best)) || null;
    return { best, alt, n: all.length };
  }

  return { planDay, recommend, scoreDive, travel, toMin, toHM };
});
