#!/usr/bin/env bash
# Self-test launcher: emit REAL Claude Code telemetry from THIS machine to the
# local billing receiver, tagged with the current repo.
#
# Usage (in a NORMAL terminal, from inside any git repo, receiver already up):
#     /Users/zaneching/Desktop/internal-billing-engine/deploy/dev-selftest.sh
#
# Then use Claude Code normally for a minute and exit. Token-usage records POST
# to the receiver within ~5s (and on exit). Reversible: this only affects the
# one session it launches — it changes nothing global.

set -euo pipefail

RECEIVER="${OTEL_ENDPOINT:-http://127.0.0.1:4318}"

remote="$(git config --get remote.origin.url 2>/dev/null || true)"
[ -z "$remote" ] && remote="unknown"

export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT="$RECEIVER"
export OTEL_METRIC_EXPORT_INTERVAL=5000        # 5s, so records show up fast (default 60s)
export OTEL_RESOURCE_ATTRIBUTES="repo=${remote}"

echo "-------------------------------------------------------------"
echo " Telemetry ON  ->  $RECEIVER"
echo " repo tag      ->  ${remote}"
echo " Launch a session, do a little work, then /exit."
echo "-------------------------------------------------------------"

exec "${CLAUDE_BIN:-claude}" "$@"
