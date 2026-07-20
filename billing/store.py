"""SQLite store for pulled Analytics data.

Four tables mirror the four report views we pull. Each is keyed on its
dimensions so re-ingesting the same range is idempotent (INSERT OR REPLACE).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_DB = os.environ.get("BILLING_DB", "./data/analytics.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_usage (
  day TEXT, product TEXT,
  uncached_input INTEGER, cache_creation_1h INTEGER, cache_creation_5m INTEGER,
  cache_read INTEGER, output INTEGER, web_search_requests INTEGER, requests INTEGER,
  ingested_at TEXT,
  PRIMARY KEY (day, product)
);
CREATE TABLE IF NOT EXISTS cc_model_usage (
  day TEXT, model TEXT,
  uncached_input INTEGER, cache_creation_1h INTEGER, cache_creation_5m INTEGER,
  cache_read INTEGER, output INTEGER, web_search_requests INTEGER, requests INTEGER,
  ingested_at TEXT,
  PRIMARY KEY (day, model)
);
CREATE TABLE IF NOT EXISTS cost (
  day TEXT, cost_type TEXT,
  amount REAL, list_amount REAL, currency TEXT, ingested_at TEXT,
  PRIMARY KEY (day, cost_type)
);
CREATE TABLE IF NOT EXISTS user_cc_usage (
  day TEXT, user_id TEXT, email TEXT, name TEXT,
  uncached_input INTEGER, cache_creation_1h INTEGER, cache_creation_5m INTEGER,
  cache_read INTEGER, output INTEGER, total_tokens INTEGER,
  web_search_requests INTEGER, requests INTEGER, ingested_at TEXT,
  PRIMARY KEY (day, user_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(iso: str | None) -> str:
    return (iso or "")[:10]


def tokens(row: dict) -> dict:
    """Pull the token fields out of a result row (handles nested cache_creation)."""
    cc = row.get("cache_creation") or {}
    stu = row.get("server_tool_use") or {}
    return {
        "uncached_input": row.get("uncached_input_tokens") or 0,
        "cache_creation_1h": cc.get("ephemeral_1h_input_tokens") or 0,
        "cache_creation_5m": cc.get("ephemeral_5m_input_tokens") or 0,
        "cache_read": row.get("cache_read_input_tokens") or 0,
        "output": row.get("output_tokens") or 0,
        "web_search_requests": stu.get("web_search_requests") or 0,
        "requests": row.get("requests") or 0,
    }


class Store:
    def __init__(self, path: str = DEFAULT_DB):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def set_meta(self, key: str, value: str | None):
        if value is None:
            return
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                        (key, value))

    def get_meta(self, key: str) -> str | None:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def upsert_product_usage(self, day_iso, row):
        t = tokens(row)
        self.db.execute(
            """INSERT OR REPLACE INTO product_usage
               (day,product,uncached_input,cache_creation_1h,cache_creation_5m,
                cache_read,output,web_search_requests,requests,ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (_day(day_iso), row.get("product") or "unknown", t["uncached_input"],
             t["cache_creation_1h"], t["cache_creation_5m"], t["cache_read"],
             t["output"], t["web_search_requests"], t["requests"], _now()))

    def upsert_cc_model_usage(self, day_iso, row):
        t = tokens(row)
        self.db.execute(
            """INSERT OR REPLACE INTO cc_model_usage
               (day,model,uncached_input,cache_creation_1h,cache_creation_5m,
                cache_read,output,web_search_requests,requests,ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (_day(day_iso), row.get("model") or "unknown", t["uncached_input"],
             t["cache_creation_1h"], t["cache_creation_5m"], t["cache_read"],
             t["output"], t["web_search_requests"], t["requests"], _now()))

    def upsert_cost(self, day_iso, row):
        self.db.execute(
            """INSERT OR REPLACE INTO cost
               (day,cost_type,amount,list_amount,currency,ingested_at)
               VALUES (?,?,?,?,?,?)""",
            (_day(day_iso), row.get("cost_type") or "unknown",
             float(row.get("amount") or 0), float(row.get("list_amount") or 0),
             row.get("currency") or "USD", _now()))

    def upsert_user_cc_usage(self, day_iso, row):
        t = tokens(row)
        actor = row.get("actor") or {}
        self.db.execute(
            """INSERT OR REPLACE INTO user_cc_usage
               (day,user_id,email,name,uncached_input,cache_creation_1h,
                cache_creation_5m,cache_read,output,total_tokens,
                web_search_requests,requests,ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_day(day_iso), actor.get("user_id") or "unknown",
             actor.get("email") or "", actor.get("name") or "",
             t["uncached_input"], t["cache_creation_1h"], t["cache_creation_5m"],
             t["cache_read"], t["output"], row.get("total_tokens") or 0,
             t["web_search_requests"], t["requests"], _now()))

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.commit()
        self.db.close()
