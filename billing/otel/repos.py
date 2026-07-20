"""Export the list of observed repos for manual client assignment, and import
the filled-in mapping back.

    # 1. after ingesting telemetry, dump every repo we've seen:
    python -m billing.otel.repos export --out repo_client_map.csv

    # 2. open the CSV, fill the `client` column for each repo (your manual step)

    # 3. load your assignments:
    python -m billing.otel.repos import --in repo_client_map.csv
"""

from __future__ import annotations

import argparse
import csv

from .clients import client_from_repo
from .otel_store import OtelStore

HEADER = ["repo", "client", "tokens", "users", "sessions"]


def export_csv(path: str, db: str | None = None) -> int:
    store = OtelStore(db) if db else OtelStore()
    rows = store.distinct_repos()
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for r in rows:
            w.writerow([r["repo"], r["client"], r["tokens"], r["users"],
                        r["sessions"]])
    store.close()
    print(f"Wrote {len(rows)} repos to {path}. "
          f"Fill the 'client' column, then: repos import --in {path}")
    return len(rows)


def import_csv(path: str, db: str | None = None) -> int:
    store = OtelStore(db) if db else OtelStore()
    n = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            repo = (row.get("repo") or "").strip()
            client = (row.get("client") or "").strip()
            if repo and client:
                store.set_mapping(repo, client)
                n += 1
    store.close()
    print(f"Imported {n} repo->client assignments from {path}.")
    return n


def automap(db: str | None = None) -> int:
    """Auto-assign clients by parsing each repo name's <client>- prefix."""
    store = OtelStore(db) if db else OtelStore()
    n = 0
    for r in store.distinct_repos():
        repo = r["repo"]
        client = client_from_repo(repo)
        if client:
            store.set_mapping(repo, client)
            n += 1
            print(f"  {repo:<44} -> {client}")
        else:
            print(f"  {repo:<44} -> (unassigned: no <client>- prefix)")
    store.close()
    print(f"Auto-mapped {n} repos from name prefix. "
          f"Override any with an import CSV.")
    return n


def main():
    ap = argparse.ArgumentParser(description="Manage the repo->client mapping.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    e.add_argument("--db", default=None)
    i = sub.add_parser("import")
    i.add_argument("--in", dest="inp", required=True)
    i.add_argument("--db", default=None)
    a = sub.add_parser("automap", help="derive clients from repo name prefixes")
    a.add_argument("--db", default=None)
    args = ap.parse_args()
    if args.cmd == "export":
        export_csv(args.out, args.db)
    elif args.cmd == "import":
        import_csv(args.inp, args.db)
    else:
        automap(args.db)


if __name__ == "__main__":
    main()
