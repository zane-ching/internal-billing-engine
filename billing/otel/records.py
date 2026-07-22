"""Dump individual token-usage records with their repo tag (and mapped client).

Each row is one OTEL data point ingested from claude_code.token.usage, showing
the repo it was tagged with via OTEL_RESOURCE_ATTRIBUTES.

    python -m billing.otel.records                 # all records
    python -m billing.otel.records --repo globex   # filter by repo substring
    python -m billing.otel.records --limit 20
"""

from __future__ import annotations

import argparse

from .normalize import repo_name
from .otel_store import OtelStore


def run(db: str | None = None, repo_filter: str | None = None, limit: int | None = None):
    store = OtelStore(db) if db else OtelStore()
    mapping = store.get_mapping()
    name_of = lambda repo: mapping.get(repo) or repo_name(repo)

    sql = ("SELECT ts, repo, repo_raw, user_email, model, token_type, tokens, "
           "session_id FROM token_usage")
    params = []
    if repo_filter:
        sql += " WHERE repo LIKE ?"
        params.append(f"%{repo_filter}%")
    sql += " ORDER BY repo, session_id, model, token_type"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    rows = store.db.execute(sql, params).fetchall()

    print(f"{'repo':<34}{'bill_name':<20}{'user':<16}{'model':<18}"
          f"{'type':<14}{'tokens':>12}")
    print("-" * 114)
    for r in rows:
        user = (r["user_email"] or "").split("@")[0]
        print(f"{r['repo']:<34}{name_of(r['repo']):<20}{user:<16}{r['model']:<18}"
              f"{r['token_type']:<14}{r['tokens']:>12,}")
    print("-" * 114)
    print(f"{len(rows)} records")

    # show one record fully expanded so every tagged attribute is visible
    if rows:
        r = rows[0]
        print("\nOne record, all fields:")
        for k in r.keys():
            print(f"  {k:<12} {r[k]}")
        print(f"  {'bill_name':<12} {name_of(r['repo'])}")
    store.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--repo", default=None, help="filter by repo substring")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(args.db, args.repo, args.limit)


if __name__ == "__main__":
    main()
