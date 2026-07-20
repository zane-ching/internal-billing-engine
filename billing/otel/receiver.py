"""Minimal OTLP/JSON receiver for Claude Code telemetry.

Accepts OTLP/HTTP JSON on /v1/metrics (and acks /v1/logs, /v1/traces), extracts
`claude_code.token.usage` data points, resolves the repo (from the injected
`repo` resource attribute) + user + model + token type, and writes deduped rows
to the OTEL store.

Point Claude Code at it with:
    OTEL_EXPORTER_OTLP_PROTOCOL=http/json
    OTEL_EXPORTER_OTLP_ENDPOINT=http://<host>:4318

Dependency-free (stdlib http.server). For production durability you'd normally
front this with an OpenTelemetry Collector; this is the lean direct path.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from ..config import load_env
from .normalize import normalize_remote
from .otel_store import OtelStore

load_env()

TOKEN_METRIC = "claude_code.token.usage"
COST_METRIC = "claude_code.cost.usage"
LOG_PATH = os.environ.get("RECEIVER_LOG", "data/receiver.log")


def _log(msg: str) -> None:
    """Append a line to a log file so inbound requests are visible after the fact."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(LOG_PATH)), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _read_body(handler) -> bytes:
    """Read the full request body, handling Content-Length, chunked transfer
    encoding, and gzip — OTLP/HTTP clients (incl. Claude Code) commonly use
    chunked + gzip, which have no Content-Length header."""
    te = (handler.headers.get("Transfer-Encoding") or "").lower()
    if "chunked" in te:
        parts = []
        while True:
            size_line = handler.rfile.readline().strip()
            if not size_line:
                break
            try:
                size = int(size_line.split(b";")[0], 16)
            except ValueError:
                break
            if size == 0:
                handler.rfile.readline()  # consume trailing CRLF
                break
            parts.append(handler.rfile.read(size))
            handler.rfile.readline()      # CRLF after each chunk
        body = b"".join(parts)
    else:
        length = int(handler.headers.get("Content-Length", 0) or 0)
        body = handler.rfile.read(length) if length else b""
    if "gzip" in (handler.headers.get("Content-Encoding") or "").lower():
        try:
            body = gzip.decompress(body)
        except OSError:
            pass
    return body


def _attr_value(v: dict):
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:
        return int(v["intValue"])
    if "doubleValue" in v:
        return v["doubleValue"]
    if "boolValue" in v:
        return v["boolValue"]
    return None


def _attrs(attr_list) -> dict:
    return {a["key"]: _attr_value(a.get("value", {})) for a in (attr_list or [])}


def _datapoints(metric: dict) -> list:
    return (metric.get("sum") or metric.get("gauge") or {}).get("dataPoints", [])


def _common(res: dict, dp: dict) -> dict:
    """Merge resource + datapoint attributes and pull the fields we store."""
    a = dict(res)
    a.update(_attrs(dp.get("attributes")))  # datapoint attrs win
    repo_raw = a.get("repo")
    return {
        "session_id": a.get("session.id") or "unknown",
        "repo": normalize_remote(repo_raw),
        "repo_raw": repo_raw or "",
        "user_email": a.get("user.email") or "",
        "user_id": a.get("user.id") or "",
        "org_id": a.get("organization.id") or "",
        "model": a.get("model") or "unknown",
        "query_source": a.get("query_source") or "main",
        "type": a.get("type") or "unknown",
        "time_unix_nano": dp.get("timeUnixNano") or dp.get("startTimeUnixNano") or 0,
    }


def ingest_metrics_payload(payload: dict, store: OtelStore) -> dict:
    """Parse an OTLP/JSON ExportMetricsServiceRequest, routing the token and
    cost metrics into their tables."""
    tok_ins = tok_dup = cost_ins = cost_dup = 0
    names = set()
    for rm in payload.get("resourceMetrics", []):
        res = _attrs(rm.get("resource", {}).get("attributes"))
        for sm in rm.get("scopeMetrics", []):
            for metric in sm.get("metrics", []):
                name = metric.get("name")
                names.add(name)
                if name == TOKEN_METRIC:
                    for dp in _datapoints(metric):
                        c = _common(res, dp)
                        val = dp.get("asInt", dp.get("asDouble", 0))
                        ok = store.insert_datapoint(
                            session_id=c["session_id"], repo=c["repo"],
                            repo_raw=c["repo_raw"], user_email=c["user_email"],
                            user_id=c["user_id"], org_id=c["org_id"],
                            model=c["model"], token_type=c["type"],
                            query_source=c["query_source"], tokens=int(val or 0),
                            time_unix_nano=c["time_unix_nano"])
                        tok_ins += 1 if ok else 0
                        tok_dup += 0 if ok else 1
                elif name == COST_METRIC:
                    for dp in _datapoints(metric):
                        c = _common(res, dp)
                        val = dp.get("asDouble", dp.get("asInt", 0))
                        ok = store.insert_cost_datapoint(
                            session_id=c["session_id"], repo=c["repo"],
                            repo_raw=c["repo_raw"], user_email=c["user_email"],
                            user_id=c["user_id"], org_id=c["org_id"],
                            model=c["model"], query_source=c["query_source"],
                            cost_usd=float(val or 0),
                            time_unix_nano=c["time_unix_nano"])
                        cost_ins += 1 if ok else 0
                        cost_dup += 0 if ok else 1
    store.commit()
    return {"inserted": tok_ins + cost_ins, "token_inserted": tok_ins,
            "cost_inserted": cost_ins, "duplicate": tok_dup + cost_dup,
            "metrics_seen": sorted(n for n in names if n)}


class Handler(BaseHTTPRequestHandler):
    store: OtelStore = None  # set by serve()

    def log_message(self, *args):  # quieter
        pass

    def _ok(self, body: bytes = b"{}"):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = _read_body(self)
        path = self.path.rstrip("/")
        ctype = self.headers.get("Content-Type", "?")
        te = self.headers.get("Transfer-Encoding", "-")
        ce = self.headers.get("Content-Encoding", "-")
        _log(f"POST {self.path} bytes={len(raw)} content-type={ctype} te={te} ce={ce}")
        if path.endswith("/v1/metrics"):
            try:
                result = ingest_metrics_payload(json.loads(raw or b"{}"), self.store)
                msg = (f"/v1/metrics tok+={result['token_inserted']} "
                       f"cost+={result['cost_inserted']} dup={result['duplicate']} "
                       f"metrics_seen={result['metrics_seen']}")
                print(f"[receiver] {msg}")
                _log(msg)
            except (ValueError, KeyError) as e:
                print(f"[receiver] bad metrics payload: {e}")
                _log(f"BAD metrics payload ({ctype} te={te} ce={ce}): {e}  first120={raw[:120]!r}")
                self.send_response(400)
                self.end_headers()
                return
        # /v1/logs, /v1/traces, anything else: just acknowledge
        self._ok()


def serve(host: str, port: int, db: str | None = None):
    Handler.store = OtelStore(db) if db else OtelStore()
    server = HTTPServer((host, port), Handler)
    print(f"[receiver] listening on http://{host}:{port}  (POST /v1/metrics)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        Handler.store.close()


def main():
    ap = argparse.ArgumentParser(description="OTLP/JSON receiver for Claude Code.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4318)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    serve(args.host, args.port, args.db)


if __name__ == "__main__":
    main()
