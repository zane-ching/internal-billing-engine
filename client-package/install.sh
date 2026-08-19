#!/usr/bin/env bash
# Claude Code usage-billing client - macOS installer (opt-in).
#
#   bash install.sh --token <TOKEN> --endpoint https://receiver.example.com:4318
#
# Thin shim: finds a Python 3 and hands off to configure.py, which does the
# settings merge. Pass --help, uninstall, or verify straight through:
#
#   bash install.sh uninstall
#   bash install.sh verify --endpoint https://receiver.example.com:4318
#
# Everything installs under your home directory. Nothing is enforced and
# nothing runs as root. See INSTRUCTIONS.md.
#
# If billing-config.json sits next to this script, --token and --endpoint are
# optional and the packaged values are used. That is how Install.command runs
# with no arguments; most people should just double-click that instead.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  cat >&2 <<'EOF'
python3 was not found on your PATH - the repo-tag hook is a Python script and
needs it. On macOS the quickest fix is the Command Line Tools:

    xcode-select --install

then re-run this installer.
EOF
  exit 1
fi

# Reject Python 2 masquerading as python3 on very old boxes.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "python3 is older than 3.8 ($(python3 --version 2>&1)). Please upgrade." >&2
  exit 1
fi

# First positional arg may be a subcommand; default to install.
case "${1:-}" in
  install|uninstall|verify) exec python3 "$SRC_DIR/configure.py" "$@" ;;
  *)                        exec python3 "$SRC_DIR/configure.py" install "$@" ;;
esac
