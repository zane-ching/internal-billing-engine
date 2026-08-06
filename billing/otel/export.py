"""Build the running usage datasets for the data lake.

Two flat, all-history CSVs — NOT partitioned into per-period folders — so Fabric
loads each as a single running table that accumulates records across months:

    claudeusagesummary.csv     one row per (usage date, repo, user_email)
    claudeusagelineitems.csv   one row per (usage date, repo, model, user_email)

`repo` is the short billing name (the last path segment, or an override from the
repo_name_map); the line-item table also carries `repo_key`, the full canonical
key, so same-named repos in different orgs stay distinguishable.

Each row carries WHEN the usage happened — usage_date_utc (the day) plus the
first_usage_at_utc / last_usage_at_utc timestamps bounding that day's activity —
and the calendar month it bills to (period_start / period_end), so a monthly
rollup is just a GROUP BY. All three are UTC, as emitted by Claude Code.
generated_at is unrelated to usage: it records when the snapshot was produced.
Regenerated in full from the store on every sync and written to a STABLE path, so
Fabric can load-to-table with OVERWRITE and always get the complete,
de-duplicated history.
"""

from __future__ import annotations

import csv
import os
from datetime import date, datetime, timezone

from .attribute import resolved_view
from .normalize import normalize_model, repo_name
from .otel_store import OtelStore

SUMMARY_TABLE = "claudeusagesummary"
LINEITEMS_TABLE = "claudeusagelineitems"

SUMMARY_FIELDS = ["usage_date_utc", "period_start", "period_end", "repo", "user_email",
                  "tokens", "actual_cost_usd", "markup", "total_billed_usd",
                  "first_usage_at_utc", "last_usage_at_utc", "generated_at"]
LINE_FIELDS = ["usage_date_utc", "period_start", "period_end", "repo", "repo_key", "model",
               "user_email", "tokens", "actual_cost_usd", "billed_usd",
               "first_usage_at_utc", "last_usage_at_utc", "generated_at"]

UNKNOWN_USER = "unknown"  # datapoints that arrived without a user.email attribute


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _month_end(ym: str) -> str:
    """'YYYY-MM' -> first day of the NEXT month, as YYYY-MM-DD."""
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y + m // 12, m % 12 + 1, 1).isoformat()


def build(store: OtelStore, markup: float):
    """Return (summary_rows, line_rows) covering ALL usage in the store,
    aggregated per usage DAY × repo/model × user."""
    mapping = store.get_mapping()
    name_of = lambda repo: mapping.get(repo) or repo_name(repo)

    # first/last datapoint time per key, merged across both source tables.
    span: dict = {}

    def _span(key, lo, hi):
        cur = span.get(key)
        if cur is None:
            span[key] = [lo, hi]
        else:
            cur[0] = min(cur[0], lo)
            cur[1] = max(cur[1], hi)

    # Repo is resolved per datapoint BEFORE the day/model/user rollup, so a
    # session that moved between repos splits into separate rows rather than
    # billing wholly to wherever it launched. See billing.otel.attribute.
    cost = {}
    for r in store.db.execute(
            f"WITH r AS ({resolved_view('cost_usage')}) "
            "SELECT substr(ts,1,10) d, resolved_repo, model, user_email, "
            "SUM(cost_usd) c, MIN(ts) lo, MAX(ts) hi "
            "FROM r GROUP BY d, resolved_repo, model, user_email"):
        key = (r["d"], r["resolved_repo"], normalize_model(r["model"]),
               r["user_email"] or UNKNOWN_USER)
        cost[key] = r["c"] or 0.0
        _span(key, r["lo"], r["hi"])

    toks = {}
    for r in store.db.execute(
            f"WITH r AS ({resolved_view('token_usage')}) "
            "SELECT substr(ts,1,10) d, resolved_repo, model, user_email, "
            "SUM(tokens) t, MIN(ts) lo, MAX(ts) hi "
            "FROM r GROUP BY d, resolved_repo, model, user_email"):
        key = (r["d"], r["resolved_repo"], normalize_model(r["model"]),
               r["user_email"] or UNKNOWN_USER)
        toks[key] = r["t"] or 0
        _span(key, r["lo"], r["hi"])

    gen = _now_iso()
    line_rows = []
    summ: dict = {}  # (day, repo, user_email) -> [tokens, cost, first, last]
    for key in sorted(set(cost) | set(toks)):
        day, repo_key, model, user = key
        c = cost.get(key, 0.0)
        t = toks.get(key, 0)
        lo, hi = span[key]
        bn = name_of(repo_key)
        ps, pe = f"{day[:7]}-01", _month_end(day[:7])
        line_rows.append({
            "usage_date_utc": day, "period_start": ps, "period_end": pe,
            "repo": bn, "repo_key": repo_key, "model": model, "user_email": user,
            "tokens": t, "actual_cost_usd": round(c, 6),
            "billed_usd": round(c * markup, 6),
            "first_usage_at_utc": lo, "last_usage_at_utc": hi, "generated_at": gen})
        agg = summ.setdefault((day, bn, user), [0, 0.0, lo, hi])
        agg[0] += t
        agg[1] += c
        agg[2] = min(agg[2], lo)
        agg[3] = max(agg[3], hi)

    summary_rows = []
    for (day, bn, user), (t, c, lo, hi) in sorted(summ.items()):
        ps, pe = f"{day[:7]}-01", _month_end(day[:7])
        summary_rows.append({
            "usage_date_utc": day, "period_start": ps, "period_end": pe,
            "repo": bn, "user_email": user, "tokens": t,
            "actual_cost_usd": round(c, 6), "markup": markup,
            "total_billed_usd": round(c * markup, 6),
            "first_usage_at_utc": lo, "last_usage_at_utc": hi, "generated_at": gen})
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


def main():
    """Write the running CSVs locally, without needing a storage target.

    The scheduler only calls build_and_enqueue() when ADLS/OneLake credentials
    are configured, so this is the way to regenerate and inspect the lake tables
    on a machine with no storage target — demos, local verification, debugging a
    row that looks wrong before it ships.
    """
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="OTEL store path (default ./data/otel.db)")
    ap.add_argument("--markup", type=float, default=1.50)
    ap.add_argument("--out-dir", default="exports")
    ap.add_argument("--no-enqueue", action="store_true",
                    help="write the CSVs but do not queue them for upload")
    args = ap.parse_args()

    store = OtelStore(args.db) if args.db else OtelStore()
    if args.no_enqueue:
        summary_rows, line_rows = build(store, args.markup)
        os.makedirs(args.out_dir, exist_ok=True)
        _write_csv(os.path.join(args.out_dir, f"{SUMMARY_TABLE}.csv"),
                   SUMMARY_FIELDS, summary_rows)
        _write_csv(os.path.join(args.out_dir, f"{LINEITEMS_TABLE}.csv"),
                   LINE_FIELDS, line_rows)
        ns, nl = len(summary_rows), len(line_rows)
    else:
        ns, nl = build_and_enqueue(store, args.markup, args.out_dir)
    store.commit()
    store.close()
    print(f"[export] {args.out_dir}/{SUMMARY_TABLE}.csv    {ns} row(s)")
    print(f"[export] {args.out_dir}/{LINEITEMS_TABLE}.csv  {nl} row(s)")


if __name__ == "__main__":
    main()
