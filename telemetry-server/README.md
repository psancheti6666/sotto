# Sotto telemetry server

A tiny Cloudflare Worker + D1 database that receives Sotto's anonymous, daily,
content-free usage rollups and shows the aggregate. First-party (you own it),
free tier, no third-party analytics processor, **no IP ever stored**.

## What it stores

One row per `(anonymous install id, day)`:
`{id, date, platform, version, dictations, words}`. Nothing else — no audio, no
transcripts, no app names, no IP, no hostname. See `schema.sql`.

## Deploy (≈5 minutes, one time)

You need a free Cloudflare account and the Wrangler CLI (`npm i -g wrangler`,
then `wrangler login`).

```bash
cd telemetry-server

# 1. Create the D1 database, then paste the printed database_id into wrangler.toml
wrangler d1 create sotto-telemetry

# 2. Create the table
wrangler d1 execute sotto-telemetry --remote --file=schema.sql

# 3. Set the admin dashboard password (this is YOUR access — pick a strong one)
wrangler secret put ADMIN_TOKEN

# 4. Ship it
wrangler deploy
```

`wrangler deploy` prints your Worker URL, e.g.
`https://sotto-telemetry.<your-subdomain>.workers.dev`.

## Turn collection on

Telemetry stays completely inert until the client knows where to send. After
deploying, paste `<your-worker-url>/ingest` into `_DEFAULT_ENDPOINT` in
[`sotto/telemetry.py`](../sotto/telemetry.py) and cut a release. Until then,
nothing is ever sent (and you can test locally with
`SOTTO_TELEMETRY_URL=http://127.0.0.1:8787/ingest` against `wrangler dev`).

## Your access

- **Dashboard:** open `https://<your-worker-url>/dashboard` — the browser prompts
  for a password; enter the `ADMIN_TOKEN` you set (username is ignored). Shows
  total installs, active today / 7d / 30d, total words + dictations, and
  breakdowns by platform and version.
- **Public transparency:** `https://<your-worker-url>/stats.json` is open by
  design (aggregate only, no per-id data) so the README badge and any curious
  user can see exactly what's measured.

## README badges

Once deployed, these render live from the public endpoint:

```markdown
![users](https://img.shields.io/endpoint?url=https://<your-worker-url>/badge/users.json)
![active](https://img.shields.io/endpoint?url=https://<your-worker-url>/badge/active.json)
```

## Notes

- The `/ingest` endpoint is public — an open-source client can't hold a secret.
  Payloads are strictly validated and counts are capped, which bounds junk but
  can't fully prevent a determined faker. For a hobby "is this useful?" signal
  that's an acceptable trade; don't treat the numbers as audited.
- Free-tier D1/Workers limits are far above what a project this size needs.
