"""Resolve which repo a usage datapoint should bill to.

The wrapper stamps `repo=` once, at session launch, into OTEL_RESOURCE_ATTRIBUTES.
OTEL resource attributes are immutable for the process lifetime, so that tag is
frozen for the whole session — a developer who starts in one client's repo and
`cd`s into another's mid-session has ALL of it billed to the first.

The fix is a second signal. A `CwdChanged` hook (see deploy/claude-repo-tag.py)
records `(session_id, ts, repo)` every time the working directory changes, and
this module joins that timeline back onto the usage datapoints:

    for each datapoint, the repo is the timeline entry with the greatest
    ts <= datapoint.ts, for the same session_id            ("as-of" join)

Why this works on the data we already store:
  - every datapoint carries `session_id` and `ts` (the OTLP `timeUnixNano`,
    not receipt time) -> both join keys already exist
  - Claude Code metrics use DELTA temporality, so each datapoint is the
    increment for its own interval -> attributing an increment to whichever
    repo was active then is arithmetically sound
  - `repo` is NOT part of `dp_key`, so resolution is a derived label:
    re-running it never breaks dedupe, and a corrected/late-arriving timeline
    retroactively fixes past bills with no re-ingest

Resolution is therefore done at QUERY time, not ingest time. The stored
`repo`/`repo_raw` columns keep the wrapper's original value untouched.

Fallback chain, most to least trustworthy:

    timeline   the hook told us where the session was at that moment
    wrapper    no timeline for this session; use the launch-time repo= tag
    no_remote  wrapper ran but the directory had no git remote (unbillable)
    absent     no repo attribute arrived at all — the session never passed
               through the wrapper (non-CLI surface, or a bypassed install)

`no_remote` and `absent` both normalize to the 'unknown' repo but mean very
different things operationally, so they're reported separately.
"""

from __future__ import annotations

TIMELINE_TABLE = "session_repo_timeline"

# Repo active at the datapoint's own timestamp.
_AS_OF = """(SELECT r.repo FROM {tl} r
              WHERE r.session_id = {a}.session_id AND r.ts <= {a}.ts
              ORDER BY r.ts DESC, r.seq DESC LIMIT 1)"""

# The session's earliest known repo. Covers a datapoint whose ts slightly
# precedes the first hook event (export-interval rounding, small clock skew):
# the session must have started somewhere, and the timeline is a better source
# than the frozen launch tag.
_FIRST = """(SELECT r.repo FROM {tl} r
              WHERE r.session_id = {a}.session_id
              ORDER BY r.ts ASC, r.seq ASC LIMIT 1)"""


def resolved_repo(alias: str = "t") -> str:
    """SQL expression: the repo this row bills to."""
    f = {"tl": TIMELINE_TABLE, "a": alias}
    return f"COALESCE({_AS_OF.format(**f)}, {_FIRST.format(**f)}, {alias}.repo)"


def attribution_source(alias: str = "t") -> str:
    """SQL expression: which signal produced the repo (see module docstring)."""
    f = {"tl": TIMELINE_TABLE, "a": alias}
    return (
        f"CASE WHEN {_AS_OF.format(**f)} IS NOT NULL "
        f"       OR {_FIRST.format(**f)} IS NOT NULL THEN 'timeline' "
        f"     WHEN {alias}.repo_raw = '' THEN 'absent' "
        f"     WHEN {alias}.repo = 'unknown' THEN 'no_remote' "
        f"     ELSE 'wrapper' END"
    )


def resolved_view(table: str, alias: str = "t") -> str:
    """A SELECT over token_usage / cost_usage with two columns added:

        resolved_repo       the repo to bill (use this instead of `repo`)
        attribution_source  timeline | wrapper | no_remote | absent

    The original `repo` / `repo_raw` columns are preserved so the wrapper's
    launch-time tag stays available for reconciliation.
    """
    return (f"SELECT {alias}.*, {resolved_repo(alias)} AS resolved_repo, "
            f"{attribution_source(alias)} AS attribution_source "
            f"FROM {table} {alias}")
