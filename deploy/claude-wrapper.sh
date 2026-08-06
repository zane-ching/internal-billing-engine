#!/usr/bin/env bash
# claude-wrapper.sh — deployed as the `claude` entrypoint on every dev machine
# (via MDM). It tags each Claude Code CLI session with its git repository so
# usage can be attributed to a client for billing.
#
# SELF-DETECTION MARKER: __CYCLOTRON_CLAUDE_WRAPPER__
#   Do not remove that string. Every copy of this script carries it, which is
#   how the script recognises itself and refuses to exec a wrapper as if it
#   were the real binary. Comparing paths is NOT enough: a second *copy* of
#   this script in another PATH directory has a different realpath, so each
#   copy would exec the other forever (`exec` doesn't fork, so that hangs the
#   session in a single spinning process with no error output).
#
# Division of labor:
#   - managed-settings.json  → telemetry ON + where to send it (static, enforced)
#   - THIS wrapper           → the per-session repo tag (dynamic)
#
# Deployment (recommended, deterministic):
#   1. Install the real Claude Code binary at a fixed path OFF the PATH,
#      e.g. /opt/cyclotron/claude-real, and set CLAUDE_REAL_BIN to it.
#   2. Install this script as the only `claude` on the PATH (e.g. /usr/local/bin/claude).
# CLAUDE_REAL_BIN must come from the SHELL environment (system profile / MDM
# environment payload) — NOT from managed-settings.json. That file's `env` is
# applied by Claude Code to itself, and this wrapper is Claude Code's parent,
# so it can never see it. See deploy/README.md.
# If CLAUDE_REAL_BIN is unset, the wrapper auto-discovers the real binary on the
# PATH (skipping any copy of itself) and then common install locations.

set -uo pipefail

# String that identifies any copy of this wrapper (see header).
wrapper_marker='__CYCLOTRON_CLAUDE_WRAPPER__'

# --- Is $1 a copy of this wrapper (rather than the real binary)? -------------
is_wrapper() {
  [ -f "$1" ] && LC_ALL=C grep -q "$wrapper_marker" "$1" 2>/dev/null
}

# --- Re-entry guard ----------------------------------------------------------
# Second line of defence behind is_wrapper(): if we ever do re-enter ourselves,
# fail loudly and immediately instead of looping until someone kills the shell.
if [ "${CLAUDE_WRAPPER_DEPTH:-0}" -ge 2 ]; then
  echo "claude-wrapper: re-entered itself — more than one copy of the wrapper is on" >&2
  echo "  the PATH. Leave exactly one, and/or set CLAUDE_REAL_BIN to the real binary." >&2
  exit 127
fi
export CLAUDE_WRAPPER_DEPTH=$(( ${CLAUDE_WRAPPER_DEPTH:-0} + 1 ))

# --- Locate the REAL claude binary ------------------------------------------
real="${CLAUDE_REAL_BIN:-}"

if [ -n "$real" ]; then
  if [ ! -x "$real" ]; then
    echo "claude-wrapper: CLAUDE_REAL_BIN=$real is not executable." >&2
    exit 127
  fi
  if is_wrapper "$real"; then
    echo "claude-wrapper: CLAUDE_REAL_BIN=$real is this wrapper, not the real binary." >&2
    exit 127
  fi
fi

if [ -z "$real" ]; then
  # Scan PATH for a `claude` that isn't this wrapper.
  IFS=':'
  for dir in $PATH; do
    cand="$dir/claude"
    [ -x "$cand" ] || continue
    is_wrapper "$cand" && continue
    real="$cand"; break
  done
  unset IFS
fi

if [ -z "$real" ]; then
  # Fall back to common install locations.
  for cand in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
    [ -x "$cand" ] || continue
    is_wrapper "$cand" && continue
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
# ...and if stripping emptied it, don't emit a valueless `repo=`.
[ -z "$remote" ] && remote="unknown"

# Rebuild the attribute list, keeping foreign pairs but DROPPING any repo= that
# an outer wrapper already set. A nested `claude` (hook, `claude -p`, a Bash
# tool call) would otherwise append a second repo= pair on every level.
base=""
if [ -n "${OTEL_RESOURCE_ATTRIBUTES:-}" ]; then
  IFS=','
  for kv in $OTEL_RESOURCE_ATTRIBUTES; do
    case "$kv" in
      repo=*|'') continue ;;
    esac
    base="${base:+$base,}$kv"
  done
  unset IFS
fi
export OTEL_RESOURCE_ATTRIBUTES="${base:+$base,}repo=${remote}"

# --- Hand off (replaces this process; exit code + signals pass through) ------
exec "$real" "$@"
