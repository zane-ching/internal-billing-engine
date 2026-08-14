#!/usr/bin/env bash
# Opt-in installer for the Cyclotron Claude Code usage-billing pilot.
#
#   bash install.sh <SHARED_TOKEN>
#
# It:
#   1. copies the repo-tag hook to ~/.cyclotron/claude-repo-tag.py
#   2. writes ~/.claude/settings.json pointing Claude Code at the pilot receiver
#      (only if you don't already have one — otherwise it tells you how to merge)
# Nothing here is enforced or hidden: everything lives in your home directory and
# uninstall is deleting two files (see INSTRUCTIONS.md).

set -euo pipefail

ENDPOINT="http://20.83.107.157:4318"

TOKEN="${1:-}"
if [ -z "$TOKEN" ]; then
  echo "Usage: bash install.sh <SHARED_TOKEN>" >&2
  echo "  Get the token from Zane over a secure channel (1Password) — it is NOT in this folder." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not on your PATH — the hook needs it. Install python3 and re-run." >&2
  exit 1
fi

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST_DIR="$HOME/.cyclotron"
HOOK="$DEST_DIR/claude-repo-tag.py"

mkdir -p "$DEST_DIR"
cp "$SRC_DIR/claude-repo-tag.py" "$HOOK"
chmod +x "$HOOK"

mkdir -p "$HOME/.claude"
SETTINGS="$HOME/.claude/settings.json"

if [ -e "$SETTINGS" ]; then
  echo "You already have $SETTINGS — NOT overwriting it."
  echo "Open settings.json in this folder and merge its \"env\" and \"hooks\" blocks into"
  echo "yours by hand. Use this exact hook path in each hook command:"
  echo "    $HOOK"
  echo "and replace PASTE_SHARED_TOKEN_HERE with the token you were given."
  exit 0
fi

cat > "$SETTINGS" <<JSON
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "none",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "$ENDPOINT",
    "OTEL_METRIC_EXPORT_INTERVAL": "60000",
    "OTEL_METRICS_INCLUDE_SESSION_ID": "true",
    "OTEL_EXPORTER_OTLP_HEADERS": "X-Billing-Token=$TOKEN",
    "CLAUDE_BILLING_RECEIVER": "$ENDPOINT",
    "CLAUDE_BILLING_TOKEN": "$TOKEN"
  },
  "hooks": {
    "SessionStart":     [{ "hooks": [{ "type": "command", "command": "$HOOK" }] }],
    "CwdChanged":       [{ "hooks": [{ "type": "command", "command": "$HOOK" }] }],
    "DirectoryAdded":   [{ "hooks": [{ "type": "command", "command": "$HOOK" }] }],
    "SessionEnd":       [{ "hooks": [{ "type": "command", "command": "$HOOK" }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "$HOOK", "async": true }] }]
  }
}
JSON

echo "Done."
echo "  hook     -> $HOOK"
echo "  settings -> $SETTINGS"
echo "Start a NEW 'claude' CLI session inside a git repo (one with a GitHub remote)"
echo "to begin reporting. Use the CLI, not the VS Code extension."
