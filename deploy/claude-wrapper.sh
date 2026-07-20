#!/usr/bin/env bash
# claude-wrapper.sh — deployed as the `claude` entrypoint on every dev machine
# (via MDM). It tags each Claude Code CLI session with its git repository so
# usage can be attributed to a client for billing.
#
# Division of labor:
#   - managed-settings.json  → telemetry ON + where to send it (static, enforced)
#   - THIS wrapper           → the per-session repo tag (dynamic)
#
# Deployment (recommended, deterministic):
#   1. Install the real Claude Code binary at a fixed path OFF the PATH,
#      e.g. /opt/cyclotron/claude-real, and set CLAUDE_REAL_BIN to it.
#   2. Install this script as the only `claude` on the PATH (e.g. /usr/local/bin/claude).
# If CLAUDE_REAL_BIN is unset, the wrapper auto-discovers the real binary on the
# PATH (skipping itself) and then common install locations.

set -uo pipefail

# --- Resolve THIS wrapper's real path (so we never exec ourselves) ----------
_resolve() { if command -v realpath >/dev/null 2>&1; then realpath "$1" 2>/dev/null || echo "$1"; else echo "$1"; fi; }
self="$(_resolve "$(command -v -- "$0" 2>/dev/null || echo "$0")")"

# --- Locate the REAL claude binary ------------------------------------------
real="${CLAUDE_REAL_BIN:-}"

if [ -n "$real" ] && [ ! -x "$real" ]; then
  echo "claude-wrapper: CLAUDE_REAL_BIN=$real is not executable." >&2
  exit 127
fi

if [ -z "$real" ]; then
  # Scan PATH for a `claude` that isn't this wrapper.
  IFS=':'
  for dir in $PATH; do
    cand="$dir/claude"
    [ -x "$cand" ] || continue
    [ "$(_resolve "$cand")" = "$self" ] && continue
    real="$cand"; break
  done
  unset IFS
fi

if [ -z "$real" ]; then
  # Fall back to common install locations.
  for cand in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
    [ -x "$cand" ] || continue
    [ "$(_resolve "$cand")" = "$self" ] && continue
    real="$cand"; break
  done
fi

if [ -z "$real" ] || [ ! -x "$real" ]; then
  echo "claude-wrapper: could not find the real claude binary. Set CLAUDE_REAL_BIN." >&2
  exit 127
fi

# --- Compute the repo tag from the current working tree ---------------------
remote="$(git config --get remote.origin.url 2>/dev/null || true)"
[ -z "$remote" ] && remote="unknown"
# OTEL_RESOURCE_ATTRIBUTES is a comma/space-delimited list of key=value pairs;
# strip any whitespace or commas from the value so it can't corrupt the list.
remote="$(printf '%s' "$remote" | tr -d '[:space:],')"

# Append repo=... to any pre-existing resource attributes.
base="${OTEL_RESOURCE_ATTRIBUTES:+${OTEL_RESOURCE_ATTRIBUTES},}"
export OTEL_RESOURCE_ATTRIBUTES="${base}repo=${remote}"

# --- Hand off (replaces this process; exit code + signals pass through) ------
exec "$real" "$@"
