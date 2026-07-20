"""Pull Analytics data for a date range into the local store.

Usage:
    python -m billing.ingest --start 2026-07-01 --end 2026-07-15

Pulls four views:
    1. usage grouped by product        -> product_usage   (chat vs claude_code vs ...)
    2. claude_code usage by model       -> cc_model_usage  (model mix for coding)
    3. cost grouped by cost_type        -> cost            (USD; reveals seat-plan $0)
    4. per-user claude_code usage        -> user_cc_usage   (attribution unit available)
"""

from __future__ import annotations

import argparse
import sys

from .analytics_client import AnalyticsClient, AnalyticsError
from .store import Store


def _pull(label, fn):
    try:
        n = fn()
        print(f"  [ok]   {label}: {n} rows")
    except AnalyticsError as e:
        print(f"  [warn] {label}: skipped ({e})", file=sys.stderr)


def run(start: str, end: str, db: str | None = None) -> None:
    client = AnalyticsClient()
    store = Store(db) if db else Store()

    print(f"Ingesting {start} -> {end}")

    def product_usage():
        n = 0
        for day, row in client.usage_report(start, end, group_by=["product"]):
            store.upsert_product_usage(day, row)
            n += 1
        return n

    def cc_model_usage():
        n = 0
        for day, row in client.usage_report(
                start, end, group_by=["model"], products=["claude_code"]):
            store.upsert_cc_model_usage(day, row)
            n += 1
        return n

    def cost():
        n = 0
        for day, row in client.cost_report(start, end, group_by=["cost_type"]):
            store.upsert_cost(day, row)
            n += 1
        return n

    def user_cc_usage():
        n = 0
        for day, row in client.user_usage_report(
                start, end, products=["claude_code"]):
            store.upsert_user_cc_usage(day, row)
            n += 1
        return n

    _pull("usage by product", product_usage)
    _pull("claude_code by model", cc_model_usage)
    _pull("cost by cost_type", cost)
    _pull("per-user claude_code", user_cc_usage)

    store.set_meta("organization_id", client.org_id)
    store.set_meta("data_refreshed_at", client.data_refreshed_at)
    store.set_meta("range_start", start)
    store.set_meta("range_end", end)
    store.close()
    print(f"Done. org={client.org_id} data_refreshed_at={client.data_refreshed_at}")


def main():
    ap = argparse.ArgumentParser(description="Ingest Anthropic Analytics data.")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (>= 2026-01-01)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive upper bound)")
    ap.add_argument("--db", default=None, help="SQLite path (default ./data/analytics.db)")
    args = ap.parse_args()
    run(args.start, args.end, args.db)


if __name__ == "__main__":
    main()
