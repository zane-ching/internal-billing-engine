"""Periodic sync orchestrator: regenerate the current month's invoices
(month-to-date) and ship the CSVs to external storage, at a configurable cadence.

    SYNC_FREQUENCY = hourly | daily | weekly | monthly   (default: daily)

The billing PERIOD is always a calendar month; SYNC_FREQUENCY only controls how
often that month-to-date snapshot is refreshed and pushed (i.e. data freshness).
Each run overwrites the period's files, so more-frequent syncs just mean fresher
data, never duplicates.

    python -m billing.otel.scheduler                 # run once now (default; cron-friendly)
    python -m billing.otel.scheduler --loop          # long-running; self-paces per SYNC_FREQUENCY
    python -m billing.otel.scheduler --emit-cron     # print the crontab line for SYNC_FREQUENCY
    python -m billing.otel.scheduler --month 2026-06 # backfill/close a specific month (once)

Two deployment styles (pick one):
  - one-shot + external scheduler: cron/systemd fires `--once` at the cadence
    (precise; see `--emit-cron`);
  - long-running `--loop`: the process self-paces (simplest; approximate timing).
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import date

from ..config import load_env
from . import export
from . import fabric_sync
from . import invoice as invoice_mod
from .fabric_client import StorageClient, SyncConfigError, target_configured
from .otel_store import OtelStore

load_env()

# frequency -> (crontab schedule, approx loop interval in seconds)
FREQUENCIES = {
    "hourly":  {"cron": "0 * * * *",   "seconds": 3600},
    "daily":   {"cron": "30 23 * * *", "seconds": 86_400},
    "weekly":  {"cron": "30 23 * * 0", "seconds": 604_800},
    "monthly": {"cron": "30 23 1 * *", "seconds": 2_592_000},
}
DEFAULT_FREQUENCY = "daily"


def get_frequency(override: str | None = None) -> str:
    freq = (override or os.environ.get("SYNC_FREQUENCY") or DEFAULT_FREQUENCY).strip().lower()
    if freq not in FREQUENCIES:
        raise SystemExit(
            f"SYNC_FREQUENCY must be one of {', '.join(FREQUENCIES)} (got {freq!r})")
    return freq


def _month_bounds(anchor: date) -> tuple[str, str]:
    start = anchor.replace(day=1)
    end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start.isoformat(), end.isoformat()


def run_once(markup: float = 1.50, db: str | None = None, max_attempts: int = 5,
             month: str | None = None, dry_run: bool = False) -> tuple[int, int]:
    """Regenerate the current month's invoice documents, refresh the running
    lake tables (all history), and ship them. Returns (ok, failed)."""
    anchor = date.fromisoformat(f"{month}-01") if month else date.today()
    start, end = _month_bounds(anchor)
    print(f"[sync] current period {start} -> {end}  (markup x{markup:.2f})")

    invoice_mod.run(start, end, markup, db)  # local, per-period invoice documents

    store = OtelStore(db) if db else OtelStore()

    # Refresh the running, all-history tables and queue them for the lake.
    if target_configured():
        ns, nl = export.build_and_enqueue(store, markup)
        print(f"[sync] running tables refreshed: "
              f"{ns} summary row(s), {nl} line-item row(s) queued")

    try:
        client = StorageClient()
    except SyncConfigError as e:
        print(f"[sync] no storage target configured ({e}); files generated locally only.")
        store.close()
        return (0, 0)
    ok, fail = fabric_sync.run_once(store, client, max_attempts, dry_run)
    store.close()
    return (ok, fail)


def main():
    ap = argparse.ArgumentParser(
        description="Periodic sync: refresh current month + ship to external storage.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run a single sync and exit (default)")
    mode.add_argument("--loop", action="store_true", help="run forever, pacing per SYNC_FREQUENCY")
    mode.add_argument("--emit-cron", action="store_true", help="print the crontab line and exit")
    ap.add_argument("--frequency", default=None,
                    help="override SYNC_FREQUENCY (hourly|daily|weekly|monthly)")
    ap.add_argument("--markup", type=float, default=1.50)
    ap.add_argument("--month", default=None, help="YYYY-MM (once mode; backfill a specific month)")
    ap.add_argument("--max-attempts", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    freq = get_frequency(args.frequency)

    if args.emit_cron:
        cron = FREQUENCIES[freq]["cron"]
        print(f"# SYNC_FREQUENCY={freq} — add to crontab on the receiver host:")
        print(f"{cron}  cd /opt/cyclotron/internal-billing-engine && "
              f"./deploy/run-sync.sh >> /var/log/billing-sync.log 2>&1")
        return

    if args.loop:
        secs = FREQUENCIES[freq]["seconds"]
        print(f"[sync] loop mode: frequency={freq} (~every {secs}s). Ctrl-C to stop.")
        try:
            while True:
                run_once(args.markup, args.db, args.max_attempts, None, args.dry_run)
                time.sleep(secs)
        except KeyboardInterrupt:
            pass
        return

    # default: one shot (exit non-zero if any delivery failed, for monitoring)
    _ok, fail = run_once(args.markup, args.db, args.max_attempts, args.month, args.dry_run)
    raise SystemExit(1 if fail else 0)


if __name__ == "__main__":
    main()
