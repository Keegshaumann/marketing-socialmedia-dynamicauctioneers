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
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.templating import Jinja2Templates

from engine.distribute.ghl import DELETE_CAVEAT
from engine.render import DEFAULT_BACKEND, FORMATS, get_backend
from engine.render.html_backend import BRAND
from engine.render.canva_backend import template_set_names
from engine.render.copy import _template_copy
from engine.render.service import apply_edits, apply_photos, copy_cache_key, render_all
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
_AD_ONLY_STATES = frozenset({"extracted", "flags_raised", "verified", "photos", "drafted"})
# States BEFORE the photos step is finished: the advert is not rendered yet, so a
# stray gallery/artifacts view must not lazily build a no-photo ad (D-log: photos
# are mandatory before the first render, to avoid re-rendering and wasted tokens).
_PRE_DRAFT_STATES = frozenset({"intake", "extracted", "flags_raised", "verified", "photos"})
AD_FORMAT = "demo_ad"


# --- pending re-render (D72) ---------------------------------------------
# Gate-2 actions used to render on every click: 5.0s each once a property is
# approved (measured, all nine formats), so picking a design, swapping the lead
# photo and fixing a headline cost fifteen seconds of watching a spinner. They
# now mark the artifacts STALE and one explicit "Regenerate" does the work once.
#
# The marker is a file beside the artifacts it describes: it survives a restart,
# it cannot drift out of sync with a database row, and rendering naturally
# clears it. Nothing may be APPROVED or POSTED while it is set - that is the
# whole risk of batching, and it is closed in the approve path rather than left
# to the marketer to remember.

_STALE_MARKER = ".stale"


def _mark_stale(db_path: str, dp: str, reason: str) -> None:
    art_dir = _artifacts_dir(db_path, dp)
    try:
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / _STALE_MARKER).write_text(reason, encoding="utf-8")
    except OSError:
        pass          # a marker we cannot write must never fail the edit itself


def _is_stale(db_path: str, dp: str) -> bool:
    return (_artifacts_dir(db_path, dp) / _STALE_MARKER).exists()


def _clear_stale(db_path: str, dp: str) -> None:
    try:
        (_artifacts_dir(db_path, dp) / _STALE_MARKER).unlink()
    except OSError:
        pass


def _render_if_stale(db_path: str, dp: str) -> bool:
    """Render pending changes and clear the marker. True when work was done."""
    if not _is_stale(db_path, dp):
        return False
    _render(db_path, dp)
    _clear_stale(db_path, dp)
    return True


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
        # The DRAFT phase does not pay for model-written copy (D93). While the
        # marketer is picking photographs and wording a callout, the advert
        # renders from the deterministic template; the paid call is made once,
        # when the full pack is built after approval. Nothing is cached in the
        # draft phase, so the pack still generates its own copy.
        draft_only = bool(formats) and set(formats) <= {AD_FORMAT}
        return render_all(dp, store, output_root=_output_root(db_path),
                          formats=formats, ai_copy=not draft_only)
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
        # The advert is built only once the photos step is complete (drafted
        # onward). A stray view of an earlier-state record must NOT lazily render
        # a no-photo ad - that is exactly the wasted render the photos gate
        # exists to prevent. Photos are mandatory before drafting.
        if _state(db_path, dp) in _PRE_DRAFT_STATES:
            return []
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


def _resolved_copy(record: PropertyRecord) -> Dict[str, Any]:
    """The copy the advert actually renders from, resolved offline.

    Gate 2 used to prefill its Headline and Price inputs from
    ``marketing.headline`` / ``marketing.price_display``, which are set only
    once a human has typed them. The renderer meanwhile always resolves a copy
    bundle (``service._resolve_copy``), so a fresh record showed "OFFERS
    INVITED" on the advert while the Price box on the form sat empty. This
    mirrors that resolution so the form is prefilled with what the advert says.

    It never makes a model call: the cached bundle on ``marketing.generated_copy``
    is reused while its fingerprint still matches the facts it was written from,
    otherwise the deterministic template copy is built (offline, free, and the
    same words the renderer would fall back to without a key). Human edits on
    ``record.marketing`` win last, exactly as they do at render time.

    Derived from ``public_view`` only, so no PII and no sale-strategy valuation
    can reach the form. The money line stays the offers/auction framing: it is
    either a human-typed asking price or ``_framing``'s label, never a municipal
    or professional valuation figure.
    """
    try:
        marketing = record.marketing
        cached = None
        if (
            marketing is not None
            and marketing.generated_copy
            and marketing.generated_copy_key == copy_cache_key(record)
        ):
            cached = dict(marketing.generated_copy)
        copy = cached if cached is not None else dict(_template_copy(record))
        if marketing is not None:
            if marketing.headline:
                copy["headline"] = marketing.headline
            if marketing.price_display:
                copy["price_display"] = marketing.price_display
        return copy
    except Exception:
        # A prefill is a convenience: a sparse or odd record must still open
        # gate 2. Fall back to the record's own values below.
        return {}


def _gate2_prefill(record: PropertyRecord) -> Dict[str, str]:
    """What the gate-2 Headline and Price inputs are filled with.

    One helper for both the page and the save handler: the page shows these, and
    the save handler needs to know them to tell a real edit from an untouched
    prefill. Human value first, else the advert's own resolved copy.
    """
    marketing = record.public_view().get("marketing") or {}
    copy = _resolved_copy(record)
    return {
        "headline": marketing.get("headline") or copy.get("headline") or "",
        "price_display": marketing.get("price_display") or copy.get("price_display") or "",
    }


def _num_str(value: Any) -> str:
    """A stored number as a person would type it: ``20.0`` -> ``20``, ``7.5`` kept."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _terms_view(record: PropertyRecord) -> Dict[str, Any]:
    """The sale terms and running costs as the gate-2 panel shows them (D80).

    Every one of these comes from a document the marketer may simply not have -
    the OTP for the terms, the managing agent's statement for the levy - so each
    is a field they can type. The panel prefills from the record and says where
    each half came from, because "R1 480" a colleague can trace to a statement
    and "R1 480" somebody remembered are different facts to put in a buyer's
    hands. Read from ``public_view`` so an existing human override shows rather
    than the sourced value underneath it.
    """
    public = record.public_view()
    sale = public.get("sale_process") or {}
    otp = sale.get("otp") or {}
    valuation = public.get("valuation") or {}
    vat = otp.get("commission_vat")
    return {
        "deposit_pct": _num_str(otp.get("deposit_pct")),
        "deposit_due": otp.get("deposit_due") or "",
        "commission_pct": _num_str(otp.get("commission_pct")),
        "commission_vat": "" if vat is None else ("yes" if vat else "no"),
        "commission_payable_by": otp.get("commission_payable_by") or "",
        "guarantee_days": _num_str(otp.get("guarantee_days")),
        "confirmation_days": _num_str(otp.get("confirmation_days")),
        "outstanding_payable_by": otp.get("outstanding_payable_by") or "",
        "monthly_rates": _num_str(valuation.get("estimated_monthly_rates")),
        "monthly_levy": _num_str(valuation.get("monthly_levy")),
        # Provenance, shown as a hint above each half of the panel.
        "otp_source": otp.get("source_file") or "",
        # The document's own contradictions (a figure that disagrees with its
        # words), surfaced so a typed correction is an informed one.
        "otp_flags": otp.get("flags") or [],
        "levy_note": valuation.get("monthly_levy_note") or "",
    }


def _email_view(record: PropertyRecord) -> Dict[str, Any]:
    """View-model for both gate emails, from public_view only (no PII)."""
    public = record.public_view()
    identity = public.get("identity") or {}
    marketing = public.get("marketing") or {}
    sale = public.get("sale_process") or {}
    viewing = sale.get("viewing") or {}
    # Same source of truth as the advert and the gate-2 form: the resolved copy,
    # so the approval email never quotes a price the ad does not carry.
    copy = _resolved_copy(record)
    return {
        "dp": record.dp,
        "headline": marketing.get("headline") or copy.get("headline") or f"Property DP{record.dp}",
        "price_display": marketing.get("price_display") or copy.get("price_display") or "Price on application",
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

    # Signed off -> verified. Before drafting the ad, offer an "add photos" step
    # (D-log): the marketer can upload property photos (or skip) so the very
    # first render already has them, rather than building a text-only ad first.
    _advance(db_path, dp, "photos", note=f"gate 1 signed off by {user['email']}")
    return RedirectResponse(url=f"/gates/{dp}/photos", status_code=303)


# --- photos step (optional, between gate 1 and gate 2) --------------------

@router.get("/{dp}/photos", response_class=HTMLResponse)
def gate_photos_page(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    """The add-photos step after gate 1: a photos-only screen where the marketer
    uploads property photos (or skips) so the first ad render already carries
    them. Reuses the gate-2 photos panel; the ad is not rendered yet (that
    happens on continue), so this page never shows the advert."""
    db_path = _db(request)
    record = _load(db_path, dp)
    return templates.TemplateResponse(
        request,
        "gate_photos.html",
        {
            "user": user,
            "dp": dp,
            "state": _state(db_path, dp),
            "headline": (record.marketing.headline if record.marketing else None),
            "photos": _photo_view(db_path, dp, record),
            "qr_src": _qr_view(db_path, dp, record),
            "max_photos": _MAX_PHOTOS_TOTAL,
            "stale": _is_stale(db_path, dp),
            "max_photos": _MAX_PHOTOS_TOTAL,
        },
    )


@router.post("/{dp}/photos/continue")
def gate_photos_continue(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    """Leave the photos step: render the ad ONCE (now that photos exist) and
    advance to gate 2.

    At least one photo is required (the min-1 rule): a zero-photo continue is
    refused and bounces back to the photos step, so the advert is never built
    without a photo and then re-rendered. The marketer either uploads a photo or
    uses the "pull from source documents" fallback first."""
    db_path = _db(request)
    # The photos step only belongs before the first draft. Reaching this URL on an
    # already-drafted or live listing (back button, bookmark) must not re-render
    # its artifacts or attempt an illegal backward advance - send it to gate 2,
    # where photo edits go through the normal reopen/approval cycle.
    if _state(db_path, dp) not in _PRE_DRAFT_STATES:
        target = f"/gates/{dp}/ads"
        if request.headers.get("HX-Request"):
            return Response(status_code=204, headers={"HX-Redirect": target})
        return RedirectResponse(url=target, status_code=303)
    if not _photo_list(_load(db_path, dp)):
        # Say why, rather than silently reloading an identical-looking page: to a
        # marketer an unexplained no-op reads as a broken button.
        toast = {
            "tone": "note", "title": "A photo is needed first",
            "text": "Add at least one photo (or use the images from the documents) "
                    "before the advert is drafted.",
        }
        if request.headers.get("HX-Request"):
            return _photo_result(request, db_path, dp, toast)
        return RedirectResponse(url=f"/gates/{dp}/photos", status_code=303)
    try:
        _render(db_path, dp)
    except Exception as exc:  # a render backend failure must not strand the step
        return _photo_result(request, db_path, dp, {
            "tone": "block", "title": "Advert could not be built",
            "text": f"The photos are saved, but rendering failed ({type(exc).__name__}). "
                    "Try Continue again in a moment.",
        })
    _advance(db_path, dp, "drafted", note=f"photos step completed by {user['email']}")
    target = f"/gates/{dp}/ads"
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(url=target, status_code=303)


@router.post("/{dp}/photos/from-source", response_class=HTMLResponse)
def gate_photos_from_source(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    """No-photo fallback: pull property images straight from the source PDFs.

    The Property Report (and EVM) usually embed real photos of the property; when
    the marketer has none of their own, this extracts those images at source
    quality (``engine.photos``), keeps the best few by area, and adds them to the
    record. Truthful (the images come from the property's own documents) and
    needs no external service. If the documents carry no usable image, the panel
    just reports that and the marketer must upload one."""
    from engine.photos import extract_photos, rank_photos

    db_path = _db(request)
    full = _photo_list(_load(db_path, dp))
    if len(full) >= _MAX_PHOTOS_TOTAL:
        return _photo_result(request, db_path, dp, {
            "tone": "note", "title": "Photo limit reached",
            "text": f"This property already has {_MAX_PHOTOS_TOTAL} photos."})

    uploads = Path(_output_root(db_path)) / f"DP{dp}" / "uploads"
    photos_dir = _photos_dir(db_path, dp)
    photos_dir.mkdir(parents=True, exist_ok=True)
    # Property Report first (it carries the on-site photos), then any other PDF.
    # Case-insensitive: a scanner or Windows machine names files ".PDF", and a
    # case-sensitive glob would report "no images" with the report sitting there.
    pdfs = sorted(p for p in uploads.iterdir()
                  if p.is_file() and p.suffix.lower() == ".pdf") if uploads.is_dir() else []
    pdfs.sort(key=lambda p: 0 if "report" in p.name.lower() else 1)

    tray = photos_dir / "_from_source"
    extracted: List[Path] = []
    for i, pdf in enumerate(pdfs):
        try:  # per-PDF subfolder so same-named images across docs don't overwrite
            extracted.extend(extract_photos(pdf, tray / f"src{i}"))
        except Exception:
            continue
    # rank_photos orders by area and drops the cover page (letterhead + map), so
    # the picks are the actual on-site inspection photos, largest first.
    picks = rank_photos(extracted).get("gallery") or [] if extracted else []

    added = 0
    for src in picks:
        if len(full) >= _MAX_PHOTOS_TOTAL:
            break
        dest = photos_dir / src.name
        stem, suffix, n = dest.stem, dest.suffix, 1
        while dest.exists():
            dest = photos_dir / f"{stem}_{n}{suffix}"
            n += 1
        dest.write_bytes(src.read_bytes())
        rel = f"photos/{dest.name}"
        if rel not in full:
            full.append(rel)
            added += 1
    shutil.rmtree(tray, ignore_errors=True)

    if added:
        # Same reopen rule as a manual upload: changing the photos of a live
        # listing is an edit, and must re-enter the approval cycle (hard rule 2).
        _reopen_if_live(db_path, dp, user["email"])
        _save_photos(db_path, dp, full, user["email"])
        toast = {"tone": "ok", "title": "Images pulled from the documents",
                 "text": f"{added} image(s) taken from the source documents. Set the lead photo, then continue."}
    else:
        toast = {"tone": "note", "title": "No images found",
                 "text": "The source documents carry no usable image. Please upload at least one photo."}
    return _photo_result(request, db_path, dp, toast)


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
    # Headline and Price come from the RESOLVED copy, which is what the advert
    # is rendered with: the record's own values are only ever set once a human
    # has typed them, so prefilling from the record alone left Price blank on a
    # fresh listing while the ad already read "OFFERS INVITED". Offline and free
    # (cached bundle or deterministic template copy), so the page stays fast.
    prefill = _gate2_prefill(record)
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
            "headline": prefill["headline"],
            "price_display": prefill["price_display"],
            "street_address": identity.get("street_address") or "",
            "suburb": identity.get("suburb") or "",
            "method": sale.get("method") or "",
            "terms": "\n".join(sale.get("terms") or []),
            # Auction specifics (D42), edited on the auction-only panel.
            "auction_type": sale.get("auction_type") or "",
            "auction_channel": sale.get("auction_channel") or "",
            "auction_date": sale.get("auction_date") or "",
            "auction_time": sale.get("auction_time") or "",
            # Viewing (D76): the mode drives which control shows.
            "viewing_mode": ((sale.get("viewing") or {}).get("mode")
                             or ("by_arrangement" if (sale.get("viewing") or {}).get("by_appointment") is not False else "none")),
            "viewing_at": (sale.get("viewing") or {}).get("viewing_at") or "",
            "contact_email": marketing_pv.get("contact_email") or "",
            "contact_phone": marketing_pv.get("contact_phone") or "",
            # Sale terms and running costs (D80): read from the OTP and the levy
            # statement when those were uploaded, typed when they were not.
            "terms_view": _terms_view(record),
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
            # The advert's feature lines and the glyph each draws (D94).
            "feature_rows": _feature_rows(record),
            "icon_choices": _icon_choices(record, dp),
            "icon_styles": __import__("engine.render.ad_icons", fromlist=["x"]).STYLES,
            "icon_style": (record.marketing.icon_style if record.marketing else None) or "line",
            "photos": _photo_view(db_path, dp, record),
            "qr_src": _qr_view(db_path, dp, record),
            "max_photos": _MAX_PHOTOS_TOTAL,
            "stale": _is_stale(db_path, dp),
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
                # no-cache: the preview re-renders as the record/photos change, so
                # the browser must revalidate rather than serve a stale copy (a
                # cached copy also kept stale response headers, e.g. the old
                # X-Frame-Options, which had blocked the preview iframe).
                return FileResponse(
                    str(path),
                    media_type=art.get("mime") or "text/plain",
                    headers={"Cache-Control": "no-cache"},
                )
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
# The most photos a property may carry. An advert uses one lead photo plus up to
# six more (2 stacked + 4 gallery = 7 used); 8 leaves a small buffer and keeps
# the panel tidy. Uploads past this are rejected with a clear message.
_MAX_PHOTOS_TOTAL = 40  # owner's number (D70). Was 8, which rejected the second
# batch of a multi-folder upload and made named galleries impossible (D52).
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


def _save_qr(db_path: str, dp: str, rel: str, user: str) -> None:
    """Write the board's QR onto the record and re-render.

    On the RECORD, not through ``human_overrides``: an override is a human
    rewriting a sourced fact (a headline, a price), and it only surfaces through
    ``public_view``. An uploaded file is an asset, the same as a photograph, so
    it is stored the way ``apply_photos`` stores those - otherwise the gate-2
    panel, which reads the record, would keep asking for a code that is already
    attached.
    """
    store = _store(db_path)
    try:
        record = _load_record_for_write(store, dp)
        if record.marketing is None:
            from engine.schema import Marketing

            record.marketing = Marketing()
        record.marketing.qr_code = rel
        store.upsert(record)
        store.record_signoff(dp, gate="2", user=user, note=f"board QR attached: {rel}")
        # No render here either (D72): the board picks the QR up on Regenerate.
    finally:
        store.close()
    _mark_stale(db_path, dp, "QR code")


def _load_record_for_write(store, dp: str):
    record = store.get(dp)
    if record is None:
        raise HTTPException(status_code=404, detail="No record for this DP.")
    return record


def _qr_view(db_path: str, dp: str, record: PropertyRecord) -> Optional[str]:
    """The URL of the board's QR code, or None while the team has not added one.

    Served through the same auth-gated photo route, so an uploaded QR is no more
    public than a property photograph.
    """
    marketing = getattr(record, "marketing", None)
    rel = getattr(marketing, "qr_code", None) if marketing else None
    if not rel:
        return None
    name = Path(rel).name
    return f"/gates/{dp}/ads/photos/{name}" if (_photos_dir(db_path, dp) / name).exists() else None


def _group_of(record: PropertyRecord, name: str) -> str:
    """The named group a photograph belongs to, or "" (1.4, D78)."""
    marketing = getattr(record, "marketing", None)
    for group, members in ((getattr(marketing, "photo_groups", None) or {}) if marketing else {}).items():
        if any(Path(m).name == name for m in (members or [])):
            return str(group)
    return ""


def _photo_view(db_path: str, dp: str, record: PropertyRecord) -> List[Dict[str, Any]]:
    """Panel view-model: one tile per photo (name, url, is_hero, low-res warning).

    Each tile carries the pixel size and a ``low_res`` flag (shorter side under
    ``_MIN_PHOTO_PX``) so the panel can warn on a soft image without blocking it.
    Dimensions that cannot be read leave ``low_res`` False (no false alarm).
    """
    photos_dir = _photos_dir(db_path, dp)
    ad_pick = [Path(n).name for n in ((record.marketing.ad_photos if record.marketing else None) or [])]
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
                "group": _group_of(record, name),
                # The record lists it, the disk does not have it (D81). The
                # renderer skips these, so without saying so the panel shows a
                # blank tile - and if it is the lead, a blank tile wearing the
                # LEAD badge - while the advert quietly renders with fewer
                # photographs than the marketer believes they chose.
                "missing": not (photos_dir / name).is_file(),
                # Which advert slot this photograph holds, 1-based (D90); None
                # when it is not picked. Shown on the tile so the marketer can
                # see the advert's four at a glance among twenty-eight.
                "ad_slot": (ad_pick.index(name) + 1) if name in ad_pick else None,
            }
        )
    return tiles


def _save_photos(db_path: str, dp: str, full: List[str], user: str) -> None:
    """Persist an ordered photo list (hero = first).

    NOTHING is rendered here (D72): on the add-photos step the single render
    happens on "continue", and at gate 2 the change is batched and the artifacts
    marked stale for the explicit Regenerate. This docstring used to describe the
    pre-D72 behaviour and claimed a render the code had stopped doing, which is
    the same trap the "Save and re-render" button fell into (D89, D93)."""
    hero = full[0] if full else None
    gallery = full[1:] if len(full) > 1 else []
    store = _store(db_path)
    try:
        state = store.get_state(dp)
        if state == "photos":
            apply_photos(dp, store, hero, gallery, user, output_root=_output_root(db_path), render=False)
        else:
            apply_photos(dp, store, hero, gallery, user,
                         output_root=_output_root(db_path), formats=[])
    finally:
        store.close()
    _mark_stale(db_path, dp, "photos")


def _photo_result(request: Request, db_path: str, dp: str, toast: Dict[str, Any]):
    """Action response: refresh the photos panel (primary swap) and, on gate 2,
    the rendered-adverts gallery out-of-band. On the add-photos step there is no
    gallery and nothing is rendered yet, so we skip building it (which would
    otherwise trigger a render) and just return the panel."""
    record = _load(db_path, dp)
    tiles = [] if _state(db_path, dp) == "photos" else _gallery(db_path, dp)
    return templates.TemplateResponse(
        request,
        "partials/_gate2_photo_result.html",
        {"dp": dp, "tiles": tiles, "photos": _photo_view(db_path, dp, record),
         "qr_src": _qr_view(db_path, dp, record),
            "max_photos": _MAX_PHOTOS_TOTAL, "toast": toast},
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
        if len(full) >= _MAX_PHOTOS_TOTAL:  # already at the per-property limit
            rejected += 1
            continue
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
            text = f"{added} photo(s) added."
            if rejected:
                text += f" {rejected} skipped (limit is {_MAX_PHOTOS_TOTAL}, or not an image / too large)."
            toast = {"tone": "ok", "title": "Photos added", "text": text}
        except Exception as exc:  # a render backend failure (e.g. Canva quota)
            toast = {"tone": "block", "title": "Upload failed",
                     "text": f"The photos could not be saved ({type(exc).__name__})."}
    elif len(full) >= _MAX_PHOTOS_TOTAL:
        toast = {"tone": "note", "title": "Photo limit reached",
                 "text": f"This property already has the maximum of {_MAX_PHOTOS_TOTAL} photos. Remove one to add another."}
    else:
        toast = {"tone": "note", "title": "No photos added",
                 "text": "Only image files (jpg, png, webp, gif) up to 12 MB are accepted."}
    return _photo_result(request, db_path, dp, toast)


@router.post("/{dp}/ads/qr/upload", response_class=HTMLResponse)
async def gate2_qr_upload(
    dp: str, request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_role("approver", "marketing")),
):
    """Attach the auction board's QR code (D69).

    The team generates the code themselves - in GoHighLevel, so a scan lands on a
    tracked page - because GoHighLevel's QR generator is a page-builder element
    and not an API the engine can call. So the platform's job is to ASK for it,
    hold it on the record and print it on the board. Stored beside the photos and
    kept out of the photo list, so it never lands in a gallery or on an advert.
    """
    db_path = _db(request)
    raw = await file.read()
    toast = None
    name = _safe_photo_name(file.filename or "qr")
    ext = Path(name).suffix.lower()
    if ext not in _IMAGE_MIME:
        derived = _CTYPE_EXT.get((file.content_type or "").lower())
        if derived is None:
            toast = {"tone": "note", "title": "Not an image",
                     "text": "The QR code must be a jpg, png, webp or gif."}
        else:
            name += derived
    elif not raw or len(raw) > _MAX_PHOTO_BYTES:
        toast = {"tone": "note", "title": "QR code not saved",
                 "text": "The file is empty or larger than the 12 MB limit."}

    if toast is None:
        photos_dir = _photos_dir(db_path, dp)
        photos_dir.mkdir(parents=True, exist_ok=True)
        dest = photos_dir / f"qr{Path(name).suffix.lower()}"
        dest.write_bytes(raw)
        _reopen_if_live(db_path, dp, user["email"])
        _save_qr(db_path, dp, f"photos/{dest.name}", user["email"])
        toast = {"tone": "ok", "title": "QR code added",
                 "text": "The auction board now carries it."}
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
        toast = {"tone": "ok", "title": "Lead photo set", "text": "Press Regenerate to rebuild the adverts with the new lead image."}
    else:
        toast = {"tone": "note", "title": "No change",
                 "text": "That photo is already the lead, or was not found."}
    return _photo_result(request, db_path, dp, toast)


# An advert shows four photographs. More than that is not a bigger advert, it is
# a smaller one per picture, so the pick is capped where the designs stop.
_MAX_AD_PHOTOS = 4


# The glyphs a marketer may choose for a feature line (D94). Names shared with
# the pack's set where one exists, so a single pick drives both surfaces; the
# four that are advert-only fall back to the pack's own rules there.
def _icon_choices(record: "PropertyRecord | None" = None, dp: str = "") -> List[Dict[str, Any]]:
    """The picker's tiles: name, label, group and the glyph's own drawing (D95).

    The drawing comes from the same module the advert renders from, so the
    picture in the picker IS the picture that prints - a dropdown of words could
    not show that, which is why choosing an icon meant guessing.
    """
    from markupsafe import Markup
    from engine.render import ad_icons

    marketing = (record.marketing if record is not None else None)
    style = (marketing.icon_style if marketing else None) or "line"
    tiles = [
        {"name": name, "label": label, "group": group,
         "svg": Markup(ad_icons.svg(name, style=style)) if name else "", "url": ""}
        for name, label, group in ad_icons.CHOICES
    ]
    # The team's own glyphs sit at the end, drawn from the file itself.
    for label in sorted((marketing.custom_icons if marketing else None) or {}):
        tiles.append({"name": f"custom:{label}", "label": label, "group": "Yours",
                      "svg": "", "url": f"/gates/{dp}/ads/icons/{label}"})
    return tiles


def _valid_icon_names() -> set:
    """Glyph names the picker offers, so a crafted post cannot store another."""
    from engine.render import ad_icons

    return set(ad_icons.NAMES)


def _feature_rows(record: PropertyRecord) -> List[Dict[str, Any]]:
    """The advert's feature lines with the glyph each currently draws (D94)."""
    from engine.render.html_backend import _ad_features, _fmt_num

    public = record.public_view()
    physical = public.get("physical") or {}
    picks = ((record.marketing.feature_icons if record.marketing else None) or {})
    lines = _ad_features(
        list(physical.get("features_main") or []) + list(physical.get("features_complex") or []),
        beds=_fmt_num(physical.get("bedrooms")),
        baths=_fmt_num(physical.get("bathrooms_main_unit")),
        garages=_fmt_num(physical.get("garages")),
    )
    return [{"text": line, "pick": picks.get(line, "")} for line in lines]


_MAX_CUSTOM_ICONS = 12
_ICON_SUFFIXES = {".svg", ".png", ".webp"}
_MAX_ICON_BYTES = 512 * 1024


def _icons_dir(db_path: str, dp: str) -> Path:
    return Path(_output_root(db_path)) / f"DP{dp}" / "icons"


@router.get("/{dp}/ads/icons/{name}")
def gate2_custom_icon(dp: str, name: str, request: Request,
                      user: dict = Depends(require_role("approver", "marketing"))):
    """Serve an uploaded glyph for the picker. Auth-gated like the photos."""
    record = _load(_db(request), dp)
    stored = ((record.marketing.custom_icons if record.marketing else None) or {}).get(name)
    path = _icons_dir(_db(request), dp) / Path(stored or name).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such icon.")
    return FileResponse(str(path))


@router.post("/{dp}/ads/icons/upload", response_class=HTMLResponse)
async def gate2_upload_icon(dp: str, request: Request,
                            file: UploadFile = File(...),
                            user: dict = Depends(require_role("approver", "marketing"))):
    """Upload a glyph of the team's own for this property (D96).

    SVG is the useful format (it scales and takes the advert's colour), so it is
    allowed - but an uploaded SVG can carry script, so it is only ever drawn
    through an ``<img>``, where that script does not run. Never inlined.
    """
    db_path = _db(request)
    raw = await file.read()
    suffix = Path(file.filename or "").suffix.lower()
    label = Path(file.filename or "icon").stem.strip()[:28] or "icon"

    if suffix not in _ICON_SUFFIXES:
        toast = {"tone": "note", "title": "Not an icon file",
                 "text": "Upload an SVG, PNG or WebP. SVG is best: it scales and takes the advert's colour."}
        return _photo_result(request, db_path, dp, toast)
    if len(raw) > _MAX_ICON_BYTES:
        toast = {"tone": "note", "title": "Icon too large",
                 "text": f"An icon should be a small drawing, under {_MAX_ICON_BYTES // 1024}KB."}
        return _photo_result(request, db_path, dp, toast)

    record = _load(db_path, dp)
    existing = dict((record.marketing.custom_icons if record.marketing else None) or {})
    if label not in existing and len(existing) >= _MAX_CUSTOM_ICONS:
        toast = {"tone": "note", "title": "That is enough icons",
                 "text": f"{_MAX_CUSTOM_ICONS} uploaded already. Remove one first."}
        return _photo_result(request, db_path, dp, toast)

    icons = _icons_dir(db_path, dp)
    icons.mkdir(parents=True, exist_ok=True)
    stored = re.sub(r"[^A-Za-z0-9._-]", "_", label) + suffix
    (icons / stored).write_bytes(raw)
    existing[label] = stored

    store = _store(db_path)
    try:
        rec = store.get(dp)
        if rec.marketing is None:
            from engine.schema import Marketing
            rec.marketing = Marketing()
        rec.marketing.custom_icons = existing
        store.upsert(rec, state=store.get_state(dp))
        store.record_signoff(dp, gate="edit", user=user["email"], note=f"icon uploaded: {label}")
    finally:
        store.close()
    _mark_stale(db_path, dp, "icons")
    return _photo_result(request, db_path, dp, {
        "tone": "ok", "title": "Icon uploaded",
        "text": f'"{label}" is now in the picker. Choose it on a line, then Regenerate.'})


@router.post("/{dp}/ads/icons", response_class=HTMLResponse)
async def gate2_feature_icons(dp: str, request: Request,
                              user: dict = Depends(require_role("approver", "marketing"))):
    """Set (or clear) the glyph for each advert feature line (D94).

    "Regenerate" is the empty choice: it drops the pick and lets the keyword
    rules choose again, which is also the repair when a record's wording changes
    and an old pick no longer suits it.
    """
    db_path = _db(request)
    form = await request.form()
    record = _load(db_path, dp)
    rows = {r["text"] for r in _feature_rows(record)}
    custom_names = {f"custom:{label}" for label in
                    ((record.marketing.custom_icons if record.marketing else None) or {})}

    picks: Dict[str, str] = {}
    for key, value in form.multi_items():
        if not key.startswith("icon:"):
            continue
        line = key[5:]
        # Only a line the advert actually prints, and only a glyph we offer.
        chosen = str(value).strip()
        if line in rows and (chosen in _valid_icon_names() or chosen in custom_names):
            picks[line] = str(value).strip()

    store = _store(db_path)
    try:
        rec = store.get(dp)
        if rec.marketing is None:
            from engine.schema import Marketing
            rec.marketing = Marketing()
        before = rec.marketing.feature_icons or {}
        rec.marketing.feature_icons = picks or None
        style = str(form.get("icon_style", "")).strip()
        if style in {n for n, _, _ in __import__("engine.render.ad_icons", fromlist=["x"]).STYLES}:
            rec.marketing.icon_style = style
        store.upsert(rec, state=store.get_state(dp))
        if before != (picks or None):
            store.record_signoff(dp, gate="edit", user=user["email"],
                                 note=f"feature icons: {picks or '(automatic)'}")
    finally:
        store.close()

    if picks:
        toast = {"tone": "ok", "title": "Icons set",
                 "text": f"{len(picks)} chosen. Press Regenerate to rebuild the artifacts."}
    else:
        toast = {"tone": "ok", "title": "Icons back to automatic",
                 "text": "The wording picks the icon again. Press Regenerate to rebuild."}
    _reopen_if_live(db_path, dp, user["email"])
    _mark_stale(db_path, dp, "icons")
    return _photo_result(request, db_path, dp, toast)


@router.post("/{dp}/ads/photos/onad", response_class=HTMLResponse)
async def gate2_photo_on_ad(dp: str, request: Request,
                            user: dict = Depends(require_role("approver", "marketing"))):
    """Toggle whether a photograph appears on the ADVERTS (D90).

    Before this the adverts took the lead plus the next three in gallery order,
    so choosing the fourth-best photo of 28 meant dragging it to the front - and
    that reorders the information pack's gallery as a side effect. The pick is
    its own list: the pack still shows every photograph, in its own order.

    An empty pick clears back to the default, so a marketer can always get back
    to "just use the first few" by unticking everything.
    """
    db_path = _db(request)
    form = await request.form()
    name = Path(str(form.get("name", ""))).name
    record = _load(db_path, dp)
    known = {Path(p).name for p in _photo_list(record)}
    if name not in known:
        return _photo_result(request, db_path, dp, {
            "tone": "note", "title": "No change",
            "text": "That photograph is not on this property."})

    current = [Path(n).name for n in ((record.marketing.ad_photos if record.marketing else None) or [])]
    if name in current:
        chosen = [n for n in current if n != name]
        toast = {"tone": "ok", "title": "Removed from the advert",
                 "text": f"{len(chosen) or 'No'} photo(s) picked. Press Regenerate to rebuild."}
    else:
        if len(current) >= _MAX_AD_PHOTOS:
            return _photo_result(request, db_path, dp, {
                "tone": "note", "title": f"That is {_MAX_AD_PHOTOS} already",
                "text": f"An advert shows {_MAX_AD_PHOTOS} photographs. Remove one first."})
        chosen = current + [name]
        toast = {"tone": "ok", "title": "Added to the advert",
                 "text": f"{len(chosen)} of {_MAX_AD_PHOTOS} picked. Press Regenerate to rebuild."}

    store = _store(db_path)
    try:
        rec = store.get(dp)
        if rec.marketing is None:
            from engine.schema import Marketing
            rec.marketing = Marketing()
        rec.marketing.ad_photos = chosen or None
        store.upsert(rec, state=store.get_state(dp))
        store.record_signoff(dp, gate="edit", user=user["email"],
                             note=f"advert photos: {', '.join(chosen) if chosen else '(default)'}")
    finally:
        store.close()
    _reopen_if_live(db_path, dp, user["email"])
    _mark_stale(db_path, dp, "ad photos")
    return _photo_result(request, db_path, dp, toast)


@router.post("/{dp}/ads/photos/group", response_class=HTMLResponse)
async def gate2_photo_group(
    dp: str, request: Request,
    user: dict = Depends(require_role("approver", "marketing")),
):
    """Put one photograph in a named group (fix list 1.4, D78).

    Groups are the buildings on the property - "Main House", "Second House",
    "Greenhouses" - and the pack's gallery prints each under its own heading.
    Names are free text: no fixed vocabulary survives contact with a farm.

    A blank name removes the photo from every group, which is how a mistake is
    undone. Groups that end up empty are dropped, so the record never carries a
    heading with nothing under it.
    """
    db_path = _db(request)
    form = await request.form()
    name = Path(str(form.get("name", ""))).name
    group = " ".join(str(form.get("group", "")).split())[:40]

    record = _load(db_path, dp)
    known = {Path(rel).name for rel in _photo_list(record)}
    if name not in known:
        return _photo_result(request, db_path, dp,
                             {"tone": "note", "title": "Photo not found", "text": ""})

    groups = dict((record.marketing.photo_groups or {}) if record.marketing else {})
    for key in list(groups):
        groups[key] = [m for m in groups[key] if Path(m).name != name]
    if group:
        groups.setdefault(group, []).append(f"photos/{name}")
    groups = {k: v for k, v in groups.items() if v}          # no empty headings

    _reopen_if_live(db_path, dp, user["email"])
    _save_edits(db_path, dp, {"marketing.photo_groups": groups or None}, user["email"])
    return _photo_result(request, db_path, dp,
                         {"tone": "ok", "title": "Group updated",
                          "text": f"{name} is in \u201c{group}\u201d." if group
                                  else f"{name} is no longer grouped."})


@router.post("/{dp}/ads/photos/order", response_class=HTMLResponse)
async def gate2_photo_order(
    dp: str, request: Request,
    user: dict = Depends(require_role("approver", "marketing")),
):
    """Reorder the photographs by drag and drop (fix list 2.4, D78).

    Order is not decoration: the FIRST photo is the lead on every advert and the
    cover of the pack, and the rest fill the gallery in sequence. Until now the
    only way to change it was the "Lead" button, which could promote one photo
    but never say which should be second.

    The posted order is validated against what the record actually holds - a
    name that is not on the record is ignored, and anything the browser left out
    is appended in its existing order - so a stale panel or a crafted POST can
    neither invent a photograph nor silently drop one.
    """
    db_path = _db(request)
    form = await request.form()
    posted = [Path(n).name for n in form.getlist("name")]

    current = _photo_list(_load(db_path, dp))
    by_name = {Path(rel).name: rel for rel in current}
    ordered = [by_name[n] for n in posted if n in by_name]
    ordered += [rel for rel in current if rel not in ordered]   # never lose one

    if ordered == current:
        return _photo_result(request, db_path, dp,
                             {"tone": "note", "title": "Order unchanged", "text": ""})

    _reopen_if_live(db_path, dp, user["email"])
    _save_photos(db_path, dp, ordered, user["email"])
    return _photo_result(request, db_path, dp,
                         {"tone": "ok", "title": "Order saved",
                          "text": f"{Path(ordered[0]).name} is the lead photo."})


@router.post("/{dp}/ads/photos/replace", response_class=HTMLResponse)
async def gate2_photo_replace(
    dp: str, request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    user: dict = Depends(require_role("approver", "marketing")),
):
    """Swap one photograph, keeping its PLACE in the list (fix list 2.5).

    Remove-and-re-upload appends to the end, so replacing the lead photo made it
    no longer the lead and quietly reordered the gallery underneath the marketer.
    The new file takes the old one's index, and the old file is left on disk: it
    costs nothing and an undo is then a re-upload rather than a lost photograph.
    """
    db_path = _db(request)
    full = _photo_list(_load(db_path, dp))
    target = f"photos/{Path(name).name}"
    if target not in full:
        return _photo_result(request, db_path, dp,
                             {"tone": "note", "title": "Photo not found",
                              "text": "It may already have been removed."})

    raw = await file.read()
    new_name = _safe_photo_name(file.filename or "photo")
    ext = Path(new_name).suffix.lower()
    if ext not in _IMAGE_MIME:
        derived = _CTYPE_EXT.get((file.content_type or "").lower())
        if derived is None:
            return _photo_result(request, db_path, dp,
                                 {"tone": "note", "title": "Not an image",
                                  "text": "Use a jpg, png, webp or gif."})
        new_name += derived
    if not raw or len(raw) > _MAX_PHOTO_BYTES:
        return _photo_result(request, db_path, dp,
                             {"tone": "note", "title": "Not replaced",
                              "text": "The file is empty or over the 12 MB limit."})

    photos_dir = _photos_dir(db_path, dp)
    photos_dir.mkdir(parents=True, exist_ok=True)
    dest = photos_dir / new_name
    stem, suffix, n = dest.stem, dest.suffix, 1
    while dest.exists():
        dest = photos_dir / f"{stem}_{n}{suffix}"
        n += 1
    dest.write_bytes(raw)

    full[full.index(target)] = f"photos/{dest.name}"      # same index, new file
    _reopen_if_live(db_path, dp, user["email"])
    _save_photos(db_path, dp, full, user["email"])
    was_lead = full.index(f"photos/{dest.name}") == 0
    return _photo_result(request, db_path, dp,
                         {"tone": "ok", "title": "Photo replaced",
                          "text": ("The lead photo was swapped; it is still the lead."
                                   if was_lead else "It kept its place in the gallery.")})


@router.post("/{dp}/ads/photos/delete", response_class=HTMLResponse)
async def gate2_photo_delete(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    form = await request.form()
    name = Path(str(form.get("name", ""))).name
    current = _photo_list(_load(db_path, dp))
    full = [p for p in current if Path(p).name != name]
    if not full and current:
        # The min-1 rule holds for the life of the listing, not just at the
        # photos step: removing the last photo would silently re-render a
        # photo-less advert that could then be approved and posted.
        toast = {
            "tone": "note", "title": "The last photo cannot be removed",
            "text": "Every advert needs at least one photo. Upload a replacement "
                    "first, then remove this one.",
        }
    elif len(full) != len(current):
        _reopen_if_live(db_path, dp, user["email"])
        _save_photos(db_path, dp, full, user["email"])
        toast = {"tone": "ok", "title": "Photo removed", "text": "Press Regenerate to rebuild the adverts without it."}
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
    # (auction_channel is validated against an allow-list separately, below.)
    "auction_type": "sale_process.auction_type",
    "auction_date": "sale_process.auction_date",
    "auction_time": "sale_process.auction_time",
    # The viewing window, when the mode is "set_time" (D76). Free display text,
    # like the auction date, so it reads exactly as the marketer wants it.
    "viewing_at": "sale_process.viewing.viewing_at",
    # Per-property contacts (2.9, D78): the client asked for the ad email to be
    # typed rather than auto-populated, because different properties are handled
    # by different people. Blank falls back to the brand values.
    "contact_email": "marketing.contact_email",
    "contact_phone": "marketing.contact_phone",
    # Sale terms typed by hand when there is no OTP to read them from (D80).
    # Free text because the document's own wording varies ("on the fall of the
    # hammer", "on signature date", "within 48 hours of acceptance").
    "deposit_due": "sale_process.otp.deposit_due",
}

# Terms and running costs that are NUMBERS. Separate from the text fields
# because they must be stored as numbers: the pack formats a percentage with
# ``f"{value:g}"``, which raises on a string, so a typed "20" saved verbatim
# would render the whole information pack unbuildable (D80).
#
#   form field -> (public-view path, python type, minimum, maximum)
#
# The bounds are sanity rails, not business rules: they exist so a slipped
# keystroke ("200" for "20", "2026" for "20") is refused rather than printed on
# a document a buyer relies on. A value outside them leaves the field untouched
# and is named in the toast.
_EDIT_NUMBER_FIELDS = {
    "deposit_pct": ("sale_process.otp.deposit_pct", float, 0.0, 100.0),
    "commission_pct": ("sale_process.otp.commission_pct", float, 0.0, 100.0),
    "guarantee_days": ("sale_process.otp.guarantee_days", int, 0, 365),
    "confirmation_days": ("sale_process.otp.confirmation_days", int, 0, 365),
    "monthly_rates": ("valuation.estimated_monthly_rates", float, 0.0, 10_000_000.0),
    "monthly_levy": ("valuation.monthly_levy", float, 0.0, 10_000_000.0),
}

# Terms that are a fixed choice. Validated against the allow-list like the sale
# method, so a crafted POST cannot store a value the select cannot show back.
_PAYER_CHOICES = ("seller", "purchaser")
_EDIT_CHOICE_FIELDS = {
    "commission_payable_by": ("sale_process.otp.commission_payable_by", _PAYER_CHOICES),
    "outstanding_payable_by": ("sale_process.otp.outstanding_payable_by", _PAYER_CHOICES),
}

# Yes / no terms, posted as "yes" / "no" / "" so the three states stay distinct:
# VAT applies, VAT does not apply, and the document did not say.
_EDIT_BOOL_FIELDS = {
    "commission_vat": "sale_process.otp.commission_vat",
}

# The human labels the toast uses when it names a value it would not accept.
_FIELD_LABELS = {
    "deposit_pct": "Deposit %",
    "commission_pct": "Commission %",
    "guarantee_days": "Guarantee (days)",
    "confirmation_days": "Confirmation (days)",
    "monthly_rates": "Monthly rates",
    "monthly_levy": "Monthly levy",
}
# The three viewing states (fix list 4.6). Validated like the sale method so a
# crafted POST cannot store an off-list value that then fails to round-trip.
_VIEWING_MODES = ("by_arrangement", "set_time", "none")
_SALE_METHODS = ("offers_invited", "auction")
_AUCTION_CHANNELS = ("Online", "On-site")


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


def _parse_number(raw: str, kind, low, high):
    """Read a typed figure, or ``None`` when it is not one we will print.

    Accepts what a person actually types into a money or percentage box: spaces
    and thousands separators ("1 250", "1,250"), a leading R or a trailing %,
    and a comma decimal ("7,5" - SA keyboards). Returns ``None`` for anything
    unparseable or out of range, and the caller leaves that field untouched and
    names it in the toast, because silently storing a misread figure is how a
    wrong deposit reaches a buyer's information pack.
    """
    text = re.sub(r"[\s R%]", "", str(raw), flags=re.I)
    # A comma is a decimal point here ("7,5") EXCEPT when it groups thousands
    # ("1,250"), which is a comma followed by exactly three digits. Getting this
    # backwards turns R1 250 of rates into R1.25, which is the kind of wrong that
    # looks entirely plausible on the page.
    if "." in text:
        text = text.replace(",", "")           # the dot is the decimal point
    elif re.fullmatch(r"-?\d{1,3}(,\d{3})+", text):
        text = text.replace(",", "")           # pure thousands grouping
    else:
        text = text.replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return None
    if not (low <= value <= high):
        return None
    return int(round(value)) if kind is int else round(value, 2)


def _collect_typed_fields(form, full: bool, rejected: list) -> dict:
    """The typed sale-terms and running-cost fields (D80).

    Kept apart from the text fields because each needs its own coercion: a
    number has to be stored AS a number (the pack formats it with ``:g``), a
    choice is validated against its allow-list, and a yes/no keeps three states
    so "the document did not say" stays different from "no".
    """
    fields: dict = {}
    for name, (path, kind, low, high) in _EDIT_NUMBER_FIELDS.items():
        if name not in form:
            continue
        raw = str(form.get(name, "")).strip()
        if not raw:
            # Blank in a complete form is a deliberate clear; in a partial POST
            # it keeps the safer rule that blank never wipes.
            if full:
                fields[path] = None
            continue
        value = _parse_number(raw, kind, low, high)
        if value is None:
            rejected.append(_FIELD_LABELS.get(name, name))
            continue
        fields[path] = value
    for name, (path, choices) in _EDIT_CHOICE_FIELDS.items():
        if name not in form:
            continue
        value = str(form.get(name, "")).strip().lower()
        if value in choices:
            fields[path] = value
        elif full and not value:
            fields[path] = None
    for name, path in _EDIT_BOOL_FIELDS.items():
        if name not in form:
            continue
        value = str(form.get(name, "")).strip().lower()
        if value in ("yes", "no"):
            fields[path] = value == "yes"
        elif full and not value:
            fields[path] = None
    return fields


def _collect_edit_fields(
    form,
    current_template_set: "str | None" = None,
    prefill: "Dict[str, str] | None" = None,
    rejected: "list | None" = None,
) -> dict:
    """Build the public-view edit map from a submitted form (non-empty only).

    A blank text input is skipped so it never wipes an existing value. ``terms``
    is a textarea, one term per line, stored as a list. The design pick is
    included only when it actually CHANGES the record: a select always submits
    a value, and without the change guard every save would silently pin the
    default set's name onto a record that never chose one (a false audit row,
    and the record would stop following a future default change). A submitted
    blank ("follow the default") clears an existing pick back to None.

    ``prefill`` carries the values the form was filled with (Headline / Price,
    see ``_gate2_prefill``). A value returned exactly as it was shown is not an
    edit, so it is skipped: storing it would pin derived copy onto the record as
    a human override, and the listing would stop following its own facts.
    """
    fields: dict = {}
    prefill = prefill or {}
    # The gate-2 form posts every field and marks itself complete, so an empty
    # box in it is a deliberate CLEAR - emptying the Sale callout has to actually
    # remove the callout, or the badge keeps rendering the old word. A partial
    # POST (no marker) keeps the safer rule that blank never wipes, so a
    # half-built or crafted request cannot empty a live listing.
    full = str(form.get("_full_form", "")).strip() == "1"
    for name, path in _EDIT_TEXT_FIELDS.items():
        if name not in form:
            continue
        value = str(form.get(name, "")).strip()
        # A value returned exactly as it was shown is not an edit (see above).
        if value and value == (prefill.get(name) or "").strip():
            continue
        if not value and not full:
            continue
        # apply_edits compares against the current public value and skips genuine
        # no-ops, so clearing an already-empty field writes nothing.
        fields[path] = value or None
    method = str(form.get("method", "")).strip()
    if method in _SALE_METHODS:
        fields["sale_process.method"] = method
    # Auction channel is a fixed choice, validated like method so a crafted POST
    # cannot store an off-list value that then fails to round-trip in the select.
    mode = str(form.get("viewing_mode", "")).strip()
    if mode in _VIEWING_MODES:
        fields["sale_process.viewing.mode"] = mode
        # Keep the old boolean consistent with the mode, so any surface still
        # reading it (and every record written before the mode existed) agrees
        # with the one the marketer chose.
        fields["sale_process.viewing.by_appointment"] = mode != "none"
    channel = str(form.get("auction_channel", "")).strip()
    if channel in _AUCTION_CHANNELS:
        fields["sale_process.auction_channel"] = channel
    elif full and "auction_channel" in form and not channel:
        fields["sale_process.auction_channel"] = None
    # The design pick (D33). Only names the picker actually offers are
    # accepted, so a crafted value cannot land on the record.
    if "template_set" in form:
        posted = str(form.get("template_set", "")).strip()
        current = (current_template_set or "").strip()
        offered = _design_sets()
        if posted != current and offered and (not posted or posted in offered):
            fields["marketing.template_set"] = posted
    if "terms" in form:
        terms_raw = str(form.get("terms", "")).strip()
        lines = [line.strip() for line in terms_raw.splitlines() if line.strip()]
        # Emptying the terms box clears the strip, same rule as the text fields.
        if lines or full:
            fields["sale_process.terms"] = lines or None
    # The typed sale terms and running costs (D80), each coerced to its own type.
    fields.update(_collect_typed_fields(form, full, rejected if rejected is not None else []))
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
        # formats=[] applies the edit WITHOUT rendering (D72); the explicit
        # Regenerate does the render once, for however many edits were made.
        apply_edits(dp, store, fields, user, output_root=_output_root(db_path), formats=[])
    finally:
        store.close()
    _mark_stale(db_path, dp, "edits")


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
    # Figures the parser would not accept (D80). Everything else still saves:
    # refusing the whole form over one mistyped percentage would lose the
    # marketer's other edits, so the bad value is named and left as it was.
    rejected: List[str] = []
    fields = _collect_edit_fields(
        form,
        current_template_set=current_pick,
        prefill=_gate2_prefill(record),
        rejected=rejected,
    )
    if fields:
        # Only a real edit reopens a live listing into the update cycle.
        _reopen_if_live(db_path, dp, user["email"])
        try:
            _save_edits(db_path, dp, fields, user["email"])
            toast = {"tone": "ok", "title": "Saved", "text": "Press Regenerate to rebuild the artifacts with your edits."}
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
    if rejected:
        # Said plainly and last, so it is not buried under a green "Saved":
        # a figure the marketer believes they entered is not on the record.
        toast = {
            "tone": "block",
            "title": "Not saved: " + ", ".join(rejected),
            "text": (
                "That is not a figure we will print. Type a plain number "
                "(20, 7.5, 1250) within a sensible range; the field is unchanged."
                + (" Your other changes were saved." if fields else "")
            ),
        }
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
    # no-cache so a regenerated design thumbnail (after a template change) is
    # revalidated rather than served stale from the browser cache.
    return FileResponse(str(png), media_type="image/png", headers={"Cache-Control": "no-cache"})


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
        # The ONE gate-2 action that still renders on the click (D72): a design
        # is picked in order to look at it, so batching it behind Regenerate
        # would mean choosing a design you cannot see. It is the ad only,
        # measured at ~1.4s, and the copy cache means no model call (D56).
        _save_edits(db_path, dp, {"marketing.template_set": value}, user["email"])
        _render(db_path, dp, formats=[AD_FORMAT])
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


@router.post("/{dp}/ads/regenerate", response_class=HTMLResponse)
def gate2_regenerate(dp: str, request: Request,
                     user: dict = Depends(require_role("approver", "marketing"))):
    """Render everything the batched edits changed, once (D72).

    Gate-2 actions no longer render on each click; they mark the artifacts stale.
    This is the button that does the work - one render for however many changes
    were made, instead of one per click at up to 5 seconds each.
    """
    db_path = _db(request)
    if not _is_stale(db_path, dp):
        toast = {"tone": "note", "title": "Nothing to regenerate",
                 "text": "The artifacts already match the record."}
    else:
        try:
            _render_if_stale(db_path, dp)
            toast = {"tone": "ok", "title": "Regenerated",
                     "text": "Every artifact now matches the record."}
        except Exception as exc:
            toast = {"tone": "block", "title": "Re-render failed",
                     "text": f"The changes are saved, but rendering failed ({type(exc).__name__})."}
    return _photo_result(request, db_path, dp, toast)


@router.post("/{dp}/ads/approve")
def gate2_approve(dp: str, request: Request, user: dict = Depends(require_role("approver", "marketing"))):
    db_path = _db(request)
    # The one risk batching introduces: approving a pack the edits have not
    # reached yet. Pending changes are rendered HERE, before the sign-off, so a
    # marketer cannot approve yesterday's advert by forgetting a button (D72).
    _render_if_stale(db_path, dp)
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
