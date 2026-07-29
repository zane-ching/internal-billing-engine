"""Push files to Azure Data Lake Storage (ADLS Gen2) — or OneLake — over the
ADLS Gen2 DFS REST API. Stdlib only (urllib), mirroring analytics_client.py.

ADLS Gen2 and OneLake speak the SAME DFS API and the SAME Entra token audience
(`storage.azure.com`), so the destination is just config:

    SYNC_TARGET=adls      -> https://<account>.dfs.core.windows.net/<container>/<prefix>/...
    SYNC_TARGET=onelake   -> https://onelake.dfs.fabric.microsoft.com/<workspace>/<lakehouse>.Lakehouse/Files/<prefix>/...

Auth (SYNC_AUTH):
    sp   (default)  Entra service principal, client-credentials  (recommended)
    msi             Managed identity of the Azure host the job runs on
    sas             A pre-signed SAS token appended to the URL

Uploads are an idempotent overwrite: create (truncate) -> append -> flush.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from ..config import load_env

load_env()

STORAGE_SCOPE = "https://storage.azure.com/.default"
STORAGE_RESOURCE = "https://storage.azure.com/"
DEFAULT_API_VERSION = "2023-11-03"


class SyncConfigError(RuntimeError):
    """Missing/invalid sync configuration (bad or absent env)."""


class StorageError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def _req_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SyncConfigError(f"{name} is not set (see .env.example).")
    return val


def target_configured() -> bool:
    """True if enough env is present to attempt a sync (used to gate enqueue)."""
    return bool(os.environ.get("ADLS_ACCOUNT") or os.environ.get("ONELAKE_WORKSPACE"))


class StorageClient:
    """Uploads a local file to ADLS Gen2 / OneLake via the DFS REST API."""

    def __init__(self):
        self.target = os.environ.get("SYNC_TARGET", "adls").strip().lower()
        self.auth = os.environ.get("SYNC_AUTH", "sp").strip().lower()
        self.api_version = os.environ.get("SYNC_API_VERSION", DEFAULT_API_VERSION)
        self._token = None
        self._token_exp = 0.0

        if self.target == "adls":
            account = _req_env("ADLS_ACCOUNT")
            suffix = os.environ.get("ADLS_ENDPOINT_SUFFIX", "core.windows.net")
            # 'blob' (default) uses the Blob endpoint + PUT Blob — works even when
            # the account has soft-delete / change-feed / blob-events enabled (the
            # DFS endpoint rejects those with 409 EndpointUnsupportedAccountFeatures).
            # 'dfs' forces the ADLS Gen2 endpoint (create/append/flush).
            self.api = os.environ.get("ADLS_API", "blob").strip().lower()
            if self.api not in ("blob", "dfs"):
                raise SyncConfigError(f"ADLS_API must be 'blob' or 'dfs', got {self.api!r}")
            self.host = f"{account}.{'blob' if self.api == 'blob' else 'dfs'}.{suffix}"
            self.container = _req_env("ADLS_CONTAINER")
            self.prefix = os.environ.get("ADLS_PREFIX", "claude-billing").strip("/")
        elif self.target == "onelake":
            self.api = "dfs"  # OneLake only speaks the DFS API
            self.host = os.environ.get("ONELAKE_HOST", "onelake.dfs.fabric.microsoft.com")
            self.container = _req_env("ONELAKE_WORKSPACE")
            lakehouse = _req_env("ONELAKE_LAKEHOUSE")
            sub = os.environ.get("ONELAKE_PREFIX", "billing").strip("/")
            self.prefix = f"{lakehouse}.Lakehouse/Files/{sub}".strip("/")
        else:
            raise SyncConfigError(
                f"SYNC_TARGET must be 'adls' or 'onelake', got {self.target!r}")

        if self.auth == "sp":
            self.tenant = _req_env("AZURE_TENANT_ID")
            self.client_id = _req_env("AZURE_CLIENT_ID")
            self.client_secret = _req_env("AZURE_CLIENT_SECRET")
        elif self.auth == "sas":
            self.sas = _req_env("AZURE_SAS_TOKEN").lstrip("?")
        elif self.auth == "msi":
            self.msi_client_id = os.environ.get("AZURE_CLIENT_ID")  # optional
        else:
            raise SyncConfigError(
                f"SYNC_AUTH must be 'sp', 'msi', or 'sas', got {self.auth!r}")

    # ---- auth -----------------------------------------------------------
    def _bearer(self) -> str:
        now = time.time()
        if self._token and now < self._token_exp - 120:
            return self._token
        if self.auth == "sp":
            url = f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token"
            data = urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": STORAGE_SCOPE,
            }).encode()
            req = urllib.request.Request(url, data=data, headers={
                "Content-Type": "application/x-www-form-urlencoded"})
        else:  # msi — token from the Azure Instance Metadata Service
            q = {"api-version": "2018-02-01", "resource": STORAGE_RESOURCE}
            if self.msi_client_id:
                q["client_id"] = self.msi_client_id
            url = ("http://169.254.169.254/metadata/identity/oauth2/token?"
                   + urllib.parse.urlencode(q))
            req = urllib.request.Request(url, headers={"Metadata": "true"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise StorageError(e.code, e.read().decode(errors="replace")) from None
        self._token = payload["access_token"]
        self._token_exp = now + int(payload.get("expires_in", 3600))
        return self._token

    # ---- DFS requests ---------------------------------------------------
    def _url(self, remote_path: str, query: dict) -> str:
        base = (f"https://{self.host}/{urllib.parse.quote(self.container)}/"
                f"{urllib.parse.quote(remote_path)}")
        q = urllib.parse.urlencode(query)
        if self.auth == "sas":
            return f"{base}?{q}&{self.sas}" if q else f"{base}?{self.sas}"
        return f"{base}?{q}" if q else base

    def _request(self, method: str, remote_path: str, query: dict,
                 body: bytes = b"", extra_headers: dict | None = None) -> int:
        headers = {"x-ms-version": self.api_version}
        if self.auth != "sas":
            headers["Authorization"] = "Bearer " + self._bearer()
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(self._url(remote_path, query), data=body,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            raise StorageError(e.code, e.read().decode(errors="replace")) from None
        except urllib.error.URLError as e:
            raise StorageError(0, str(e)) from None

    def _mkdir(self, dir_path: str) -> None:
        if not dir_path:
            return
        try:
            self._request("PUT", dir_path, {"resource": "directory"})
        except StorageError as e:
            if e.status != 409:  # 409 = already exists, which is fine
                raise

    def full_uri(self, remote_rel: str) -> str:
        """The destination URL for a period-relative path (no upload)."""
        remote_path = f"{self.prefix}/{remote_rel}".strip("/")
        return f"https://{self.host}/{self.container}/{remote_path}"

    def upload_file(self, local_path: str, remote_rel: str) -> str:
        """Overwrite <prefix>/<remote_rel> in the lake with the local file.

        Idempotent overwrite either way — a re-run or a retry replaces the blob
        cleanly. Uses a single PUT Blob on the Blob endpoint (default) or the DFS
        create/append/flush sequence when ADLS_API=dfs / target=onelake."""
        remote_path = f"{self.prefix}/{remote_rel}".strip("/")
        with open(local_path, "rb") as fh:
            data = fh.read()
        if self.api == "blob":
            self._request("PUT", remote_path, {}, body=data, extra_headers={
                "x-ms-blob-type": "BlockBlob",
                "Content-Type": "text/csv; charset=utf-8"})
        else:
            parent = remote_path.rsplit("/", 1)[0] if "/" in remote_path else ""
            self._mkdir(parent)
            self._request("PUT", remote_path, {"resource": "file"})      # create/truncate
            if data:
                self._request("PATCH", remote_path, {"action": "append", "position": "0"},
                              body=data, extra_headers={"Content-Type": "application/octet-stream"})
            self._request("PATCH", remote_path, {"action": "flush", "position": str(len(data))})
        return f"https://{self.host}/{self.container}/{remote_path}"
