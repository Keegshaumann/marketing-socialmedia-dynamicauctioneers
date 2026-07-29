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
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from engine.render.base import FORMATS, Artifact, RenderBackend, RenderRequest

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

# The set name a legacy flat CANVA_TEMPLATE_MAP is filed under, and the label
# shown for it in the gate-2 template picker.
DEFAULT_TEMPLATE_SET = "Default"
_POLL_TIMEOUT_S = 120.0

# Canva job statuses that mean "keep waiting" versus terminal outcomes.
_JOB_SUCCESS = {"success", "completed", "done"}
_JOB_FAILURE = {"failed", "error"}


def _now_iso() -> str:
    """UTC timestamp in ISO 8601, seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- record -> ad-tile field composers -----------------------------------
# A DA property brand template exposes short "stat bar" fields (beds, baths,
# garages, size) and a features tagline in addition to headline/price. These
# helpers turn public-view physical data into those tile strings. Every value
# traces to a record field (SPEC hard rule 3): a missing or unconfirmed stat
# returns None (field left unset) rather than an invented figure.

def _stat_label(value: Any, unit: str) -> Optional[str]:
    """``3 -> "3 BED"``. Non-positive / non-int -> None (field stays unset)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return f"{value} {unit}"
    return None


def _size_label(value: Any) -> Optional[str]:
    """``185 -> "185m²"``. Falsy -> None."""
    if isinstance(value, (int, float)) and value > 0:
        return f"{int(value)}m²"
    return None


def _garage_label(physical: Dict[str, Any]) -> Optional[str]:
    """Garage tile text, honestly.

    A confirmed count renders as ``DOUBLE GARAGE`` / ``3 GARAGES``. When the
    count is unknown (``garages`` is null — e.g. the DP3060 Lightstone-vs-
    inspection conflict) the engine never invents one: it falls back to
    ``GUEST PARKING`` only if the record's complex features actually mention
    parking, otherwise leaves the field unset.
    """
    count = physical.get("garages")
    if isinstance(count, bool):
        count = None
    if isinstance(count, int) and count > 0:
        return {1: "1 GARAGE", 2: "DOUBLE GARAGE"}.get(count, f"{count} GARAGES")
    complex_feats = " ".join(physical.get("features_complex") or []).lower()
    if "parking" in complex_feats:
        return "GUEST PARKING"
    return None


def _feature_tagline(physical: Dict[str, Any]) -> Optional[str]:
    """A short ``A | B | C`` highlight strip from the record's features.

    Prefers named draws (separate flatlet, pool, estate security, braai);
    falls back to the first complex feature verbatim. Capped at three parts.
    """
    parts: List[str] = []
    flatlet = physical.get("flatlet") or {}
    if flatlet.get("present"):
        parts.append("SEPARATE FLATLET")
    complex_feats = physical.get("features_complex") or []
    joined = " ".join(complex_feats).lower()
    for needle, label in (
        ("pool", "POOL & GARDEN"),
        ("security", "ESTATE SECURITY"),
        ("braai", "BRAAI AREA"),
    ):
        if needle in joined and len(parts) < 3:
            parts.append(label)
    if not parts and complex_feats:
        parts.append(str(complex_feats[0]).upper())
    return " | ".join(parts[:3]) or None


_AD_HEADLINE_MAX = 40  # chars that fit the tile headline box before it wraps into the FOR SALE badge

# Fields the engine always sends when the template exposes them, even blank, so a
# stale template default (e.g. a demo "MASTER REF", or a "PROPERTY REF: DP..."
# that would leak the internal DP, D37) is cleared rather than shown.
_ALWAYS_EMIT = {"master_ref", "property_ref"}


def _ad_headline(record: Dict[str, Any], copy: Dict[str, Any]) -> Optional[str]:
    """A headline sized for the ad tile.

    Uses the record/copy headline when it fits; when it would overflow the box
    (wrapping into the badge, as on DP3060) it falls back to a concise form
    composed from structured fields — still every word tracing to the record.
    """
    headline = (copy or {}).get("headline") or (record.get("marketing") or {}).get("headline")
    if not headline:
        return None
    if len(headline) <= _AD_HEADLINE_MAX:
        return headline
    physical = record.get("physical") or {}
    identity = record.get("identity") or {}
    beds = physical.get("bedrooms")
    core = f"{beds}-Bed Home" if isinstance(beds, int) and beds > 0 else "Home"
    if (physical.get("flatlet") or {}).get("present"):
        core += " + Flatlet"
    suburb = identity.get("suburb")
    return f"{core} in {suburb}" if suburb else core


def _master_ref(identity: Dict[str, Any]) -> str:
    """Ad "MASTER REF" line from the DA mandate ref, or ``""`` to blank the field.

    Never inherits another property's number: with no mandate on record the
    engine returns an empty string so the template placeholder is cleared.
    """
    ref = identity.get("mandate_ref")
    return f"MASTER REF: {ref}" if ref else ""


def _slot_index(name: str) -> int:
    """Trailing integer of a ``photoN`` field name, for natural slot ordering."""
    match = re.search(r"(\d+)$", name)
    return int(match.group(1)) if match else 0


class CanvaBackend(RenderBackend):
    """Canva Connect autofill backend (D14 scaffold, stdlib ``urllib`` only)."""

    name: str = "canva"
    renders_locally: bool = False  # artifact is rendered by Canva's cloud, then downloaded

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
            template_sets = self._load_template_sets()
        except Exception as exc:  # unreadable/invalid map is a config problem, not a crash
            return (False, f"Canva backend CANVA_TEMPLATE_MAP unreadable: {exc}")
        if not template_sets:
            return (
                False,
                "Canva backend CANVA_TEMPLATE_MAP is empty; no brand templates mapped.",
            )
        # The default (first) set defines the renderable formats (D33); an
        # empty default set would make supports() False for everything while
        # this backend claims to be available -- refuse loudly instead.
        if not next(iter(template_sets.values())):
            return (
                False,
                "Canva backend CANVA_TEMPLATE_MAP default (first) template set "
                "maps no formats.",
            )
        return (True, "ok")

    def supports(self, fmt: str) -> bool:
        """Whether the DEFAULT template set maps ``fmt``. Never raises.

        The default (first-configured) set defines which formats render through
        Canva; later sets only restyle those formats (overlay semantics, D33).
        Answering for the default set keeps the invariant the service relies
        on: ``supports(fmt)`` implies ``render`` can resolve a template for any
        record, whatever its ``template_set`` pick -- a union answer would make
        a format mapped only in a non-default set crash a canva-only render
        pass for records on the default set.
        """
        try:
            sets = self._load_template_sets()
            if not sets:
                return False
            return fmt in next(iter(sets.values()))
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

        brand_template_id = self._resolve_template(request.fmt, request.template_set)
        if brand_template_id is None:
            raise ValueError(
                f"Canva backend has no brand template mapped for format {request.fmt!r}."
            )

        access_token = self._access_token()
        dataset = self._get_dataset(access_token, brand_template_id)
        image_slots = sorted(
            (name for name, typ in dataset.items() if typ == "image"),
            key=_slot_index,
        )
        # Upload only as many photos as the template has image slots — no
        # wasted uploads, and Canva rejects autofills that reference a slot
        # the template does not expose.
        photos = [photo for photo in request.photos if photo][: len(image_slots)]
        asset_ids = [self._upload_asset(access_token, photo) for photo in photos]
        data = self._autofill_data(request, asset_ids, dataset, image_slots)
        design = self._run_autofill(access_token, brand_template_id, data)
        design_id = design.get("id")
        edit_url = self._design_url(design)
        export_url, ext, mime = self._export_design(access_token, design_id, request.fmt)
        out_path = self._download(export_url, request, ext)
        return Artifact(
            dp=request.dp,
            fmt=request.fmt,
            backend=self.name,
            path=str(out_path),
            mime=mime,
            version=1,
            design_id=design_id,
            edit_url=edit_url,
        )

    # --- config ----------------------------------------------------------

    def _load_template_sets(self) -> "Dict[str, Dict[str, str]]":
        """Load the named template sets: ``set name -> {fmt: brand_template_id}``.

        ``CANVA_TEMPLATE_MAP`` may be a path to a JSON file or an inline JSON
        object, in either of two shapes:

        - legacy flat ``{"demo_ad": "<id>", ...}`` -- one set named "Default";
        - named sets ``{"Classic gold": {"demo_ad": "<id>"}, ...}`` -- kept
          as-is. JSON object order is preserved, and the FIRST set is the
          default: it is used when a record names no set (or a stale one), and
          later sets act as overlays on it (a set that maps only ``demo_ad``
          restyles the demo ad and inherits everything else from the default).

        Mixing the two shapes is a config error. Returns ``{}`` when unset.
        """
        raw = os.getenv("CANVA_TEMPLATE_MAP", "")
        if not raw:
            return {}
        candidate = Path(os.path.expanduser(raw))
        text = candidate.read_text(encoding="utf-8") if candidate.exists() else raw
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(
                "CANVA_TEMPLATE_MAP must be a JSON object: either "
                "fmt -> brand_template_id, or set name -> {fmt: brand_template_id}."
            )
        if not data:
            return {}
        values = list(data.values())
        if all(isinstance(v, dict) for v in values):
            # A malformed flat map ({"demo_ad": {...}}) would pass the
            # all-dicts sniff and silently become a named set called
            # "demo_ad", turning a loud config error into Canva quietly
            # rendering nothing -- refuse set names that are format names.
            colliding = [name for name in data if name in FORMATS]
            if colliding:
                raise ValueError(
                    "CANVA_TEMPLATE_MAP set name(s) "
                    + ", ".join(repr(n) for n in colliding)
                    + " collide with format names; in a flat map each format "
                    "maps to a brand template id string, not an object."
                )
            return {
                str(name): {str(fmt): str(tid) for fmt, tid in mapping.items()}
                for name, mapping in data.items()
            }
        if any(isinstance(v, dict) for v in values):
            raise ValueError(
                "CANVA_TEMPLATE_MAP mixes flat fmt -> id entries with named "
                "template sets; use one shape or the other."
            )
        return {DEFAULT_TEMPLATE_SET: {str(k): str(v) for k, v in data.items()}}

    def _resolve_template(self, fmt: str, template_set: Optional[str]) -> Optional[str]:
        """The brand template id for ``fmt`` under ``template_set``.

        Resolution order: the named set (when it exists and maps ``fmt``), then
        the default (first-configured) set. A stale or unknown set name on a
        record therefore degrades to the default design rather than failing the
        render. Returns ``None`` when no set maps the format at all.
        """
        sets = self._load_template_sets()
        if not sets:
            return None
        chosen = sets.get(template_set) if template_set else None
        if chosen is not None and fmt in chosen:
            return chosen[fmt]
        default = next(iter(sets.values()))
        return default.get(fmt)

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

    def _get_dataset(self, access_token: str, brand_template_id: str) -> Dict[str, str]:
        """Return ``{field_name: type}`` for a brand template's autofill dataset.

        Lets the backend emit only the fields a template actually exposes, so one
        backend serves every DA template (ad tile, auction board, mailer) and
        Canva never 400s on an unknown field.
        """
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        payload = self._http_json(
            "GET",
            f"{_API_BASE}/brand-templates/{brand_template_id}/dataset",
            headers=headers,
        )
        dataset = payload.get("dataset") or {}
        return {name: (spec or {}).get("type") for name, spec in dataset.items()}

    def _autofill_data(
        self,
        request: RenderRequest,
        asset_ids: List[str],
        dataset: Dict[str, str],
        image_slots: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Build the Canva autofill ``data`` map from public fields + copy only.

        A candidate value is computed for every field a DA template might expose,
        then only the fields the template's ``dataset`` actually declares (and of
        the matching type) are sent. Only ``public_record`` (public_view) and
        ``copy`` are read, so owner PII is structurally out of reach; every value
        traces to a record field, and a missing/unconfirmed stat is left unset
        rather than invented (SPEC hard rule 3).
        """
        record = request.public_record or {}
        copy = request.copy or {}
        identity = record.get("identity") or {}
        marketing = record.get("marketing") or {}
        physical = record.get("physical") or {}

        candidates: Dict[str, Any] = {
            "headline": _ad_headline(record, copy),
            "price": copy.get("price_display") or marketing.get("price_display"),
            "body": copy.get("body"),
            "address": identity.get("street_address"),
            "suburb": identity.get("suburb"),
            "dp": request.dp,
            # PROPERTY REF: DP<n> on the ad, matching the team's real ads (D42
            # reverses the earlier D37 blanking). The public reference pair is
            # MASTER REF (mandate) + PROPERTY REF (DP), both shown on the ad
            # chrome. (This Canva path is dormant - html is the production
            # renderer - kept consistent for if Canva autofill is ever enabled.)
            "property_ref": f"DP{request.dp}",
            "master_ref": _master_ref(identity),
            "beds": _stat_label(physical.get("bedrooms"), "BED"),
            "baths": _stat_label(physical.get("bathrooms_main_unit"), "BATH"),
            "garages": _garage_label(physical),
            "size": _size_label(physical.get("unit_size_m2")),
            "features": _feature_tagline(physical),
        }

        fields: Dict[str, Dict[str, Any]] = {}
        for name, value in candidates.items():
            if dataset.get(name) != "text":
                continue
            if value:
                fields[name] = {"type": "text", "text": str(value)}
            elif name in _ALWAYS_EMIT:
                fields[name] = {"type": "text", "text": ""}

        for slot, asset_id in zip(image_slots, asset_ids):
            fields[slot] = {"type": "image", "asset_id": asset_id}

        return fields

    def _run_autofill(
        self, access_token: str, brand_template_id: str, data: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Create an autofill job on the brand template and poll to a design.

        Returns the finished ``design`` object (carries at least ``id`` and,
        depending on the API version, an edit ``url`` / ``urls`` block) so the
        caller can record a link back to the editable Canva design.
        """
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
        design = job["result"]["design"]
        return design if isinstance(design, dict) else {"id": design}

    @staticmethod
    def _design_url(design: Dict[str, Any]) -> Optional[str]:
        """Best link to open/edit a Canva design, across API-version shapes.

        Prefers an explicit edit URL, then a view URL, then a top-level ``url``,
        and finally reconstructs the standard edit URL from the design id. Returns
        ``None`` only when the design carries no id at all.
        """
        urls = design.get("urls") or {}
        direct = (
            urls.get("edit_url")
            or urls.get("view_url")
            or design.get("url")
            or design.get("view_url")
        )
        if direct:
            return direct
        design_id = design.get("id")
        return f"https://www.canva.com/design/{design_id}/edit" if design_id else None

    # --- export + download ----------------------------------------------

    def _export_format(self, fmt: str) -> Tuple[str, str, str]:
        """Return ``(canva_export_type, file_ext, mime)`` for a format.

        PNG is the default: it previews inline in the gate-2 gallery (no Canva
        login needed to see the design) and is attachable as post media
        (D24 static images). The info pack keeps PDF - it is a multi-page
        print/email document, not a social image.
        """
        if fmt == "info_pack":
            return ("pdf", "pdf", "application/pdf")
        return ("png", "png", "image/png")

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
        if len(urls) > 1:
            # A PNG export of a multi-page design yields one URL per page; a
            # PDF carries every page in one file. Our social/preview formats
            # are single-page templates, so page 1 is the design - but if a
            # template ever grows pages, say so instead of silently dropping
            # them (map such a format to PDF in _export_format).
            print(
                f"warning: Canva export of {fmt} returned {len(urls)} pages; "
                "using page 1 only",
                file=sys.stderr,
            )
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


def template_set_names() -> List[str]:
    """The configured template set names, in declared (default-first) order.

    The gate-2 template picker lists these. Never raises: an unset or
    unreadable ``CANVA_TEMPLATE_MAP`` returns ``[]`` (the picker hides), so the
    webapp needs no Canva-specific error handling.
    """
    try:
        return list(CanvaBackend()._load_template_sets().keys())
    except Exception:
        return []
