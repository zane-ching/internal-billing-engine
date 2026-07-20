"""Reconcile OTEL-captured Claude Code usage against the authoritative
Analytics API totals, to quantify coverage and the attribution gap.

The Analytics API knows the TRUE org-wide claude_code token totals (every user,
every machine). The OTEL pipeline only sees usage from machines that are
actually emitting telemetry, and can only bill usage that carries a repo tag
mapped to a client. Comparing the two answers: "what fraction of real usage are
we capturing and attributing?"

    python -m billing.reconcile --start 2026-07-14 --end 2026-07-15

Coverage funnel (tokens):
    Analytics truth  ── org-wide claude_code (authoritative)
      └ OTEL captured        gap = telemetry never received (machines not emitting)
          └ repo-tagged      gap = received but no repo tag (wrapper missing)
              └ client-mapped gap = tagged but repo not assigned to a client

Token types are mapped between the two APIs (they name them differently).
"""

from __future__ import annotations

import argparse

from .analytics_client import AnalyticsClient, AnalyticsError
from .otel.otel_store import OtelStore
from .store import tokens as analytics_tokens

# canonical token buckets used on both sides
CANON = ["input", "output", "cacheRead", "cacheCreation"]


def ftok(n) -> str:
    n = n or 0
    if n >= 1_000_000_000:
        return f"{n/1e9:.2f}B"
    if n >= 1_000_000:
        return f"{n/1e6:.1f}M"
    if n >= 1_000:
        return f"{n/1e3:.1f}K"
    return str(int(n))


def pct(part, whole) -> str:
    return f"{(100.0 * part / whole):.2f}%" if whole else "n/a"


def analytics_claude_code_totals(start, end) -> dict:
    """Authoritative org-wide claude_code tokens, in canonical buckets."""
    client = AnalyticsClient()
    out = {k: 0 for k in CANON}
    for _day, row in client.usage_report(start, end, products=["claude_code"]):
        t = analytics_tokens(row)
        out["input"] += t["uncached_input"]
        out["output"] += t["output"]
        out["cacheRead"] += t["cache_read"]
        out["cacheCreation"] += t["cache_creation_1h"] + t["cache_creation_5m"]
    return out


def otel_totals(store: OtelStore, start, end) -> dict:
    """OTEL captured tokens in [start, end), split into captured / tagged / mapped."""
    mapping = {r: c for r, c in store.get_mapping().items() if c}
    rows = store.db.execute(
        """SELECT repo, token_type, SUM(tokens) tok FROM token_usage
           WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) < ?
           GROUP BY repo, token_type""", (start, end)).fetchall()
    captured = {k: 0 for k in CANON}
    tagged = {k: 0 for k in CANON}
    mapped = {k: 0 for k in CANON}
    for r in rows:
        tt = r["token_type"] if r["token_type"] in CANON else None
        if tt is None:
            continue
        captured[tt] += r["tok"] or 0
        if r["repo"] and r["repo"] != "unknown":
            tagged[tt] += r["tok"] or 0
            if r["repo"] in mapping:
                mapped[tt] += r["tok"] or 0
    return {"captured": captured, "tagged": tagged, "mapped": mapped}


def run(start: str, end: str, db: str | None = None):
    store = OtelStore(db) if db else OtelStore()

    print("=" * 70)
    print(f"RECONCILIATION  period {start} -> {end}  (product=claude_code)")
    print("=" * 70)

    try:
        truth = analytics_claude_code_totals(start, end)
    except AnalyticsError as e:
        print(f"\n!! Could not reach Analytics API: {e}")
        print("   (Is the token in .env valid? It may have been revoked.)")
        store.close()
        return

    otel = otel_totals(store, start, end)
    captured, tagged, mapped = otel["captured"], otel["tagged"], otel["mapped"]

    # Per-token-type: truth vs captured -----------------------------------
    print(f"\nBY TOKEN TYPE   {'analytics(truth)':>18}{'otel captured':>16}{'coverage':>11}")
    print("-" * 70)
    for k in CANON:
        print(f"  {k:<14}{ftok(truth[k]):>18}{ftok(captured[k]):>16}"
              f"{pct(captured[k], truth[k]):>11}")
    A = sum(truth.values())
    C = sum(captured.values())
    T = sum(tagged.values())
    M = sum(mapped.values())
    print("-" * 70)
    print(f"  {'TOTAL':<14}{ftok(A):>18}{ftok(C):>16}{pct(C, A):>11}")

    # Coverage funnel -----------------------------------------------------
    print("\nCOVERAGE FUNNEL (total tokens)")
    print("-" * 70)
    print(f"  Analytics truth (org claude_code)   {ftok(A):>12}   100.00%")
    print(f"  OTEL captured                       {ftok(C):>12}   {pct(C, A):>7}"
          f"   gap {ftok(A - C)} not received")
    print(f"    of which repo-tagged              {ftok(T):>12}   {pct(T, A):>7}"
          f"   gap {ftok(C - T)} received, no repo")
    print(f"      of which client-mapped          {ftok(M):>12}   {pct(M, A):>7}"
          f"   gap {ftok(T - M)} tagged, unmapped")

    print("\nBILLABLE COVERAGE = client-mapped / truth = " + pct(M, A))
    if C < A * 0.99:
        print("\nNote: OTEL data here is SYNTHETIC (sample_payload), so low coverage")
        print("      is expected — it reflects that no real machines emit yet, not a")
        print("      bug. Against live telemetry, this % is your real attribution rate.")
    store.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive)")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    run(args.start, args.end, args.db)


if __name__ == "__main__":
    main()
