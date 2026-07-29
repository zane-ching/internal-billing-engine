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

- **`claudeusagesummary`** — `period_start, period_end, bill_name, tokens, actual_cost_usd, markup, total_billed_usd, generated_at`
- **`claudeusagelineitems`** — `period_start, period_end, bill_name, repo, model, tokens, actual_cost_usd, billed_usd, generated_at`
