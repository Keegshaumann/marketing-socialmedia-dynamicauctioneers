"""Save a generated proposal in the property's SharePoint folder, and make its PDF there (M9, D103).

Why SharePoint makes the PDF: the proposals are set in Century Gothic, which the
server does not have. LibreOffice substitutes a different face and the contract
reflows (measured on DP2821: the same 26 pages, but clause breaks move). SharePoint
renders a ``.docx`` with the real Office fonts, the same as Save as PDF in Word, and
both files land where the properties team already keeps them:
``3. PROPERTIES 2026/6. AAA Properties/<DP> - <name>/Auction Prep/``.

App-only Microsoft Graph (client credentials), the pattern the P24 auto-responder
already uses in this tenant. It needs an app registration with the
``Sites.ReadWrite.All`` application permission and admin consent. Configuration is
by environment:

    MS_GRAPH_TENANT_ID, MS_GRAPH_CLIENT_ID, MS_GRAPH_CLIENT_SECRET      (required)
    PROPOSAL_SHAREPOINT_HOST    default dynamicauctioneers.sharepoint.com
    PROPOSAL_SHAREPOINT_SITE    default sites/Marketing
    PROPOSAL_SHAREPOINT_FOLDER  default 3. PROPERTIES 2026/6. AAA Properties

Without the three credentials the platform still builds the Word file and says the
PDF needs SharePoint.

It never overwrites a file it did not create. A proposal's first upload uses
conflictBehavior ``rename``, so a hand-made ``2821 - Auction proposal.docx`` stays
and ours arrives beside it as ``2821 - Auction proposal 1.docx``. The item id is
kept on the proposal, and a regenerate replaces that same item by id.
"""

from __future__ import annotations

import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

import httpx

from engine.proposal.model import Proposal, SharePointFile, base_dp, output_stem

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
AUCTION_PREP = "Auction Prep"
_CHUNK = 320 * 1024 * 16  # 5 MiB; Graph wants upload chunks in multiples of 320 KiB
_REDIRECTS = (301, 302, 303, 307, 308)


class SharePointError(RuntimeError):
    """A step failed; the message says which, in words the team can act on."""


@dataclass(frozen=True)
class SharePointConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    hostname: str = "dynamicauctioneers.sharepoint.com"
    site_path: str = "sites/Marketing"
    properties_folder: str = "3. PROPERTIES 2026/6. AAA Properties"


def config_from_env(env: Optional[Mapping[str, str]] = None) -> Optional[SharePointConfig]:
    env = os.environ if env is None else env
    tenant, client, secret = (
        (env.get(k) or "").strip()
        for k in ("MS_GRAPH_TENANT_ID", "MS_GRAPH_CLIENT_ID", "MS_GRAPH_CLIENT_SECRET")
    )
    if not (tenant and client and secret):
        return None
    overrides = {}
    for key, field in (
        ("PROPOSAL_SHAREPOINT_HOST", "hostname"),
        ("PROPOSAL_SHAREPOINT_SITE", "site_path"),
        ("PROPOSAL_SHAREPOINT_FOLDER", "properties_folder"),
    ):
        value = (env.get(key) or "").strip().strip("/")
        if value:
            overrides[field] = value
    return SharePointConfig(tenant, client, secret, **overrides)


def _ok(response: httpx.Response, doing: str) -> httpx.Response:
    if response.status_code >= 400:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            pass
        raise SharePointError(f"Could not {doing} (HTTP {response.status_code}){': ' + detail if detail else ''}.")
    return response


class Graph:
    def __init__(
        self,
        cfg: SharePointConfig,
        http: Optional[httpx.Client] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self.http = http or httpx.Client(timeout=120.0)
        self.sleep = sleep
        self._token = ""
        self._expires = 0.0
        self._drive = ""

    # --- plumbing ---------------------------------------------------------------

    def _bearer(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        response = self.http.post(
            f"{LOGIN}/{self.cfg.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        _ok(response, "sign in to Microsoft 365")
        body = response.json()
        self._token = body["access_token"]
        self._expires = time.time() + int(body.get("expires_in", 3600))
        return self._token

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = path if path.startswith("https://") else GRAPH + path
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._bearer()}"
        return self.http.request(method, url, headers=headers, **kwargs)

    def drive_id(self) -> str:
        if not self._drive:
            site = _ok(
                self._call("GET", f"/sites/{self.cfg.hostname}:/{self.cfg.site_path}"),
                f"find the SharePoint site {self.cfg.site_path}",
            ).json()["id"]
            self._drive = _ok(self._call("GET", f"/sites/{site}/drive"), "open the document library").json()["id"]
        return self._drive

    # --- folders -------------------------------------------------------------------

    def property_folder(self, dp: str) -> Dict[str, Any]:
        """The one folder under the properties folder named for this DP."""
        folder = urllib.parse.quote(self.cfg.properties_folder)
        url: Optional[str] = (
            f"/drives/{self.drive_id()}/root:/{folder}:/children?$select=id,name,folder,webUrl&$top=999"
        )
        base = re.compile(rf"^{re.escape(base_dp(dp))}(?!\d)")
        exact = re.compile(rf"^{re.escape(dp)}(?!\d)")
        found = []
        while url:
            body = _ok(self._call("GET", url), f"list {self.cfg.properties_folder}").json()
            found += [i for i in body.get("value", []) if "folder" in i and base.match(i["name"].strip())]
            url = body.get("@odata.nextLink")
        if "." in dp:
            narrowed = [i for i in found if exact.match(i["name"].strip())]
            found = narrowed or found
        if not found:
            raise SharePointError(
                f"There is no folder for {base_dp(dp)} in {self.cfg.properties_folder}. Create the property folder, then generate again."
            )
        if len(found) > 1:
            names = ", ".join(sorted(i["name"] for i in found))
            raise SharePointError(f"More than one folder matches {dp}: {names}. Rename or merge them, then generate again.")
        return found[0]

    def child_folder(self, parent_id: str, name: str) -> Dict[str, Any]:
        drive = self.drive_id()
        body = _ok(
            self._call("GET", f"/drives/{drive}/items/{parent_id}/children?$select=id,name,folder,webUrl&$top=999"),
            f"open the property folder",
        ).json()
        for item in body.get("value", []):
            if "folder" in item and item["name"].strip().lower() == name.lower():
                return item
        return _ok(
            self._call(
                "POST",
                f"/drives/{drive}/items/{parent_id}/children",
                json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
            ),
            f"create the {name} folder",
        ).json()

    # --- files ------------------------------------------------------------------------

    def upload(self, parent_id: str, name: str, data: bytes, item_id: str = "") -> Dict[str, Any]:
        """Replace ``item_id`` if it still exists, else add ``name`` without overwriting anything."""
        if not data:
            raise SharePointError(f"{name} is empty; nothing to upload.")
        drive = self.drive_id()
        session_url = ""
        if item_id:
            response = self._call("POST", f"/drives/{drive}/items/{item_id}/createUploadSession", json={})
            if response.status_code != 404:  # 404: someone deleted our file; add it afresh
                session_url = _ok(response, f"replace {name}").json()["uploadUrl"]
        if not session_url:
            response = self._call(
                "POST",
                f"/drives/{drive}/items/{parent_id}:/{urllib.parse.quote(name)}:/createUploadSession",
                json={"item": {"@microsoft.graph.conflictBehavior": "rename"}},
            )
            session_url = _ok(response, f"start uploading {name}").json()["uploadUrl"]

        total = len(data)
        start = 0
        last: Optional[httpx.Response] = None
        while start < total:
            chunk = data[start:start + _CHUNK]
            end = start + len(chunk) - 1
            # The upload URL is pre-authorised; Graph rejects a bearer token on it.
            last = self.http.put(
                session_url,
                content=chunk,
                headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{end}/{total}"},
            )
            if last.status_code not in (200, 201, 202):
                raise SharePointError(f"The upload of {name} stopped at byte {start} (HTTP {last.status_code}).")
            start = end + 1
        return last.json()

    def pdf(self, item_id: str, attempts: int = 4) -> bytes:
        """SharePoint's own PDF rendering of a stored Word file."""
        path = f"/drives/{self.drive_id()}/items/{item_id}/content?format=pdf"
        response: Optional[httpx.Response] = None
        for attempt in range(attempts):
            response = self._call("GET", path, follow_redirects=False)
            if response.status_code in _REDIRECTS:
                response = self.http.get(response.headers["Location"])  # pre-authorised download
            # A file uploaded a moment ago can still be processing.
            if response.status_code in (202, 406, 423, 429, 500, 502, 503, 504) and attempt < attempts - 1:
                self.sleep(3 * (attempt + 1))
                continue
            break
        _ok(response, "turn the proposal into a PDF")
        return response.content


def publish(
    proposal: Proposal,
    files_root: Path,
    cfg: SharePointConfig,
    http: Optional[httpx.Client] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Proposal:
    """Upload the built ``.docx``, fetch SharePoint's PDF, store both. Returns the updated proposal."""
    if not proposal.docx_file or not (files_root / proposal.docx_file).is_file():
        raise SharePointError("Generate the Word file first.")
    graph = Graph(cfg, http, sleep)
    folder = graph.property_folder(proposal.dp)
    prep = graph.child_folder(folder["id"], AUCTION_PREP)
    stem = output_stem(proposal)

    docx_item = graph.upload(
        prep["id"], f"{stem}.docx", (files_root / proposal.docx_file).read_bytes(), proposal.sharepoint_docx.item_id
    )
    pdf_bytes = graph.pdf(docx_item["id"])
    pdf_rel = f"out/{stem}.pdf"
    (files_root / pdf_rel).parent.mkdir(parents=True, exist_ok=True)
    (files_root / pdf_rel).write_bytes(pdf_bytes)
    # The PDF takes the Word file's final name, so a renamed "... 1.docx" gets "... 1.pdf".
    pdf_item = graph.upload(
        prep["id"], Path(docx_item["name"]).stem + ".pdf", pdf_bytes, proposal.sharepoint_pdf.item_id
    )

    updated = proposal.model_copy(deep=True)
    updated.sharepoint_folder = f"{folder['name']}/{AUCTION_PREP}"
    updated.sharepoint_docx = SharePointFile(
        item_id=docx_item["id"], name=docx_item.get("name", ""), web_url=docx_item.get("webUrl", "")
    )
    updated.sharepoint_pdf = SharePointFile(
        item_id=pdf_item["id"], name=pdf_item.get("name", ""), web_url=pdf_item.get("webUrl", "")
    )
    updated.pdf_file = pdf_rel
    updated.pdf_note = ""
    return updated
