// Created by Pratik Sancheti / https://github.com/psancheti6666
// Sotto anonymous usage telemetry — Cloudflare Worker + D1.
//
// Routes:
//   POST /ingest            store one {id,date,platform,version,dictations,words}
//   GET  /stats.json        public aggregate (no per-id data)
//   GET  /badge/users.json  shields.io endpoint badge: total installs
//   GET  /badge/active.json shields.io endpoint badge: active (7d)
//   GET  /dashboard         Basic-Auth dashboard (password = ADMIN_TOKEN secret)
//
// The client IP is never read or stored. Payloads carry only aggregate counts.
// The DB keeps one row per (anonymous install, local day), upserted through the
// day — the full per-day history every chart below is drawn from. The dashboard
// is served with cache-control: no-store (never a stale tab) and auto-refreshes.

const CORS = { "access-control-allow-origin": "*" };
const DAY_MS = 86400000;

function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...CORS, ...extra },
  });
}

function isDate(s) { return typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s); }
function isId(s) { return typeof s === "string" && /^[a-f0-9]{16,64}$/i.test(s); }
function clampInt(v, max) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.min(Math.floor(n), max);
}
// Attacker-controlled labels (the /ingest endpoint is public) — keep them to a
// safe character class so nothing hostile can reach storage, then the admin
// page escapes on render as defence in depth. Non-matching → "unknown" (drop
// the label, keep the count).
function safeTag(v, max) {
  const s = (typeof v === "string" ? v : "").slice(0, max);
  return /^[a-z0-9._-]+$/i.test(s) ? s : "unknown";
}

async function ingest(request, env) {
  let body;
  try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
  // Validate hard: reject anything malformed so junk can't pollute the numbers.
  // (The endpoint is public by necessity — an OSS client can't hold a secret —
  // so treat all input as untrusted; caps bound abuse, they don't prevent it.)
  if (!body || !isId(body.id) || !isDate(body.date)) return json({ error: "invalid" }, 400);
  const row = {
    id: body.id.toLowerCase(),
    date: body.date,
    platform: safeTag(body.platform, 40),
    version: safeTag(body.version, 20),
    dictations: clampInt(body.dictations, 1_000_000),
    words: clampInt(body.words, 100_000_000),
    updated_at: new Date().toISOString(),
  };
  await env.DB.prepare(
    `INSERT INTO daily (id, date, platform, version, dictations, words, updated_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
     ON CONFLICT(id, date) DO UPDATE SET
       platform=excluded.platform, version=excluded.version,
       dictations=MAX(daily.dictations, excluded.dictations),
       words=MAX(daily.words, excluded.words),
       updated_at=excluded.updated_at`
  ).bind(row.id, row.date, row.platform, row.version, row.dictations, row.words, row.updated_at).run();
  return new Response(null, { status: 204, headers: CORS });
}

// ---------------------------------------------------------------- queries --

async function stats(env, withUsers = false) {
  // "today"/cutoffs use the server's UTC date while stored `date` is the
  // client's LOCAL day — so near midnight, users in far offsets can land in an
  // adjacent bucket. Accepted approximation for a rough "is it used?" signal.
  const today = new Date().toISOString().slice(0, 10);
  const cutoff = (days) => new Date(Date.now() - (days - 1) * DAY_MS).toISOString().slice(0, 10);
  const one = async (sql, ...b) => (await env.DB.prepare(sql).bind(...b).first()) || {};
  const all = async (sql, ...b) => (await env.DB.prepare(sql).bind(...b).all()).results || [];

  const totals = await one(
    "SELECT COUNT(DISTINCT id) AS installs, COALESCE(SUM(words),0) AS words, COALESCE(SUM(dictations),0) AS dictations FROM daily");
  const a7 = await one("SELECT COUNT(DISTINCT id) AS n FROM daily WHERE date >= ?1", cutoff(7));
  const a30 = await one("SELECT COUNT(DISTINCT id) AS n FROM daily WHERE date >= ?1", cutoff(30));
  const todayRow = await one(
    "SELECT COUNT(DISTINCT id) AS active, COALESCE(SUM(words),0) AS words, COALESCE(SUM(dictations),0) AS dictations FROM daily WHERE date = ?1",
    today);
  const platforms = await all(
    "SELECT platform, COUNT(DISTINCT id) AS installs, COALESCE(SUM(words),0) AS words FROM daily GROUP BY platform ORDER BY installs DESC");
  const versions = await all(
    "SELECT version, COUNT(DISTINCT id) AS installs FROM daily GROUP BY version ORDER BY installs DESC LIMIT 12");
  // Per-day series (90d) — active installs, words, dictations.
  const daily = await all(
    "SELECT date, COUNT(DISTINCT id) AS active, COALESCE(SUM(words),0) AS words, COALESCE(SUM(dictations),0) AS dictations FROM daily WHERE date >= ?1 GROUP BY date ORDER BY date",
    cutoff(90));
  // New installs per day = each id's first-ever reported day.
  const newInstalls = await all(
    "SELECT first AS date, COUNT(*) AS installs FROM (SELECT id, MIN(date) AS first FROM daily GROUP BY id) GROUP BY first ORDER BY first");

  const s = {
    generated_at: new Date().toISOString(),
    total_installs: totals.installs || 0,
    active_today: todayRow.active || 0,
    active_7d: a7.n || 0,
    active_30d: a30.n || 0,
    total_words: totals.words || 0,
    total_dictations: totals.dictations || 0,
    words_today: todayRow.words || 0,
    dictations_today: todayRow.dictations || 0,
    retention_7d: totals.installs ? Math.round(100 * (a7.n || 0) / totals.installs) : 0,
    platforms, versions, daily, new_installs: newInstalls,
  };
  if (withUsers) {
    // ADMIN-ONLY: per-install summary. Still anonymous (random ids, truncated
    // for display) — never exposed on the public stats.json.
    s.users = await all(
      `SELECT substr(id,1,8) AS id8,
              (SELECT platform FROM daily p WHERE p.id = d.id ORDER BY p.date DESC LIMIT 1) AS platform,
              (SELECT version  FROM daily v WHERE v.id = d.id ORDER BY v.date DESC LIMIT 1) AS version,
              MIN(date) AS first_seen, MAX(date) AS last_seen, COUNT(*) AS days,
              COALESCE(SUM(dictations),0) AS dictations, COALESCE(SUM(words),0) AS words
       FROM daily d GROUP BY id ORDER BY last_seen DESC, words DESC LIMIT 200`);
  }
  return s;
}

// ----------------------------------------------------------------- badges --

function badge(label, message, color) {
  return json({ schemaVersion: 1, label, message: String(message), color },
    200, { "cache-control": "max-age=1800" });
}

// ------------------------------------------------------------------- auth --

function safeEqual(a, b) {
  // Constant-time within equal length (the length itself isn't secret here).
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

function checkAuth(request, env) {
  const secret = env.ADMIN_TOKEN;
  if (!secret) return false;
  const h = request.headers.get("authorization") || "";
  const m = h.match(/^Basic\s+(.+)$/i);
  if (!m) return false;
  let decoded = "";
  try { decoded = atob(m[1]); } catch { return false; }
  const pass = decoded.slice(decoded.indexOf(":") + 1);
  return safeEqual(pass, secret);
}

function unauthorized() {
  return new Response("Auth required", {
    status: 401,
    headers: { "www-authenticate": 'Basic realm="Sotto telemetry"' },
  });
}

// -------------------------------------------------------------- dashboard --

const esc = (v) => String(v ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Zero-fill a sparse per-day series into one slot per calendar day, so chart
// bars are evenly spaced and gaps read as the quiet days they were.
function fillDays(rows, key, days) {
  const by = Object.fromEntries(rows.map(r => [r.date, r[key] || 0]));
  const out = [];
  const start = Date.now() - (days - 1) * DAY_MS;
  for (let i = 0; i < days; i++) {
    const d = new Date(start + i * DAY_MS).toISOString().slice(0, 10);
    out.push({ date: d, v: by[d] || 0 });
  }
  return out;
}

function barChart(series, color) {
  const W = 640, H = 110, PAD = 2, BASE = H - 4;
  const max = Math.max(...series.map(d => d.v), 1);
  const slot = (W - PAD * 2) / series.length;
  const bw = Math.max(1, slot - 1.5);
  const bars = series.map((d, i) => {
    const h = d.v ? Math.max(2, Math.round((d.v / max) * (H - 20))) : 0;
    if (!h) return "";
    return `<rect x="${(PAD + i * slot).toFixed(1)}" y="${BASE - h}" width="${bw.toFixed(1)}" height="${h}" rx="1" fill="${color}"><title>${esc(d.date)} · ${esc(d.v.toLocaleString())}</title></rect>`;
  }).join("");
  const first = series[0]?.date || "", last = series[series.length - 1]?.date || "";
  return `<div class=chart><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <line x1="0" y1="${BASE + 0.5}" x2="${W}" y2="${BASE + 0.5}" stroke="#e7e2d8"/>${bars}</svg>
  <div class=meta><span>${esc(first)}</span><span>peak ${esc(max.toLocaleString())}</span><span>${esc(last)}</span></div></div>`;
}

function adminPage(s) {
  const updated = esc(String(s.generated_at).replace("T", " ").slice(0, 16)) + " UTC";
  const card = (n, l) => `<div class=card><div class=n>${esc(n)}</div><div class=l>${esc(l)}</div></div>`;
  const rows = (arr, cols) => arr.map(r => `<tr>${cols.map(c => `<td>${esc(typeof r[c] === "number" ? r[c].toLocaleString() : r[c])}</td>`).join("")}</tr>`).join("");
  const DAYS = 60;
  const active = barChart(fillDays(s.daily, "active", DAYS), "#232939");
  const words = barChart(fillDays(s.daily, "words", DAYS), "#ff6f61");
  const installs = barChart(fillDays(s.new_installs, "installs", DAYS), "#1a7f5a");
  const users = (s.users || []).map(u => `<tr><td class=mono>${esc(u.id8)}…</td><td>${esc(u.platform)}</td><td>${esc(u.version)}</td><td>${esc(u.first_seen)}</td><td>${esc(u.last_seen)}</td><td>${esc(u.days)}</td><td>${esc(u.dictations.toLocaleString())}</td><td>${esc(u.words.toLocaleString())}</td></tr>`).join("");
  return `<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Sotto — usage</title>
<meta http-equiv=refresh content=300>
<style>
 :root{--ink:#232939;--paper:#faf7f2;--line:#e7e2d8;--mut:#8a8578;--coral:#ff6f61}
 body{font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:900px;margin:36px auto;padding:0 20px;color:var(--ink);background:var(--paper)}
 h1{font-size:22px;margin:0 0 2px} h3{margin:30px 0 10px;font-size:15px}
 .muted{color:var(--mut);font-size:12.5px}
 .cards{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}
 .card{flex:1 1 130px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 14px}
 .card .n{font-size:26px;font-weight:700;letter-spacing:-.5px} .card .l{color:var(--mut);font-size:12.5px}
 .chart{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 12px 8px}
 .chart svg{width:100%;height:110px;display:block}
 .chart .meta{display:flex;justify-content:space-between;color:var(--mut);font-size:11.5px;padding-top:4px}
 .grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
 @media(max-width:680px){.grid2{grid-template-columns:1fr}}
 .tw{overflow-x:auto} table{border-collapse:collapse;width:100%;margin:6px 0 8px}
 td,th{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;font-size:13px;white-space:nowrap}
 th{color:var(--mut);font-weight:600} .mono{font-family:ui-monospace,monospace;font-size:12px}
</style>
<h1>Sotto — usage</h1>
<p class=muted>Last updated ${updated} · live from the database · auto-refreshes every 5 min</p>
<div class=cards>
 ${card(s.total_installs, "total installs")}
 ${card(s.active_today, "active today")}
 ${card(s.active_7d, "active (7d)")}
 ${card(s.active_30d, "active (30d)")}
 ${card(s.retention_7d + "%", "of installs active in 7d")}
</div>
<div class=cards>
 ${card(s.total_words.toLocaleString(), "words dictated")}
 ${card(s.words_today.toLocaleString(), "words today")}
 ${card(s.total_dictations.toLocaleString(), "dictations")}
 ${card(s.dictations_today.toLocaleString(), "dictations today")}
</div>
<h3>Active installs per day <span class=muted>— last ${DAYS} days</span></h3>${active}
<h3>Words dictated per day <span class=muted>— last ${DAYS} days</span></h3>${words}
<h3>New installs per day <span class=muted>— last ${DAYS} days</span></h3>${installs}
<div class=grid2>
 <div><h3>By platform</h3><div class=tw><table><tr><th>platform</th><th>installs</th><th>words</th></tr>${rows(s.platforms, ["platform", "installs", "words"])}</table></div></div>
 <div><h3>By version</h3><div class=tw><table><tr><th>version</th><th>installs</th></tr>${rows(s.versions, ["version", "installs"])}</table></div></div>
</div>
<h3>Installs <span class=muted>— anonymous, per-install lifetime summary</span></h3>
<div class=tw><table><tr><th>install</th><th>platform</th><th>version</th><th>first seen</th><th>last seen</th><th>active days</th><th>dictations</th><th>words</th></tr>${users}</table></div>
<p class=muted>anonymous counts only · no content · no IP stored · public aggregate at /stats.json</p>`;
}

// ------------------------------------------------------------------ entry --

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const { pathname } = url;
    try {
      if (request.method === "POST" && pathname === "/ingest") return await ingest(request, env);
      if (request.method === "GET" && pathname === "/stats.json")
        return json(await stats(env), 200, { "cache-control": "max-age=300" });
      if (request.method === "GET" && pathname === "/badge/users.json") {
        const s = await stats(env); return badge("users", s.total_installs, "1a7f5a");
      }
      if (request.method === "GET" && pathname === "/badge/active.json") {
        const s = await stats(env); return badge("active (7d)", s.active_7d, "ff6f61");
      }
      if (request.method === "GET" && (pathname === "/dashboard" || pathname === "/")) {
        if (!checkAuth(request, env)) return unauthorized();
        return new Response(adminPage(await stats(env, true)), {
          headers: { "content-type": "text/html; charset=utf-8",
                     "cache-control": "no-store" },
        });
      }
      return new Response("Not found", { status: 404 });
    } catch (e) {
      // Log server-side; never leak internal detail to a public caller.
      console.error("worker error", (e && e.stack) || e);
      return json({ error: "server" }, 500);
    }
  },
};
