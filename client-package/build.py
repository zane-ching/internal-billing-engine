#!/usr/bin/env python3
"""Build the distributable zip for the usage-billing client package.

    python3 build.py            -> ../client-package.zip

Files land at the zip root (so a developer unzips and runs `install.sh` /
`install.ps1` from the extracted folder, with no nested directory).

Deliberately excluded: this build script, VERSION-less junk, caches, and anything
carrying a real token. The token is never packaged — it goes over 1Password
separately (see ADMIN.md).

Timestamps are pinned so rebuilding identical sources produces an identical zip,
which makes the published checksum meaningful.
"""

from __future__ import annotations

import hashlib
import os
import sys
import zipfile

# Order is cosmetic (what `unzip -l` shows first) but kept stable for diffs.
CONTENTS = (
    "INSTRUCTIONS.md",
    "ADMIN.md",
    "install.sh",
    "install.ps1",
    "configure.py",
    "claude-repo-tag.py",
    "VERSION",
)

# Fixed timestamp (1980-01-01, the zip epoch) for reproducible archives.
FIXED_DATE = (1980, 1, 1, 0, 0, 0)


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(os.path.dirname(here), "client-package.zip")

    missing = [n for n in CONTENTS if not os.path.exists(os.path.join(here, n))]
    if missing:
        print("Missing from package: %s" % ", ".join(missing), file=sys.stderr)
        return 1

    version = ""
    with open(os.path.join(here, "VERSION"), "r", encoding="utf-8") as fh:
        version = fh.read().strip()

    # Guard: never ship a real token. Catches a stray edit before it leaves.
    for name in CONTENTS:
        with open(os.path.join(here, name), "r", encoding="utf-8") as fh:
            body = fh.read()
        for line in body.splitlines():
            low = line.lower()
            if "sk-ant-" in low:
                print("Refusing to build: %s appears to contain an API key." % name,
                      file=sys.stderr)
                return 1
            # A 64-char hex run is what `openssl rand -hex 32` produces.
            if "token" in low:
                import re
                if re.search(r"\b[0-9a-f]{64}\b", line):
                    print("Refusing to build: %s line looks like a real token:\n  %s"
                          % (name, line.strip()[:120]), file=sys.stderr)
                    return 1

    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in CONTENTS:
            src = os.path.join(here, name)
            with open(src, "rb") as fh:
                data = fh.read()
            # Force LF on everything the shell has to read. A CRLF install.sh
            # dies on macOS with "$'\r': command not found" before it runs a
            # single line. .gitattributes should prevent CRLF ever reaching the
            # working copy; this makes the zip correct even if it does.
            if not name.endswith(".ps1"):
                data = data.replace(b"\r\n", b"\n")
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            # 0o755 for the installers so the exec bit survives unzip on macOS.
            mode = 0o755 if name.endswith((".sh", ".py")) else 0o644
            info.external_attr = (mode << 16) | 0o600
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)

    with open(out, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()

    print("built %s  (v%s, %d files, %d bytes)"
          % (out, version, len(CONTENTS), os.path.getsize(out)))
    print("sha256 %s" % digest)
    print("\nPublish the checksum alongside the zip — the package is unsigned, so")
    print("this is the only way a recipient can tell it wasn't modified in transit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
