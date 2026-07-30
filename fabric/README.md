# Fabric side — turn the pushed CSVs into running Delta tables

The billing engine pushes two flat, all-history CSVs to ADLS:

```
<container>/<prefix>/claudeusagesummary.csv
<container>/<prefix>/claudeusagelineitems.csv
```
(here: `cyclotroninsights/claude-billing/…`). Each row carries `period_start` /
`period_end` (usage month) and `generated_at` — the date lives in the columns, not
in the folder path, so all months accumulate in one table.

A **shortcut only points at files — it does not convert CSV to a Delta table.**
CSV placed under **Tables** shows up as **Unidentified**. The correct flow:

## 1. Shortcut the data into **Files** (not Tables)

Lakehouse → **Files** → **New shortcut** → **Azure Blob Storage**
(use the **blob** endpoint — the `dfs` endpoint 409s on this account's soft-delete):

- URL: `https://sacyclotroninsights.blob.core.windows.net/cyclotroninsights`
- Path: `claude-billing`
- Auth: the service principal `cyclotron-billing-sync` (has the container role).

You'll see `Files/claude-billing/claudeusagesummary.csv` and `…lineitems.csv`.

## 2. Convert each CSV → a Delta table

**Option A — one-off, in the UI:** in Files, right-click `claudeusagesummary.csv`
→ **Load to tables → New table** → name `claudeusagesummary`, mode **Overwrite**.
Repeat for `claudeusagelineitems.csv`. (Re-run to refresh.)

**Option B — running, automatic (recommended):** run `refresh_billing_tables.py`
in a Fabric notebook and **schedule it after the sync** (e.g. sync 23:30 → notebook
23:45). It overwrites both Delta tables from the full-history CSVs each run, so the
tables stay current with no duplicates.

## Result

Two running Delta tables, queryable from the SQL endpoint / Power BI:

- **`claudeusagesummary`** — `usage_date_utc, period_start, period_end, bill_name, user_email, tokens, actual_cost_usd, markup, total_billed_usd, first_usage_at_utc, last_usage_at_utc, generated_at`
- **`claudeusagelineitems`** — `usage_date_utc, period_start, period_end, bill_name, repo, model, user_email, tokens, actual_cost_usd, billed_usd, first_usage_at_utc, last_usage_at_utc, generated_at`

`user_email` is the employee whose Claude Code session produced the usage
(`unknown` if the datapoint arrived without a user attribute). Both tables are
grained by it, so a repo with several developers now yields one row per developer
— aggregate it away in the semantic model to get the per-repo billing total.

### The date columns

- **`usage_date_utc`** — the UTC day the usage actually happened. This is the finest
  time grain in the tables; both are grained by it, so one repo-month becomes one
  row per active day per user.
- **`first_usage_at_utc` / `last_usage_at_utc`** — UTC timestamps of the earliest and
  latest datapoint rolled into that row, so intra-day timing survives the
  aggregation without a row per datapoint.
- **`period_start` / `period_end`** — the calendar month the row bills to.
  Redundant with `usage_date_utc` (derivable from it) but kept so a monthly invoice
  total is a plain `GROUP BY period_start` with no date maths.
- **`generated_at`** — when the snapshot was built. Pipeline metadata, restamped
  on every row each sync; **not** a usage time.

All timestamps are UTC — a late-evening session in a western timezone lands on the
next `usage_date_utc`. Convert in the semantic model if you need local-day reporting.
