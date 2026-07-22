-- Created by Pratik Sancheti / https://github.com/psancheti6666
-- One row per (anonymous install id, local day). Upserted by /ingest.
-- No IP, no hostname, no content — see telemetry-server/README.md.
CREATE TABLE IF NOT EXISTS daily (
  id         TEXT NOT NULL,   -- random install UUID, tied to nothing
  date       TEXT NOT NULL,   -- YYYY-MM-DD (the client's local day)
  platform   TEXT NOT NULL,   -- e.g. "darwin-arm64"
  version    TEXT NOT NULL,   -- Sotto version string
  dictations INTEGER NOT NULL DEFAULT 0,
  words      INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,   -- server receive time (UTC ISO), for freshness only
  PRIMARY KEY (id, date)
);
CREATE INDEX IF NOT EXISTS daily_by_date ON daily(date);
