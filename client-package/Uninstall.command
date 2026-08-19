#!/usr/bin/env bash
# Claude Code usage billing - macOS one-click uninstall.
#
# Double-click this file in Finder. It removes the hook and only the settings
# this package added; your own Claude Code settings are left alone, and a
# timestamped backup is written first.

cd "$(dirname "$0")" || {
    echo "Cannot enter the package folder."
    read -r -p "Press Return to close... " _
    exit 1
}

bash install.sh uninstall
status=$?

echo
if [ "$status" -eq 0 ]; then
    echo "Uninstall finished. You can close this window."
else
    echo "Uninstall did not complete cleanly (exit code $status). See above."
fi
echo
read -r -p "Press Return to close... " _
exit $status
