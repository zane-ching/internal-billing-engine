# internal-billing-engine

Backend engine to obtain Claude Code usage data and its link with its associated GitHub repositories. Attributes Cyclotron employees' Claude usage to the repository it was done in, and in turn to the client it was done for. 

Can run locally because of .claude > settings.local.json, settings (deploy/) will have to be pushed to all dev machines in order for global telemetry to be captured.

## 1: Receiving telemetry data
The engine gets data via an OTLP/JSON receiver (receiver.py) that ingests telemetry emitted by Claude Code on each dev machine. Deduped datapoints are stored in a single-host SQLite database. 

## 2: Push data to ADLS
A scheduler (scheduler.py) regenerates two flat all-history CSVs (claudeuseagesummary, claudeusagelineitems) and enqueues them. Sync worker (fabric_sync.py) drains outbox and uploads to ADLS (rg-cyclotron-insights > sacyclotroninsights > cyclotroninsights), overwriting each file. Scheduled daily but can be modified (SYNC_FREQUENCY in .env).

## 3: Load into Fabric as Delta tables
Connection through service principal allows for shortcut into CyclotronInsights workspace's lake_insights_db lakehouse > Files. claude-billing-push notebook scheduled to lake_insights_db > Tables as claudeusagesummary and claudeusagelineitems.

## 4: Generate invoices/reports
NOT BUILT OUT YET; need to create repo --> client mapping table (manually), aggregate by client, and generate monthly invoices.

## Folder layout

```
billing/            Python package — both pipelines + reconciliation
  otel/             the OTEL (repo-level) path
deploy/             client-side config pushed to dev machines (MDM)
fabric/             Fabric-side notebook + docs (CSV → Delta tables)
data/               generated SQLite stores + logs (gitignored)
.claude/            local Claude Code settings (gitignored)
Dockerfile          container image for the receiver
docker-compose.yml  receiver + sync worker services
DEMO.md             end-to-end demo runbook
```

### `billing/` — the engine

Shared:
- **`__init__.py`** — marks `billing/` (and `billing/otel/`) as Python packages; no code.
- **`config.py`** — loads `.env` into the environment so tools find the API token.

**Analytics path (per-user):**
- **`analytics_client.py`** — client for the Anthropic Analytics API (`usage_report`, `cost_report`, `user_usage_report`); auth, 31-day windowing, pagination
- **`ingest.py`** — pulls a date range from the Analytics API into the store.
  `python -m billing.ingest --start 2026-07-01 --end 2026-07-15`
- **`store.py`** — SQLite store for Analytics data + the `tokens()` field extractor.
- **`report.py`** — prints what the Analytics data reveals (product/model/user breakdown, cost) and where it can't attribute (no repo dimension).

**Bridge:**
- **`reconcile.py`** — compares OTEL-captured tokens against the authoritative Analytics total as a funnel (truth → captured → repo-tagged). Repo-tagged usage is billable, so that's the last stage.
  This is the acceptance test / coverage metric for the OTEL rollout.
  `python -m billing.reconcile --start 2026-07-14 --end 2026-07-15`

### `billing/otel/` — the OTEL (repo-level) path

- **`receiver.py`** — minimal OTLP/JSON HTTP server. Accepts `claude_code.token.usage` from Claude Code (handles chunked + gzip bodies), extracts repo/user/model/token-type, dedupes, writes to the store.
  `python -m billing.otel.receiver`
- **`otel_store.py`** — SQLite store: deduped `token_usage` datapoints, persisted invoices, the optional `repo_name_map` override table, and the `fabric_outbox` delivery queue.
- **`normalize.py`** — collapses git remote forms (ssh vs https, `.git`, case) into one canonical repo key so a repo isn't billed twice, and derives the short repo name (`repo_name`) that is the billing identity.
- **`repos.py`** — manage the OPTIONAL repo→billing-name override map: `export` observed repos to CSV, edit the `bill_name` column to rename/group a repo, then `import`. Not needed by default — every repo bills under its own name.
  `python -m billing.otel.repos export --out repo_name_map.csv`
- **`rating.py`** — `RatingService`: token counts → billable USD (per-model rates × markup, cache-token multipliers) RATES ARE PLACEHOLDERS AND NEED TO BE REPLACED W/ REAL PRICES
- **`bill.py`** — aggregates usage → repo, bills on actual cost (`claude_code.cost.usage`) × markup with a rate-card cross-check; flags `unknown` usage (sessions with no git remote). `python -m billing.otel.bill`
- **`invoice.py`** — generates per-repo invoices for a billing period: persists immutable invoice + line-item records and writes a human-readable `.txt` invoice + `summary.csv` / `line_items.csv` under `invoices/`. `python -m billing.otel.invoice --start 2026-07-01 --end 2026-08-01`
- **`records.py`** — dumps individual usage records with their repo tag + resolved billing name.
- **`sample_payload.py`** — generates a synthetic OTLP payload to exercise the pipeline without live machines.

**Data-lake sync (asynchronous):**
- **`fabric_client.py`** — uploads a file to **ADLS Gen2** (or **OneLake**) via the ADLS Gen2 DFS REST API; Entra service-principal / managed-identity / SAS auth (stdlib only). Idempotent overwrite (create → append → flush).
- **`export.py`** — builds the two **running, all-history** lake tables (`claudeusagesummary`, `claudeusagelineitems`) from the store: flat single CSVs (no date-partition folders) with the usage month + `generated_at` as columns, regenerated in full each sync.
- **`fabric_sync.py`** — drains the delivery outbox: ships queued CSVs with retry/backoff. `python -m billing.otel.fabric_sync` (run-once; `--watch`, `--status`, `--dry-run`).
- **`scheduler.py`** — the periodic sync job: regenerate the current month (month-to-date) and ship it, at the cadence in `SYNC_FREQUENCY` (hourly/daily/weekly/monthly). `python -m billing.otel.scheduler` (once; `--loop`, `--emit-cron`, `--month` backfill).

### `deploy/` — client-side rollout (pushed via MDM)

- **`managed-settings.json`** — enforces telemetry ON + the exporter endpoint.
  Placed at the system-level managed-settings path so developers can't disable it.
- **`claude-wrapper.sh`** — the `claude` entrypoint on each machine; stamps every
  session with `OTEL_RESOURCE_ATTRIBUTES=repo=<git remote>`.
- **`dev-selftest.sh`** — scoped launcher to generate real telemetry from one
  machine into a local receiver (for testing before a fleet rollout).
- **`run-sync.sh`** — one-shot sync wrapper for cron/systemd: refresh the current
  month and ship the CSVs (the `sync` compose service runs the self-pacing loop instead).
- **`README.md`** — deployment instructions for IT (paths, enforcement, verify).

### `data/` — generated (gitignored)

- **`otel.db`** — OTEL token usage + optional repo→billing-name override map.
- **`analytics.db`** — data pulled from the Analytics API.
- **`receiver.log`** — every request the receiver handled (diagnostics).

### `.claude/` — local Claude Code settings (gitignored)

- **`settings.local.json`** — personal telemetry config used when testing this
  machine as a telemetry source (points Claude Code at a local receiver).

### `fabric/` — Fabric-side (CSV → running Delta tables)

The engine pushes two flat, all-history CSVs to ADLS; these turn them into
queryable Delta tables inside Microsoft Fabric.

- **`README.md`** — the Fabric setup runbook: shortcut the CSVs into **Files**
  (not Tables), then convert each into a Delta table (one-off in the UI, or the
  scheduled notebook below).
- **`refresh_billing_tables.py`** — Fabric notebook cell that overwrites both
  Delta tables (`claudeusagesummary`, `claudeusagelineitems`) from the full-history
  CSVs each run; schedule it to fire shortly after the sync job.

### Container & compose

- **`Dockerfile`** — image for the telemetry receiver (stdlib-only, `python:3.12-slim`);
  persists the store + request log on a mounted `/data` volume, exposes `4318`.
- **`docker-compose.yml`** — hosts the `receiver` service plus the long-running
  `sync` worker (self-pacing scheduler shipping CSVs to the lake), sharing one
  `./otel-data` volume; includes a commented Caddy TLS sidecar for production.
- **`.dockerignore`** — keeps secrets, data, and local settings out of the build context.

### Demo & repo hygiene

- **`DEMO.md`** — end-to-end demo runbook: a live Claude Code session → repo-tagged
  telemetry → captured → per-repo invoice, plus talking points and quick fixes.
- **`.gitignore`** — excludes secrets (`.env*`), generated data/stores, invoices,
  and personal `.claude` settings from version control.

---

## Config & secrets

- **`.env`** (gitignored) — holds `ANTHROPIC_ANALYTICS_TOKEN`. Copy from
  **`.env.example`** (which carries only a placeholder).
- **`repo_name_map.csv`** — optional repo→billing-name overrides (editable working file; only needed to rename or group repos).

## Typical OTEL flow

```
receiver.py (ingest telemetry)  →  bill  →  reconcile (coverage check)  →  invoice (per period)

optional, only to rename/group repos:  repos export  →  edit bill_name  →  repos import
```

## Shipping invoices to a data lake (Fabric / ADLS Gen2)

Usage is pushed to a data lake as **two running, all-history tables**,
**asynchronously and durably**:

```
scheduler → export.build (all history) → enqueues to fabric_outbox (pending)
fabric_sync → drains the outbox → uploads to ADLS Gen2 / OneLake (retry + backoff) → marks sent
```

- **The two tables** (each a single flat CSV, no date folders, so Fabric loads it
  as one running table):
  - `claudeusagesummary.csv` — one row per (usage month, repo/bill_name)
  - `claudeusagelineitems.csv` — one row per (usage month, repo, model)
- **Date is a column, not a folder:** each row carries `period_start` / `period_end`
  (the usage month) and `generated_at` (when the snapshot was produced), so records
  accumulate across months in one table.
- **Where it lands:** `<prefix>/claudeusagesummary.csv` and
  `<prefix>/claudeusagelineitems.csv` in the configured container. Regenerated in
  full each sync and overwritten → Fabric loads-to-table with **overwrite**, always
  the complete de-duplicated history, no partitioning to reconcile.
- **Configurable freshness:** `scheduler.py` regenerates the *current* month
  (month-to-date) and ships it, at the cadence in `SYNC_FREQUENCY`
  (hourly/daily/weekly/monthly, default daily). Run it as a self-pacing loop (the
  `sync` compose service) or fire `deploy/run-sync.sh` from cron
  (`scheduler --emit-cron` prints the line). Late-arriving telemetry is picked up
  by the next run. The billing *period* stays a calendar month — frequency only
  controls how often the snapshot is pushed.
- **Durability:** the outbox lives in `otel.db`, so a crash or a lake/network
  outage never loses an invoice — it stays `pending` and the next run retries.
- **Config:** set the target + auth in `.env` (see `.env.example` — `SYNC_TARGET`,
  `ADLS_*`/`ONELAKE_*`, `AZURE_*`). Unset → invoices are written locally only.
- **Runs where `otel.db` lives** (the receiver host); SQLite is single-host.

No third-party Python dependencies — standard library only.
