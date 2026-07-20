"""Derive a client bucket from a repo name.

Cyclotron repos are named `<client>-<description>` (client, a hyphen, then a
description). The client is the segment before the first hyphen of the repo
NAME (the last path segment), so both of these map to client "acme":

    github.com/cyclotron/acme-web        -> acme
    github.com/cyclotron/acme-data-loader -> acme
"""

from __future__ import annotations


def client_from_repo(repo: str) -> str:
    """Return the client bucket, or '' if none can be derived."""
    if not repo or repo == "unknown":
        return ""
    name = repo.rstrip("/").split("/")[-1]   # repo name = last path segment
    if "-" not in name:
        return ""                            # no <client>- prefix -> can't derive
    prefix = name.split("-", 1)[0].strip().lower()
    return prefix
