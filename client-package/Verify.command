#!/usr/bin/env bash
# Claude Code usage billing - macOS one-click check.
#
# Double-click this file to re-check that the receiver is reachable and your
# token is accepted. It writes nothing and changes nothing - run it any time
# your usage stops showing up.

cd "$(dirname "$0")" || {
    echo "Cannot enter the package folder."
    read -r -p "Press Return to close... " _
    exit 1
}

bash install.sh verify
status=$?

echo
if [ "$status" -eq 0 ]; then
    echo "Check passed. You can close this window."
else
    echo "Check FAILED (exit code $status) - until this is fixed, none of your"
    echo "usage is being recorded. See the reason above."
fi
echo
read -r -p "Press Return to close... " _
exit $status
