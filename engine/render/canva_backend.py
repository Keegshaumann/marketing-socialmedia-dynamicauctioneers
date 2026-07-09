"""Canva Connect render backend (M5, D14) — the removable scaffold.

Dynamic Auctioneers renders every marketing artifact through the default
``html`` backend today. This module is a config-gated alternative that drives
the Canva Connect autofill API instead, so a branded one-pager, auction board or
alert mailer is produced by a designer-owned Canva brand template rather than a
Jinja2 template. It only lights up if DA ever moves to a Canva plan whose API
tier exposes autofill (Enterprise); on the current Canva Teams plan it stays
dark and ``available()`` reports why.

# PLACEHOLDER(Canva Enterprise, D12): live calls untested, DA is on Canva Teams; scaffold only.

Design rules baked in here:
- Standard library only (``urllib``). Nothing else in the engine imports this
  module, and the registry holds a single ``"canva"`` line, so removing the
  scaffold is: delete this file, that one line, and ``tests/test_canva.py``.
- The backend receives **only** ``public_view`` (SPEC 4.4). Anything sent to
  Canva's cloud is client-facing by definition, so owner PII is never in scope;
  the same poison-marker PII test that guards the html backend guards this one.
- Refresh tokens rotate. Canva returns a new refresh token on every exchange and
  invalidates the old one, so the rotated token is persisted to a local state
  file (atomically, mode 600) and preferred over the seed ``CANVA_REFRESH_TOKEN``
  on the next call. Losing the rotated token means re-authorising by hand.
- ``available()`` never raises. A missing credential or an unreadable template
  map is returned as ``(False, reason)`` so ``engine backends`` and the html
  path keep working regardless of Canva configuration.

The autofill flow (all against ``https://api.canva.com/rest/v1``):
  1. exchange the refresh token for a short-lived access token (rotation saved);
  2. upload each photo as an asset and poll the upload job to an asset id;
  3. create an autofill job on the format's brand template with text + images;
  4. poll the autofill job to a finished design id;
  5. create an export job (PDF, or PNG for the icon tile) and poll it;
  6. download the export URL to ``<output_root>/DP<dp>/artifacts/<fmt>.<ext>``.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from engine.render.base import Artifact, RenderBackend, RenderRequest

_API_BASE = "https://api.canva.com/rest/v1"
_TOKEN_URL = f"{_API_BASE}/oauth/token"

# Seed refresh token comes from the environment; the rotated one is written here
# and preferred thereafter. Override the path with ``CANVA_STATE_FILE``.
_DEFAULT_STATE_FILE = "~/.dynamic-auctioneers/canva-state.json"

# Credentials that must all be present before the backend is usable.
_REQUIRED_ENV: Tuple[str, ...] = (
    "CANVA_CLIENT_ID",
    "CANVA_CLIENT_SECRET",
    "CANVA_REFRESH_TOKEN",
    "CANVA_TEMPLATE_MAP",
)

_HTTP_TIMEOUT_S = 30.0
_POLL_INTERVAL_S = 2.0
_POLL_TIMEOUT_S = 120.0

# Canva job statuses that mean "keep waiting" versus terminal outcomes.
_JOB_SUCCESS = {"success", "completed", "done"}
_JOB_FAILURE = {"failed", "error"}


def _now_iso() -> str:
    """UTC timestamp in ISO 8601, seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CanvaBackend(RenderBackend):
    """Canva Connect autofill backend (D14 scaffold, stdlib ``urllib`` only)."""

    name: str = "canva"

    # --- capability reporting -------------------------------------------

    def available(self) -> Tuple[bool, str]:
        """Return ``(ok, reason)``. Never raises.

        Unavailable when any credential env var is unset or when
        ``CANVA_TEMPLATE_MAP`` cannot be read as a non-empty JSON object.
        """
        missing = [name for name in _REQUIRED_ENV if not os.getenv(name)]
        if missing:
            return (
                False,
                "Canva backend unconfigured: set " + ", ".join(missing) + ".",
            )
        try:
            template_map = self._load_template_map()
        except Exception as exc:  # unreadable/invalid map is a config problem, not a crash
            return (False, f"Canva backend CANVA_TEMPLATE_MAP unreadable: {exc}")
        if not template_map:
            return (
                False,
                "Canva backend CANVA_TEMPLATE_MAP is empty; no brand templates mapped.",
            )
        return (True, "ok")

    def supports(self, fmt: str) -> bool:
        """Whether a brand template is mapped for ``fmt``. Never raises."""
        try:
            return fmt in self._load_template_map()
        except Exception:
            return False

    # --- rendering -------------------------------------------------------

    def render(self, request: RenderRequest) -> Artifact:
        """Run the full autofill flow for one format and return the artifact.

        Raises ``RuntimeError`` if the backend is not configured (callers reach
        this only when Canva was explicitly selected) or ``ValueError`` if no
        brand template is mapped for the requested format.
        """
        ok, reason = self.available()
        if not ok:
            raise RuntimeError(reason)

        template_map = self._load_template_map()
        brand_template_id = template_map.get(request.fmt)
        if brand_template_id is None:
            raise ValueError(
                f"Canva backend has no brand template mapped for format {request.fmt!r}."
            )

        access_token = self._access_token()
        asset_ids = [
            self._upload_asset(access_token, photo)
            for photo in request.photos
            if photo
        ]
        data = self._autofill_data(request, asset_ids)
        design_id = self._run_autofill(access_token, brand_template_id, data)
        export_url, ext, mime = self._export_design(access_token, design_id, request.fmt)
        out_path = self._download(export_url, request, ext)
        return Artifact(
            dp=request.dp,
            fmt=request.fmt,
            backend=self.name,
            path=str(out_path),
            mime=mime,
            version=1,
        )

    # --- config ----------------------------------------------------------

    def _load_template_map(self) -> Dict[str, str]:
        """Load the ``fmt -> brand_template_id`` map.

        ``CANVA_TEMPLATE_MAP`` may be a path to a JSON file or an inline JSON
        object. Returns ``{}`` when the var is unset.
        """
        raw = os.getenv("CANVA_TEMPLATE_MAP", "")
        if not raw:
            return {}
        candidate = Path(os.path.expanduser(raw))
        text = candidate.read_text(encoding="utf-8") if candidate.exists() else raw
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(
                "CANVA_TEMPLATE_MAP must be a JSON object of fmt -> brand_template_id."
            )
        return {str(key): str(value) for key, value in data.items()}

    # --- OAuth with refresh-token rotation -------------------------------

    def _state_file(self) -> Path:
        return Path(os.path.expanduser(os.getenv("CANVA_STATE_FILE", _DEFAULT_STATE_FILE)))

    def _read_state(self) -> Dict[str, Any]:
        path = self._state_file()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # a corrupt state file falls back to the seed token
            return {}
        return data if isinstance(data, dict) else {}

    def _current_refresh_token(self) -> str:
        """The rotated refresh token if one was persisted, else the seed env var."""
        return self._read_state().get("refresh_token") or os.environ["CANVA_REFRESH_TOKEN"]

    def _persist_refresh_token(self, refresh_token: str) -> None:
        """Atomically write the rotated refresh token to the state file (mode 600)."""
        path = self._state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        state = self._read_state()
        state["refresh_token"] = refresh_token
        state["rotated_at"] = _now_iso()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
        try:
            os.chmod(path, 0o600)
        except OSError:  # best effort; some filesystems ignore chmod
            pass

    def _access_token(self) -> str:
        """Exchange the refresh token for an access token; persist the rotation."""
        client_id = os.environ["CANVA_CLIENT_ID"]
        client_secret = os.environ["CANVA_CLIENT_SECRET"]
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": self._current_refresh_token(),
            }
        ).encode("utf-8")
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        payload = self._http_json("POST", _TOKEN_URL, headers=headers, body=body)

        rotated = payload.get("refresh_token")
        if rotated:
            self._persist_refresh_token(rotated)
        access = payload.get("access_token")
        if not access:
            raise RuntimeError("Canva token exchange returned no access_token.")
        return access

    # --- asset upload ----------------------------------------------------

    def _upload_asset(self, access_token: str, photo_path: str) -> str:
        """Upload one photo and poll the upload job to an asset id."""
        path = Path(photo_path)
        payload_bytes = path.read_bytes()
        name_b64 = base64.b64encode(path.name.encode("utf-8")).decode("ascii")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
            "Asset-Upload-Metadata": json.dumps({"name_base64": name_b64}),
        }
        created = self._http_json(
            "POST", f"{_API_BASE}/asset-uploads", headers=headers, body=payload_bytes
        )
        job_id = created["job"]["id"]
        job = self._poll_job(
            access_token, f"{_API_BASE}/asset-uploads/{job_id}", "asset upload"
        )
        return job["asset"]["id"]

    # --- autofill --------------------------------------------------------

    def _autofill_data(
        self, request: RenderRequest, asset_ids: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Build the Canva autofill ``data`` map from public fields + copy only.

        Field names below are the placeholder names a DA brand template would
        expose; they are scaffolded until real templates exist (D12). Only
        ``public_record`` (public_view) and ``copy`` are read, so owner PII is
        structurally out of reach.
        """
        record = request.public_record or {}
        copy = request.copy or {}
        identity = record.get("identity") or {}
        marketing = record.get("marketing") or {}

        fields: Dict[str, Dict[str, Any]] = {}

        def _text(field_name: str, value: Any) -> None:
            if value:
                fields[field_name] = {"type": "text", "text": str(value)}

        _text("headline", copy.get("headline") or marketing.get("headline"))
        _text("price", copy.get("price_display") or marketing.get("price_display"))
        _text("body", copy.get("body"))
        _text("address", identity.get("street_address"))
        _text("suburb", identity.get("suburb"))
        _text("dp", request.dp)

        for index, asset_id in enumerate(asset_ids, start=1):
            fields[f"photo{index}"] = {"type": "image", "asset_id": asset_id}

        return fields

    def _run_autofill(
        self, access_token: str, brand_template_id: str, data: Dict[str, Dict[str, Any]]
    ) -> str:
        """Create an autofill job on the brand template and poll to a design id."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        body = json.dumps(
            {"brand_template_id": brand_template_id, "data": data}
        ).encode("utf-8")
        created = self._http_json("POST", f"{_API_BASE}/autofills", headers=headers, body=body)
        job_id = created["job"]["id"]
        job = self._poll_job(access_token, f"{_API_BASE}/autofills/{job_id}", "autofill")
        return job["result"]["design"]["id"]

    # --- export + download ----------------------------------------------

    def _export_format(self, fmt: str) -> Tuple[str, str, str]:
        """Return ``(canva_export_type, file_ext, mime)`` for a format."""
        if fmt == "webapp_icon":
            return ("png", "png", "image/png")
        return ("pdf", "pdf", "application/pdf")

    def _export_design(
        self, access_token: str, design_id: str, fmt: str
    ) -> Tuple[str, str, str]:
        """Create an export job, poll it, and return ``(url, ext, mime)``."""
        export_type, ext, mime = self._export_format(fmt)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        body = json.dumps(
            {"design_id": design_id, "format": {"type": export_type}}
        ).encode("utf-8")
        created = self._http_json("POST", f"{_API_BASE}/exports", headers=headers, body=body)
        job_id = created["job"]["id"]
        job = self._poll_job(access_token, f"{_API_BASE}/exports/{job_id}", "export")
        urls = job.get("urls") or []
        if not urls:
            raise RuntimeError("Canva export produced no download URL.")
        return urls[0], ext, mime

    def _download(self, url: str, request: RenderRequest, ext: str) -> Path:
        """Download the export URL to the DP artifacts directory."""
        out_dir = Path(request.output_root) / f"DP{request.dp}" / "artifacts"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{request.fmt}.{ext}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
                out_path.write_bytes(resp.read())
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Canva export download failed: {exc.reason}") from exc
        return out_path

    # --- HTTP + polling helpers -----------------------------------------

    def _poll_job(self, access_token: str, url: str, label: str) -> Dict[str, Any]:
        """Poll a Canva job URL until success, failure or timeout.

        Returns the job object on success. Raises ``RuntimeError`` on a failed
        job and ``TimeoutError`` if it does not finish within the poll window.
        """
        headers = {"Authorization": f"Bearer {access_token}"}
        deadline = time.monotonic() + _POLL_TIMEOUT_S
        while True:
            payload = self._http_json("GET", url, headers=headers)
            job = payload.get("job", payload)
            status = job.get("status")
            if status in _JOB_SUCCESS:
                return job
            if status in _JOB_FAILURE:
                raise RuntimeError(
                    f"Canva {label} job failed: {job.get('error') or status}"
                )
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Canva {label} job did not complete within {_POLL_TIMEOUT_S:.0f}s."
                )
            time.sleep(_POLL_INTERVAL_S)

    def _http_json(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Issue an HTTP request and parse a JSON response body.

        Wraps ``urllib`` errors in ``RuntimeError`` with the server detail so a
        live failure surfaces a readable message rather than a raw traceback.
        """
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace") if exc.fp else ""
            raise RuntimeError(
                f"Canva API {method} {url} failed: HTTP {exc.code} {detail}".rstrip()
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Canva API {method} {url} unreachable: {exc.reason}") from exc
        if not raw:
            return {}
        return json.loads(raw)
