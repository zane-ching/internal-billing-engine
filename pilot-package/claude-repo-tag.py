#!/usr/bin/env python3
"""Claude Code hook: record which repo a session is working in, over time.

Registered on SessionStart / CwdChanged / DirectoryAdded / SessionEnd /
UserPromptSubmit (see managed-settings.json). Claude Code passes a JSON object
on stdin carrying `session_id`, `cwd`, and `hook_event_name`; this resolves
`cwd` to a git remote and POSTs one timeline entry to the billing receiver.

Why a hook and not the wrapper: OTEL resource attributes (where the wrapper puts
`repo=`) are frozen at process start, so the wrapper cannot see a mid-session
`cd`. Hooks fire on the transition and run on every Claude Code surface (CLI,
IDE extension, desktop, web), not just the CLI the wrapper shims.

Why UserPromptSubmit on top of the transition events: on non-CLI surfaces the
wrapper never runs, so this hook is the ONLY repo signal. A session whose very
first delivery is missed (receiver down, or the process started before the hook
was registered) has no timeline row at all, both rungs of the as-of join return
NULL, and the WHOLE session silently bills to 'unknown'. Re-tagging on every
prompt makes any single miss self-healing. It's registered `async` so a slow or
unreachable receiver never adds latency to a prompt, and the receiver dedupes
byte-identical replays on its primary key.

    Receiver URL:  CLAUDE_BILLING_RECEIVER  (default http://127.0.0.1:4318)

DESIGN RULE — this hook must never break a developer's session:
  * always exits 0. Never exit 2 (that would BLOCK the tool call).
  * short network timeout, failures swallowed.
  * a lost timeline entry degrades attribution for one interval; a blocked
    tool call breaks someone's work. The tradeoff is not close.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

RECEIVER = os.environ.get("CLAUDE_BILLING_RECEIVER", "http://127.0.0.1:4318")
ENDPOINT = "/v1/session-repo"
TIMEOUT = float(os.environ.get("CLAUDE_BILLING_TIMEOUT", "2.0"))
# Shared fleet token; sent so the receiver accepts this write. Empty = the
# receiver is running open (see billing/otel/receiver.py). Never block a
# session over auth: a rejected post is dropped like any other failed one.
TOKEN = os.environ.get("CLAUDE_BILLING_TOKEN", "").strip()


def git_remote(cwd: str) -> str:
    """The origin remote for `cwd`, or '' if it isn't a git repo / has no origin."""
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=TIMEOUT, check=False)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return 0  # nothing to report; never block

    session_id = payload.get("session_id") or ""
    cwd = payload.get("cwd") or ""
    event = payload.get("hook_event_name") or ""
    if not session_id or not cwd:
        return 0

    now = datetime.now(timezone.utc)
    body = json.dumps({
        "session_id": session_id,
        # Second-precision UTC, identical format to token_usage.ts so the
        # as-of join compares lexicographically. `seq` orders events that
        # land inside the same second.
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seq": now.microsecond // 1000,
        "cwd": cwd,
        "repo_raw": git_remote(cwd),
        "event": event,
        # Subagents can run in a different directory than the main loop; keep
        # the id so a future refinement can attribute them separately.
        "agent_id": payload.get("agent_id") or "",
    }).encode()

    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["X-Billing-Token"] = TOKEN
    req = urllib.request.Request(
        RECEIVER.rstrip("/") + ENDPOINT, data=body,
        headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
    except (urllib.error.URLError, OSError, ValueError):
        pass  # receiver down / unreachable — drop it, never block the session
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:      # noqa: BLE001 - a telemetry hook must never fail loudly
        sys.exit(0)
