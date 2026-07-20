"""SQLite store for OTEL-ingested Claude Code token usage + repo->client map.

Claude Code exports token metrics with DELTA temporality by default: each data
point is an increment. To accumulate correctly and stay idempotent across
re-sends, we store one row per data point keyed by a dp_key that includes the
data point's timestamp; INSERT OR IGNORE dedupes identical points. Billing then
just SUMs `tokens` over rows.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_DB = os.environ.get("OTEL_DB", "./data/otel.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS token_usage (
  dp_key TEXT PRIMARY KEY,          -- dedupe key (dims + timestamp)
  ts TEXT,                          -- data point time (UTC)
  session_id TEXT,
  repo TEXT,                        -- normalized repo key (attribution unit)
  repo_raw TEXT,                    -- original OTEL_RESOURCE_ATTRIBUTES repo value
  user_email TEXT,
  user_id TEXT,
  org_id TEXT,
  model TEXT,
  token_type TEXT,                  -- input | output | cacheRead | cacheCreation
  query_source TEXT,                -- main | subagent | auxiliary
  tokens INTEGER,
  ingested_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_token_repo ON token_usage(repo);

CREATE TABLE IF NOT EXISTS cost_usage (
  dp_key TEXT PRIMARY KEY,          -- dedupe key (dims + timestamp)
  ts TEXT,
  session_id TEXT,
  repo TEXT,
  repo_raw TEXT,
  user_email TEXT,
  user_id TEXT,
  org_id TEXT,
  model TEXT,
  query_source TEXT,
  cost_usd REAL,                    -- Anthropic's actual USD for this datapoint
  ingested_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_cost_repo ON cost_usage(repo);

CREATE TABLE IF NOT EXISTS repo_client_map (
  repo TEXT PRIMARY KEY,            -- normalized repo key
  client TEXT,                      -- client bucket (you assign manually)
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
  invoice_number TEXT PRIMARY KEY,
  client TEXT,
  period_start TEXT,
  period_end TEXT,
  currency TEXT,
  actual_cost REAL,                -- summed claude_code.cost.usage for the period
  markup REAL,
  total_billed REAL,               -- actual_cost * markup
  tokens INTEGER,
  status TEXT,                     -- draft | issued
  generated_at TEXT
);
CREATE TABLE IF NOT EXISTS invoice_line_items (
  invoice_number TEXT,
  repo TEXT,
  model TEXT,
  tokens INTEGER,
  actual_cost REAL,
  billed_amount REAL
);
CREATE INDEX IF NOT EXISTS ix_lineitem_inv ON invoice_line_items(invoice_number);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ns_to_iso(time_unix_nano) -> str:
    try:
        secs = int(time_unix_nano) / 1e9
        return datetime.fromtimestamp(secs, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return _now()


def dp_key(session_id, model, token_type, query_source, time_unix_nano) -> str:
    raw = f"{session_id}|{model}|{token_type}|{query_source}|{time_unix_nano}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class OtelStore:
    def __init__(self, path: str = DEFAULT_DB):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def insert_datapoint(self, *, session_id, repo, repo_raw, user_email, user_id,
                         org_id, model, token_type, query_source, tokens,
                         time_unix_nano) -> bool:
        """Returns True if inserted, False if it was a duplicate."""
        key = dp_key(session_id, model, token_type, query_source, time_unix_nano)
        cur = self.db.execute(
            """INSERT OR IGNORE INTO token_usage
               (dp_key, ts, session_id, repo, repo_raw, user_email, user_id,
                org_id, model, token_type, query_source, tokens, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, _ns_to_iso(time_unix_nano), session_id, repo, repo_raw,
             user_email, user_id, org_id, model, token_type, query_source,
             int(tokens or 0), _now()))
        return cur.rowcount > 0

    def insert_cost_datapoint(self, *, session_id, repo, repo_raw, user_email,
                              user_id, org_id, model, query_source, cost_usd,
                              time_unix_nano) -> bool:
        """Returns True if inserted, False if it was a duplicate."""
        key = dp_key(session_id, model, "__cost__", query_source, time_unix_nano)
        cur = self.db.execute(
            """INSERT OR IGNORE INTO cost_usage
               (dp_key, ts, session_id, repo, repo_raw, user_email, user_id,
                org_id, model, query_source, cost_usd, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, _ns_to_iso(time_unix_nano), session_id, repo, repo_raw,
             user_email, user_id, org_id, model, query_source,
             float(cost_usd or 0), _now()))
        return cur.rowcount > 0

    # ---- repo -> client mapping ----------------------------------------
    def distinct_repos(self):
        return self.db.execute(
            """SELECT t.repo AS repo,
                      COALESCE(m.client, '') AS client,
                      SUM(t.tokens) AS tokens,
                      COUNT(DISTINCT t.user_email) AS users,
                      COUNT(DISTINCT t.session_id) AS sessions
               FROM token_usage t
               LEFT JOIN repo_client_map m ON m.repo = t.repo
               GROUP BY t.repo ORDER BY tokens DESC""").fetchall()

    def set_mapping(self, repo: str, client: str):
        self.db.execute(
            "INSERT OR REPLACE INTO repo_client_map(repo,client,updated_at) VALUES(?,?,?)",
            (repo, client, _now()))

    def get_mapping(self) -> dict:
        return {r["repo"]: r["client"]
                for r in self.db.execute("SELECT repo, client FROM repo_client_map")}

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.commit()
        self.db.close()
