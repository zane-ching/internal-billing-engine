"""Optional repo -> billing-name overrides.

By default usage bills to the repo it was done in (the short repo name), with no
setup step — there are no prefix-parsing rules to configure. This module is only
needed when you want to OVERRIDE that default: give a repo a friendlier billing
name, or group several repos under one name.

    # 1. dump every repo we've seen, with its current (default or overridden) name:
    python -m billing.otel.repos export --out repo_name_map.csv

    # 2. edit the CSV: change the `bill_name` column for any repo you want to
    #    rename or group (leave it as-is to keep the default short repo name)

    # 3. load your overrides:
    python -m billing.otel.repos import --in repo_name_map.csv
"""

from __future__ import annotations

import argparse
import csv

from .normalize import repo_name
from .otel_store import OtelStore

HEADER = ["repo", "bill_name", "tokens", "users", "sessions"]


def export_csv(path: str, db: str | None = None) -> int:
    store = OtelStore(db) if db else OtelStore()
    rows = store.distinct_repos()
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for r in rows:
            # pre-fill with the resolved name (override if set, else the default)
            # so the file shows what each repo will bill as.
            name = r["bill_name"] or repo_name(r["repo"])
            w.writerow([r["repo"], name, r["tokens"], r["users"], r["sessions"]])
    store.close()
    print(f"Wrote {len(rows)} repos to {path}. "
          f"Edit 'bill_name' only to override; then: repos import --in {path}")
    return len(rows)


def import_csv(path: str, db: str | None = None) -> int:
    """Store a bill_name override for each row whose name differs from the
    default short repo name (rows left at the default are skipped — the default
    needs no override)."""
    store = OtelStore(db) if db else OtelStore()
    n = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            repo = (row.get("repo") or "").strip()
            bill_name = (row.get("bill_name") or "").strip()
            if repo and bill_name and bill_name != repo_name(repo):
                store.set_mapping(repo, bill_name)
                n += 1
    store.close()
    print(f"Imported {n} repo billing-name override(s) from {path}.")
    return n


def main():
    ap = argparse.ArgumentParser(
        description="Manage optional repo -> billing-name overrides.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    e.add_argument("--db", default=None)
    i = sub.add_parser("import")
    i.add_argument("--in", dest="inp", required=True)
    i.add_argument("--db", default=None)
    args = ap.parse_args()
    if args.cmd == "export":
        export_csv(args.out, args.db)
    else:
        import_csv(args.inp, args.db)


if __name__ == "__main__":
    main()
