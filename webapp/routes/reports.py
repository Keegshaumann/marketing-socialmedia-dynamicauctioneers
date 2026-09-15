"""Property reports (M11, D116): the screen the properties team writes a property report on.

A property report is the inspection report sent to the trustee, liquidator or
bank that instructed a sale, before the instruction to market it. It is the third
place beside the auction proposals (M9) and the OTPs (M10), with its own records
and files under ``DP<dp>/report/``, reached through the same switcher.

The page follows the team's report: the property facts (copied from this DP's
OTP, proposal or marketing record when a report starts, read from a Lightstone
report, or typed), the viewing checklist as fields (D117), rates and levies, the
terms, the viewing contact, the Lightstone picture for the cover and the photos.
Generate fills ``engine/propertyreport/templates/property-report.docx``.

Access: the ``properties`` role, and admin (D104). The page and the report carry
the owner's name and number.
"""

from __future__ import annotations

import io
import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from PIL import Image, ImageOps

from engine.otpgen.store import OtpStore
from engine.propertyreport import checks, docx_build
from engine.propertyreport.model import (
    EMAILS,
    FARM,
    FEATURES,
    PROPERTY_TYPES,
    ROOMS,
    SECURITY,
    Inspection,
    PropertyReport,
    output_stem,
)
from engine.propertyreport.store import ReportStore
from engine.proposal.model import normalise_dp, parse_cost, pct_text, rands
from engine.proposal.store import ProposalStore
from webapp import auth, jobs, models
from webapp.routes import proposals as shared

router = APIRouter()
templates = shared.templates

_ROLES = ("properties",)  # the same people as the proposals and OTPs (D104)
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_IMAGES = (".png", ".jpg", ".jpeg")
_MAX_PHOTOS = 120
_MAX_PHOTO_UPLOAD = 40
_PHOTO_EDGE = 1600  # phone photos are shrunk so a report with 50 of them stays a sensible size
_MAX_LINES = 20

require = auth.require_role(*_ROLES)


# --- plumbing -------------------------------------------------------------------

def _files_root(db: str, dp: str) -> Path:
    return Path(shared._output_root(db)) / f"DP{dp}" / "report"


def _store(db: str) -> ReportStore:
    return ReportStore(models.resolve_db_path(db))


def _load(db: str, raw_dp: str) -> PropertyReport:
    dp = normalise_dp(raw_dp)
    if dp is None:
        raise HTTPException(status_code=404, detail="That is not a DP number.")
    with _store(db) as store:
        report = store.get(dp)
    if report is None:
        raise HTTPException(status_code=404, detail=f"There is no property report for {dp}.")
    return report


def _save(db: str, report: PropertyReport, user: Dict[str, Any]) -> None:
    with _store(db) as store:
        store.save(report, user.get("email", ""))


def _back(request: Request, dp: str, anchor: str = "", notice: str = "") -> Response:
    # A fresh query string on every reload, as on the proposals (D107).
    url = f"/reports/{dp}?at={int(datetime.now(timezone.utc).timestamp() * 1_000_000)}"
    if notice:
        url += "&notice=" + quote(notice[:400])
    url += anchor
    if shared._is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def _plain_number(value) -> str:
    return "" if value is None else str(value)


def _ctx(request: Request, user: Dict[str, Any], db: str, report: PropertyReport, **extra: Any) -> Dict[str, Any]:
    issues = checks.run(report, _files_root(db, report.dp))
    prefill_job = shared._job(db, report.prefill_job)
    i = report.inspection
    ctx: Dict[str, Any] = {
        "request": request,
        "user": user,
        "rep": report,
        "p": report,  # the shared checks and prefill partials call the record p
        "i": i,
        "issues": issues,
        "blocking": [x for x in issues if x.level == "block"],
        "warnings": [x for x in issues if x.level == "warn"],
        "flagged": {x.field for x in issues if x.level == "block"},
        "types": PROPERTY_TYPES,
        "emails": EMAILS,
        "rooms": ROOMS,
        "features": FEATURES,
        "farm": FARM,
        "security_items": SECURITY,
        "prefill_job": prefill_job,
        "prefill_running": shared._running(prefill_job),
        "prefill_url": f"/reports/{report.dp}/prefill-status",
        "has_key": bool(os.getenv("ANTHROPIC_API_KEY")),
        "form": {
            "municipal_valuation": rands(report.municipal_valuation) if report.municipal_valuation is not None else "",
            "municipal_valuation_year": _plain_number(report.municipal_valuation_year),
            "rates_outstanding": rands(report.rates_outstanding) if report.rates_outstanding is not None else "",
            "levies_outstanding": rands(report.levies_outstanding) if report.levies_outstanding is not None else "",
            "deposit_pct": pct_text(report.deposit_pct),
            "commission_pct": pct_text(report.commission_pct),
            "guarantee_days": _plain_number(report.guarantee_days),
            "confirmation_days": _plain_number(report.confirmation_days),
            "other_improvements": "\n".join(i.other_improvements),
            "other_features": "\n".join(i.other_features),
        },
        "notice": request.query_params.get("notice", "")[:400],
    }
    ctx.update(extra)
    return ctx


def _saved(request: Request, user: Dict[str, Any], db: str, report: PropertyReport, toast: Dict[str, str]) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/_report_saved.html", _ctx(request, user, db, report, toast=toast))


# --- a starting point ------------------------------------------------------------------

def _fill(obj, field: str, value) -> bool:
    if value in (None, "") or getattr(obj, field) not in (None, ""):
        return False
    setattr(obj, field, value)
    return True


def _seed(db: str, report: PropertyReport) -> None:
    """Copy what this DP already has: its OTP, its auction proposal, then its marketing record. Blanks only."""
    from engine.store import RecordStore

    path = models.resolve_db_path(db)
    with OtpStore(path) as store:
        otp = store.get(report.dp)
    with ProposalStore(path) as store:
        proposal = store.get(report.dp)
    sources: List[str] = []
    for source, label in ((otp, "OTP"), (proposal, "auction proposal")):
        if source is None:
            continue
        sources.append(label)
        _fill(report, "owner_name", source.seller_name.strip())
        _fill(report, "owner_id", source.seller_id.strip())
        erf = source.erven[0] if source.erven else None
        if erf is not None:
            _fill(report, "legal_description", erf.legal_description.strip())
            _fill(report, "known_as", erf.known_as.strip())
            _fill(report, "title_deed", erf.title_deed.strip())
            _fill(report, "extent", erf.extent.strip())
    if otp is not None:
        for field in ("deposit_pct", "commission_pct", "guarantee_days", "confirmation_days"):
            value = getattr(otp, field)
            if value is not None:
                setattr(report, field, value)
    if proposal is not None and proposal.deeds_images and not report.cover_image:
        picture = Path(shared._output_root(db)) / f"DP{report.dp}" / "proposal" / proposal.deeds_images[0]
        if picture.is_file():
            dest = shared._unique(_files_root(db, report.dp) / "cover", picture.name)
            dest.write_bytes(picture.read_bytes())
            report.cover_image = f"cover/{dest.name}"

    records = RecordStore(path)
    try:
        record = records.get(report.dp) or (records.get(report.dp.split(".")[0]) if "." in report.dp else None)
    finally:
        records.close()
    if record is not None:
        sources.append("marketing record")
        dig = shared._dig

        def text(field: str) -> str:
            return str(dig(record, field) or "").strip()

        _fill(report, "owner_name", text("financials_internal.owner.name").upper())
        _fill(report, "owner_id", text("financials_internal.owner.id_number"))
        _fill(report, "legal_description", text("identity.legal_description").upper())
        _fill(report, "known_as", text("identity.street_address").upper())
        _fill(report, "title_deed", text("identity.title_deed_no").upper())
        _fill(report, "local_authority", text("identity.municipality").upper())
        _fill(report, "zoning", text("physical.zoning").upper())
        size = dig(record, "physical.unit_size_m2")
        if size:
            _fill(report, "extent", f"{size:g} m2")
        gps = dig(record, "identity.gps")
        if gps and len(gps) == 2:
            _fill(report, "gps", f"{gps[0]},{gps[1]}")
        valuation = dig(record, "valuation.municipal_valuation")
        if valuation:
            _fill(report, "municipal_valuation", Decimal(str(valuation)))
            _fill(report, "municipal_valuation_year", dig(record, "valuation.municipal_valuation_year"))
        for field, path_ in (("rates_outstanding", "financials_internal.outstanding_rates_taxes_water"),
                             ("levies_outstanding", "financials_internal.outstanding_levies")):
            amount = dig(record, path_)
            if amount is not None:
                _fill(report, field, Decimal(str(amount)))
        as_at = shared._parse_date(text("financials_internal.as_at")[:10])
        if as_at:
            _fill(report, "rates_as_at", as_at if report.rates_outstanding is not None else None)
            _fill(report, "levies_as_at", as_at if report.levies_outstanding is not None else None)
        i = report.inspection
        _fill(i, "bedrooms", dig(record, "physical.bedrooms"))
        _fill(i, "bathrooms", dig(record, "physical.bathrooms_main_unit"))
        _fill(i, "garages", dig(record, "physical.garages"))
        if dig(record, "physical.separate_toilet"):
            _fill(i, "separate_toilets", 1)
        report.inspection = i
        title_type = text("identity.title_type").lower()
        if not report.property_type and "section" in title_type:
            report.property_type = "unit"

    if report.title_deed.upper().startswith("ST") and not report.property_type:
        report.property_type = "unit"
    if sources:
        report.prefill_note = (
            "Copied from this DP's " + ", ".join(sources) + ". Check each field against the title deed and the inspection."
        )


# --- the form ------------------------------------------------------------------------------

def _money(raw: str, label: str, errors: List[str]) -> Optional[Decimal]:
    try:
        amount, word = parse_cost(raw)
    except ValueError:
        errors.append(f'{label}: "{raw}" is not a rand amount.')
        return None
    if word:
        errors.append(f"{label}: type an amount.")
        return None
    return amount


def _whole(raw: str, label: str, errors: List[str], top: int = 99) -> Optional[int]:
    if not raw:
        return None
    if not raw.isdigit() or int(raw) > top:
        errors.append(f'{label}: "{raw}" is not a number.')
        return None
    return int(raw)


def _choices(form: Any, key: str, allowed: Dict[str, str]) -> List[str]:
    return [value for value in allowed if value in form.getlist(key)]


def _textlines(form: Any, key: str) -> List[str]:
    return [line.strip()[:300] for line in shared._text(form, key, 6000).split("\n") if line.strip()][:_MAX_LINES]


def _apply_form(r: PropertyReport, form: Any) -> List[str]:
    """Copy the posted form onto the report. Returns messages for values it refused."""
    errors: List[str] = []
    if not form.get("_full_form"):
        return errors
    text = shared._text

    kind = text(form, "property_type")
    r.property_type = kind if kind in PROPERTY_TYPES else ""
    r.owner_name = text(form, "owner_name", 300)
    r.owner_id = text(form, "owner_id", 60)
    r.legal_description = text(form, "legal_description")
    r.known_as = text(form, "known_as", 300)
    r.extent = text(form, "extent", 40)
    r.zoning = text(form, "zoning", 80)
    r.local_authority = text(form, "local_authority", 120)
    r.municipal_valuation = _money(text(form, "municipal_valuation", 30), "Municipal valuation", errors)
    year = _whole(text(form, "municipal_valuation_year", 4), "Valuation year", errors, top=2100)
    r.municipal_valuation_year = year if year is None or year >= 1990 else None
    r.title_deed = text(form, "title_deed", 40)
    r.gps = text(form, "gps", 60)
    r.description = text(form, "description", 600)

    r.inspection_date = shared._parse_date(text(form, "inspection_date", 10))
    r.prepared_by = text(form, "prepared_by", 120)
    email = text(form, "prepared_email")
    r.prepared_email = email if email in EMAILS else EMAILS[0]
    r.prepared_cell = text(form, "prepared_cell", 40)

    literal = lambda key, allowed: (lambda v: v if v in allowed else "")(text(form, key, 20))  # noqa: E731
    r.inspection = Inspection(
        bedrooms=_whole(text(form, "i_bedrooms", 3), "Bedrooms", errors),
        bathrooms=_whole(text(form, "i_bathrooms", 3), "Bathrooms", errors),
        ensuite=_whole(text(form, "i_ensuite", 3), "En-suite bathrooms", errors),
        separate_toilets=_whole(text(form, "i_separate_toilets", 3), "Separate toilets", errors),
        rooms=_choices(form, "i_rooms", ROOMS),
        floors=text(form, "i_floors", 200),
        other_improvements=_textlines(form, "i_other_improvements"),
        garages=_whole(text(form, "i_garages", 3), "Garages", errors),
        carports=_whole(text(form, "i_carports", 3), "Carports", errors),
        patio=text(form, "i_patio", 200),
        features=_choices(form, "i_features", FEATURES),
        outbuildings=text(form, "i_outbuildings", 300),
        homes=_whole(text(form, "i_homes", 3), "Homes", errors),
        farm=_choices(form, "i_farm", FARM),
        roof=text(form, "i_roof", 120),
        walls=text(form, "i_walls", 120),
        ceilings=text(form, "i_ceilings", 120),
        other_features=_textlines(form, "i_other_features"),
        security=text(form, "i_security", 600),
        security_items=_choices(form, "i_security_items", SECURITY),
        occupancy=literal("i_occupancy", ("vacant", "occupied")),
        occupant=text(form, "i_occupant", 200),
        area=literal("i_area", ("low", "medium", "high")),
        impression=text(form, "i_impression", 300),
        electricity=literal("i_electricity", ("on", "off")),
        water=literal("i_water", ("on", "off")),
        meters=text(form, "i_meters", 200),
        defects=text(form, "i_defects", 600),
    )

    r.rates_outstanding = _money(text(form, "rates_outstanding", 30), "Outstanding rates", errors)
    r.rates_as_at = shared._parse_date(text(form, "rates_as_at", 10))
    r.rates_note = text(form, "rates_note", 300)
    r.levies_outstanding = _money(text(form, "levies_outstanding", 30), "Outstanding levies", errors)
    r.levies_as_at = shared._parse_date(text(form, "levies_as_at", 10))
    r.managing_agent = text(form, "managing_agent", 200)

    r.deposit_pct = shared._parse_pct(text(form, "deposit_pct", 10), "Deposit", errors)
    r.commission_pct = shared._parse_pct(text(form, "commission_pct", 10), "Commission", errors)
    r.guarantee_days = shared._parse_days(text(form, "guarantee_days", 5), "Guarantee period", errors)
    r.confirmation_days = shared._parse_days(text(form, "confirmation_days", 5), "Confirmation period", errors)
    r.viewing_contact = text(form, "viewing_contact", 200)
    r.conclusion = text(form, "conclusion", 600)
    return errors


# --- files --------------------------------------------------------------------------------------

def _save_photo(raw: bytes, dest: Path) -> None:
    """Upright (phones store the turn in EXIF, which Word ignores), RGB, at most 1600 px on a side."""
    with Image.open(io.BytesIO(raw)) as im:
        upright = ImageOps.exif_transpose(im).convert("RGB")
    upright.thumbnail((_PHOTO_EDGE, _PHOTO_EDGE))
    upright.save(dest, "JPEG", quality=85, optimize=True)


# --- routes ------------------------------------------------------------------------------------

@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, user: dict = Depends(require)):
    with _store(shared._db(request)) as store:
        rows = store.list()
    return templates.TemplateResponse(
        request, "reports.html", {"request": request, "user": user, "rows": rows, "error": request.query_params.get("error", "")}
    )


@router.post("/reports/new")
async def report_new(request: Request, dp: str = Form(""), user: dict = Depends(require)):
    clean = normalise_dp(dp)
    if clean is None:
        return RedirectResponse("/reports?error=dp", status_code=303)
    db = shared._db(request)
    with _store(db) as store:
        if store.get(clean) is None:
            report = PropertyReport(dp=clean)  # prepared_by stays blank: the login is shared
            try:
                _seed(db, report)
            except Exception:  # an unreadable OTP, proposal or record is no reason not to start
                report = PropertyReport(dp=clean)
            store.save(report, user.get("email", ""))
    return RedirectResponse(f"/reports/{clean}", status_code=303)


@router.get("/reports/{dp}", response_class=HTMLResponse)
def report_page(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    report = _load(db, dp)
    if report.dp != dp:
        return RedirectResponse(f"/reports/{report.dp}", status_code=303)
    return templates.TemplateResponse(request, "report_edit.html", _ctx(request, user, db, report))


@router.post("/reports/{dp}/save")
async def report_save(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    report = _load(db, dp)
    errors = _apply_form(report, await request.form())
    _save(db, report, user)
    if not shared._is_htmx(request):
        return _back(request, report.dp, notice=" ".join(errors))
    if errors:
        toast = {"tone": "note", "title": "Saved, but not everything", "text": " ".join(errors)}
    else:
        toast = {"tone": "ok", "title": "Saved", "text": "The checks have been run again."}
    return _saved(request, user, db, report, toast)


@router.post("/reports/{dp}/upload/{kind}")
async def report_upload(
    dp: str, kind: str, request: Request, files: List[UploadFile] = File(default=[]), user: dict = Depends(require)
):
    if kind not in ("lightstone", "cover", "photos"):
        raise HTTPException(status_code=404, detail="Unknown upload.")
    db = shared._db(request)
    report = _load(db, dp)
    _apply_form(report, await request.form())  # typing on the page is kept
    root = _files_root(db, report.dp)
    accept = (".pdf",) if kind == "lightstone" else _IMAGES
    limit = _MAX_PHOTO_UPLOAD if kind == "photos" else shared._MAX_FILES
    notes: List[str] = []
    added: List[str] = []
    for upload in files[:limit]:
        name = shared._safe_name(upload.filename)
        if Path(name).suffix.lower() not in accept:
            notes.append(f"{name} was skipped: this takes {' or '.join(accept)} files.")
            continue
        raw = await upload.read()
        if not raw or len(raw) > shared._MAX_BYTES:
            notes.append(f"{name} is empty or over 30 MB.")
            continue
        if kind == "photos":
            if len(report.photos) + len(added) >= _MAX_PHOTOS:
                notes.append(f"A report holds at most {_MAX_PHOTOS} photos.")
                break
            dest = shared._unique(root / "photos", f"{Path(name).stem}.jpg")
            try:
                _save_photo(raw, dest)
            except Exception:
                dest.unlink(missing_ok=True)
                notes.append(f"{name} was skipped: it is not a picture that can be read.")
                continue
        else:
            dest = shared._unique(root / kind, name)
            dest.write_bytes(raw)
        added.append(f"{kind}/{dest.name}")

    if kind == "lightstone" and added:
        from engine.proposal import lightstone

        report.lightstone_files = [*report.lightstone_files, *added]
        if not report.cover_image:
            image = shared._unique(root / "cover", f"{Path(added[0]).stem}.png")
            try:
                lightstone.deeds_image(root / added[0], image)
                report.cover_image = f"cover/{image.name}"
            except Exception:
                notes.append(f"The cover picture could not be drawn from {Path(added[0]).name}; upload a screenshot instead.")
        if os.getenv("ANTHROPIC_API_KEY"):
            report.prefill_job = jobs.enqueue(db, "report_prefill", report.dp, payload={"output_root": shared._output_root(db)})
        else:
            notes.append("Reading the report needs the Anthropic key, which is not set here, so type the facts in.")
    elif kind == "cover" and added:
        report.cover_image = added[-1]
    elif kind == "photos":
        report.photos = [*report.photos, *added]
    if not added and not notes:
        notes.append("No file was added.")
    _save(db, report, user)
    return _back(request, report.dp, "#documents", notice=" ".join(notes))


@router.post("/reports/{dp}/remove/{kind}/{index}")
async def report_remove(dp: str, kind: str, index: int, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    report = _load(db, dp)
    _apply_form(report, await request.form())
    if kind == "lightstone" and 0 <= index < len(report.lightstone_files):
        report.lightstone_files = [f for n, f in enumerate(report.lightstone_files) if n != index]
    elif kind == "photos" and 0 <= index < len(report.photos):
        report.photos = [f for n, f in enumerate(report.photos) if n != index]
    elif kind == "cover" and report.cover_image:
        report.cover_image = ""
    else:
        raise HTTPException(status_code=404, detail="Nothing to remove.")
    _save(db, report, user)
    return _back(request, report.dp, "#documents")


@router.post("/reports/{dp}/earlier/{index}")
async def report_photo_earlier(dp: str, index: int, request: Request, user: dict = Depends(require)):
    """Move a photo one place earlier; the report prints them in this order, six to a page."""
    db = shared._db(request)
    report = _load(db, dp)
    _apply_form(report, await request.form())
    if 0 < index < len(report.photos):
        photos = list(report.photos)
        photos[index - 1], photos[index] = photos[index], photos[index - 1]
        report.photos = photos
    _save(db, report, user)
    return _back(request, report.dp, "#documents")


@router.get("/reports/{dp}/file/{kind}/{index}")
def report_file(dp: str, kind: str, index: int, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    report = _load(db, dp)
    if kind == "cover" and report.cover_image:
        rel = report.cover_image
    elif kind == "photos" and 0 <= index < len(report.photos):
        rel = report.photos[index]
    else:
        raise HTTPException(status_code=404, detail="No such picture.")
    root = _files_root(db, report.dp).resolve()
    path = (root / rel).resolve()
    if root not in path.parents or not path.is_file() or path.suffix.lower() not in shared._IMAGE_MIME:
        raise HTTPException(status_code=404, detail="No such picture.")
    return FileResponse(str(path), media_type=shared._IMAGE_MIME[path.suffix.lower()])


@router.post("/reports/{dp}/generate")
async def report_generate(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    report = _load(db, dp)
    errors = _apply_form(report, await request.form())
    root = _files_root(db, report.dp)
    if checks.blocking(checks.run(report, root)) or errors:
        _save(db, report, user)
        text = " ".join(errors) if errors else "Fix the items under Checks first."
        return _saved(request, user, db, report, {"tone": "block", "title": "Not generated", "text": text})

    rel = f"out/{output_stem(report)}.docx"
    try:
        docx_build.build(report, root, root / rel)
    except Exception as exc:
        _save(db, report, user)
        return _saved(request, user, db, report, {
            "tone": "block", "title": "Not generated", "text": f"The Word file could not be built: {exc}",
        })
    report.docx_file = rel
    report.generated_at = shared._now()
    report.generated_by = user.get("email", "")
    _save(db, report, user)
    return _saved(request, user, db, report, {
        "tone": "ok", "title": "Property report generated", "text": "The Word file is ready to download.",
    })


@router.get("/reports/{dp}/download")
def report_download(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    report = _load(db, dp)
    path = _files_root(db, report.dp) / report.docx_file if report.docx_file else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Generate the property report first.")
    return FileResponse(str(path), media_type=_DOCX_MIME, filename=f"{output_stem(report)}.docx")


@router.get("/reports/{dp}/prefill-status", response_class=HTMLResponse)
def report_prefill_status(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    report = _load(db, dp)
    if not shared._running(shared._job(db, report.prefill_job)):
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "partials/_proposal_prefill.html", _ctx(request, user, db, report))


@router.post("/reports/{dp}/delete")
async def report_delete(dp: str, request: Request, user: dict = Depends(require)):
    db = shared._db(request)
    report = _load(db, dp)
    with _store(db) as store:
        store.delete(report.dp)
    if shared._is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": "/reports"})
    return RedirectResponse("/reports", status_code=303)
