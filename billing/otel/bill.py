"""Aggregate OTEL-attributed usage into a per-client bill.

Bills on Claude Code's own reported cost (`claude_code.cost.usage`) — the
actual USD Anthropic charges — marked up. The rate-card estimate (RatingService
on token counts) is shown alongside as a cross-check. Repos with no client
assignment land in UNASSIGNED and are called out (unbilled revenue at risk).

If no cost data has been captured yet (token-only store), it falls back to the
rate-card as the billing basis and says so.

    python -m billing.otel.bill                 # bill on actual cost x markup
    python -m billing.otel.bill --basis rates    # force rate-card basis
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from .normalize import normalize_model
from .otel_store import OtelStore
from .rating import RatingService

UNASSIGNED = "UNASSIGNED"


def ftok(n) -> str:
    n = n or 0
    if n >= 1_000_000_000:
        return f"{n/1e9:.2f}B"
    if n >= 1_000_000:
        return f"{n/1e6:.1f}M"
    if n >= 1_000:
        return f"{n/1e3:.1f}K"
    return str(n)


def rule(ch="-", n=68):
    print(ch * n)


def run(db: str | None = None, markup: float = 1.50, basis: str = "actual"):
    store = OtelStore(db) if db else OtelStore()
    rates = RatingService(markup=markup)
    mapping = store.get_mapping()
    client_of = lambda repo: mapping.get(repo) or UNASSIGNED

    # Actual cost (Anthropic's own figure) per repo x model.
    actual_rows = store.db.execute(
        "SELECT repo, model, SUM(cost_usd) c FROM cost_usage "
        "GROUP BY repo, model").fetchall()
    have_cost = bool(actual_rows)

    # Rate-card estimate per repo x model (from token counts).
    token_rows = store.db.execute(
        "SELECT repo, model, token_type, SUM(tokens) tok FROM token_usage "
        "GROUP BY repo, model, token_type").fetchall()

    if basis == "actual" and not have_cost:
        basis = "rates"

    actual_by_client = defaultdict(float)
    actual_by_client_model = defaultdict(lambda: defaultdict(float))
    ratecard_by_client = defaultdict(float)
    ratecard_by_client_model = defaultdict(lambda: defaultdict(float))
    tokens_by_client = defaultdict(int)
    repos_by_client = defaultdict(set)
    unassigned = set()

    for r in actual_rows:
        cl = client_of(r["repo"])
        actual_by_client[cl] += r["c"] or 0
        actual_by_client_model[cl][normalize_model(r["model"])] += r["c"] or 0
        repos_by_client[cl].add(r["repo"])
        if cl == UNASSIGNED:
            unassigned.add(r["repo"])

    for r in token_rows:
        cl = client_of(r["repo"])
        est = rates.billed(r["model"], r["token_type"], r["tok"]) / markup  # raw, pre-markup
        ratecard_by_client[cl] += est
        ratecard_by_client_model[cl][normalize_model(r["model"])] += est
        tokens_by_client[cl] += r["tok"] or 0
        repos_by_client[cl].add(r["repo"])
        if cl == UNASSIGNED:
            unassigned.add(r["repo"])

    use_actual = basis == "actual"
    cost_by_client = actual_by_client if use_actual else ratecard_by_client
    cost_by_client_model = actual_by_client_model if use_actual else ratecard_by_client_model

    print("=" * 68)
    print("PER-CLIENT BILL  (OTEL repo-attributed Claude Code usage)")
    if use_actual:
        print(f"basis: ACTUAL cost from claude_code.cost.usage  x{markup:.2f} markup")
    else:
        print(f"basis: RATE-CARD estimate (placeholder rates)   x{markup:.2f} markup"
              + ("" if have_cost else "   [no cost data captured yet]"))
    print("=" * 68)

    grand_basis = grand_billed = 0.0
    for client in sorted(cost_by_client, key=lambda c: (c == UNASSIGNED, -cost_by_client[c])):
        base = cost_by_client[client]
        billed = base * markup
        grand_basis += base
        grand_billed += billed
        print(f"\n{client}")
        rule()
        print(f"  repos:  {', '.join(sorted(repos_by_client[client]))}")
        print(f"  tokens: {ftok(tokens_by_client[client])}")
        for model, amt in sorted(cost_by_client_model[client].items(), key=lambda x: -x[1]):
            print(f"    {model:<32} ${amt:>10,.4f}")
        print(f"  {'cost basis':<34} ${base:>10,.4f}")
        print(f"  {'BILLED (x%.2f)' % markup:<34} ${billed:>10,.4f}")

    rule("=")
    print(f"{'GRAND TOTAL cost basis':<36} ${grand_basis:>10,.4f}")
    print(f"{'GRAND TOTAL billed':<36} ${grand_billed:>10,.4f}")

    # Cross-check: actual vs rate-card (only meaningful when we have both)
    if have_cost:
        rc = sum(ratecard_by_client.values())
        ac = sum(actual_by_client.values())
        print(f"\ncross-check  actual=${ac:,.4f}  rate-card=${rc:,.4f}"
              + (f"  (rate-card is {rc/ac:.2f}x actual)" if ac else ""))

    if unassigned:
        print("\n⚠  UNASSIGNED repos (no client mapping -> not billed to anyone):")
        for repo in sorted(unassigned):
            print(f"     - {repo}")
        print("   Assign them:  repos export → fill client → repos import (or automap)")

    store.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--markup", type=float, default=1.50)
    ap.add_argument("--basis", choices=["actual", "rates"], default="actual",
                    help="actual = claude_code.cost.usage; rates = RatingService estimate")
    args = ap.parse_args()
    run(args.db, args.markup, args.basis)


if __name__ == "__main__":
    main()
