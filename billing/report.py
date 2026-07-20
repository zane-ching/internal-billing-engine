"""Print what the ingested Analytics data can (and cannot) tell us.

    python -m billing.report

Reads the local store populated by `billing.ingest`.
"""

from __future__ import annotations

import argparse

from .store import Store

# --- ILLUSTRATIVE placeholder rates (USD per 1M tokens) ---------------------
# Stand-in for your RatingService. Swap for your negotiated per-model rates.
# input / output list prices; cache read ~0.1x input, cache writes ~1.25x/2x.
PRICES = {
    "fable-5": (10.0, 50.0),
    "opus":    (5.0, 25.0),
    "sonnet":  (3.0, 15.0),
    "haiku":   (1.0, 5.0),
}
DEFAULT_PRICE = (5.0, 25.0)
MARKUP = 1.50  # illustrative 50% markup


def _rate(model: str):
    for key, price in PRICES.items():
        if key in (model or ""):
            return price
    return DEFAULT_PRICE


def rated_usd(model, uncached, cc1h, cc5m, cache_read, output) -> float:
    inp, outp = _rate(model)
    per_m = lambda n, r: (n / 1_000_000.0) * r
    cost = (per_m(uncached, inp) + per_m(output, outp)
            + per_m(cache_read, inp * 0.1)
            + per_m(cc1h, inp * 2.0) + per_m(cc5m, inp * 1.25))
    return cost * MARKUP


def ftok(n) -> str:
    n = n or 0
    if n >= 1_000_000_000:
        return f"{n/1e9:.2f}B"
    if n >= 1_000_000:
        return f"{n/1e6:.1f}M"
    if n >= 1_000:
        return f"{n/1e3:.1f}K"
    return str(n)


def usd(n) -> str:
    return f"${n:,.2f}"


def rule(char="-", n=72):
    print(char * n)


def run(db: str | None = None):
    store = Store(db) if db else Store()
    c = store.db

    org = store.get_meta("organization_id")
    refreshed = store.get_meta("data_refreshed_at")
    r0, r1 = store.get_meta("range_start"), store.get_meta("range_end")

    rule("=")
    print("CLAUDE USAGE — what the Analytics API reveals")
    rule("=")
    print(f"org={org}   range={r0} -> {r1}   data_refreshed_at={refreshed}")

    # 1. Usage by product ----------------------------------------------------
    print("\n1) USAGE BY PRODUCT  (is it even Claude Code?)")
    rule()
    print(f"{'product':<16}{'requests':>12}{'input':>10}{'cache_read':>12}"
          f"{'output':>10}")
    rows = c.execute(
        """SELECT product, SUM(requests) rq, SUM(uncached_input) inp,
                  SUM(cache_read) cr, SUM(output) outp
           FROM product_usage GROUP BY product ORDER BY rq DESC""").fetchall()
    for r in rows:
        print(f"{r['product']:<16}{r['rq'] or 0:>12,}{ftok(r['inp']):>10}"
              f"{ftok(r['cr']):>12}{ftok(r['outp']):>10}")

    # 2. Claude Code by model ------------------------------------------------
    print("\n2) CLAUDE CODE BY MODEL")
    rule()
    print(f"{'model':<30}{'requests':>10}{'input':>9}{'cache_read':>12}"
          f"{'output':>9}")
    mrows = c.execute(
        """SELECT model, SUM(requests) rq, SUM(uncached_input) inp,
                  SUM(cache_read) cr, SUM(output) outp,
                  SUM(cache_creation_1h) c1, SUM(cache_creation_5m) c5
           FROM cc_model_usage GROUP BY model ORDER BY rq DESC""").fetchall()
    for r in mrows:
        print(f"{r['model']:<30}{r['rq'] or 0:>10,}{ftok(r['inp']):>9}"
              f"{ftok(r['cr']):>12}{ftok(r['outp']):>9}")

    # 3. Cost (USD) ----------------------------------------------------------
    print("\n3) COST REPORT (USD)")
    rule()
    print(f"{'cost_type':<18}{'billed (amount)':>18}{'list_amount':>16}")
    crows = c.execute(
        """SELECT cost_type, SUM(amount) amt, SUM(list_amount) lst
           FROM cost GROUP BY cost_type ORDER BY lst DESC""").fetchall()
    tot_amt = tot_lst = 0.0
    for r in crows:
        tot_amt += r["amt"] or 0
        tot_lst += r["lst"] or 0
        print(f"{r['cost_type']:<18}{usd(r['amt'] or 0):>18}{usd(r['lst'] or 0):>16}")
    rule()
    print(f"{'TOTAL':<18}{usd(tot_amt):>18}{usd(tot_lst):>16}")
    if tot_lst > 0 and tot_amt == 0:
        print("\n  >> Billed amount is $0 while list value is not: this org is on a")
        print("     seat-based Enterprise plan. 'Anthropic's actual $' as a billing")
        print("     basis would be $0 -> you must rate usage with your own prices")
        print("     (or treat list_amount as a notional basis).")

    # 4. Illustrative rated value of Claude Code usage -----------------------
    print("\n4) ILLUSTRATIVE RATED VALUE OF CLAUDE CODE USAGE")
    print("   (placeholder rates x %.2f markup — this is the invoice-number preview)"
          % MARKUP)
    rule()
    total_rated = 0.0
    for r in mrows:
        v = rated_usd(r["model"], r["inp"], r["c1"], r["c5"], r["cr"], r["outp"])
        total_rated += v
        print(f"{r['model']:<30}{usd(v):>14}")
    rule()
    print(f"{'TOTAL (all clients, unallocated)':<30}{usd(total_rated):>14}")

    # 5. Per-user (the only attribution unit available) ----------------------
    print("\n5) PER-USER CLAUDE CODE USAGE  (top 15 by total tokens)")
    print("   This is the finest attribution the API offers: user, not repo.")
    rule()
    print(f"{'user':<34}{'requests':>10}{'total_tokens':>14}")
    urows = c.execute(
        """SELECT email, name, SUM(requests) rq, SUM(total_tokens) tt
           FROM user_cc_usage GROUP BY user_id
           ORDER BY tt DESC LIMIT 15""").fetchall()
    for r in urows:
        who = r["email"] or r["name"] or "(unknown)"
        print(f"{who:<34}{r['rq'] or 0:>10,}{ftok(r['tt']):>14}")
    n_users = c.execute(
        "SELECT COUNT(DISTINCT user_id) n FROM user_cc_usage").fetchone()["n"]
    print(f"\n  {n_users} distinct users used Claude Code in this window.")

    # 6. Sample raw usage records -------------------------------------------
    print("\n6) SAMPLE USAGE RECORDS  (2 individual per-user / per-day rows)")
    print("   The atomic record from user_usage_report: one user, one day bucket.")
    print("   Finest time granularity the API offers is 1d / 1h / 1m (here: 1d).")
    rule()
    fields = [
        ("day", "day (1d bucket start, UTC)"),
        ("email", "user (actor.email)"),
        ("name", "actor.name"),
        ("user_id", "actor.user_id"),
        ("uncached_input", "uncached_input_tokens"),
        ("cache_creation_1h", "cache_creation.ephemeral_1h_input_tokens"),
        ("cache_creation_5m", "cache_creation.ephemeral_5m_input_tokens"),
        ("cache_read", "cache_read_input_tokens"),
        ("output", "output_tokens"),
        ("total_tokens", "total_tokens"),
        ("web_search_requests", "server_tool_use.web_search_requests"),
        ("requests", "requests"),
    ]
    token_cols = {"uncached_input", "cache_creation_1h", "cache_creation_5m",
                  "cache_read", "output", "total_tokens"}
    samples = c.execute(
        """SELECT * FROM user_cc_usage WHERE total_tokens > 0
           ORDER BY day DESC, total_tokens DESC LIMIT 2""").fetchall()
    for i, r in enumerate(samples, 1):
        print(f"  record #{i}")
        for col, label in fields:
            v = r[col]
            shown = f"{v:,}  ({ftok(v)})" if col in token_cols else v
            print(f"    {label:<44} {shown}")
        print()

    # 7. The gap -------------------------------------------------------------
    print("\n7) WHAT THIS DATA CANNOT TELL YOU")
    rule()
    print("  - Which GitHub repo (-> which client) any usage belongs to.")
    print("    No repo / git / project / session dimension exists in any")
    print("    group_by. Confirmed against your live org above.")
    print("  - Therefore usage cannot be split per client from this API alone.")
    print("    Options: (a) map user -> client (works if staffing is dedicated),")
    print("             (b) add OTEL telemetry stamped with the repo (accurate).")

    store.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--start", default=None, help="unused; range comes from ingest")
    args = ap.parse_args()
    run(args.db)


if __name__ == "__main__":
    main()
