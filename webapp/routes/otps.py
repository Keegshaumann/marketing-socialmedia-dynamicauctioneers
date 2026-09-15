"""Offers to purchase (M10, D114): the screen the properties team generates an OTP on.

A separate place from the auction proposals (M9): its own records, its own
files under ``DP<dp>/otp/``, reached through the switcher at the top of both
screens. A proposal still takes an uploaded OTP; nothing here changes that.

The page collects what differs between OTPs (the seller and the kind of seller,
the property, the sale terms and any special conditions) and generate fills the
team's master OTP with it (``engine.otpgen``). Starting an OTP copies the facts
from this DP's auction proposal when there is one, else from its marketing
record; a Lightstone report can fill the rest.

Access: the ``properties`` role, and admin, which passes every role (D104). The
page shows the seller's name and number, which the rest of the platform keeps
off its screens.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, Form
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from engine.otpgen import checks, docx_build
from engine.otpgen.model import SELLER_HEADINGS, Otp, output_stem
from engine.otpgen.store import OtpStore
from engine.proposal.model import Erf, Proposal, normalise_dp, pct_text
from engine.proposal.store import ProposalStore
from webapp import auth, jobs, models
from webapp.routes import proposals as shared

router = APIRouter()
templates = shared.templates

_ROLES = ("properties",)  # the same people as the proposals (D104)
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MAX_EXTRA = 12

require = auth.require_role(*_ROLES)


# --- plumbing -------------------------------------------------------------------

def _files_root(db: str, dp: str) -> Path:
    return Path(shared._output_root(db)) / f"DP{dp}" / "otp"


def _store(db: str) -> OtpStore:
    return OtpStore(models.resolve_db_path(db))


def _load(db: str, raw_dp: str) -> Otp:
    dp = normalise_dp(raw_dp)
    if dp is None:
        raise HTTPException(status_code=404, detail="That is not a DP number.")
    with _store(db) as store:
        otp = store.get(dp)
    if otp is None:
        raise HTTPException(status_code=404, detail=f"There is no OTP for {dp}.")
    return otp


def _save(db: str, otp: Otp, user: Dict[str, Any]) -> None:
    with _store(db) as store:
        store.save(otp, user.get("email", ""))


def _back(request: Request, dp: str, anchor: str = "", notice: str = "") -> Response:
    # A fresh query string on every reload, as on the proposals (D107).
    url = f"/otps/{dp}?at={int(datetime.now(timezone.utc).timestamp() * 1_000_000)}"
    if notice:
        url += "&notice=" + quote(notice[:400])
    url += anchor
    if shared._is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def _days(value) -> str:
    return "" if value is None else str(value)


def _ctx(request: Request, user: Dict[str, Any], db: str, otp: Otp, **extra: Any) -> Dict[str, Any]:
    issues = checks.run(otp)
    prefill_job = shared._job(db, otp.prefill_job)
    ctx: Dict[str, Any] = {
        "request": request,
        "user": user,
        "o": otp,
        "p": otp,  # the shared checks and prefill partials call the record p
        "issues": issues,
        "blocking": [i for i in issues if i.level == "block"],
        "warnings": [i for i in issues if i.level == "warn"],
        "flagged": {i.field for i in issues if i.level == "block"},
        "headings": SELLER_HEADINGS,
        "prefill_job": prefill_job,
        "prefill_running": shared._running(prefill_job),
        "prefill_url": f"/otps/{otp.dp}/prefill-status",
        "has_key": bool(os.getenv("ANTHROPIC_API_KEY")),
        "form": {
            "deposit_pct": pct_text(otp.deposit_pct),
            "guarantee_days": _days(otp.guarantee_days),
            "interest_pct": pct_text(otp.interest_pct),
            "confirmation_days": _days(otp.confirmation_days),
            "commission_pct": pct_text(otp.commission_pct),
            "extra_conditions": "\n".join(otp.extra_conditions),
        },
        "notice": request.query_params.get("notice", "")[:400],
    }
    ctx.update(extra)
    return ctx


def _saved(request: Request, user: Dict[str, Any], db: str, otp: Otp, toast: Dict[str, str]) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/_otp_saved.html", _ctx(request, user, db, otp, toast=toast))


# --- a starting point ------------------------------------------------------------------

def _seed(db: str, otp: Otp) -> None:
    """Copy the facts this DP already has: its auction proposal first, else its marketing record."""
    with ProposalStore(models.resolve_db_path(db)) as store:
        source = store.get(otp.dp)
    note = "Copied from this DP's auction proposal. Check each field against the title deed."
    if source is None:
        source = Proposal(dp=otp.dp)
        shared._seed_from_record(db, source)
        if not source.prefill_note:
            return
        note = source.prefill_note
    if source.seller_capacity in SELLER_HEADINGS:
        otp.seller_capacity = source.seller_capacity
    otp.seller_name = source.seller_name
    otp.seller_id = source.seller_id
    otp.masters_ref = source.masters_ref
    otp.erven = [erf.model_copy() for erf in source.erven] or [Erf()]
    for field in ("deposit_pct", "confirmation_days", "commission_pct"):
        value = getattr(source.terms, field)
        if value is not None:
            setattr(otp, field, value)
    if source.terms.commission_payer in ("seller", "purchaser"):
        otp.commission_payer = source.terms.commission_payer
    if source.terms.commission_vat is not None:
        otp.commission_vat = source.terms.commission_vat
    otp.prefill_note = note


# --- the form ------------------------------------------------------------------------------

def _apply_form(o: Otp, form: Any) -> List[str]:
    """Copy the posted form onto the OTP. Returns messages for values it refused."""
    errors: List[str] = []
    if not form.get("_full_form"):
        return errors
    text = shared._text

    capacity = text(form, "seller_capacity")
    o.seller_capacity = capacity if capacity in SELLER_HEADINGS else ""
    o.seller_name = text(form, "seller_name", 300)
    o.seller_id = text(form, "seller_id", 60)
    o.masters_ref = text(form, "masters_ref", 60)
    o.erven = [
        Erf(
            legal_description=text(form, f"erf-{n}-legal"),
            known_as=text(form, f"erf-{n}-known_as", 300),
            title_deed=text(form, f"erf-{n}-title_deed", 40),
            extent=text(form, f"erf-{n}-extent", 40),
        )
        for n in range(1, shared._count(form, "erf_count", shared._MAX_ERVEN) + 1)
    ] or [Erf()]

    o.deposit_pct = shared._parse_pct(text(form, "deposit_pct", 10), "Deposit", errors)
    o.guarantee_days = shared._parse_days(text(form, "guarantee_days", 5), "Guarantee period", errors)
    o.interest_pct = shared._parse_pct(text(form, "interest_pct", 10), "Interest", errors)
    o.confirmation_days = shared._parse_days(text(form, "confirmation_days", 5), "Confirmation period", errors)
    o.commission_pct = shared._parse_pct(text(form, "commission_pct", 10), "Commission", errors)
    payer = text(form, "commission_payer")
    o.commission_payer = payer if payer in ("seller", "purchaser") else "seller"
    o.commission_vat = text(form, "commission_vat") != "no"

    o.body_corporate = text(form, "body_corporate", 200)
    lines = [line.strip() for line in text(form, "extra_conditions", 6000).split("\n") if line.strip()]
    if len(lines) > _MAX_EXTRA:
        errors.append(f"Only the first {_MAX_EXTRA} special conditions were kept.")
    o.extra_conditions = [line[:1000] for line in lines[:_MAX_EXTRA]]
    return errors


def _apply_action(o: Otp, action: str):
    if action == "add_erf":
        if len(o.erven) < shared._MAX_ERVEN:
            o.erven = [*o.erven, Erf()]
        return "#property"
    match = re.fullmatch(r"remove_erf_(\d+)", action)
    if match:
        index = int(match.group(1)) - 1
        if len(o.erven) > 1 and 0 <= index < len(o.erven):
            o.erven = [e for i, e in enumerate(o.erven) if i != index]
        return "#property"
    return None


# --- routes ------------------------------------------------------------------------------------

@router.get("/otps", response_class=HTMLResponse)
def otps_page(request: Request, user: dict = Depends(require)):
    with _store(shared._db(request)) as store:
        rows = store.list()
    return templates.TemplateResponse(
        request, "otps.html", {"request": request, "user": user, "rows": rows, "error": request.query_params.get("error", "")}
    )


@router.post("/otps/new")
async def otp_new(request: Request, dp: str = Form(""), user: dict = Depends(require)):
    clean = normalise_dp(dp)
    if clean is None:
        return RedirectResponse("/otps?error=dp", status_code=303)
    db = shared._db(request)
    with _store(db) as store:
        if store.get(clean) is None:
            otp = Otp(dp=clean)
            try:
                _seed(db, otp)
            except Exception:  # an unreadable proposal or record is no reason not to start
                otp = Otp(dp=clean)
            store.save(otp, user.get("email", ""))
    return RedirectResponse(f"/otps/{clean}", status_code=303)


@router.get("/otps/{dp}", response_class=HTMLResponse)
def otp_page(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    otp = _load(db, dp)
    if otp.dp != dp:
        return RedirectResponse(f"/otps/{otp.dp}", status_code=303)
    return templates.TemplateResponse(request, "otp_edit.html", _ctx(request, user, db, otp))


@router.post("/otps/{dp}/save")
async def otp_save(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    otp = _load(db, dp)
    form = await request.form()
    errors = _apply_form(otp, form)
    anchor = _apply_action(otp, str(form.get("action") or ""))
    _save(db, otp, user)
    if anchor is not None or not shared._is_htmx(request):
        return _back(request, otp.dp, anchor or "", notice=" ".join(errors))
    if errors:
        toast = {"tone": "note", "title": "Saved, but not everything", "text": " ".join(errors)}
    else:
        toast = {"tone": "ok", "title": "Saved", "text": "The checks have been run again."}
    return _saved(request, user, db, otp, toast)


@router.post("/otps/{dp}/upload/lightstone")
async def otp_upload(dp: str, request: Request, files: List[UploadFile] = File(default=[]), user: dict = Depends(require)):
    db = shared._db(request)
    otp = _load(db, dp)
    _apply_form(otp, await request.form())  # typing on the page is kept
    root = _files_root(db, otp.dp)
    notes: List[str] = []
    added: List[str] = []
    for upload in files[: shared._MAX_FILES]:
        name = shared._safe_name(upload.filename)
        if Path(name).suffix.lower() != ".pdf":
            notes.append(f"{name} was skipped: this takes .pdf files.")
            continue
        raw = await upload.read()
        if not raw or len(raw) > shared._MAX_BYTES:
            notes.append(f"{name} is empty or over 30 MB.")
            continue
        dest = shared._unique(root / "lightstone", name)
        dest.write_bytes(raw)
        added.append(f"lightstone/{dest.name}")

    if added:
        otp.lightstone_files = [*otp.lightstone_files, *added]
        if os.getenv("ANTHROPIC_API_KEY"):
            otp.prefill_job = jobs.enqueue(db, "otp_prefill", otp.dp, payload={"output_root": shared._output_root(db)})
        else:
            notes.append("Reading the report needs the Anthropic key, which is not set here, so type the seller and property in.")
    elif not notes:
        notes.append("No file was added.")
    _save(db, otp, user)
    return _back(request, otp.dp, "#documents", notice=" ".join(notes))


@router.post("/otps/{dp}/remove/lightstone/{index}")
async def otp_remove(dp: str, index: int, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    otp = _load(db, dp)
    _apply_form(otp, await request.form())
    if not 0 <= index < len(otp.lightstone_files):
        raise HTTPException(status_code=404, detail="Nothing to remove.")
    otp.lightstone_files = [f for i, f in enumerate(otp.lightstone_files) if i != index]
    _save(db, otp, user)
    return _back(request, otp.dp, "#documents")


@router.post("/otps/{dp}/generate")
async def otp_generate(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    otp = _load(db, dp)
    errors = _apply_form(otp, await request.form())
    if checks.blocking(checks.run(otp)) or errors:
        _save(db, otp, user)
        text = " ".join(errors) if errors else "Fix the items under Checks first."
        return _saved(request, user, db, otp, {"tone": "block", "title": "Not generated", "text": text})

    root = _files_root(db, otp.dp)
    rel = f"out/{output_stem(otp)}.docx"
    try:
        docx_build.build(otp, root / rel)
    except Exception as exc:
        _save(db, otp, user)
        return _saved(request, user, db, otp, {
            "tone": "block", "title": "Not generated", "text": f"The Word file could not be built: {exc}",
        })
    otp.docx_file = rel
    otp.generated_at = shared._now()
    otp.generated_by = user.get("email", "")
    _save(db, otp, user)
    return _saved(request, user, db, otp, {
        "tone": "ok", "title": "OTP generated", "text": "The Word file is ready to download.",
    })


@router.get("/otps/{dp}/download")
def otp_download(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    otp = _load(db, dp)
    path = _files_root(db, otp.dp) / otp.docx_file if otp.docx_file else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Generate the OTP first.")
    return FileResponse(str(path), media_type=_DOCX_MIME, filename=f"{output_stem(otp)}.docx")


@router.get("/otps/{dp}/prefill-status", response_class=HTMLResponse)
def otp_prefill_status(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    otp = _load(db, dp)
    if not shared._running(shared._job(db, otp.prefill_job)):
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "partials/_proposal_prefill.html", _ctx(request, user, db, otp))


@router.post("/otps/{dp}/delete")
async def otp_delete(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    otp = _load(db, dp)
    with _store(db) as store:
        store.delete(otp.dp)
    if shared._is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": "/otps"})
    return RedirectResponse("/otps", status_code=303)
