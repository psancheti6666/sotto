var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// src/worker.js
var CORS = { "access-control-allow-origin": "*" };
var DAY_MS = 864e5;
function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...CORS, ...extra }
  });
}
__name(json, "json");
function isDate(s) {
  return typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s);
}
__name(isDate, "isDate");
function isId(s) {
  return typeof s === "string" && /^[a-f0-9]{16,64}$/i.test(s);
}
__name(isId, "isId");
function clampInt(v, max) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.min(Math.floor(n), max);
}
__name(clampInt, "clampInt");
function safeTag(v, max) {
  const s = (typeof v === "string" ? v : "").slice(0, max);
  return /^[a-z0-9._-]+$/i.test(s) ? s : "unknown";
}
__name(safeTag, "safeTag");
async function ingest(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "bad json" }, 400);
  }
  if (!body || !isId(body.id) || !isDate(body.date)) return json({ error: "invalid" }, 400);
  const row = {
    id: body.id.toLowerCase(),
    date: body.date,
    platform: safeTag(body.platform, 40),
    version: safeTag(body.version, 20),
    dictations: clampInt(body.dictations, 1e6),
    words: clampInt(body.words, 1e8),
    updated_at: (/* @__PURE__ */ new Date()).toISOString()
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
__name(ingest, "ingest");
async function stats(env) {
  const today = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
  const cutoff = /* @__PURE__ */ __name((days) => new Date(Date.now() - (days - 1) * DAY_MS).toISOString().slice(0, 10), "cutoff");
  const one = /* @__PURE__ */ __name(async (sql, ...b) => await env.DB.prepare(sql).bind(...b).first() || {}, "one");
  const all = /* @__PURE__ */ __name(async (sql, ...b) => (await env.DB.prepare(sql).bind(...b).all()).results || [], "all");
  const totals = await one(
    "SELECT COUNT(DISTINCT id) AS installs, COALESCE(SUM(words),0) AS words, COALESCE(SUM(dictations),0) AS dictations FROM daily"
  );
  const a7 = await one("SELECT COUNT(DISTINCT id) AS n FROM daily WHERE date >= ?1", cutoff(7));
  const a30 = await one("SELECT COUNT(DISTINCT id) AS n FROM daily WHERE date >= ?1", cutoff(30));
  const today_active = await one("SELECT COUNT(DISTINCT id) AS n FROM daily WHERE date = ?1", today);
  const platforms = await all(
    "SELECT platform, COUNT(DISTINCT id) AS installs FROM daily GROUP BY platform ORDER BY installs DESC"
  );
  const versions = await all(
    "SELECT version, COUNT(DISTINCT id) AS installs FROM daily GROUP BY version ORDER BY installs DESC LIMIT 12"
  );
  const daily = await all(
    "SELECT date, COUNT(DISTINCT id) AS active, COALESCE(SUM(words),0) AS words FROM daily WHERE date >= ?1 GROUP BY date ORDER BY date",
    cutoff(30)
  );
  return {
    generated_at: (/* @__PURE__ */ new Date()).toISOString(),
    total_installs: totals.installs || 0,
    active_7d: a7.n || 0,
    active_30d: a30.n || 0,
    active_today: today_active.n || 0,
    total_words: totals.words || 0,
    total_dictations: totals.dictations || 0,
    platforms,
    versions,
    daily
  };
}
__name(stats, "stats");
function badge(label, message, color) {
  return json(
    { schemaVersion: 1, label, message: String(message), color },
    200,
    { "cache-control": "max-age=1800" }
  );
}
__name(badge, "badge");
function unauthorized() {
  return new Response("Auth required", {
    status: 401,
    headers: { "www-authenticate": 'Basic realm="Sotto telemetry"' }
  });
}
__name(unauthorized, "unauthorized");
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}
__name(safeEqual, "safeEqual");
function checkAuth(request, env) {
  const secret = env.ADMIN_TOKEN;
  if (!secret) return false;
  const h = request.headers.get("authorization") || "";
  const m = h.match(/^Basic\s+(.+)$/i);
  if (!m) return false;
  let decoded = "";
  try {
    decoded = atob(m[1]);
  } catch {
    return false;
  }
  const pass = decoded.slice(decoded.indexOf(":") + 1);
  return safeEqual(pass, secret);
}
__name(checkAuth, "checkAuth");
function adminPage(s) {
  const esc = /* @__PURE__ */ __name((v) => String(v ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  ), "esc");
  const rows = /* @__PURE__ */ __name((arr, cols) => arr.map((r) => `<tr>${cols.map((c) => `<td>${esc(r[c])}</td>`).join("")}</tr>`).join(""), "rows");
  const spark = s.daily.map((d) => `<tr><td>${esc(d.date)}</td><td>${esc(d.active)}</td><td>${esc(d.words)}</td></tr>`).join("");
  const updated = esc(String(s.generated_at).replace("T", " ").slice(0, 16)) + " UTC";
  return `<!doctype html><meta charset=utf-8><title>Sotto telemetry</title>
<meta http-equiv=refresh content=300>
<style>
 body{font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:820px;margin:40px auto;padding:0 20px;color:#232939;background:#faf7f2}
 h1{font-size:22px} .cards{display:flex;flex-wrap:wrap;gap:14px;margin:20px 0}
 .card{flex:1 1 140px;background:#fff;border:1px solid #e7e2d8;border-radius:12px;padding:14px}
 .card .n{font-size:28px;font-weight:700} .card .l{color:#8a8578;font-size:13px}
 table{border-collapse:collapse;width:100%;margin:10px 0 26px} td,th{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;font-size:13px}
 .muted{color:#8a8578;font-size:12px}
</style>
<h1>Sotto \u2014 usage</h1>
<p class=muted>Last updated ${updated} \xB7 live from the database, this page auto-refreshes every 5 min</p>
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
<p class=muted>anonymous counts only, no content, no IP stored</p>`;
}
__name(adminPage, "adminPage");
var worker_default = {
  async fetch(request, env) {
    const url = new URL(request.url);
    const { pathname } = url;
    try {
      if (request.method === "POST" && pathname === "/ingest") return await ingest(request, env);
      if (request.method === "GET" && pathname === "/stats.json")
        return json(await stats(env), 200, { "cache-control": "max-age=300" });
      if (request.method === "GET" && pathname === "/badge/users.json") {
        const s = await stats(env);
        return badge("users", s.total_installs, "1a7f5a");
      }
      if (request.method === "GET" && pathname === "/badge/active.json") {
        const s = await stats(env);
        return badge("active (7d)", s.active_7d, "ff6f61");
      }
      if (request.method === "GET" && (pathname === "/dashboard" || pathname === "/")) {
        if (!checkAuth(request, env)) return unauthorized();
        return new Response(adminPage(await stats(env)), {
          headers: { "content-type": "text/html; charset=utf-8" }
        });
      }
      return new Response("Not found", { status: 404 });
    } catch (e) {
      console.error("worker error", e && e.stack || e);
      return json({ error: "server" }, 500);
    }
  }
};
export {
  worker_default as default
};
//# sourceMappingURL=worker.js.map
