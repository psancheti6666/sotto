// Created by Pratik Sancheti / https://github.com/psancheti6666
// Sotto anonymous usage telemetry — Cloudflare Worker + D1.
//
// Routes:
//   POST /ingest            store one {id,date,platform,version,dictations,words}
//   GET  /stats.json        public aggregate (no per-id data)
//   GET  /badge/users.json  shields.io endpoint badge: total installs
//   GET  /badge/active.json shields.io endpoint badge: active (7d)
//   GET  /admin             Basic-Auth dashboard (password = ADMIN_TOKEN secret)
//
// The client IP is never read or stored. Payloads carry only aggregate counts.

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
function str(v, max) { return (typeof v === "string" ? v : "").slice(0, max); }

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
    platform: str(body.platform, 40) || "unknown",
    version: str(body.version, 20) || "unknown",
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

async function stats(env) {
  const today = new Date().toISOString().slice(0, 10);
  const cutoff = (days) => new Date(Date.now() - (days - 1) * DAY_MS).toISOString().slice(0, 10);
  const one = async (sql, ...b) => (await env.DB.prepare(sql).bind(...b).first()) || {};
  const all = async (sql, ...b) => (await env.DB.prepare(sql).bind(...b).all()).results || [];

  const totals = await one(
    "SELECT COUNT(DISTINCT id) AS installs, COALESCE(SUM(words),0) AS words, COALESCE(SUM(dictations),0) AS dictations FROM daily");
  const a7 = await one("SELECT COUNT(DISTINCT id) AS n FROM daily WHERE date >= ?1", cutoff(7));
  const a30 = await one("SELECT COUNT(DISTINCT id) AS n FROM daily WHERE date >= ?1", cutoff(30));
  const today_active = await one("SELECT COUNT(DISTINCT id) AS n FROM daily WHERE date = ?1", today);
  const platforms = await all(
    "SELECT platform, COUNT(DISTINCT id) AS installs FROM daily GROUP BY platform ORDER BY installs DESC");
  const versions = await all(
    "SELECT version, COUNT(DISTINCT id) AS installs FROM daily GROUP BY version ORDER BY installs DESC LIMIT 12");
  const daily = await all(
    "SELECT date, COUNT(DISTINCT id) AS active, COALESCE(SUM(words),0) AS words FROM daily WHERE date >= ?1 GROUP BY date ORDER BY date",
    cutoff(30));

  return {
    generated_at: new Date().toISOString(),
    total_installs: totals.installs || 0,
    active_7d: a7.n || 0,
    active_30d: a30.n || 0,
    active_today: today_active.n || 0,
    total_words: totals.words || 0,
    total_dictations: totals.dictations || 0,
    platforms, versions, daily,
  };
}

function badge(label, message, color) {
  return json({ schemaVersion: 1, label, message: String(message), color },
    200, { "cache-control": "max-age=1800" });
}

function unauthorized() {
  return new Response("Auth required", {
    status: 401,
    headers: { "www-authenticate": 'Basic realm="Sotto telemetry"' },
  });
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
  return pass === secret;
}

function adminPage(s) {
  const rows = (arr, cols) => arr.map(r => `<tr>${cols.map(c => `<td>${r[c] ?? ""}</td>`).join("")}</tr>`).join("");
  const spark = s.daily.map(d => `<tr><td>${d.date}</td><td>${d.active}</td><td>${d.words}</td></tr>`).join("");
  return `<!doctype html><meta charset=utf-8><title>Sotto telemetry</title>
<style>
 body{font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:820px;margin:40px auto;padding:0 20px;color:#232939;background:#faf7f2}
 h1{font-size:22px} .cards{display:flex;flex-wrap:wrap;gap:14px;margin:20px 0}
 .card{flex:1 1 140px;background:#fff;border:1px solid #e7e2d8;border-radius:12px;padding:14px}
 .card .n{font-size:28px;font-weight:700} .card .l{color:#8a8578;font-size:13px}
 table{border-collapse:collapse;width:100%;margin:10px 0 26px} td,th{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;font-size:13px}
 .muted{color:#8a8578;font-size:12px}
</style>
<h1>Sotto — usage</h1>
<div class=cards>
 <div class=card><div class=n>${s.total_installs}</div><div class=l>total installs</div></div>
 <div class=card><div class=n>${s.active_today}</div><div class=l>active today</div></div>
 <div class=card><div class=n>${s.active_7d}</div><div class=l>active (7d)</div></div>
 <div class=card><div class=n>${s.active_30d}</div><div class=l>active (30d)</div></div>
 <div class=card><div class=n>${s.total_words.toLocaleString()}</div><div class=l>words dictated</div></div>
 <div class=card><div class=n>${s.total_dictations.toLocaleString()}</div><div class=l>dictations</div></div>
</div>
<h3>By platform</h3><table><tr><th>platform</th><th>installs</th></tr>${rows(s.platforms, ["platform", "installs"])}</table>
<h3>By version</h3><table><tr><th>version</th><th>installs</th></tr>${rows(s.versions, ["version", "installs"])}</table>
<h3>Last 30 days</h3><table><tr><th>date</th><th>active</th><th>words</th></tr>${spark}</table>
<p class=muted>generated ${s.generated_at} · anonymous counts only, no content, no IP stored</p>`;
}

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
      if (request.method === "GET" && (pathname === "/admin" || pathname === "/")) {
        if (!checkAuth(request, env)) return unauthorized();
        return new Response(adminPage(await stats(env)), {
          headers: { "content-type": "text/html; charset=utf-8" },
        });
      }
      return new Response("Not found", { status: 404 });
    } catch (e) {
      return json({ error: "server", detail: String(e && e.message || e) }, 500);
    }
  },
};
