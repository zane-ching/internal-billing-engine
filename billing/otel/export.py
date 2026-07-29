"""Build the running usage datasets for the data lake.

Two flat, all-history CSVs — NOT partitioned into per-period folders — so Fabric
loads each as a single running table that accumulates records across months:

    claudeusagesummary.csv     one row per (usage month, bill_name, user_email)
    claudeusagelineitems.csv   one row per (usage month, repo, model, user_email)

Each row carries the usage month as columns (period_start / period_end) plus
generated_at (when the snapshot was produced) — so the date lives IN the table,
not in the folder path. Regenerated in full from the store on every sync and
written to a STABLE path, so Fabric can load-to-table with OVERWRITE and always
get the complete, de-duplicated history.
"""

from __future__ import annotations

import csv
import os
from datetime import date, datetime, timezone

from .normalize import normalize_model, repo_name
from .otel_store import OtelStore

SUMMARY_TABLE = "claudeusagesummary"
LINEITEMS_TABLE = "claudeusagelineitems"

SUMMARY_FIELDS = ["period_start", "period_end", "bill_name", "user_email", "tokens",
                  "actual_cost_usd", "markup", "total_billed_usd", "generated_at"]
LINE_FIELDS = ["period_start", "period_end", "bill_name", "repo", "model", "user_email",
               "tokens", "actual_cost_usd", "billed_usd", "generated_at"]

UNKNOWN_USER = "unknown"  # datapoints that arrived without a user.email attribute


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _month_end(ym: str) -> str:
    """'YYYY-MM' -> first day of the NEXT month, as YYYY-MM-DD."""
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y + m // 12, m % 12 + 1, 1).isoformat()


def build(store: OtelStore, markup: float):
    """Return (summary_rows, line_rows) covering ALL usage in the store,
    aggregated per calendar month × repo/model × user."""
    mapping = store.get_mapping()
    name_of = lambda repo: mapping.get(repo) or repo_name(repo)

    cost = {}
    for r in store.db.execute(
            "SELECT substr(ts,1,7) ym, repo, model, user_email, SUM(cost_usd) c "
            "FROM cost_usage GROUP BY ym, repo, model, user_email"):
        cost[(r["ym"], r["repo"], normalize_model(r["model"]),
              r["user_email"] or UNKNOWN_USER)] = r["c"] or 0.0

    toks = {}
    for r in store.db.execute(
            "SELECT substr(ts,1,7) ym, repo, model, user_email, SUM(tokens) t "
            "FROM token_usage GROUP BY ym, repo, model, user_email"):
        toks[(r["ym"], r["repo"], normalize_model(r["model"]),
              r["user_email"] or UNKNOWN_USER)] = r["t"] or 0

    gen = _now_iso()
    line_rows = []
    summ: dict = {}  # (ym, bill_name, user_email) -> [tokens, cost]
    for key in sorted(set(cost) | set(toks)):
        ym, repo, model, user = key
        c = cost.get(key, 0.0)
        t = toks.get(key, 0)
        bn = name_of(repo)
        ps, pe = f"{ym}-01", _month_end(ym)
        line_rows.append({
            "period_start": ps, "period_end": pe, "bill_name": bn,
            "repo": repo, "model": model, "user_email": user, "tokens": t,
            "actual_cost_usd": round(c, 6), "billed_usd": round(c * markup, 6),
            "generated_at": gen})
        agg = summ.setdefault((ym, bn, user), [0, 0.0])
        agg[0] += t
        agg[1] += c

    summary_rows = []
    for (ym, bn, user), (t, c) in sorted(summ.items()):
        ps, pe = f"{ym}-01", _month_end(ym)
        summary_rows.append({
            "period_start": ps, "period_end": pe, "bill_name": bn,
            "user_email": user, "tokens": t, "actual_cost_usd": round(c, 6),
            "markup": markup, "total_billed_usd": round(c * markup, 6),
            "generated_at": gen})
    return summary_rows, line_rows


def _write_csv(path: str, fields: list, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def build_and_enqueue(store: OtelStore, markup: float, out_dir: str = "exports"):
    """Write the two running CSVs and queue them for upload to stable, flat
    paths (<prefix>/claudeusagesummary.csv, <prefix>/claudeusagelineitems.csv)."""
    os.makedirs(out_dir, exist_ok=True)
    summary_rows, line_rows = build(store, markup)

    sp = os.path.join(out_dir, f"{SUMMARY_TABLE}.csv")
    lp = os.path.join(out_dir, f"{LINEITEMS_TABLE}.csv")
    _write_csv(sp, SUMMARY_FIELDS, summary_rows)
    _write_csv(lp, LINE_FIELDS, line_rows)

    # 'running' period marker -> one pending row per table, replaced each run.
    store.fabric_enqueue(kind=SUMMARY_TABLE, period_start="running", period_end="running",
                         local_path=os.path.abspath(sp), onelake_path=f"{SUMMARY_TABLE}.csv")
    store.fabric_enqueue(kind=LINEITEMS_TABLE, period_start="running", period_end="running",
                         local_path=os.path.abspath(lp), onelake_path=f"{LINEITEMS_TABLE}.csv")
    return len(summary_rows), len(line_rows)
