#!/usr/bin/env bash
# Claude Code usage billing - macOS one-click install.
#
# Double-click this file in Finder. No arguments, no terminal, no admin rights.
# Everything it needs (receiver URL and token) is baked into the package.
#
# It delegates to install.sh -> configure.py, so there is one copy of the
# install logic and this file stays a launcher.
#
# GATEKEEPER: a .command extracted from a downloaded zip is quarantined, and
# double-clicking it gives "cannot be opened because it is from an unidentified
# developer". Right-click the file and choose Open, then Open again - that is a
# per-file exception, not a system setting. See INSTRUCTIONS.md.

cd "$(dirname "$0")" || {
    echo "Cannot enter the package folder. Move it somewhere without odd permissions."
    read -r -p "Press Return to close... " _
    exit 1
}

bash install.sh install --interactive
status=$?

echo
if [ "$status" -eq 0 ]; then
    echo "Install finished. You can close this window."
else
    echo "Install did not complete cleanly (exit code $status)."
    echo "Read the messages above - they say which step failed and what to do."
fi
echo
# Terminal's default is to leave the window open, but that is a user preference
# and cannot be relied on. Hold it here so nobody loses the output.
read -r -p "Press Return to close... " _
exit $status
