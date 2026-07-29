"""SQLite store for OTEL-ingested Claude Code token usage + optional repo-name map.

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

CREATE TABLE IF NOT EXISTS repo_name_map (
  repo TEXT PRIMARY KEY,            -- normalized repo key
  bill_name TEXT,                   -- OPTIONAL override billing name
                                    -- (defaults to the short repo name if absent)
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
  invoice_number TEXT PRIMARY KEY,
  bill_name TEXT,                  -- billing entity: the repo name (or its override)
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

-- Durable outbox for asynchronous delivery of invoice CSVs to Fabric OneLake.
-- invoice.py enqueues a 'pending' row per file; billing.otel.fabric_sync ships
-- it with retry/backoff and flips it to 'sent'. Nothing is lost on a crash.
CREATE TABLE IF NOT EXISTS fabric_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,                       -- 'summary' | 'line_items'
  period_start TEXT,
  period_end TEXT,
  local_path TEXT,                 -- source CSV on disk
  onelake_path TEXT,               -- destination path under the Lakehouse's Files/
  table_name TEXT,                 -- optional Delta table to load into ('' = files only)
  status TEXT,                     -- pending | sent | failed
  attempts INTEGER DEFAULT 0,
  next_attempt_at TEXT,            -- earliest UTC time to (re)try
  last_error TEXT,
  enqueued_at TEXT,
  sent_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_outbox_status ON fabric_outbox(status, next_attempt_at);

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

    # ---- repo -> billing-name override map (optional) ------------------
    def distinct_repos(self):
        return self.db.execute(
            """SELECT t.repo AS repo,
                      COALESCE(m.bill_name, '') AS bill_name,
                      SUM(t.tokens) AS tokens,
                      COUNT(DISTINCT t.user_email) AS users,
                      COUNT(DISTINCT t.session_id) AS sessions
               FROM token_usage t
               LEFT JOIN repo_name_map m ON m.repo = t.repo
               GROUP BY t.repo ORDER BY tokens DESC""").fetchall()

    def set_mapping(self, repo: str, bill_name: str):
        self.db.execute(
            "INSERT OR REPLACE INTO repo_name_map(repo,bill_name,updated_at) VALUES(?,?,?)",
            (repo, bill_name, _now()))

    def get_mapping(self) -> dict:
        """repo -> override billing name (only rows that have been overridden)."""
        return {r["repo"]: r["bill_name"]
                for r in self.db.execute("SELECT repo, bill_name FROM repo_name_map")}

    # ---- Fabric delivery outbox (async) --------------------------------
    def fabric_enqueue(self, *, kind, period_start, period_end, local_path,
                       onelake_path, table_name=""):
        """Queue one file for delivery. Re-queues cleanly on re-run: any not-yet-
        sent row for the same (kind, period) is replaced so we never pile up
        duplicate pending deliveries."""
        self.db.execute(
            """DELETE FROM fabric_outbox
               WHERE kind=? AND period_start=? AND period_end=? AND status!='sent'""",
            (kind, period_start, period_end))
        self.db.execute(
            """INSERT INTO fabric_outbox
               (kind, period_start, period_end, local_path, onelake_path,
                table_name, status, attempts, next_attempt_at, enqueued_at)
               VALUES (?,?,?,?,?,?, 'pending', 0, ?, ?)""",
            (kind, period_start, period_end, local_path, onelake_path,
             table_name, _now(), _now()))
        self.db.commit()

    def fabric_pending(self, now_iso: str, max_attempts: int = 5):
        """Rows due for a delivery attempt now (pending and not backing off,
        under the attempt ceiling)."""
        return self.db.execute(
            """SELECT * FROM fabric_outbox
               WHERE status='pending' AND attempts < ?
                 AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
               ORDER BY id""", (max_attempts, now_iso)).fetchall()

    def fabric_mark_sent(self, row_id: int):
        self.db.execute(
            "UPDATE fabric_outbox SET status='sent', sent_at=?, last_error=NULL "
            "WHERE id=?", (_now(), row_id))
        self.db.commit()

    def fabric_mark_retry(self, row_id: int, error: str, next_attempt_at: str,
                          max_attempts: int = 5):
        """Record a failed attempt; keep 'pending' for another try, or flip to
        'failed' once the attempt ceiling is reached."""
        self.db.execute(
            """UPDATE fabric_outbox
               SET attempts = attempts + 1,
                   last_error = ?,
                   next_attempt_at = ?,
                   status = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE 'pending' END
               WHERE id=?""",
            (error[:2000], next_attempt_at, max_attempts, row_id))
        self.db.commit()

    def fabric_outbox_counts(self) -> dict:
        return {r["status"]: r["n"] for r in self.db.execute(
            "SELECT status, COUNT(*) n FROM fabric_outbox GROUP BY status")}

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.commit()
        self.db.close()
