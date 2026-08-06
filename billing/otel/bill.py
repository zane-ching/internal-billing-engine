"""Aggregate OTEL-attributed usage into a per-repo bill.

Usage bills to the repo it was done in (the short repo name); an optional
override map can rename or group repos (see billing.otel.repos).

Bills on Claude Code's own reported cost (`claude_code.cost.usage`) — the
actual USD Anthropic charges — marked up. The rate-card estimate (RatingService
on token counts) is shown alongside as a cross-check. Usage from sessions with
no git remote lands in the `unknown` bucket and is called out (unattributable).

If no cost data has been captured yet (token-only store), it falls back to the
rate-card as the billing basis and says so.

    python -m billing.otel.bill                 # bill on actual cost x markup
    python -m billing.otel.bill --basis rates    # force rate-card basis
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from .attribute import resolved_view
from .normalize import normalize_model, repo_name
from .otel_store import OtelStore
from .rating import RatingService

UNATTRIBUTED = "unknown"  # sessions with no git remote -> not tied to a repo


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
    name_of = lambda repo: mapping.get(repo) or repo_name(repo)

    # Repo is RESOLVED per datapoint against the session->repo timeline, so a
    # session that moved between repos splits across them instead of billing
    # entirely to wherever it launched. Falls back to the wrapper's launch-time
    # tag when no timeline exists. See billing.otel.attribute.
    actual_rows = store.db.execute(
        f"WITH r AS ({resolved_view('cost_usage')}) "
        "SELECT resolved_repo AS repo, model, SUM(cost_usd) c FROM r "
        "GROUP BY resolved_repo, model").fetchall()
    have_cost = bool(actual_rows)

    # Rate-card estimate per repo x model (from token counts).
    token_rows = store.db.execute(
        f"WITH r AS ({resolved_view('token_usage')}) "
        "SELECT resolved_repo AS repo, model, token_type, SUM(tokens) tok FROM r "
        "GROUP BY resolved_repo, model, token_type").fetchall()

    # How much of the bill each signal is carrying, and which sessions moved.
    source_rows = store.db.execute(
        f"WITH r AS ({resolved_view('token_usage')}) "
        "SELECT attribution_source, SUM(tokens) tok FROM r "
        "GROUP BY attribution_source ORDER BY tok DESC").fetchall()
    multi_repo = store.multi_repo_sessions()

    if basis == "actual" and not have_cost:
        basis = "rates"

    actual_by_name = defaultdict(float)
    actual_by_name_model = defaultdict(lambda: defaultdict(float))
    ratecard_by_name = defaultdict(float)
    ratecard_by_name_model = defaultdict(lambda: defaultdict(float))
    tokens_by_name = defaultdict(int)
    repos_by_name = defaultdict(set)
    unattributed = set()

    for r in actual_rows:
        nm = name_of(r["repo"])
        actual_by_name[nm] += r["c"] or 0
        actual_by_name_model[nm][normalize_model(r["model"])] += r["c"] or 0
        repos_by_name[nm].add(r["repo"])
        if r["repo"] == UNATTRIBUTED:
            unattributed.add(r["repo"])

    for r in token_rows:
        nm = name_of(r["repo"])
        est = rates.billed(r["model"], r["token_type"], r["tok"]) / markup  # raw, pre-markup
        ratecard_by_name[nm] += est
        ratecard_by_name_model[nm][normalize_model(r["model"])] += est
        tokens_by_name[nm] += r["tok"] or 0
        repos_by_name[nm].add(r["repo"])
        if r["repo"] == UNATTRIBUTED:
            unattributed.add(r["repo"])

    use_actual = basis == "actual"
    cost_by_name = actual_by_name if use_actual else ratecard_by_name
    cost_by_name_model = actual_by_name_model if use_actual else ratecard_by_name_model

    print("=" * 68)
    print("PER-REPO BILL  (OTEL repo-attributed Claude Code usage)")
    if use_actual:
        print(f"basis: ACTUAL cost from claude_code.cost.usage  x{markup:.2f} markup")
    else:
        print(f"basis: RATE-CARD estimate (placeholder rates)   x{markup:.2f} markup"
              + ("" if have_cost else "   [no cost data captured yet]"))
    print("=" * 68)

    grand_basis = grand_billed = 0.0
    for name in sorted(cost_by_name, key=lambda c: (c == UNATTRIBUTED, -cost_by_name[c])):
        base = cost_by_name[name]
        billed = base * markup
        grand_basis += base
        grand_billed += billed
        print(f"\n{name}")
        rule()
        print(f"  repos:  {', '.join(sorted(repos_by_name[name]))}")
        print(f"  tokens: {ftok(tokens_by_name[name])}")
        for model, amt in sorted(cost_by_name_model[name].items(), key=lambda x: -x[1]):
            print(f"    {model:<32} ${amt:>10,.4f}")
        print(f"  {'cost basis':<34} ${base:>10,.4f}")
        print(f"  {'BILLED (x%.2f)' % markup:<34} ${billed:>10,.4f}")

    rule("=")
    print(f"{'GRAND TOTAL cost basis':<36} ${grand_basis:>10,.4f}")
    print(f"{'GRAND TOTAL billed':<36} ${grand_billed:>10,.4f}")

    # Cross-check: actual vs rate-card (only meaningful when we have both)
    if have_cost:
        rc = sum(ratecard_by_name.values())
        ac = sum(actual_by_name.values())
        print(f"\ncross-check  actual=${ac:,.4f}  rate-card=${rc:,.4f}"
              + (f"  (rate-card is {rc/ac:.2f}x actual)" if ac else ""))

    # Attribution provenance — which signal produced each repo label.
    SOURCE_NOTE = {
        "timeline": "hook timeline (mid-session switches attributed)",
        "wrapper":  "wrapper launch tag only (no timeline for that session)",
        "no_remote": "wrapper ran outside a git repo -> unbillable",
        "absent":   "no repo attribute at all -> session bypassed the wrapper",
    }
    if source_rows:
        total_tok = sum(r["tok"] or 0 for r in source_rows) or 1
        print("\nATTRIBUTION SOURCE")
        rule()
        for r in source_rows:
            src, tok = r["attribution_source"], r["tok"] or 0
            print(f"  {src:<11} {ftok(tok):>8}  {tok/total_tok*100:>5.1f}%"
                  f"  {SOURCE_NOTE.get(src, '')}")

    if multi_repo:
        print(f"\n⚠  MULTI-REPO SESSIONS ({len(multi_repo)}) — usage split across repos:")
        for sid, repos in sorted(multi_repo.items()):
            print(f"     {sid[:8]}  {' + '.join(repos)}")
        print("   Split by the timeline's as-of join. Review before invoicing —")
        print("   a switch inside one 60s export interval lands wholly on one side.")

    if unattributed:
        print("\n⚠  UNATTRIBUTED usage -> 'unknown' bucket, not tied to any repo.")
        print("   'no_remote' = ran outside a git repo (genuinely unbillable).")
        print("   'absent'    = no repo tag arrived; the session never passed")
        print("                 through the wrapper (non-CLI surface, or a bad")
        print("                 install). That usage IS billable but unattributed.")

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
