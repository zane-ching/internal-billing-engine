"""Client for the Anthropic Enterprise/Admin Analytics API.

Endpoints (base = /v1/organizations/analytics):
  - usage_report        aggregate token usage
  - cost_report         USD cost (amount = post-discount, list_amount = list price)
  - user_usage_report   per-user token usage (carries actor.email)

Auth: this org accepts a standard key via `x-api-key`. (The docs also document
an OAuth Bearer token with the read:analytics scope; either works.)

Dependency-free — uses only the standard library so it runs anywhere.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Iterator

from .config import load_env

load_env()

DEFAULT_BASE = "https://api.anthropic.com/v1/organizations/analytics"
DEFAULT_VERSION = "2023-06-01"
MAX_WINDOW_DAYS = 31  # API caps a single query at 31 days


class AnalyticsError(RuntimeError):
    pass


def _iso(value) -> str:
    """Accept 'YYYY-MM-DD', datetime, or an RFC3339 string; return RFC3339 Z."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and "T" in value:
        return value
    else:
        dt = datetime.strptime(str(value), "%Y-%m-%d")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_dt(value) -> datetime:
    return datetime.strptime(_iso(value)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


class AnalyticsClient:
    def __init__(self, token: str | None = None, base: str | None = None,
                 version: str | None = None):
        self.token = (token or os.environ.get("ANTHROPIC_ANALYTICS_TOKEN")
                      or os.environ.get("ANALYTICS_TOKEN"))
        if not self.token:
            raise AnalyticsError(
                "No token. Set ANTHROPIC_ANALYTICS_TOKEN (see .env.example).")
        self.base = base or os.environ.get("ANTHROPIC_ANALYTICS_BASE", DEFAULT_BASE)
        self.version = version or os.environ.get("ANTHROPIC_VERSION", DEFAULT_VERSION)
        self.org_id: str | None = None
        self.data_refreshed_at: str | None = None

    # ---- HTTP -----------------------------------------------------------
    def _request(self, path: str, params: list[tuple[str, str]]) -> dict:
        url = f"{self.base}/{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "anthropic-version": self.version,
            "x-api-key": self.token,
        })
        last = None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    payload = json.loads(resp.read().decode())
                    self.org_id = payload.get("organization_id", self.org_id)
                    self.data_refreshed_at = payload.get(
                        "data_refreshed_at", self.data_refreshed_at)
                    return payload
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")
                if e.code == 429 or e.code >= 500:
                    last = f"HTTP {e.code}: {body}"
                    time.sleep(2 ** attempt)
                    continue
                raise AnalyticsError(f"HTTP {e.code}: {body}") from None
            except urllib.error.URLError as e:  # transient network
                last = str(e)
                time.sleep(2 ** attempt)
        raise AnalyticsError(f"Request failed after retries: {last}")

    def _windows(self, start, end) -> Iterator[tuple[datetime, datetime]]:
        cur, stop = _to_dt(start), _to_dt(end)
        if cur >= stop:
            raise AnalyticsError(f"start ({start}) must be before end ({end})")
        while cur < stop:
            nxt = min(cur + timedelta(days=MAX_WINDOW_DAYS), stop)
            yield cur, nxt
            cur = nxt

    def _params(self, start, end, bucket_width, group_by, products, limit, page):
        params = [("starting_at", _iso(start)), ("ending_at", _iso(end)),
                  ("bucket_width", bucket_width)]
        for g in (group_by or []):
            params.append(("group_by[]", g))
        for p in (products or []):
            params.append(("products[]", p))
        if limit is not None:
            params.append(("limit", str(limit)))
        if page:
            params.append(("page", page))
        return params

    # ---- Reports --------------------------------------------------------
    def _bucketed(self, path, start, end, bucket_width, group_by, products, limit):
        """usage_report / cost_report: data[] -> {starting_at, results[]}."""
        for wstart, wend in self._windows(start, end):
            page = None
            while True:
                params = self._params(wstart, wend, bucket_width, group_by,
                                       products, limit, page)
                data = self._request(path, params)
                for bucket in data.get("data", []):
                    for row in bucket.get("results", []):
                        yield bucket.get("starting_at"), row
                if data.get("has_more") and data.get("next_page"):
                    page = data["next_page"]
                else:
                    break

    def usage_report(self, start, end, bucket_width="1d", group_by=None,
                     products=None, limit=None):
        return self._bucketed("usage_report", start, end, bucket_width,
                              group_by, products, limit)

    def cost_report(self, start, end, bucket_width="1d", group_by=None,
                    products=None, limit=None):
        return self._bucketed("cost_report", start, end, bucket_width,
                             group_by, products, limit)

    def user_usage_report(self, start, end, bucket_width="1d", group_by=None,
                          products=None, limit=None):
        """Flat data[] of per-user records, each carrying an `actor` object."""
        for wstart, wend in self._windows(start, end):
            page = None
            while True:
                params = self._params(wstart, wend, bucket_width, group_by,
                                       products, limit, page)
                data = self._request("user_usage_report", params)
                for row in data.get("data", []):
                    yield row.get("starting_at"), row
                if data.get("has_more") and data.get("next_page"):
                    page = data["next_page"]
                else:
                    break
