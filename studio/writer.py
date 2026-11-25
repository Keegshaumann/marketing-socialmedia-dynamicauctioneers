"""Saves the studio's files into the property folders on SharePoint (D129).

Its own app registration, "Dynamic Ad Studio", with ``Sites.Selected`` and write
on the Marketing site only. The reading app (``STUDIO_GRAPH_*``, the director's)
stays read-only, and the proposal upload's ``MS_GRAPH_*`` stay unset.

    STUDIO_WRITE_TENANT_ID, STUDIO_WRITE_CLIENT_ID      (required)
    STUDIO_WRITE_CERT     a PEM file with the private key and certificate,
                          made on the server (scripts/make_studio_cert.sh), so
                          no secret ever leaves it; or
    STUDIO_WRITE_CLIENT_SECRET

Without them the studio still prepares every set; it just can't file them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import uuid
from typing import Optional
from urllib.parse import quote

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
_TYPES = {".png": "image/png", ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8", ".mp4": "video/mp4"}


class WriteError(RuntimeError):
    """``kind``: conflict (a file of that name is there), gone (deleted), denied, other."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _assertion(tenant: str, client_id: str, pem: bytes) -> str:
    """A signed client assertion (RFC 7523) from the certificate's private key."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(pem, password=None)
    cert = x509.load_pem_x509_certificate(pem)
    thumb = cert.fingerprint(hashes.SHA1())  # noqa: S303 - x5t is defined as SHA-1
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "x5t": _b64url(thumb)}
    claims = {"aud": f"{LOGIN}/{tenant}/oauth2/v2.0/token", "iss": client_id, "sub": client_id,
              "jti": str(uuid.uuid4()), "nbf": now - 60, "iat": now, "exp": now + 600}
    signing = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    sig = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing}.{_b64url(sig)}"


class Writer:
    def __init__(self, tenant: str, client_id: str, *, secret: str = "", cert_path: str = "",
                 http: Optional[httpx.Client] = None):
        self.tenant, self.client_id, self.secret, self.cert_path = tenant, client_id, secret, cert_path
        self.http = http or httpx.Client(timeout=httpx.Timeout(180, connect=15), follow_redirects=True)
        self._token, self._until = "", 0.0
        self._lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------------

    def _auth(self) -> str:
        with self._lock:
            if self._token and time.time() < self._until:
                return self._token
            data = {"grant_type": "client_credentials", "client_id": self.client_id,
                    "scope": "https://graph.microsoft.com/.default"}
            if self.cert_path:
                with open(self.cert_path, "rb") as fh:
                    data["client_assertion"] = _assertion(self.tenant, self.client_id, fh.read())
                data["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            else:
                data["client_secret"] = self.secret
            r = self.http.post(f"{LOGIN}/{self.tenant}/oauth2/v2.0/token", data=data)
            if r.status_code != 200:
                raise WriteError("denied", f"Microsoft sign-in for saving failed ({r.status_code})")
            body = r.json()
            self._token = body["access_token"]
            self._until = time.time() + int(body.get("expires_in", 3600)) - 120
            return self._token

    def _call(self, method: str, url: str, *, headers: Optional[dict] = None, **kw) -> httpx.Response:
        r = None
        for attempt in range(5):
            h = {**(headers or {}), "Authorization": f"Bearer {self._auth()}"}
            try:
                r = self.http.request(method, GRAPH + url, headers=h, **kw)
            except httpx.TransportError:
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(30, int(r.headers.get("Retry-After", 2 ** attempt) or 1)))
                continue
            if r.status_code == 401 and attempt == 0:
                self._token = ""
                continue
            return r
        if r is None:
            raise WriteError("other", "Could not reach Microsoft")
        return r

    @staticmethod
    def _fail(r: httpx.Response, doing: str) -> WriteError:
        kind = {409: "conflict", 404: "gone", 403: "denied", 401: "denied", 412: "conflict"}.get(r.status_code, "other")
        detail = ""
        try:
            detail = r.json().get("error", {}).get("message", "")
        except ValueError:
            pass
        return WriteError(kind, f"Could not {doing} (SharePoint {r.status_code}){': ' + detail if detail else ''}")

    # -- what the studio does -------------------------------------------------------

    def item(self, drive_id: str, item_id: str) -> Optional[dict]:
        """The file as SharePoint has it now, or None when it was deleted."""
        r = self._call("GET", f"/drives/{drive_id}/items/{item_id}?$select=id,name,cTag,eTag,size,webUrl,parentReference,lastModifiedBy")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise self._fail(r, "check a file")
        return r.json()

    def add(self, drive_id: str, path: str, data: bytes) -> dict:
        """Add a new file at ``path`` (library-relative). Never replaces anything:
        a file of that name already there is a WriteError("conflict")."""
        ext = os.path.splitext(path)[1].lower()
        r = self._call("PUT", f"/drives/{drive_id}/root:/{quote(path)}:/content?@microsoft.graph.conflictBehavior=fail",
                       content=data, headers={"Content-Type": _TYPES.get(ext, "application/octet-stream")})
        if r.status_code not in (200, 201):
            raise self._fail(r, f"add {path.rsplit('/', 1)[-1]}")
        return r.json()

    def replace(self, drive_id: str, item_id: str, name: str, data: bytes) -> dict:
        ext = os.path.splitext(name)[1].lower()
        r = self._call("PUT", f"/drives/{drive_id}/items/{item_id}/content", content=data,
                       headers={"Content-Type": _TYPES.get(ext, "application/octet-stream")})
        if r.status_code not in (200, 201):
            raise self._fail(r, f"update {name}")
        return r.json()

    def by_path(self, drive_id: str, path: str) -> Optional[dict]:
        r = self._call("GET", f"/drives/{drive_id}/root:/{quote(path)}?$select=id,name,cTag,eTag,size,webUrl,lastModifiedBy")
        return r.json() if r.status_code == 200 else None

    def made_by_us(self, item: dict) -> bool:
        return ((item.get("lastModifiedBy") or {}).get("application") or {}).get("id") == self.client_id

    def folder_url(self, drive_id: str, path: str) -> str:
        r = self._call("GET", f"/drives/{drive_id}/root:/{quote(path)}?$select=webUrl")
        return r.json().get("webUrl", "") if r.status_code == 200 else ""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_writer: Optional[Writer] = None


def writer() -> Optional[Writer]:
    """The configured writer, or None while the studio has no write access."""
    global _writer
    if _writer is None:
        tenant, cid = os.environ.get("STUDIO_WRITE_TENANT_ID", ""), os.environ.get("STUDIO_WRITE_CLIENT_ID", "")
        cert, secret = os.environ.get("STUDIO_WRITE_CERT", ""), os.environ.get("STUDIO_WRITE_CLIENT_SECRET", "")
        if tenant and cid and ((cert and os.path.exists(cert)) or secret):
            _writer = Writer(tenant, cid, secret=secret, cert_path=cert if cert and os.path.exists(cert) else "")
    return _writer
