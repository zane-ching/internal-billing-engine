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

  > ⚠️ **`period_end` is EXCLUSIVE.** July 2026 is
  > `period_start = 2026-07-01`, `period_end = 2026-08-01` — the row covers usage
  > *up to but not including* August 1. Filter with `>= period_start AND < period_end`;
  > a `BETWEEN` double-counts the boundary day. When you put a period on anything a
  > client reads, render it inclusive (`July 2026`, or `2026-07-01 → 2026-07-31`)
  > while keeping the exclusive bound in the query — otherwise they will ask why
  > they were billed for August 1.

- **`generated_at`** — when the snapshot was built. Pipeline metadata, restamped
  on every row each sync; **not** a usage time.

All timestamps are UTC — a late-evening session in a western timezone lands on the
next `usage_date_utc`. Convert in the semantic model if you need local-day reporting.

## 3. Joining to clients (the invoice notebook)

The invoice notebook joins `claudeusagelineitems` to the `repoclientmap` table on
the repo name. Two things to get right before billing on it:

- **Use a LEFT join, never an INNER join.** An inner join silently drops every repo
  missing from the map — real usage, invisibly unbilled, with no error anywhere.
  Left-join instead and surface unmapped repos as an explicit exception row that
  someone clears before the invoice is issued. **Never default an unmapped repo to
  a client.**
- **Mind the refresh order.** `repoclientmap` refreshes from the SharePoint sheet
  daily at **23:59 PST**, but the sync ships CSVs at **23:30 UTC** (16:30 PST) and
  this notebook runs shortly after — so it can join against a map up to a day
  stale, and a repo mapped today won't bill correctly until tomorrow. It is also
  the one PST schedule in an otherwise all-UTC pipeline, so DST shifts it twice a
  year relative to everything else.

The mapping sheet is a billing input, not a convenience: see Phase 6 of the
top-level [`README.md`](../README.md) for the auditability work still outstanding
(a SharePoint workbook cannot reconstruct which mapping produced last quarter's
invoice, which is exactly what a client dispute asks for).
