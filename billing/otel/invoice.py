"""Generate per-client invoices for a billing period.

For each client with attributed usage in [start, end):
  - aggregate actual cost (claude_code.cost.usage) and tokens per repo x model,
  - apply markup,
  - persist an immutable invoice + line items to the store,
  - write a human-readable invoice + CSVs under ./invoices/<start>_<end>/.

Repos with no client mapping (UNASSIGNED) are reported but NOT invoiced.

    python -m billing.otel.invoice --start 2026-07-01 --end 2026-08-01 --markup 1.5
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from datetime import datetime, timezone

from .normalize import normalize_model
from .otel_store import OtelStore

UNASSIGNED = "UNASSIGNED"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ftok(n) -> str:
    n = n or 0
    if n >= 1_000_000_000:
        return f"{n/1e9:.2f}B"
    if n >= 1_000_000:
        return f"{n/1e6:.1f}M"
    if n >= 1_000:
        return f"{n/1e3:.1f}K"
    return str(int(n))


def _in_period(col_table, start, end):
    return (f"WHERE substr(ts,1,10) >= '{start}' AND substr(ts,1,10) < '{end}'")


def gather(store: OtelStore, start: str, end: str):
    """Return {client: {(repo, model): {tokens, actual_cost}}} for the period."""
    mapping = store.get_mapping()
    client_of = lambda repo: mapping.get(repo) or UNASSIGNED

    # actual cost per repo x model
    cost = defaultdict(float)
    for r in store.db.execute(
            "SELECT repo, model, SUM(cost_usd) c FROM cost_usage "
            "WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) < ? "
            "GROUP BY repo, model", (start, end)):
        cost[(r["repo"], normalize_model(r["model"]))] += r["c"] or 0.0

    # tokens per repo x model
    toks = defaultdict(int)
    for r in store.db.execute(
            "SELECT repo, model, SUM(tokens) t FROM token_usage "
            "WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) < ? "
            "GROUP BY repo, model", (start, end)):
        toks[(r["repo"], normalize_model(r["model"]))] += r["t"] or 0

    clients = defaultdict(lambda: defaultdict(lambda: {"tokens": 0, "actual_cost": 0.0}))
    for key in set(cost) | set(toks):
        repo, model = key
        cl = client_of(repo)
        clients[cl][(repo, model)] = {
            "tokens": toks.get(key, 0),
            "actual_cost": cost.get(key, 0.0),
        }
    return clients


def persist_invoice(store, invoice_number, client, start, end, markup,
                    line_items, actual_cost, tokens, total_billed):
    store.db.execute("DELETE FROM invoice_line_items WHERE invoice_number = ?",
                     (invoice_number,))
    store.db.execute(
        """INSERT OR REPLACE INTO invoices
           (invoice_number, client, period_start, period_end, currency,
            actual_cost, markup, total_billed, tokens, status, generated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (invoice_number, client, start, end, "USD", actual_cost, markup,
         total_billed, tokens, "draft", _now()))
    for li in line_items:
        store.db.execute(
            """INSERT INTO invoice_line_items
               (invoice_number, repo, model, tokens, actual_cost, billed_amount)
               VALUES (?,?,?,?,?,?)""",
            (invoice_number, li["repo"], li["model"], li["tokens"],
             li["actual_cost"], li["billed"]))
    store.commit()


def write_invoice_text(path, invoice_number, client, start, end, markup,
                       line_items, actual_cost, tokens, total_billed):
    W = 104
    money = lambda x: f"${x:,.4f}"
    trunc = lambda s, n: s if len(s) <= n else s[:n - 1] + "…"
    lines = []
    lines.append("CYCLOTRON — Claude Usage Invoice")
    lines.append("=" * W)
    lines.append(f"Invoice #:  {invoice_number}")
    lines.append(f"Client:     {client}")
    lines.append(f"Period:     {start}  →  {end}")
    lines.append(f"Generated:  {_now()}")
    lines.append("Status:     DRAFT")
    lines.append("")
    lines.append(f"{'Repo':<46}{'Model':<24}{'Tokens':>8}{'Cost':>13}{'Billed':>13}")
    lines.append("-" * W)
    for li in sorted(line_items, key=lambda x: -x["billed"]):
        lines.append(f"{trunc(li['repo'], 45):<46}{trunc(li['model'], 23):<24}"
                     f"{ftok(li['tokens']):>8}{money(li['actual_cost']):>13}"
                     f"{money(li['billed']):>13}")
    lines.append("-" * W)
    lines.append(f"{'Subtotal (Anthropic actual cost)':<88}{money(actual_cost):>16}")
    lines.append(f"{'Markup':<88}{'x%.2f' % markup:>16}")
    lines.append(f"{'TOTAL DUE (USD)':<88}{money(total_billed):>16}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def run(start: str, end: str, markup: float = 1.50, db: str | None = None,
        out_dir: str | None = None):
    store = OtelStore(db) if db else OtelStore()
    out_dir = out_dir or os.path.join("invoices", f"{start}_{end}")
    os.makedirs(out_dir, exist_ok=True)

    clients = gather(store, start, end)

    summary_rows = []
    all_line_items = []
    print(f"Generating invoices for {start} → {end}  (markup x{markup:.2f})\n")

    for client in sorted(c for c in clients if c != UNASSIGNED):
        items = clients[client]
        line_items = []
        actual_cost = 0.0
        tokens = 0
        for (repo, model), v in items.items():
            billed = v["actual_cost"] * markup
            line_items.append({"repo": repo, "model": model,
                               "tokens": v["tokens"], "actual_cost": v["actual_cost"],
                               "billed": billed})
            actual_cost += v["actual_cost"]
            tokens += v["tokens"]
        total_billed = actual_cost * markup
        invoice_number = f"INV-{start}-{client}"

        persist_invoice(store, invoice_number, client, start, end, markup,
                        line_items, actual_cost, tokens, total_billed)
        text_path = os.path.join(out_dir, f"{invoice_number}.txt")
        write_invoice_text(text_path, invoice_number, client, start, end, markup,
                           line_items, actual_cost, tokens, total_billed)

        summary_rows.append({
            "invoice_number": invoice_number, "client": client,
            "period_start": start, "period_end": end, "tokens": tokens,
            "actual_cost_usd": round(actual_cost, 6), "markup": markup,
            "total_billed_usd": round(total_billed, 6),
        })
        for li in line_items:
            all_line_items.append({
                "invoice_number": invoice_number, "client": client,
                "repo": li["repo"], "model": li["model"], "tokens": li["tokens"],
                "actual_cost_usd": round(li["actual_cost"], 6),
                "billed_usd": round(li["billed"], 6),
            })
        print(f"  {invoice_number:<34} {ftok(tokens):>7} tok  "
              f"cost ${actual_cost:,.4f}  billed ${total_billed:,.4f}")

    # CSVs
    summary_csv = os.path.join(out_dir, "summary.csv")
    with open(summary_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["invoice_number", "client",
            "period_start", "period_end", "tokens", "actual_cost_usd", "markup",
            "total_billed_usd"])
        w.writeheader()
        w.writerows(summary_rows)
    line_csv = os.path.join(out_dir, "line_items.csv")
    with open(line_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["invoice_number", "client", "repo",
            "model", "tokens", "actual_cost_usd", "billed_usd"])
        w.writeheader()
        w.writerows(all_line_items)

    grand = sum(r["total_billed_usd"] for r in summary_rows)
    print(f"\n{len(summary_rows)} invoice(s), grand total billed ${grand:,.4f}")
    print(f"Written to {out_dir}/  (per-client .txt, summary.csv, line_items.csv)")

    # UNASSIGNED report (not invoiced)
    if UNASSIGNED in clients:
        un_cost = sum(v["actual_cost"] for v in clients[UNASSIGNED].values())
        repos = sorted({repo for (repo, _m) in clients[UNASSIGNED]})
        print(f"\n⚠  UNINVOICED (no client mapping): ${un_cost:,.4f} actual cost across "
              f"{len(repos)} repo(s): {', '.join(repos)}")
        print("   Map them (repos automap / import) and re-run to capture this revenue.")

    store.close()


def main():
    ap = argparse.ArgumentParser(description="Generate per-client invoices.")
    ap.add_argument("--start", required=True, help="period start YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", required=True, help="period end YYYY-MM-DD (exclusive)")
    ap.add_argument("--markup", type=float, default=1.50)
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default=None, help="output dir (default ./invoices/<start>_<end>)")
    args = ap.parse_args()
    run(args.start, args.end, args.markup, args.db, args.out)


if __name__ == "__main__":
    main()
