"""Generate a realistic OTLP/JSON `claude_code.token.usage` payload and POST it.

Lets you exercise the whole pipeline without a fleet of Claude Code machines.
The data mirrors what Claude Code actually emits: one resource per (repo, user,
session), token metric with delta sum data points across models and token types.

Note two repos below are the SAME repo expressed as ssh vs https — the receiver's
normalization must collapse them into one bucket.

    python -m billing.otel.sample_payload           # POST to the receiver
    python -m billing.otel.sample_payload --emit     # print the JSON instead
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone

from ..config import load_env

load_env()

# (repo_remote_as_emitted, user_email, user_id, session_id)
SESSIONS = [
    ("git@github.com:Cyclotron/acme-web.git",       "nathan.hart@cyclotron.com",  "user_01a", "sess_aaa"),
    ("https://github.com/cyclotron/Acme-Web.git",    "ryan.brown@cyclotron.com",   "user_01b", "sess_bbb"),  # SAME repo, https
    ("https://github.com/cyclotron/globex-api",      "stuart.poe@cyclotron.com",   "user_01c", "sess_ccc"),
    ("git@github.com:cyclotron/initech-mobile.git",  "miles.barnes@cyclotron.com", "user_01d", "sess_ddd"),
    ("https://github.com/cyclotron/globex-api",      "ramya.pasupuleti@cyclotron.com", "user_01e", "sess_eee"),
    (None,                                           "emilia.reyes@cyclotron.com", "user_01f", "sess_fff"),  # no git remote
]

# per session: {model: {token_type: tokens}}
USAGE = {
    "sess_aaa": {"claude-opus-4-8":  {"input": 210_000, "output": 340_000, "cacheRead": 22_000_000, "cacheCreation": 1_800_000}},
    "sess_bbb": {"claude-sonnet-5":  {"input": 120_000, "output": 190_000, "cacheRead": 9_500_000,  "cacheCreation": 700_000}},
    "sess_ccc": {"claude-opus-4-8":  {"input": 90_000,  "output": 150_000, "cacheRead": 12_000_000, "cacheCreation": 900_000},
                 "claude-haiku-4-5": {"input": 40_000,  "output": 60_000,  "cacheRead": 400_000,    "cacheCreation": 50_000}},
    "sess_ddd": {"claude-sonnet-5":  {"input": 300_000, "output": 520_000, "cacheRead": 31_000_000, "cacheCreation": 2_400_000}},
    "sess_eee": {"claude-fable-5":   {"input": 70_000,  "output": 110_000, "cacheRead": 6_000_000,  "cacheCreation": 500_000}},
    "sess_fff": {"claude-sonnet-5":  {"input": 20_000,  "output": 30_000,  "cacheRead": 800_000,    "cacheCreation": 90_000}},
}

BASE_NS = int(datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1e9)


def _kv(key, value):
    if isinstance(value, bool):
        v = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}


def build_payload() -> dict:
    resource_metrics = []
    dp_time = BASE_NS
    for repo, email, user_id, session_id in SESSIONS:
        res_attrs = [
            _kv("service.name", "claude-code"),
            _kv("user.email", email),
            _kv("user.id", user_id),
            _kv("organization.id", "org_017BoAXhqPPAikCCgiaTvzVU"),
            _kv("session.id", session_id),
        ]
        if repo:
            res_attrs.append(_kv("repo", repo))  # <- injected via OTEL_RESOURCE_ATTRIBUTES
        points = []
        cost_points = []
        for model, types in USAGE[session_id].items():
            for ttype, tokens in types.items():
                dp_time += 1
                points.append({
                    "asInt": str(tokens),
                    "startTimeUnixNano": str(BASE_NS),
                    "timeUnixNano": str(dp_time),
                    "attributes": [
                        _kv("type", ttype),
                        _kv("model", model),
                        _kv("query_source", "main"),
                    ],
                })
            # one cost data point per model (synthetic USD ~ tokens x rate)
            dp_time += 1
            cost = round(sum(types.values()) / 1_000_000 * 3.0, 6)
            cost_points.append({
                "asDouble": cost,
                "startTimeUnixNano": str(BASE_NS),
                "timeUnixNano": str(dp_time),
                "attributes": [_kv("model", model), _kv("query_source", "main")],
            })
        resource_metrics.append({
            "resource": {"attributes": res_attrs},
            "scopeMetrics": [{
                "scope": {"name": "com.anthropic.claude_code"},
                "metrics": [
                    {
                        "name": "claude_code.token.usage",
                        "unit": "tokens",
                        "sum": {"aggregationTemporality": 1, "isMonotonic": True,
                                "dataPoints": points},
                    },
                    {
                        "name": "claude_code.cost.usage",
                        "unit": "USD",
                        "sum": {"aggregationTemporality": 1, "isMonotonic": True,
                                "dataPoints": cost_points},
                    },
                ],
            }],
        })
    return {"resourceMetrics": resource_metrics}


def post(payload: dict, endpoint: str) -> None:
    url = endpoint.rstrip("/") + "/v1/metrics"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f"POST {url} -> HTTP {resp.status}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true", help="print JSON instead of POSTing")
    ap.add_argument("--endpoint", default=os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318"))
    args = ap.parse_args()
    payload = build_payload()
    if args.emit:
        print(json.dumps(payload, indent=2))
    else:
        post(payload, args.endpoint)


if __name__ == "__main__":
    main()
