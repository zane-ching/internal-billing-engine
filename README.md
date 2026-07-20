# internal-billing-engine

Backend engine that attributes Cyclotron employees' Claude usage to the client projects it was done for, and turns that into per-client bills.

## Folder layout

```
billing/            Python package — both pipelines + reconciliation
  otel/             the OTEL (repo-level) path
data/               generated SQLite stores + logs (gitignored)
deploy/             client-side config pushed to dev machines (MDM)
.claude/            local Claude Code settings (gitignored)
```

### `billing/` — the engine

Shared:
- **`config.py`** — loads `.env` into the environment so tools find the API token.

**Analytics path (per-user):**
- **`analytics_client.py`** — client for the Anthropic Analytics API (`usage_report`, `cost_report`, `user_usage_report`); auth, 31-day windowing, pagination
- **`ingest.py`** — pulls a date range from the Analytics API into the store.
  `python -m billing.ingest --start 2026-07-01 --end 2026-07-15`
- **`store.py`** — SQLite store for Analytics data + the `tokens()` field extractor.
- **`report.py`** — prints what the Analytics data reveals (product/model/user breakdown, cost) and where it can't attribute (no repo dimension).

**Bridge:**
- **`reconcile.py`** — compares OTEL-captured tokens against the authoritative Analytics total as a funnel (truth → captured → repo-tagged → client-mapped).
  This is the acceptance test / coverage metric for the OTEL rollout.
  `python -m billing.reconcile --start 2026-07-14 --end 2026-07-15`

### `billing/otel/` — the OTEL (repo-level) path

- **`receiver.py`** — minimal OTLP/JSON HTTP server. Accepts `claude_code.token.usage` from Claude Code (handles chunked + gzip bodies), extracts repo/user/model/token-type, dedupes, writes to the store.
  `python -m billing.otel.receiver`
- **`otel_store.py`** — SQLite store: deduped `token_usage` datapoints + the `repo_client_map` table.
- **`normalize.py`** — collapses git remote forms (ssh vs https, `.git`, case) into one canonical repo key so a repo isn't billed twice.
- **`clients.py`** — derives the client from a repo name's `<client>-…` prefix.
- **`repos.py`** — manage the repo→client map: `export` the observed repos to CSV, `import` filled-in assignments, or `automap` from name prefixes.
  `python -m billing.otel.repos export --out repo_client_map.csv`
- **`rating.py`** — `RatingService`: token counts → billable USD (per-model rates × markup, cache-token multipliers) RATES ARE PLACEHOLDERS AND NEED TO BE REPLACED W/ REAL PRICES
- **`bill.py`** — aggregates usage → client, bills on actual cost (`claude_code.cost.usage`) × markup with a rate-card cross-check; flags `UNASSIGNED` repos. `python -m billing.otel.bill`
- **`invoice.py`** — generates per-client invoices for a billing period: persists immutable invoice + line-item records and writes a human-readable `.txt` invoice + `summary.csv` / `line_items.csv` under `invoices/`. `python -m billing.otel.invoice --start 2026-07-01 --end 2026-08-01`
- **`records.py`** — dumps individual usage records with their repo tag + client.
- **`sample_payload.py`** — generates a synthetic OTLP payload to exercise the pipeline without live machines.

### `deploy/` — client-side rollout (pushed via MDM)

- **`managed-settings.json`** — enforces telemetry ON + the exporter endpoint.
  Placed at the system-level managed-settings path so developers can't disable it.
- **`claude-wrapper.sh`** — the `claude` entrypoint on each machine; stamps every
  session with `OTEL_RESOURCE_ATTRIBUTES=repo=<git remote>`.
- **`dev-selftest.sh`** — scoped launcher to generate real telemetry from one
  machine into a local receiver (for testing before a fleet rollout).
- **`README.md`** — deployment instructions for IT (paths, enforcement, verify).

### `data/` — generated (gitignored)

- **`otel.db`** — OTEL token usage + repo→client map.
- **`analytics.db`** — data pulled from the Analytics API.
- **`receiver.log`** — every request the receiver handled (diagnostics).

### `.claude/` — local Claude Code settings (gitignored)

- **`settings.local.json`** — personal telemetry config used when testing this
  machine as a telemetry source (points Claude Code at a local receiver).

---

## Config & secrets

- **`.env`** (gitignored) — holds `ANTHROPIC_ANALYTICS_TOKEN`. Copy from
  **`.env.example`** (which carries only a placeholder).
- **`repo_client_map.csv`** — the repo→client assignments (editable working file).

## Typical OTEL flow

```
receiver.py (ingest telemetry)  →  repos export  →  assign clients (or automap)
  →  repos import  →  bill  →  reconcile (coverage check)  →  invoice (per period)
```

No third-party Python dependencies — standard library only.
