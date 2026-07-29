#!/usr/bin/env bash
# One-shot sync: refresh the current month's invoices (month-to-date) and ship
# the CSVs to external storage. Run-and-exit — schedule it at the cadence set by
# SYNC_FREQUENCY (hourly/daily/weekly/monthly).
#
# Must run on the host that holds otel.db (the receiver box) — SQLite is single-host.
#
# --- Schedule it ------------------------------------------------------------
# Print the crontab line matching your SYNC_FREQUENCY:
#   python3 -m billing.otel.scheduler --emit-cron
# e.g. daily at 23:30:
#   30 23 * * *  cd /opt/cyclotron/internal-billing-engine && ./deploy/run-sync.sh >> /var/log/billing-sync.log 2>&1
#
# Alternatively run the long-running self-pacing loop instead of cron:
#   python3 -m billing.otel.scheduler --loop      (or the `sync` service in docker-compose)
#
# Exit code is non-zero if any delivery failed (rows stay queued for the next
# run) — surface that to your monitoring.

set -euo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

# Destination + auth + cadence all come from .env / the environment.
exec python3 -m billing.otel.scheduler --once --markup "${MARKUP:-1.5}" "$@"
