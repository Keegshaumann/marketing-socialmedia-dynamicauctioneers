"""Auction proposals (M9, D103): the screen the properties team builds a proposal on.

A proposal goes to the liquidator, trustee or executor before an auction is
approved. The page collects the facts (copied from the marketing record when the
DP is already on the board, read from a Lightstone report when one is uploaded,
typed when neither), the budget the liquidator set, the deeds page, marketing's
draft advert and the property's own OTP. Generate runs the checks and builds the
Word file; when SharePoint is connected a background job saves it in the property
folder and fetches SharePoint's PDF.

Access: the ``properties`` role, and admin, which passes every role (D104).
Marketing and approver accounts cannot open it. The page shows the seller's name
and ID number, which the rest of the platform keeps off its screens, and nothing
here reaches a public renderer.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from engine.proposal import checks, docx_build, otp_docx, sharepoint
from engine.proposal.model import (
    DEFAULT_BUDGET,
    SELLER_LABELS,
    BudgetLine,
    Erf,
    Proposal,
    Terms,
    base_dp,
    budget_totals,
    email_text,
    normalise_dp,
    output_stem,
    pct_text,
    rands,
)
from engine.proposal.store import ProposalStore
from webapp import auth, jobs, models

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_ROLES = ("properties",)  # admin passes every role; marketing and approver do not (D104)
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_KINDS = {
    "lightstone": (".pdf",),
    "deeds": tuple(_IMAGE_MIME),
    "advert": tuple(_IMAGE_MIME),
    "otp": (".docx",),
}
_MAX_BYTES = 30 * 1024 * 1024
_MAX_FILES = 10
_MAX_ERVEN = 12
_MAX_LINES = 30
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

require = auth.require_role(*_ROLES)


# --- plumbing -------------------------------------------------------------------

def _db(request: Request) -> str:
    return auth.db_path_for(request)


def _output_root(db: str) -> str:
    return models.get_setting(db, "output_root") or "."


def _files_root(db: str, dp: str) -> Path:
    return Path(_output_root(db)) / f"DP{dp}" / "proposal"


def _store(db: str) -> ProposalStore:
    return ProposalStore(models.resolve_db_path(db))


def _load(db: str, raw_dp: str) -> Proposal:
    dp = normalise_dp(raw_dp)
    if dp is None:
        raise HTTPException(status_code=404, detail="That is not a DP number.")
    with _store(db) as store:
        proposal = store.get(dp)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"There is no proposal for {dp}.")
    return proposal


def _save(db: str, proposal: Proposal, user: Dict[str, Any]) -> None:
    with _store(db) as store:
        store.save(proposal, user.get("email", ""))


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _back(request: Request, dp: str, anchor: str = "", notice: str = "") -> Response:
    url = f"/proposals/{dp}"
    if notice:
        url += "?notice=" + quote(notice[:400])
    url += anchor
    if _is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _job(db: str, job_id: int) -> Optional[Dict[str, Any]]:
    return models.get_job(db, job_id) if job_id else None


def _running(job: Optional[Dict[str, Any]]) -> bool:
    return bool(job) and job.get("state") in ("queued", "running")


def _engine_advert(db: str, dp: str) -> Optional[Path]:
    """The advert the marketing platform already rendered for this DP, if any."""
    for candidate in dict.fromkeys((dp, base_dp(dp))):
        path = Path(_output_root(db)) / f"DP{candidate}" / "artifacts" / "demo_ad.png"
        if path.is_file():
            return path
    return None


def _terms_summary(terms: Terms) -> str:
    parts = []
    if terms.deposit_pct is not None:
        parts.append(f"deposit {pct_text(terms.deposit_pct)}%")
    if terms.confirmation_days is not None:
        parts.append(f"confirmation {terms.confirmation_days} days")
    if terms.commission_pct is not None:
        payer = f" paid by the {terms.commission_payer}" if terms.commission_payer else ""
        parts.append(f"commission {pct_text(terms.commission_pct)}%{payer}")
    return ", ".join(parts)


def _ctx(request: Request, user: Dict[str, Any], db: str, proposal: Proposal, **extra: Any) -> Dict[str, Any]:
    root = _files_root(db, proposal.dp)
    otp_text = None
    if proposal.otp_file and (root / proposal.otp_file).is_file():
        try:
            otp_text = otp_docx.read_text(root / proposal.otp_file)
        except Exception:  # a corrupt upload shows as a check, not a crash
            otp_text = None
    issues = checks.run(proposal, root, otp_text, date.today())
    try:
        total, vat, incl = budget_totals(proposal)
        totals = {"total": rands(total), "vat": rands(vat), "incl": rands(incl)}
    except ValueError:
        totals = None
    subject, body = email_text(proposal)
    prefill_job = _job(db, proposal.prefill_job)
    publish_job = _job(db, proposal.publish_job)
    ctx: Dict[str, Any] = {
        "request": request,
        "user": user,
        "p": proposal,
        "issues": issues,
        "blocking": [i for i in issues if i.level == "block"],
        "warnings": [i for i in issues if i.level == "warn"],
        "flagged": {i.field for i in issues if i.level == "block"},
        "totals": totals,
        "labels": SELLER_LABELS,
        "email_subject": subject,
        "email_body": body,
        "prefill_job": prefill_job,
        "publish_job": publish_job,
        "prefill_running": _running(prefill_job),
        "publish_running": _running(publish_job),
        "sharepoint_ready": sharepoint.config_from_env() is not None,
        "has_key": bool(os.getenv("ANTHROPIC_API_KEY")),
        "engine_advert": _engine_advert(db, proposal.dp) is not None,
        "otp_summary": _terms_summary(proposal.otp_terms),
        "terms_form": {
            "deposit_pct": pct_text(proposal.terms.deposit_pct),
            "confirmation_days": "" if proposal.terms.confirmation_days is None else str(proposal.terms.confirmation_days),
            "commission_pct": pct_text(proposal.terms.commission_pct),
        },
        "notice": request.query_params.get("notice", "")[:400],
    }
    ctx.update(extra)
    return ctx


# --- the marketing record as a starting point ----------------------------------------

def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


def _seed_from_record(db: str, proposal: Proposal) -> None:
    """Copy what the marketing record already knows. Only called on a new proposal."""
    from engine.store import RecordStore

    store = RecordStore(models.resolve_db_path(db))
    try:
        record = store.get(proposal.dp)
        if record is None and "." in proposal.dp:
            record = store.get(base_dp(proposal.dp))
    finally:
        store.close()
    if record is None:
        return

    def text(path: str) -> str:
        return str(_dig(record, path) or "").strip()

    erf = Erf(
        legal_description=text("identity.legal_description").upper(),
        known_as=text("identity.street_address").upper(),
        title_deed=text("identity.title_deed_no").upper(),
    )
    size = _dig(record, "physical.unit_size_m2")
    if size:
        erf.extent = f"{size:g} m2"
    proposal.erven = [erf]
    proposal.seller_name = text("financials_internal.owner.name").upper()
    proposal.seller_id = text("financials_internal.owner.id_number")
    proposal.masters_ref = text("identity.mandate_ref").upper()
    channel = text("sale_process.auction_channel").lower()
    proposal.channel = "online" if "online" in channel else ("onsite" if "site" in channel else "")
    otp = _dig(record, "sale_process.otp")
    for field in ("deposit_pct", "commission_pct"):
        value = _dig(otp, field)
        if value is not None:
            setattr(proposal.terms, field, Decimal(str(value)))
    days = _dig(otp, "confirmation_days")
    if days is not None:
        proposal.terms.confirmation_days = int(days)
    proposal.prefill_note = "Copied from this DP's marketing record. Check each field against the title deed."


# --- the form ------------------------------------------------------------------------------

def _text(form: Any, key: str, limit: int = 600) -> str:
    return str(form.get(key) or "").replace("\r\n", "\n").strip()[:limit]


def _parse_date(raw: str) -> Optional[date]:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _parse_pct(raw: str, label: str, errors: List[str]) -> Optional[Decimal]:
    cleaned = raw.replace("%", "").replace(",", ".").strip()
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        errors.append(f'{label}: "{raw}" is not a number.')
        return None
    if not Decimal("0") <= value <= Decimal("100"):
        errors.append(f"{label}: {raw} is not a percentage.")
        return None
    return value


def _parse_days(raw: str, label: str, errors: List[str]) -> Optional[int]:
    cleaned = raw.strip()
    if not cleaned:
        return None
    if not cleaned.isdigit() or int(cleaned) > 365:
        errors.append(f'{label}: "{raw}" is not a number of days.')
        return None
    return int(cleaned)


def _count(form: Any, key: str, cap: int) -> int:
    try:
        return max(0, min(int(form.get(key) or 0), cap))
    except (TypeError, ValueError):
        return 0


def _apply_form(p: Proposal, form: Any) -> List[str]:
    """Copy the posted form onto the proposal. Returns messages for values it refused."""
    errors: List[str] = []
    if not form.get("_full_form"):
        return errors

    capacity = _text(form, "seller_capacity")
    p.seller_capacity = capacity if capacity in SELLER_LABELS else ""
    p.seller_name = _text(form, "seller_name", 300)
    p.seller_id = _text(form, "seller_id", 60)
    p.masters_ref = _text(form, "masters_ref", 60)

    p.erven = [
        Erf(
            legal_description=_text(form, f"erf-{n}-legal"),
            known_as=_text(form, f"erf-{n}-known_as", 300),
            title_deed=_text(form, f"erf-{n}-title_deed", 40),
            extent=_text(form, f"erf-{n}-extent", 40),
        )
        for n in range(1, _count(form, "erf_count", _MAX_ERVEN) + 1)
    ] or [Erf()]

    p.auction_date = _parse_date(_text(form, "auction_date", 10))
    for key in ("time_from", "time_to"):
        raw = _text(form, key, 5)
        if raw and not _TIME.match(raw):
            errors.append(f'"{raw}" is not a time like 10:00.')
            raw = ""
        setattr(p, key, raw)
    channel = _text(form, "channel")
    p.channel = channel if channel in ("online", "onsite") else ""
    p.venue_address = _text(form, "venue_address", 300)
    p.administrator = _text(form, "administrator", 120)
    p.deadline = _parse_date(_text(form, "deadline", 10))

    p.budget = [
        BudgetLine(
            media=_text(form, f"line-{n}-media", 300),
            description=_text(form, f"line-{n}-description", 200),
            placement=_text(form, f"line-{n}-placement", 40),
            cost=_text(form, f"line-{n}-cost", 40),
        )
        for n in range(1, _count(form, "line_count", _MAX_LINES) + 1)
    ]

    p.terms.deposit_pct = _parse_pct(_text(form, "deposit_pct", 10), "Deposit", errors)
    p.terms.confirmation_days = _parse_days(_text(form, "confirmation_days", 5), "Confirmation period", errors)
    p.terms.commission_pct = _parse_pct(_text(form, "commission_pct", 10), "Commission", errors)
    payer = _text(form, "commission_payer")
    p.terms.commission_payer = payer if payer in ("seller", "purchaser") else ""
    vat = _text(form, "commission_vat")
    p.terms.commission_vat = True if vat == "yes" else (False if vat == "no" else None)
    return errors


def _apply_action(p: Proposal, action: str) -> Optional[str]:
    """Add/remove rows. Returns the anchor to land on, or None when there was no action."""
    if action == "add_erf":
        if len(p.erven) < _MAX_ERVEN:
            p.erven = [*p.erven, Erf()]
        return "#property"
    match = re.fullmatch(r"remove_erf_(\d+)", action)
    if match:
        index = int(match.group(1)) - 1
        if len(p.erven) > 1 and 0 <= index < len(p.erven):
            p.erven = [e for i, e in enumerate(p.erven) if i != index]
        return "#property"
    if action == "add_line":
        if len(p.budget) < _MAX_LINES:
            p.budget = [*p.budget, BudgetLine()]
        return "#budget"
    match = re.fullmatch(r"remove_line_(\d+)", action)
    if match:
        index = int(match.group(1)) - 1
        if 0 <= index < len(p.budget):
            p.budget = [line for i, line in enumerate(p.budget) if i != index]
        return "#budget"
    if action == "reset_budget":
        p.budget = [BudgetLine(media=m, description=d, placement=pl, cost=c) for m, d, pl, c in DEFAULT_BUDGET]
        return "#budget"
    return None


def _saved(request: Request, user: Dict[str, Any], db: str, p: Proposal, toast: Dict[str, str]) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/_proposal_saved.html", _ctx(request, user, db, p, toast=toast))


# --- files --------------------------------------------------------------------------------------

def _safe_name(name: Optional[str]) -> str:
    base = Path(name or "file").name
    base = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", base).strip(" .") or "file"
    return base[-120:]


def _unique(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    stem, suffix, n = dest.stem, dest.suffix, 1
    while dest.exists():
        dest = folder / f"{stem}_{n}{suffix}"
        n += 1
    return dest


# --- routes ------------------------------------------------------------------------------------

@router.get("/proposals", response_class=HTMLResponse)
def proposals_page(request: Request, user: dict = Depends(require)):
    with _store(_db(request)) as store:
        rows = store.list()
    return templates.TemplateResponse(
        request,
        "proposals.html",
        {"request": request, "user": user, "rows": rows, "error": request.query_params.get("error", "")},
    )


@router.post("/proposals/new")
async def proposal_new(request: Request, dp: str = Form(""), user: dict = Depends(require)):
    clean = normalise_dp(dp)
    if clean is None:
        return RedirectResponse("/proposals?error=dp", status_code=303)
    db = _db(request)
    with _store(db) as store:
        if store.get(clean) is None:
            proposal = Proposal(dp=clean)
            try:
                _seed_from_record(db, proposal)
            except Exception:  # an unreadable record is no reason not to start
                proposal = Proposal(dp=clean)
            store.save(proposal, user.get("email", ""))
    return RedirectResponse(f"/proposals/{clean}", status_code=303)


@router.get("/proposals/{dp}", response_class=HTMLResponse)
def proposal_page(dp: str, request: Request, user: dict = Depends(require)):
    db = _db(request)
    proposal = _load(db, dp)
    if proposal.dp != dp:
        return RedirectResponse(f"/proposals/{proposal.dp}", status_code=303)
    return templates.TemplateResponse(request, "proposal_edit.html", _ctx(request, user, db, proposal))


@router.post("/proposals/{dp}/save")
async def proposal_save(dp: str, request: Request, user: dict = Depends(require)):
    db = _db(request)
    proposal = _load(db, dp)
    form = await request.form()
    errors = _apply_form(proposal, form)
    anchor = _apply_action(proposal, str(form.get("action") or ""))
    _save(db, proposal, user)
    if anchor is not None or not _is_htmx(request):
        return _back(request, proposal.dp, anchor or "", notice=" ".join(errors))
    if errors:
        toast = {"tone": "note", "title": "Saved, but not everything", "text": " ".join(errors)}
    else:
        toast = {"tone": "ok", "title": "Saved", "text": "The checks have been run again."}
    return _saved(request, user, db, proposal, toast)


@router.post("/proposals/{dp}/upload/{kind}")
async def proposal_upload(
    dp: str,
    kind: str,
    request: Request,
    files: List[UploadFile] = File(default=[]),
    user: dict = Depends(require),
):
    if kind not in _KINDS:
        raise HTTPException(status_code=404, detail="Unknown upload.")
    db = _db(request)
    proposal = _load(db, dp)
    _apply_form(proposal, await request.form())  # typing on the page is kept
    root = _files_root(db, proposal.dp)
    notes: List[str] = []
    added: List[str] = []
    for upload in files[:_MAX_FILES]:
        name = _safe_name(upload.filename)
        if Path(name).suffix.lower() not in _KINDS[kind]:
            notes.append(f"{name} was skipped: this takes {' or '.join(_KINDS[kind])} files.")
            continue
        if upload.size is not None and upload.size > _MAX_BYTES:
            notes.append(f"{name} is over 30 MB.")
            continue
        raw = await upload.read()
        if not raw or len(raw) > _MAX_BYTES:
            notes.append(f"{name} is empty or over 30 MB.")
            continue
        dest = _unique(root / kind, name)
        dest.write_bytes(raw)
        added.append(f"{kind}/{dest.name}")

    if kind == "lightstone" and added:
        from engine.proposal import lightstone

        for rel in added:
            proposal.lightstone_files = [*proposal.lightstone_files, rel]
            image = _unique(root / "deeds", f"{Path(rel).stem}.png")
            try:
                lightstone.deeds_image(root / rel, image)
                proposal.deeds_images = [*proposal.deeds_images, f"deeds/{image.name}"]
            except Exception:
                notes.append(f"The deeds page could not be drawn from {Path(rel).name}; upload a screenshot instead.")
        if os.getenv("ANTHROPIC_API_KEY"):
            proposal.prefill_job = jobs.enqueue(
                db, "proposal_prefill", proposal.dp, payload={"output_root": _output_root(db)}
            )
        else:
            notes.append("Reading the report needs the Anthropic key, which is not set here, so type the seller and property in.")
    elif kind == "deeds":
        proposal.deeds_images = [*proposal.deeds_images, *added]
    elif kind == "advert" and added:
        proposal.advert_image = added[-1]
    elif kind == "otp" and added:
        proposal.otp_file = added[-1]
        try:
            terms, otp_notes = otp_docx.read_terms(root / proposal.otp_file)
        except Exception:
            terms, otp_notes = Terms(), ["The OTP could not be read. Check that it is a Word document (.docx)."]
        proposal.otp_terms = terms
        proposal.otp_notes = otp_notes
        for field in ("deposit_pct", "confirmation_days", "commission_pct", "commission_payer", "commission_vat"):
            value = getattr(terms, field)
            if value not in (None, "") and getattr(proposal.terms, field) in (None, ""):
                setattr(proposal.terms, field, value)

    if not added and not notes:
        notes.append("No file was added.")
    _save(db, proposal, user)
    return _back(request, proposal.dp, "#documents", notice=" ".join(notes))


@router.post("/proposals/{dp}/remove/{kind}/{index}")
async def proposal_remove(dp: str, kind: str, index: int, request: Request, user: dict = Depends(require)):
    db = _db(request)
    proposal = _load(db, dp)
    _apply_form(proposal, await request.form())
    if kind == "deeds" and 0 <= index < len(proposal.deeds_images):
        proposal.deeds_images = [f for i, f in enumerate(proposal.deeds_images) if i != index]
    elif kind == "lightstone" and 0 <= index < len(proposal.lightstone_files):
        proposal.lightstone_files = [f for i, f in enumerate(proposal.lightstone_files) if i != index]
    elif kind == "advert":
        proposal.advert_image = ""
    elif kind == "otp":
        proposal.otp_file = ""
        proposal.otp_terms = Terms()
        proposal.otp_notes = []
    else:
        raise HTTPException(status_code=404, detail="Nothing to remove.")
    _save(db, proposal, user)
    return _back(request, proposal.dp, "#documents")


@router.post("/proposals/{dp}/advert/engine")
async def proposal_engine_advert(dp: str, request: Request, user: dict = Depends(require)):
    db = _db(request)
    proposal = _load(db, dp)
    _apply_form(proposal, await request.form())
    source = _engine_advert(db, proposal.dp)
    if source is None:
        _save(db, proposal, user)
        return _back(request, proposal.dp, "#documents", notice="This DP has no advert on the marketing platform yet.")
    dest = _unique(_files_root(db, proposal.dp) / "advert", f"{proposal.dp} platform advert.png")
    dest.write_bytes(source.read_bytes())
    proposal.advert_image = f"advert/{dest.name}"
    _save(db, proposal, user)
    return _back(request, proposal.dp, "#documents")


@router.get("/proposals/{dp}/file/{kind}/{index}")
def proposal_file(dp: str, kind: str, index: int, request: Request, user: dict = Depends(require)):
    db = _db(request)
    proposal = _load(db, dp)
    if kind == "deeds" and 0 <= index < len(proposal.deeds_images):
        rel = proposal.deeds_images[index]
    elif kind == "advert" and proposal.advert_image:
        rel = proposal.advert_image
    else:
        raise HTTPException(status_code=404, detail="No such picture.")
    root = _files_root(db, proposal.dp).resolve()
    path = (root / rel).resolve()
    if root not in path.parents or not path.is_file() or path.suffix.lower() not in _IMAGE_MIME:
        raise HTTPException(status_code=404, detail="No such picture.")
    return FileResponse(str(path), media_type=_IMAGE_MIME[path.suffix.lower()])


@router.post("/proposals/{dp}/generate")
async def proposal_generate(dp: str, request: Request, user: dict = Depends(require)):
    db = _db(request)
    proposal = _load(db, dp)
    errors = _apply_form(proposal, await request.form())
    ctx = _ctx(request, user, db, proposal)
    if ctx["blocking"] or errors:
        _save(db, proposal, user)
        text = " ".join(errors) if errors else "Fix the items under Checks first."
        return _saved(request, user, db, proposal, {"tone": "block", "title": "Not generated", "text": text})

    root = _files_root(db, proposal.dp)
    rel = f"out/{output_stem(proposal)}.docx"
    try:
        docx_build.build(proposal, root, root / rel)
    except Exception as exc:
        _save(db, proposal, user)
        return _saved(request, user, db, proposal, {
            "tone": "block", "title": "Not generated", "text": f"The Word file could not be built: {exc}",
        })

    if proposal.pdf_file:
        (root / proposal.pdf_file).unlink(missing_ok=True)  # it describes the previous version
    proposal.docx_file = rel
    proposal.pdf_file = ""
    proposal.generated_at = _now()
    proposal.generated_by = user.get("email", "")
    if sharepoint.config_from_env() is not None:
        proposal.publish_job = jobs.enqueue(
            db, "proposal_publish", proposal.dp, payload={"output_root": _output_root(db)}
        )
        proposal.pdf_note = "Saving to SharePoint and making the PDF there."
        text = "The Word file is ready. The PDF follows once SharePoint has made it."
    else:
        # D105: the team saves the PDF from Word once they are happy with the
        # file, so this is the normal path, not a missing piece.
        proposal.pdf_note = "Download the Word file, check it in Word, then save it as a PDF to send."
        text = "The Word file is ready to download."
    _save(db, proposal, user)
    return _saved(request, user, db, proposal, {"tone": "ok", "title": "Proposal generated", "text": text})


@router.get("/proposals/{dp}/download/{fmt}")
def proposal_download(dp: str, fmt: str, request: Request, user: dict = Depends(require)):
    db = _db(request)
    proposal = _load(db, dp)
    rel = {"docx": proposal.docx_file, "pdf": proposal.pdf_file}.get(fmt)
    path = _files_root(db, proposal.dp) / rel if rel else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Generate the proposal first.")
    media = _DOCX_MIME if fmt == "docx" else "application/pdf"
    return FileResponse(str(path), media_type=media, filename=f"{output_stem(proposal)}.{fmt}")


@router.get("/proposals/{dp}/status", response_class=HTMLResponse)
def proposal_status(dp: str, request: Request, user: dict = Depends(require)):
    db = _db(request)
    proposal = _load(db, dp)
    return templates.TemplateResponse(request, "partials/_proposal_output.html", _ctx(request, user, db, proposal))


@router.get("/proposals/{dp}/prefill-status", response_class=HTMLResponse)
def proposal_prefill_status(dp: str, request: Request, user: dict = Depends(require)):
    db = _db(request)
    proposal = _load(db, dp)
    if not _running(_job(db, proposal.prefill_job)):
        # The report has been read: reload so the filled fields show.
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "partials/_proposal_prefill.html", _ctx(request, user, db, proposal))


@router.post("/proposals/{dp}/delete")
async def proposal_delete(dp: str, request: Request, user: dict = Depends(require)):
    db = _db(request)
    proposal = _load(db, dp)
    with _store(db) as store:
        store.delete(proposal.dp)
    if _is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": "/proposals"})
    return RedirectResponse("/proposals", status_code=303)
