"""Minimal OTLP/JSON receiver for Claude Code telemetry.

Accepts OTLP/HTTP JSON on /v1/metrics (and acks /v1/logs, /v1/traces), extracts
`claude_code.token.usage` data points, resolves the repo (from the injected
`repo` resource attribute) + user + model + token type, and writes deduped rows
to the OTEL store.

Also accepts POST /v1/session-repo — NOT an OTLP endpoint. That's the
session->repo timeline fed by the CwdChanged hook (deploy/claude-repo-tag.py),
which is how mid-session repo switches get attributed; the frozen `repo=`
resource attribute can't see them. See billing.otel.attribute.

Point Claude Code at it with:
    OTEL_EXPORTER_OTLP_PROTOCOL=http/json
    OTEL_EXPORTER_OTLP_ENDPOINT=http://<host>:4318

Authentication (shared fleet token):
    Set RECEIVER_AUTH_TOKEN and every write (POST) must present it, either as
        X-Billing-Token: <token>          (preferred — no spaces to encode)
        Authorization: Bearer <token>
    Clients: Claude Code sends it via OTEL_EXPORTER_OTLP_HEADERS, the CwdChanged
    hook via CLAUDE_BILLING_TOKEN (see deploy/). The token authenticates a fleet
    machine, not an individual user — it stops unauthorized injection from
    anything that can merely reach the port, not forgery by a holder of the
    token. If RECEIVER_AUTH_TOKEN is unset the receiver stays open (with a loud
    warning) so the token can be rolled out to machines before enforcement is
    turned on; pass --require-auth to refuse to start without one.

Dependency-free (stdlib http.server). For production durability you'd normally
front this with an OpenTelemetry Collector; this is the lean direct path.
"""

from __future__ import annotations

import argparse
import gzip
import hmac
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
AUTH_TOKEN = os.environ.get("RECEIVER_AUTH_TOKEN", "").strip()


def _presented_token(headers) -> str:
    """The credential a request presents, from X-Billing-Token or a Bearer
    Authorization header ('' if neither is present)."""
    tok = headers.get("X-Billing-Token")
    if tok:
        return tok.strip()
    auth = headers.get("Authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return ""


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


def ingest_session_repo_payload(payload: dict, store: OtelStore) -> dict:
    """Record one session->repo timeline entry, as POSTed by the CwdChanged hook
    (deploy/claude-repo-tag.py). See billing.otel.attribute for how it's joined
    back onto the usage datapoints at billing time."""
    session_id = (payload.get("session_id") or "").strip()
    ts = (payload.get("ts") or "").strip()
    if not session_id or not ts:
        raise ValueError("session_id and ts are required")
    repo_raw = payload.get("repo_raw") or ""
    inserted = store.insert_session_repo(
        session_id=session_id, ts=ts, seq=payload.get("seq") or 0,
        repo=normalize_remote(repo_raw), repo_raw=repo_raw,
        cwd=payload.get("cwd") or "", event=payload.get("event") or "")
    store.commit()
    return {"inserted": 1 if inserted else 0,
            "duplicate": 0 if inserted else 1,
            "repo": normalize_remote(repo_raw)}


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

    def _authorized(self) -> bool:
        """True if auth is disabled, or the request presents the right token.
        Compared in constant time so a wrong token leaks nothing via timing."""
        if not AUTH_TOKEN:
            return True
        presented = _presented_token(self.headers)
        return bool(presented) and hmac.compare_digest(presented, AUTH_TOKEN)

    def _unauthorized(self):
        body = b'{"error":"unauthorized"}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Reject before reading/parsing the body: an unauthenticated caller
        # never reaches the store, and we don't spend work on junk traffic.
        if not self._authorized():
            _log(f"401 POST {self.path} (missing/invalid token)")
            self._unauthorized()
            return
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
        elif path.endswith("/v1/session-repo"):
            try:
                result = ingest_session_repo_payload(json.loads(raw or b"{}"), self.store)
                msg = (f"/v1/session-repo repo={result['repo']} "
                       f"new={result['inserted']} dup={result['duplicate']}")
                print(f"[receiver] {msg}")
                _log(msg)
            except (ValueError, KeyError) as e:
                print(f"[receiver] bad session-repo payload: {e}")
                _log(f"BAD session-repo payload: {e}  first120={raw[:120]!r}")
                self.send_response(400)
                self.end_headers()
                return
        # /v1/logs, /v1/traces, anything else: just acknowledge
        self._ok()


def serve(host: str, port: int, db: str | None = None, require_auth: bool = False):
    if require_auth and not AUTH_TOKEN:
        raise SystemExit(
            "[receiver] --require-auth set but RECEIVER_AUTH_TOKEN is empty — refusing "
            "to start. Set the token, or drop --require-auth to run open.")
    Handler.store = OtelStore(db) if db else OtelStore()
    server = HTTPServer((host, port), Handler)
    auth_state = "ENABLED" if AUTH_TOKEN else "DISABLED"
    print(f"[receiver] listening on http://{host}:{port}  auth={auth_state}  "
          f"(POST /v1/metrics, /v1/session-repo)")
    if not AUTH_TOKEN:
        print("[receiver] WARNING: RECEIVER_AUTH_TOKEN is unset — any client that can "
              "reach this port can write billing rows. Set it to require a token.")
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
    ap.add_argument("--require-auth", action="store_true",
                    help="refuse to start unless RECEIVER_AUTH_TOKEN is set")
    args = ap.parse_args()
    serve(args.host, args.port, args.db, require_auth=args.require_auth)


if __name__ == "__main__":
    main()
