"""Normalize a git remote URL into a canonical repo key.

Collapses the many ways the same repo can be expressed so that ssh and https
clones of the same repo bucket together:

    git@github.com:Cyclotron/Acme-API.git    -> github.com/cyclotron/acme-api
    https://github.com/Cyclotron/Acme-API.git -> github.com/cyclotron/acme-api
    ssh://git@github.com/Cyclotron/Acme-API    -> github.com/cyclotron/acme-api
"""

from __future__ import annotations

import re

_SCHEME = re.compile(r"^[a-zA-Z0-9+.\-]+://")
_SCP = re.compile(r"^[\w.\-]+@([^:/]+):(.+)$")   # git@host:path
_USERINFO = re.compile(r"^[^@/]+@")

_MODEL_VARIANT = re.compile(r"\[[^\]]*\]")       # context variant, e.g. [1m]
_MODEL_DATE = re.compile(r"-\d{8}$")             # dated snapshot, e.g. -20251001


def normalize_model(model: str | None) -> str:
    """Collapse a model label to its billing identity: drop context-window
    variants (`[1m]`) and dated-snapshot suffixes so the token metric and the
    cost metric group onto the same line.

        claude-opus-4-8[1m]         -> claude-opus-4-8
        claude-haiku-4-5-20251001   -> claude-haiku-4-5
    """
    if not model:
        return "unknown"
    m = _MODEL_VARIANT.sub("", model)
    m = _MODEL_DATE.sub("", m)
    return m.strip() or "unknown"


def normalize_remote(raw: str | None) -> str:
    if not raw:
        return "unknown"
    s = raw.strip()
    s = re.sub(r"\.git$", "", s)
    m = _SCP.match(s)
    if m:
        host, path = m.group(1), m.group(2)
    else:
        s = _SCHEME.sub("", s)          # strip scheme://
        s = _USERINFO.sub("", s)        # strip user@
        host, _, path = s.partition("/")
    key = f"{host.lower()}/{path.strip('/').lower()}".rstrip("/")
    return key or "unknown"
