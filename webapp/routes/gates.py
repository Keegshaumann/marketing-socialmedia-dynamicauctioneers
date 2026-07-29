"""The three human approval gates (M8, Phase 4).

This module owns the operator-facing gate screens and the actions behind them.
Each gate is a point where a named human takes responsibility before a record
moves closer to being marketed, and every action here is recorded on the audit
trail (``engine.store`` state events) so the board always reflects the truth.

- **Gate 1 (verification review).** Renders the deterministic verification memo
  and its flags. A blocking flag must be resolved or overridden *with a written
  reason* before sign-off; the reason is passed straight to
  ``engine.verify.sign_off`` (which refuses otherwise). A successful sign-off
  moves the record ``-> verified``, generates the draft artifacts, and advances
  it ``-> drafted`` so it lands on gate 2.
- **Gate 2 (ad review).** Shows the artifact gallery rendered *only* from
  ``record.public_view()`` (POPIA: no owner or occupant PII can reach a tile).
  An approver may edit the human copy (stored back on ``record.marketing`` and
  re-rendered), request changes (re-runs render, stays on gate 2), or approve
  (``drafted -> approved``). The page also renders the internal approval email
  with tokenised one-click links, so an approver can action gate 2 from their
  inbox without logging in (see ``email_approve.py``).
- **Gate 3 (client approval).** Renders a pre-drafted client email to copy and
  send manually, and logs the client's approval (date + user) which transitions
  the record ``approved -> client_approved``.

Design rules baked in here:
- Public artifacts and both emails are built from ``public_view()`` only.
- The worker never renders owner PII; neither does any template in this module.
- SA English, no em/en dashes, no emojis in any copy.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.templating import Jinja2Templates

from engine.distribute.ghl import DELETE_CAVEAT
from engine.render import DEFAULT_BACKEND, FORMATS, get_backend
from engine.render.html_backend import BRAND
from engine.render.canva_backend import template_set_names
from engine.render.service import apply_edits, apply_photos, render_all
from engine.schema import (
    PHYSICAL_SOURCE_PRECEDENCE,
    SOURCE_LABELS,
    PropertyRecord,
    resolve_physical_conflicts,
)
from engine.store import IllegalTransition, RecordStore
from engine.verify import (
    Flag,
    SignOffRefused,
    build_memo,
    deterministic_checks,
    sign_off,
)
from webapp import models, tokens
from webapp.auth import current_user, require_role

router = APIRouter(prefix="/gates", tags=["gates"])

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# One-click approval links stay valid for a week (an approver may be travelling).
_APPROVE_TTL = 7 * 24 * 3600

# Formats worth previewing inline as text vs embedding as a visual frame.
_TEXT_MIME_HINTS = ("text/markdown", "text/plain")


# --- small helpers --------------------------------------------------------

def _db(request: Request) -> str:
    override = getattr(getattr(request, "app", None), "state", None)
    candidate = getattr(override, "db_path", None) if override is not None else None
    return models.resolve_db_path(candidate)


def _output_root(db_path: str) -> str:
    return models.get_setting(db_path, "output_root") or "."


def _store(db_path: str) -> RecordStore:
    return RecordStore(db_path)


def _load(db_path: str, dp: str) -> PropertyRecord:
    store = _store(db_path)
    try:
        record = store.get(dp)
    finally:
        store.close()
    if record is None:
        raise HTTPException(status_code=404, detail=f"No record for DP {dp}.")
    return record


def _state(db_path: str, dp: str) -> Optional[str]:
    store = _store(db_path)
    try:
        return store.get_state(dp)
    finally:
        store.close()


def _advance(db_path: str, dp: str, to_state: str, note: str) -> "tuple[bool, str]":
    """Transition if legal (idempotent, never crashes).

    Returns ``(moved, new_state)`` where ``moved`` is True when the record is now
    at ``to_state`` (either it just transitioned or it was already there) and
    False when the transition was illegal and refused. Callers use ``moved`` so
    they never record a sign-off for a transition that did not actually happen.
    """
    store = _store(db_path)
    try:
        current = store.get_state(dp)
        if current == to_state:
            return True, current
        try:
            store.transition(dp, to_state, note=note)
        except IllegalTransition:
            return False, current
        return True, store.get_state(dp)
    finally:
        store.close()


def _signoff(db_path: str, dp: str, gate: str, user: str, note: str) -> None:
    store = _store(db_path)
    try:
        store.record_signoff(dp, gate=gate, user=user, note=note)
    finally:
        store.close()


# The first draft renders ONLY the branded ad; the rest of the collateral (channel
# copy, info pack, banners, board, tile) is generated after the ad is approved
# (owner directive D39). These are the pre-approval states, where only the ad
# exists; from "approved" onward the full set renders.
_AD_ONLY_STATES = frozenset({"extracted", "flags_raised", "verified", "drafted"})
AD_FORMAT = "demo_ad"


def _formats_for_state(state: Optional[str]) -> Optional[List[str]]:
    """Which formats to render in ``state``: the ad only before approval, the
    full set (None) once approved (D39)."""
    return [AD_FORMAT] if state in _AD_ONLY_STATES else None


def _render(db_path: str, dp: str, formats: Any = "auto") -> List[Any]:
    """Render the artifacts for ``dp`` from public_view. Never raises upward.

    ``formats="auto"`` (default) picks the set from the current lifecycle state
    (ad-only before approval, full after, D39); pass an explicit list/None to
    override.
    """
    store = _store(db_path)
    try:
        if formats == "auto":
            formats = _formats_for_state(store.get_state(dp))
        return render_all(dp, store, output_root=_output_root(db_path), formats=formats)
    finally:
        store.close()


def _artifacts_dir(db_path: str, dp: str) -> Path:
    return Path(_output_root(db_path)) / f"DP{dp}" / "artifacts"


def _manifest(db_path: str, dp: str) -> List[Dict[str, Any]]:
    path = _artifacts_dir(db_path, dp) / "manifest.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def _gallery(db_path: str, dp: str) -> List[Dict[str, Any]]:
    """Return the gallery view-model: one entry per rendered artifact.

    Text artifacts carry an inline preview (safe: rendered from public_view);
    visual artifacts (html/svg) are shown via the artifact-serving route in an
    embedded frame. The manifest is rebuilt on demand if it is missing.
    """
    manifest = _manifest(db_path, dp)
    if not manifest:
        try:
            _render(db_path, dp)
        except Exception:
            # Render backend unavailable (e.g. Canva quota/network); show an
            # empty gallery rather than 500 the whole editor page.
            return []
        manifest = _manifest(db_path, dp)

    tiles: List[Dict[str, Any]] = []
    for art in manifest:
        fmt = art.get("fmt")
        mime = art.get("mime") or ""
        path = art.get("path")
        is_text = any(mime.startswith(h) for h in _TEXT_MIME_HINTS)
        preview = ""
        if is_text and path:
            try:
                preview = Path(path).read_text(encoding="utf-8")[:1600]
            except OSError:
                preview = ""
        tiles.append(
            {
                "fmt": fmt,
                "label": (fmt or "").replace("_", " ").title(),
                "mime": mime,
                "is_text": is_text,
                # Raster/vector images render inline as <img>; PDFs embed in a
                # viewer. Both mean the design is visible right here, without
                # opening Canva or downloading anything.
                "is_image": mime.startswith("image/"),
                "is_pdf": mime == "application/pdf",
                "preview": preview,
                "src": f"/gates/{dp}/ads/artifact/{fmt}",
                "version": art.get("version", 1),
                "edit_url": art.get("edit_url"),
            }
        )
    tiles.sort(key=lambda t: FORMATS.index(t["fmt"]) if t["fmt"] in FORMATS else 999)
    return tiles


def _memo_view(record: PropertyRecord) -> Dict[str, Any]:
    """Build the gate-1 view-model: flags + corroborated facts + memo markdown."""
    flags = deterministic_checks(record)
    identity = record.identity
    valuation = record.valuation
    physical = record.physical

    facts: List[tuple] = []
    if physical is not None and physical.unit_size_m2 is not None:
        facts.append(("Extent / unit size", f"{physical.unit_size_m2:g} m2"))
    if identity is not None and identity.title_deed_no:
        facts.append(("Title deed", identity.title_deed_no))
    if valuation is not None and valuation.municipal_valuation is not None:
        facts.append(("Municipal valuation", f"R{int(valuation.municipal_valuation):,}".replace(",", " ")))
    if identity is not None and identity.gps and len(identity.gps) == 2:
        facts.append(("GPS", f"{identity.gps[0]}, {identity.gps[1]}"))
    if physical is not None and physical.bedrooms is not None:
        facts.append(("Bedrooms (main unit)", str(physical.bedrooms)))
    if physical is not None and physical.zoning:
        facts.append(("Zoning", physical.zoning))

    address = ""
    suburb = ""
    if identity is not None:
        address = identity.street_address or identity.legal_description or ""
        suburb = identity.suburb or ""

    block_flags = [f for f in flags if f.severity == "block"]
    note_flags = [f for f in flags if f.severity != "block"]
    return {
        "flags": flags,
        "block_flags": block_flags,
        "note_flags": note_flags,
        "facts": facts,
        "address": address,
        "suburb": suburb,
        "source_labels": SOURCE_LABELS,
        "memo_markdown": build_memo(record, flags),
    }


def _email_view(record: PropertyRecord) -> Dict[str, Any]:
    """View-model for both gate emails, from public_view only (no PII)."""
    public = record.public_view()
    identity = public.get("identity") or {}
    marketing = public.get("marketing") or {}
    sale = public.get("sale_process") or {}
    viewing = sale.get("viewing") or {}
    return {
        "dp": record.dp,
        "headline": marketing.get("headline") or f"Property DP{record.dp}",
        "price_display": marketing.get("price_display") or "Price on application",
        "address": identity.get("street_address") or identity.get("legal_description") or "",
        "suburb": identity.get("suburb") or "",
        "method": sale.get("method") or "offers_invited",
        "terms": sale.get("terms") or [],
        "contact_public": viewing.get("contact_public") or f"{BRAND['name']} | {BRAND['phone']} | {BRAND['email']}",
        "brand": BRAND,
    }


def _abs(request: Request, path: str) -> str:
    return str(request.base_url).rstrip("/") + path


def _approval_links(request: Request, db_path: str, dp: str, approver: str) -> Dict[str, str]:
    """Sign two single-use tokens (approve / request-changes) for gate 2."""
    approve = tokens.sign(
        {"dp": dp, "gate": "2", "action": "approve", "approver": approver},
        _APPROVE_TTL,
        db_path,
    )
    changes = tokens.sign(
        {"dp": dp, "gate": "2", "action": "changes", "approver": approver},
        _APPROVE_TTL,
        db_path,
    )
    return {
        "approve": _abs(request, f"/email/gate2?token={approve}"),
        "changes": _abs(request, f"/email/gate2?token={changes}"),
    }


# --- gate 1: verification review -----------------------------------------

@router.get("/{dp}/verify", response_class=HTMLResponse)
def gate1_page(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    record = _load(db_path, dp)
    view = _memo_view(record)
    return templates.TemplateResponse(
        request,
        "gate1_verify.html",
        {
            "user": user,
            "dp": dp,
            "state": _state(db_path, dp),
            "record": record,
            **view,
        },
    )


@router.post("/{dp}/verify")
async def gate1_signoff(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    record = _load(db_path, dp)
    form = await request.form()

    store = _store(db_path)
    try:
        # 1. Resolve each physical conflict from the human's source pick (default
        #    pre-selected = the precedence winner). Confirming or overriding sets
        #    override_reason, which flips the block flag to a resolved note and
        #    writes the chosen value onto the field (D35).
        physical = record.physical
        if physical is not None and physical.conflicts:
            touched = False
            for c in physical.conflicts:
                # Only a conflict the human actually addressed in this submission
                # (its radio group is present) is confirmed; anything left out
                # stays blocking, so an empty POST cannot sign a conflict away.
                if form.get(f"conflict_source__{c.field}") is None:
                    continue
                touched = True
                chosen = str(form.get(f"conflict_source__{c.field}", "")).strip()
                reason = str(form.get(f"conflict_reason__{c.field}", "")).strip()
                if chosen in PHYSICAL_SOURCE_PRECEDENCE and getattr(c, chosen, None) is not None:
                    c.resolved_source = chosen
                label = SOURCE_LABELS.get(c.resolved_source, c.resolved_source)
                c.override_reason = reason or f"Confirmed the {label} value at gate 1."
            if touched:
                resolve_physical_conflicts(record)
                store.upsert(record)
                record = store.get(dp)  # reload so the resolutions are the source of truth

        # 2. Any remaining (non-conflict) block flag still needs a written reason.
        overrides: Dict[str, str] = {}
        for f in deterministic_checks(record):
            if f.severity != "block":
                continue
            reason = str(form.get(f"override__{f.code}", "")).strip()
            if reason:
                overrides[f.code] = reason

        sign_off(dp, store, user=user["email"], override_notes=overrides or None)
    except SignOffRefused as exc:
        view = _memo_view(record)
        return templates.TemplateResponse(
            request,
            "gate1_verify.html",
            {
                "user": user,
                "dp": dp,
                "state": _state(db_path, dp),
                "record": record,
                "error": str(exc),
                **view,
            },
            status_code=400,
        )
    finally:
        store.close()

    # Signed off -> verified. Draft the artifacts and advance to gate 2.
    _render(db_path, dp)
    _advance(db_path, dp, "drafted", note=f"drafts generated after sign-off by {user['email']}")
    return RedirectResponse(url=f"/gates/{dp}/ads", status_code=303)


# --- gate 2: ad review ----------------------------------------------------

@router.get("/{dp}/ads", response_class=HTMLResponse)
def gate2_page(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    record = _load(db_path, dp)
    tiles = _gallery(db_path, dp)
    links = _approval_links(request, db_path, dp, user["email"])
    email_html = templates.get_template("emails/gate2_internal.html").render(
        **_email_view(record), links=links
    )
    state = _state(db_path, dp)
    # Prefill from public_view so any existing human override shows (not the
    # sourced value), and the editor edits from what the ad actually says.
    pv = record.public_view()
    identity = pv.get("identity") or {}
    sale = pv.get("sale_process") or {}
    marketing_pv = pv.get("marketing") or {}
    # A client-ready email draft for the "email the ad" action (no PII, no DP).
    _where = identity.get("street_address") or identity.get("suburb") or "the property"
    email_subject = f"Property advert for approval: {identity.get('suburb') or _where}"
    email_body = (
        "Hi,\n\n"
        f"Please find the attached property advert for {_where}. It is ready for "
        "your approval before we take it to market. Let us know if you would like "
        "any changes.\n\nKind regards,\nDynamic Auctioneers"
    )
    return templates.TemplateResponse(
        request,
        "gate2_ads.html",
        {
            "user": user,
            "dp": dp,
            "state": state,
            "record": record,
            "tiles": tiles,
            "headline": marketing_pv.get("headline") or "",
            "price_display": marketing_pv.get("price_display") or "",
            "street_address": identity.get("street_address") or "",
            "suburb": identity.get("suburb") or "",
            "method": sale.get("method") or "",
            "terms": "\n".join(sale.get("terms") or []),
            # Auction specifics (D42), edited on the auction-only panel.
            "auction_type": sale.get("auction_type") or "",
            "auction_channel": sale.get("auction_channel") or "",
            "auction_date": sale.get("auction_date") or "",
            "auction_time": sale.get("auction_time") or "",
            # Design picker (D33): hidden unless more than one set is
            # configured AND the active renderer routes through Canva; the
            # first configured set is the default a blank pick follows.
            "template_sets": _design_sets(),
            "template_set": marketing_pv.get("template_set") or "",
            # HTML ad-design library (D41): the gallery of designs to pick from,
            # and the current pick (Classic when unset).
            "ad_templates": _ad_templates_list(),
            "current_ad_template": marketing_pv.get("template_set") or "classic",
            "is_update": state in ("live", "updated"),
            "delete_caveat": DELETE_CAVEAT,
            "photos": _photo_view(db_path, dp, record),
            "approval_email": email_html,
            "links": links,
            "email_subject": email_subject,
            "email_body": email_body,
        },
    )


@router.get("/{dp}/ads/artifact/{fmt}")
def gate2_artifact(dp: str, fmt: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    """Serve one rendered artifact file (PII-free: rendered from public_view)."""
    if fmt not in FORMATS:
        raise HTTPException(status_code=404, detail="Unknown format.")
    db_path = _db(request)
    for art in _manifest(db_path, dp):
        if art.get("fmt") == fmt and art.get("path"):
            path = Path(art["path"])
            if path.exists():
                return FileResponse(str(path), media_type=art.get("mime") or "text/plain")
    raise HTTPException(status_code=404, detail="Artifact not rendered yet.")


# --- gate 2: photo upload + management ------------------------------------
# Photos are the property's marketing images. Managing them here (rather than
# depending on an OneDrive/Graph watcher) means a listing with no photos in the
# source PDF can still get a real gallery. Serving DP<dp>/photos/ also makes the
# relative <img src="../photos/x.png"> in the html adverts resolve, so the
# gate-2 previews stop showing broken images.

_IMAGE_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
}
_CTYPE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
_MAX_PHOTO_BYTES = 12 * 1024 * 1024  # 12 MB per image
_MAX_PHOTOS_PER_UPLOAD = 40  # files processed per request; the excess is rejected
# A photo is flagged low-res (a non-blocking warning) when its shorter side is
# under this. Social feeds render around 1080px wide, so smaller images upscale
# and look soft. The photos extracted from a Property Report PDF are often tiny
# thumbnails (DP3060: ~276x207), which is exactly what this warns about.
_MIN_PHOTO_PX = 1080


def _image_dimensions(path: Path) -> Optional[Tuple[int, int]]:
    """Return (width, height) of an image file, or None if it cannot be read.

    Uses Pillow's lazy header read (no full decode). Any failure (missing file,
    unreadable/corrupt image) returns None so the caller simply shows no warning
    rather than breaking the panel.
    """
    try:
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
        return int(width), int(height)
    except Exception:  # noqa: BLE001 - a bad/missing image just yields no warning
        return None


def _photos_dir(db_path: str, dp: str) -> Path:
    return Path(_output_root(db_path)) / f"DP{dp}" / "photos"


def _safe_photo_name(name: str) -> str:
    """Basename only, restricted to a safe character set (no path traversal)."""
    base = Path(name).name
    keep = "".join(c if (c.isalnum() or c in "._-") else "_" for c in base).lstrip(".")
    return keep or "photo"


def _photo_list(record: PropertyRecord) -> List[str]:
    """The record's full ordered photo list: hero first, then gallery (deduped)."""
    marketing = record.marketing
    ordered: List[str] = []
    if marketing is not None:
        if marketing.hero_photo:
            ordered.append(marketing.hero_photo)
        ordered.extend(p for p in (marketing.gallery or []) if p)
    seen: set = set()
    uniq: List[str] = []
    for path in ordered:
        if path not in seen:
            seen.add(path)
            uniq.append(path)
    return uniq


def _photo_view(db_path: str, dp: str, record: PropertyRecord) -> List[Dict[str, Any]]:
    """Panel view-model: one tile per photo (name, url, is_hero, low-res warning).

    Each tile carries the pixel size and a ``low_res`` flag (shorter side under
    ``_MIN_PHOTO_PX``) so the panel can warn on a soft image without blocking it.
    Dimensions that cannot be read leave ``low_res`` False (no false alarm).
    """
    photos_dir = _photos_dir(db_path, dp)
    tiles: List[Dict[str, Any]] = []
    for i, path in enumerate(_photo_list(record)):
        name = Path(path).name
        dims = _image_dimensions(photos_dir / name)
        low_res = dims is not None and min(dims) < _MIN_PHOTO_PX
        tiles.append(
            {
                "name": name,
                "url": f"/gates/{dp}/ads/photos/{name}",
                "is_hero": i == 0,
                "dims": f"{dims[0]}x{dims[1]}" if dims else None,
                "low_res": low_res,
            }
        )
    return tiles


def _save_photos(db_path: str, dp: str, full: List[str], user: str) -> None:
    """Persist an ordered photo list (hero = first) and re-render, once."""
    hero = full[0] if full else None
    gallery = full[1:] if len(full) > 1 else []
    store = _store(db_path)
    try:
        apply_photos(dp, store, hero, gallery, user, output_root=_output_root(db_path))
    finally:
        store.close()


def _photo_result(request: Request, db_path: str, dp: str, toast: Dict[str, Any]):
    """Action response: swap the rendered-adverts gallery + the photos panel (OOB)."""
    record = _load(db_path, dp)
    return templates.TemplateResponse(
        request,
        "partials/_gate2_photo_result.html",
        {"dp": dp, "tiles": _gallery(db_path, dp), "photos": _photo_view(db_path, dp, record), "toast": toast},
    )


@router.get("/{dp}/ads/photos/{name}")
def gate2_photo(dp: str, name: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    """Serve one property photo (editor thumbnails + advert-preview images)."""
    db_path = _db(request)
    path = _photos_dir(db_path, dp) / Path(name).name  # basename => no traversal
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such photo.")
    return FileResponse(str(path), media_type=_IMAGE_MIME.get(path.suffix.lower(), "application/octet-stream"))


@router.post("/{dp}/ads/photos/upload", response_class=HTMLResponse)
async def gate2_photo_upload(
    dp: str, request: Request,
    files: List[UploadFile] = File(default=[]),
    user: dict = Depends(require_role("approver", "marketing")),
):
    db_path = _db(request)
    full = _photo_list(_load(db_path, dp))
    photos_dir = _photos_dir(db_path, dp)
    photos_dir.mkdir(parents=True, exist_ok=True)
    added = rejected = 0
    rejected += max(0, len(files) - _MAX_PHOTOS_PER_UPLOAD)  # cap files per request
    for upload in files[:_MAX_PHOTOS_PER_UPLOAD]:
        # Reject an oversized part before pulling it into memory (Starlette sets
        # upload.size as the part spools); the len(raw) check below is the belt
        # for clients that send no size header.
        if upload.size is not None and upload.size > _MAX_PHOTO_BYTES:
            rejected += 1
            continue
        raw = await upload.read()
        if not raw:
            continue
        name = _safe_photo_name(upload.filename or "photo")
        ext = Path(name).suffix.lower()
        if ext not in _IMAGE_MIME:
            derived = _CTYPE_EXT.get((upload.content_type or "").lower())
            if derived is None:
                rejected += 1
                continue
            name += derived
        if len(raw) > _MAX_PHOTO_BYTES:
            rejected += 1
            continue
        dest = photos_dir / name
        stem, suffix, n = dest.stem, dest.suffix, 1
        while dest.exists():
            dest = photos_dir / f"{stem}_{n}{suffix}"
            n += 1
        dest.write_bytes(raw)
        rel = f"photos/{dest.name}"
        if rel not in full:
            full.append(rel)
            added += 1

    if added:
        _reopen_if_live(db_path, dp, user["email"])
        try:
            _save_photos(db_path, dp, full, user["email"])
            text = f"{added} photo(s) uploaded and re-rendered."
            if rejected:
                text += f" {rejected} skipped (not an image or too large)."
            toast = {"tone": "ok", "title": "Photos added", "text": text}
        except Exception as exc:  # a render backend failure (e.g. Canva quota)
            toast = {"tone": "block", "title": "Re-render failed",
                     "text": f"Photos saved, but the adverts could not be re-rendered ({type(exc).__name__})."}
    else:
        toast = {"tone": "note", "title": "No photos added",
                 "text": "Only image files (jpg, png, webp, gif) up to 12 MB are accepted."}
    return _photo_result(request, db_path, dp, toast)


@router.post("/{dp}/ads/photos/hero", response_class=HTMLResponse)
async def gate2_photo_hero(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    form = await request.form()
    name = Path(str(form.get("name", ""))).name
    full = _photo_list(_load(db_path, dp))
    chosen = next((p for p in full if Path(p).name == name), None)
    if chosen and full and full[0] != chosen:
        full = [chosen] + [p for p in full if p != chosen]
        _reopen_if_live(db_path, dp, user["email"])
        _save_photos(db_path, dp, full, user["email"])
        toast = {"tone": "ok", "title": "Lead photo set", "text": "Adverts re-rendered with the new lead image."}
    else:
        toast = {"tone": "note", "title": "No change",
                 "text": "That photo is already the lead, or was not found."}
    return _photo_result(request, db_path, dp, toast)


@router.post("/{dp}/ads/photos/delete", response_class=HTMLResponse)
async def gate2_photo_delete(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    form = await request.form()
    name = Path(str(form.get("name", ""))).name
    current = _photo_list(_load(db_path, dp))
    full = [p for p in current if Path(p).name != name]
    if len(full) != len(current):
        _reopen_if_live(db_path, dp, user["email"])
        _save_photos(db_path, dp, full, user["email"])
        toast = {"tone": "ok", "title": "Photo removed", "text": "Adverts re-rendered."}
    else:
        toast = {"tone": "note", "title": "No change", "text": "That photo was not found."}
    return _photo_result(request, db_path, dp, toast)


# Editable public fields: form field name -> dotted public-view path. Owner /
# occupant / financial fields are absent from public_view, so they can never
# appear here (POPIA hard rule 1); apply_edits refuses a protected path anyway.
_EDIT_TEXT_FIELDS = {
    "headline": "marketing.headline",
    "price_display": "marketing.price_display",
    "street_address": "identity.street_address",
    "suburb": "identity.suburb",
    # Auction specifics (D42): shown on auction ads only. Blank inputs are
    # skipped by _collect_edit_fields, so they never wipe an existing value.
    "auction_type": "sale_process.auction_type",
    "auction_channel": "sale_process.auction_channel",
    "auction_date": "sale_process.auction_date",
    "auction_time": "sale_process.auction_time",
}
_SALE_METHODS = ("offers_invited", "auction")


def _design_sets() -> list:
    """The template-set names the gate-2 design picker offers (D33).

    Empty (picker hidden) unless a choice is real: more than one set is
    configured AND the active renderer actually routes formats through Canva
    (``ENGINE_RENDERER`` is ``canva`` or ``mixed`` and the backend is
    available). Otherwise a pick would be a silent no-op -- the html backend
    ignores ``template_set`` -- while the UI promises a re-rendered design.
    """
    renderer = (os.getenv("ENGINE_RENDERER") or DEFAULT_BACKEND).strip()
    if renderer not in ("canva", "mixed"):
        return []
    try:
        ok, _reason = get_backend("canva").available()
    except Exception:
        return []
    if not ok:
        return []
    names = template_set_names()
    return names if len(names) > 1 else []


def _ad_templates_list() -> list:
    """The HTML ad-design library for the gate-2 picker (D41).

    Shown when the ad renders through the html backend (renderer ``html`` or
    ``mixed``) and more than one design exists. A pure-``canva`` renderer uses the
    Canva design-sets picker instead, so the html library is hidden there.
    """
    renderer = (os.getenv("ENGINE_RENDERER") or DEFAULT_BACKEND).strip()
    if renderer == "canva":
        return []
    from engine.render import ad_templates

    tpls = ad_templates.list_templates()
    return tpls if len(tpls) > 1 else []


def _collect_edit_fields(form, current_template_set: "str | None" = None) -> dict:
    """Build the public-view edit map from a submitted form (non-empty only).

    A blank text input is skipped so it never wipes an existing value. ``terms``
    is a textarea, one term per line, stored as a list. The design pick is
    included only when it actually CHANGES the record: a select always submits
    a value, and without the change guard every save would silently pin the
    default set's name onto a record that never chose one (a false audit row,
    and the record would stop following a future default change). A submitted
    blank ("follow the default") clears an existing pick back to None.
    """
    fields: dict = {}
    for name, path in _EDIT_TEXT_FIELDS.items():
        value = str(form.get(name, "")).strip()
        if value:
            fields[path] = value
    method = str(form.get("method", "")).strip()
    if method in _SALE_METHODS:
        fields["sale_process.method"] = method
    # The design pick (D33). Only names the picker actually offers are
    # accepted, so a crafted value cannot land on the record.
    if "template_set" in form:
        posted = str(form.get("template_set", "")).strip()
        current = (current_template_set or "").strip()
        offered = _design_sets()
        if posted != current and offered and (not posted or posted in offered):
            fields["marketing.template_set"] = posted
    terms_raw = str(form.get("terms", "")).strip()
    if terms_raw:
        fields["sale_process.terms"] = [
            line.strip() for line in terms_raw.splitlines() if line.strip()
        ]
    return fields


def _save_edits(db_path: str, dp: str, fields: dict, user: str) -> None:
    """Apply human edits and re-render once (engine ``apply_edits``).

    No-op when nothing changed. Price is formatted and every other public fact
    rides ``human_overrides`` so the sourced value stays intact (SPEC hard
    rule 3); each field is logged to the audit trail.
    """
    if not fields:
        return
    store = _store(db_path)
    try:
        formats = _formats_for_state(store.get_state(dp))
        apply_edits(dp, store, fields, user, output_root=_output_root(db_path), formats=formats)
    finally:
        store.close()


def _reopen_if_live(db_path: str, dp: str, user: str) -> None:
    """Move a ``live`` listing into the update cycle on its first edit.

    Editing an already-posted listing starts an update: ``live -> updated`` so
    the record re-passes the internal approval before it can repost. Any other
    state is left alone (a first-time edit at ``drafted`` must not jump ahead).
    """
    store = _store(db_path)
    try:
        if store.get_state(dp) == "live":
            store.transition(dp, "updated", note=f"reopened for edit by {user}")
    finally:
        store.close()


@router.post("/{dp}/ads/copy", response_class=HTMLResponse)
async def gate2_copy(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    form = await request.form()
    record = _load(db_path, dp)
    current_pick = record.marketing.template_set if record.marketing else None
    fields = _collect_edit_fields(form, current_template_set=current_pick)
    if fields:
        # Only a real edit reopens a live listing into the update cycle.
        _reopen_if_live(db_path, dp, user["email"])
        try:
            _save_edits(db_path, dp, fields, user["email"])
            toast = {"tone": "ok", "title": "Saved", "text": "Artifacts re-rendered with your edits."}
        except ValueError as exc:
            # A POPIA-protected field was refused before anything was saved.
            toast = {"tone": "block", "title": "Edit refused", "text": str(exc)}
        except Exception as exc:  # a render backend failure (e.g. Canva quota / network)
            toast = {
                "tone": "block",
                "title": "Re-render failed",
                "text": (
                    "Your edit was saved, but the artifacts could not be re-rendered "
                    f"({type(exc).__name__}). Try again, or switch the render backend."
                ),
            }
    else:
        toast = {"tone": "note", "title": "No changes", "text": "Nothing to save. A blank field keeps its current value."}
    return templates.TemplateResponse(
        request,
        "partials/_gate2_gallery.html",
        {"dp": dp, "tiles": _gallery(db_path, dp), "toast": toast},
    )


@router.post("/{dp}/ads/headline", response_class=HTMLResponse)
async def gate2_suggest_headline(
    dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))
):
    """Generate one marketing headline with Claude and return the pre-filled
    headline input for review. Key-gated with a deterministic fallback (always
    returns a usable headline); does NOT save - saving stays the Save action.
    """
    from engine.render.copy import generate_headline

    record = _load(_db(request), dp)
    return templates.TemplateResponse(
        request,
        "partials/_headline_input.html",
        {"headline": generate_headline(record)},
    )


@router.get("/{dp}/ad.png")
def gate2_ad_png(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    """Serve the branded ad as a PNG attachment, for emailing a client (D39).

    Backend-agnostic: the Canva backend outputs ``demo_ad.png`` directly (served
    as-is), while the html backend outputs ``demo_ad.html`` which is rasterised to
    PNG via headless Chromium (cached; re-rendered when the HTML is newer). The
    download filename uses the suburb, never the internal DP (D37). Degrades to a
    clear message if the rasteriser is unavailable.
    """
    from engine.render.rasterize import RasterizeUnavailable, html_to_png

    db_path = _db(request)
    art_dir = _artifacts_dir(db_path, dp)
    html_path = art_dir / "demo_ad.html"
    png_path = art_dir / "demo_ad.png"

    if not html_path.exists() and not png_path.exists():
        _render(db_path, dp, formats=[AD_FORMAT])  # ensure the ad exists (either backend)

    # Rasterise only when the html is the live source (present and newer than any
    # png). A Canva-produced png with no html is served directly.
    if html_path.exists() and (
        not png_path.exists() or png_path.stat().st_mtime < html_path.stat().st_mtime
    ):
        try:
            html_to_png(html_path, png_path)
        except RasterizeUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:  # a render failure must not 500 opaquely
            raise HTTPException(status_code=500, detail=f"Could not render the ad image: {exc}")

    if not png_path.exists():
        raise HTTPException(status_code=404, detail="The ad has not been rendered yet.")

    record = _load(db_path, dp)
    suburb = (record.identity.suburb if record.identity else None) or "property"
    slug = "".join(c if c.isalnum() else "-" for c in suburb).strip("-").lower() or "property"
    return FileResponse(str(png_path), media_type="image/png", filename=f"{slug}-advert.png")


@router.get("/ad-template/{template_id}/thumb.png")
def ad_template_thumb(template_id: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    """A cached sample-data preview of an ad design, for the gate-2 picker (D41)."""
    from engine.render import ad_templates
    from engine.render.ad_thumbs import thumbnail

    if template_id not in ad_templates.template_ids():
        raise HTTPException(status_code=404, detail="Unknown ad template.")
    png = thumbnail(template_id, _output_root(_db(request)))
    if png is None:
        raise HTTPException(status_code=503, detail="Preview unavailable (rasteriser not installed).")
    return FileResponse(str(png), media_type="image/png")


@router.post("/{dp}/ads/template", response_class=HTMLResponse)
async def gate2_pick_template(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    """Pick an ad design for this property (D41): store it on marketing.template_set
    and re-render the ad through the chosen template."""
    from engine.render import ad_templates

    db_path = _db(request)
    form = await request.form()
    tid = str(form.get("template", "")).strip()
    if tid not in ad_templates.template_ids():
        tid = ad_templates.DEFAULT_ID
    # Default = "no explicit pick" -> store empty so the record follows Classic.
    value = "" if tid == ad_templates.DEFAULT_ID else tid
    _reopen_if_live(db_path, dp, user["email"])
    try:
        _save_edits(db_path, dp, {"marketing.template_set": value}, user["email"])
        toast = {"tone": "ok", "title": "Design applied", "text": "The ad was re-rendered with the chosen design."}
    except Exception as exc:  # a render failure must not break the picker
        toast = {"tone": "block", "title": "Could not apply", "text": f"{type(exc).__name__}."}
    return templates.TemplateResponse(
        request,
        "partials/_gate2_gallery.html",
        {"dp": dp, "tiles": _gallery(db_path, dp), "toast": toast},
    )


@router.post("/{dp}/ads/changes", response_class=HTMLResponse)
async def gate2_changes(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    form = await request.form()
    note = str(form.get("note", "")).strip() or "Changes requested."
    _signoff(db_path, dp, gate="2", user=user["email"], note=f"changes requested: {note}")
    _render(db_path, dp)  # regenerate and return to gate 2
    return templates.TemplateResponse(
        request,
        "partials/_gate2_gallery.html",
        {
            "dp": dp,
            "tiles": _gallery(db_path, dp),
            "toast": {"tone": "note", "title": "Changes requested", "text": "Artifacts regenerated. Still on gate 2 for review."},
        },
    )


def action_gate2_approve(db_path: str, dp: str, approver: str) -> str:
    """Approve gate 2. Returns the resulting state.

    Shared by the logged-in button and the tokenised email link so both paths
    behave identically. Two cases:
    - an update cycle (state ``updated``) is a small-edit repost: record one
      internal approval (``gate=repost``) and stay in ``updated`` -- the client
      already approved this listing at first go-live, so gate 3 is not re-run;
    - otherwise move ``drafted -> approved`` and record the internal ad approval.
    The sign-off is recorded only when the move actually happens, so an
    out-of-order approval leaves no false audit entry.
    """
    if _state(db_path, dp) == "updated":
        _signoff(db_path, dp, gate="repost", user=approver, note="internal approval for repost")
        return _state(db_path, dp)
    moved, state = _advance(db_path, dp, "approved", note=f"gate 2 approved by {approver}")
    if moved:
        _signoff(db_path, dp, gate="2", user=approver, note="internal ad approval")
        # The ad is approved: NOW build the rest of the collateral (channel copy,
        # info pack, banners, board, tile). Until now only the ad existed (D39).
        _render(db_path, dp)  # state is "approved" -> full set
    return state


def action_gate2_changes(db_path: str, dp: str, approver: str, note: str) -> str:
    """Request changes via the email path: log the note and re-render."""
    _signoff(db_path, dp, gate="2", user=approver, note=f"changes requested: {note}")
    _render(db_path, dp)
    return _state(db_path, dp)


@router.post("/{dp}/ads/approve")
def gate2_approve(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    state = action_gate2_approve(db_path, dp, user["email"])
    if state == "updated":
        target = f"/post/{dp}"          # small-edit repost: straight to distribution
    elif state == "approved":
        target = f"/gates/{dp}/client"  # first-time: on to gate 3 (client approval)
    else:
        target = f"/gates/{dp}/ads"     # nothing moved (e.g. still live): back to editor
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(url=target, status_code=303)


# --- gate 3: client approval ---------------------------------------------

@router.get("/{dp}/client", response_class=HTMLResponse)
def gate3_page(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    record = _load(db_path, dp)
    client_email = templates.get_template("emails/client_draft.html").render(**_email_view(record))
    return templates.TemplateResponse(
        request,
        "gate3_client.html",
        {
            "user": user,
            "dp": dp,
            "state": _state(db_path, dp),
            "record": record,
            "client_email": client_email,
            "today": date.today().isoformat(),
        },
    )


@router.post("/{dp}/client/approve")
async def gate3_approve(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    form = await request.form()
    when = str(form.get("approved_on", "")).strip() or date.today().isoformat()
    moved, _ = _advance(db_path, dp, "client_approved", note=f"client approval logged by {user['email']}")
    if moved:
        _signoff(db_path, dp, gate="3", user=user["email"], note=f"client approved on {when}")
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": f"/board"})
    return RedirectResponse(url="/board", status_code=303)
