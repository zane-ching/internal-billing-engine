"""Minimal .env loader (no dependency on python-dotenv).

Reads KEY=VALUE lines from ./.env into os.environ without overriding
values already set in the real environment. Called on import by the
client/receiver modules so `python -m billing.*` "just works" locally.
"""

from __future__ import annotations

import os

_LOADED = False


def load_env(path: str = ".env") -> None:
    global _LOADED
    if _LOADED or not os.path.exists(path):
        _LOADED = True
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)
    _LOADED = True
