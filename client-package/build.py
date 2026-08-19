#!/usr/bin/env python3
"""Build the distributable zip for the usage-billing client package.

    python3 build.py --endpoint https://receiver:4318 --token <TOKEN>
    python3 build.py --no-config          -> credential-free zip, flags required at install

Files land at the zip root (so a developer unzips and double-clicks
`Install.command` / `Install.bat` from the extracted folder, with no nested
directory).

ONE-CLICK MODE AND WHAT IT COSTS
  With --endpoint/--token, this writes `billing-config.json` into the archive.
  `configure.py` reads it, so the launchers need no arguments and the developer
  needs no terminal - that is the whole point of the one-click package.

  The consequence: the zip now contains a live shared token. Anyone who obtains
  the file can write arbitrary rows into billing truth, and the package is
  unsigned. Distribute it over a trusted channel (1Password item, internal
  share with access control) - not email, not Slack, not a public bucket. Use
  --no-config to build the older flavour where the token travels separately.

  The config is generated in memory and never written into the source tree, so
  the working copy and git history stay clean of the token.

Timestamps are pinned so rebuilding identical sources produces an identical zip,
which makes the published checksum meaningful. Note that a baked token is part
of the archive, so zips built for different tokens have different checksums -
publish the checksum of the exact file you sent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile

import configure  # reuse the endpoint/token validation the installer applies

# Order is cosmetic (what `unzip -l` shows first) but kept stable for diffs.
# Launchers first: they are what the developer is meant to touch.
CONTENTS = (
    "INSTRUCTIONS.md",
    "Install.command",
    "Install.bat",
    "Uninstall.command",
    "Uninstall.bat",
    "Verify.command",
    "Verify.bat",
    "ADMIN.md",
    "install.sh",
    "install.ps1",
    "configure.py",
    "claude-repo-tag.py",
    "VERSION",
)

# Scripts Windows runs. CRLF is native there, and a batch file with LF line
# endings can mis-parse (labels and multi-line blocks are the usual casualties).
WINDOWS_SCRIPTS = (".bat", ".cmd", ".ps1")

# Files that must keep the executable bit through unzip, or a double-click on
# macOS does nothing at all.
EXECUTABLE = (".sh", ".py", ".command")

# Fixed timestamp (1980-01-01, the zip epoch) for reproducible archives.
FIXED_DATE = (1980, 1, 1, 0, 0, 0)


def scan_for_secrets(here: str) -> None:
    """Refuse to build if a source file has a real credential pasted into it.

    Only source files are scanned. The generated billing-config.json is exempt
    by construction - it is supposed to hold the token, and it never touches
    disk here.
    """
    for name in CONTENTS:
        with open(os.path.join(here, name), "r", encoding="utf-8") as fh:
            body = fh.read()
        for line in body.splitlines():
            low = line.lower()
            if "sk-ant-" in low:
                raise SystemExit("Refusing to build: %s appears to contain an API key." % name)
            # A 64-char hex run is what `openssl rand -hex 32` produces.
            if "token" in low and re.search(r"\b[0-9a-f]{64}\b", line):
                raise SystemExit(
                    "Refusing to build: %s line looks like a real token:\n  %s"
                    % (name, line.strip()[:120]))


def build_config(args) -> str:
    """The billing-config.json body, or "" when building without credentials."""
    if args.no_config:
        return ""
    endpoint = configure.validate_endpoint(args.endpoint, args.allow_insecure)
    token = configure.validate_token(args.token)
    obj = {
        "endpoint": endpoint,
        "token": token,
        # Carried so the installer does not re-prompt the insecure-endpoint
        # refusal on a package the admin deliberately built that way.
        "allow_insecure": bool(args.allow_insecure),
        "_comment": ("Written by build.py. Contains a live billing token - treat "
                     "this package as a credential."),
    }
    return json.dumps(obj, indent=2) + "\n"


def add(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    """Add one entry with pinned time, correct line endings and mode."""
    text_ext = name.endswith((".md", ".sh", ".py", ".ps1", ".bat", ".cmd",
                              ".command", ".json")) or name == "VERSION"
    if text_ext:
        # Normalise first, then convert only what Windows runs. .gitattributes
        # should prevent CRLF reaching the working copy; this makes the zip
        # correct even if it does. A CRLF install.sh dies on macOS with
        # "$'\r': command not found" before it runs a single line.
        data = data.replace(b"\r\n", b"\n")
        if name.endswith(WINDOWS_SCRIPTS):
            data = data.replace(b"\n", b"\r\n")

    info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
    mode = 0o755 if name.endswith(EXECUTABLE) else 0o644
    info.external_attr = (mode << 16) | 0o600
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default="",
                    help="receiver URL to bake in, e.g. https://receiver:4318")
    ap.add_argument("--token", default="",
                    help="shared billing token to bake in (makes the zip a credential)")
    ap.add_argument("--allow-insecure", action="store_true",
                    help="permit baking a plaintext http:// endpoint")
    ap.add_argument("--no-config", action="store_true",
                    help="build without credentials; installers then require flags")
    ap.add_argument("--out", default="",
                    help="output path (default: ../client-package.zip)")
    args = ap.parse_args()

    if not args.no_config and not (args.endpoint and args.token):
        raise SystemExit(
            "Give --endpoint and --token to build a one-click package, or\n"
            "--no-config to build one where the developer supplies both.")

    here = os.path.dirname(os.path.abspath(__file__))
    out = args.out or os.path.join(os.path.dirname(here), "client-package.zip")

    missing = [n for n in CONTENTS if not os.path.exists(os.path.join(here, n))]
    if missing:
        raise SystemExit("Missing from package: %s" % ", ".join(missing))

    with open(os.path.join(here, "VERSION"), "r", encoding="utf-8") as fh:
        version = fh.read().strip()

    scan_for_secrets(here)
    config_body = build_config(args)

    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        if config_body:
            add(zf, configure.CONFIG_NAME, config_body.encode("utf-8"))
        for name in CONTENTS:
            with open(os.path.join(here, name), "rb") as fh:
                add(zf, name, fh.read())

    with open(out, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()

    count = len(CONTENTS) + (1 if config_body else 0)
    print("built %s  (v%s, %d files, %d bytes)"
          % (out, version, count, os.path.getsize(out)))
    print("sha256 %s" % digest)

    if config_body:
        print("\n  endpoint  %s" % json.loads(config_body)["endpoint"])
        print("  token     %s  (BAKED IN)" % configure.mask(args.token))
        print("\nThis zip contains a live billing token. Anyone who gets the file can")
        print("write rows into billing truth. Send it over 1Password or an access-")
        print("controlled share - never email, chat, or a public link. Rebuild with a")
        print("new token if it leaks; old tokens keep working until the receiver stops")
        print("accepting them.")
    else:
        print("\nNo credentials baked in - developers must pass --token/--endpoint,")
        print("and the double-click launchers will fail without them.")

    print("\nPublish the checksum alongside the zip - the package is unsigned, so")
    print("this is the only way a recipient can tell it wasn't modified in transit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
