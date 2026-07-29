"""Drain the delivery outbox: upload queued invoice CSVs to ADLS Gen2 / OneLake.

This is the asynchronous mover. invoice.py enqueues the period's CSVs; this
worker ships them and marks them sent, with retry/backoff on failure so nothing
is lost when the lake or network is briefly unavailable.

    python -m billing.otel.fabric_sync              # run once and exit (cron/timer)
    python -m billing.otel.fabric_sync --watch      # loop, for ad-hoc/manual use
    python -m billing.otel.fabric_sync --status     # show outbox counts
    python -m billing.otel.fabric_sync --dry-run    # show what would upload, no network

Run-and-exit is the intended production mode (see deploy/daily-sync.sh).
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone

from .fabric_client import StorageClient, StorageError, SyncConfigError
from .otel_store import OtelStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _backoff_iso(next_attempt: int) -> str:
    """Exponential backoff, capped at 60 min, from the upcoming attempt number."""
    mins = min(60, 2 ** max(0, next_attempt - 1))
    return (datetime.now(timezone.utc) + timedelta(minutes=mins)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def run_once(store: OtelStore, client: StorageClient | None, max_attempts: int,
             dry_run: bool) -> tuple[int, int]:
    rows = store.fabric_pending(_now_iso(), max_attempts)
    if not rows:
        print("[fabric_sync] nothing pending.")
        return (0, 0)
    ok = fail = 0
    for r in rows:
        tag = f"{r['kind']} {r['period_start']}_{r['period_end']}"
        try:
            if dry_run:
                dest = client.full_uri(r["onelake_path"]) if client else "(target not configured)"
                print(f"[dry-run] {r['local_path']}  ->  {dest}")
                ok += 1
                continue
            url = client.upload_file(r["local_path"], r["onelake_path"])
            store.fabric_mark_sent(r["id"])
            print(f"[fabric_sync] sent {tag}  ->  {url}")
            ok += 1
        except FileNotFoundError as e:
            store.fabric_mark_retry(r["id"], f"local file missing: {e}",
                                    _backoff_iso(r["attempts"] + 1), max_attempts)
            print(f"[fabric_sync] MISSING {tag}: {e}")
            fail += 1
        except (StorageError, Exception) as e:  # keep going; row-level retry
            store.fabric_mark_retry(r["id"], str(e),
                                    _backoff_iso(r["attempts"] + 1), max_attempts)
            print(f"[fabric_sync] FAILED {tag} (attempt {r['attempts'] + 1}): {e}")
            fail += 1
    print(f"[fabric_sync] done: {ok} ok, {fail} failed/deferred")
    return (ok, fail)


def main():
    ap = argparse.ArgumentParser(description="Ship queued invoice CSVs to the data lake.")
    ap.add_argument("--watch", action="store_true", help="loop instead of run-once")
    ap.add_argument("--interval", type=int, default=300, help="--watch poll seconds")
    ap.add_argument("--max-attempts", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true", help="no network; show planned uploads")
    ap.add_argument("--status", action="store_true", help="print outbox counts and exit")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    store = OtelStore(args.db) if args.db else OtelStore()

    if args.status:
        counts = store.fabric_outbox_counts()
        print("[fabric_sync] outbox:", counts or "(empty)")
        store.close()
        return

    try:
        client = StorageClient()
    except SyncConfigError as e:
        if args.dry_run:
            print(f"[fabric_sync] (not configured: {e})")
            client = None
        else:
            print(f"[fabric_sync] not configured: {e}")
            store.close()
            raise SystemExit(2)

    if args.watch:
        print(f"[fabric_sync] watching (every {args.interval}s), Ctrl-C to stop.")
        try:
            while True:
                run_once(store, client, args.max_attempts, args.dry_run)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
        store.close()
    else:
        _ok, fail = run_once(store, client, args.max_attempts, args.dry_run)
        store.close()
        raise SystemExit(1 if fail else 0)


if __name__ == "__main__":
    main()
