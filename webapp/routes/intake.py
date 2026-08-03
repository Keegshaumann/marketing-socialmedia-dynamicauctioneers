"""Intake screen: drag-drop document pair upload (M8, Phase 4, screen 2).

A marketing user drops a property's two source PDFs (the Lightstone EVM report
and the Property Report). This route:

1. saves the uploaded files under ``<output_root>/DP<dp>/uploads/``;
2. reads the DP number from the filenames and classifies each PDF
   (``engine.intake``), so the correct file lands in the correct slot;
3. creates a base ``PropertyRecord`` in the ``intake`` state if the record is
   new (never clobbering an already-extracted record);
4. enqueues an ``extract`` job carrying the source paths, then returns a job
   card that polls its status over HTMX until the job settles.

Key-gated reality: without an ``ANTHROPIC_API_KEY`` the worker marks the extract
job ``skipped: no API key`` and the record stays at ``intake``; the flow still
records the pair and shows on the board. With a key, extraction fills the record
and moves it to ``extracted``.

POPIA: nothing rendered here comes from the record body; the screen shows only
the DP number and job status, so no PII can surface.
"""

from __future__ import annotations

import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from webapp import auth, jobs, models

router = APIRouter(prefix="/intake")

# Job states the worker uses that mean "still working" (keep polling).
_ACTIVE = {"queued", "running"}

# A DP typed by a human: digits, optional ".<lot>", an optional "DP" prefix.
_DP_INPUT_RE = re.compile(r"^\s*(?:DP)?\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
# A batch id is our own uuid4 hex; validate before using it as a path segment.
_BATCH_RE = re.compile(r"^[0-9a-f]{32}$")


def _normalize_dp_input(raw: str) -> Optional[str]:
    """A human-typed DP -> canonical ``3060`` / ``3035.1``, or None if unusable."""
    match = _DP_INPUT_RE.match(raw or "")
    return match.group(1) if match else None


def _tray_dir(output_root: str, batch_id: str) -> Optional[Path]:
    """The staged-uploads folder for ``batch_id``, or None if the id is malformed."""
    if not _BATCH_RE.match(batch_id or ""):
        return None
    return Path(output_root) / "_intake_tray" / batch_id


# A staged batch older than this is abandoned (the user closed the DP prompt) and
# is swept on the next upload. Source PDFs carry POPIA-internal data (owner name,
# ID number, bond, arrears), so they must not linger in the tray indefinitely.
_TRAY_TTL_SECONDS = 24 * 60 * 60


def _sweep_stale_trays(output_root: str) -> None:
    """Delete staged batches older than the TTL. Never raises (best effort)."""
    tray = Path(output_root) / "_intake_tray"
    if not tray.is_dir():
        return
    cutoff = time.time() - _TRAY_TTL_SECONDS
    for child in tray.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def _view(request: Request, name: str, ctx: Optional[dict] = None, status_code: int = 200):
    templates = request.app.state.templates
    data: Dict[str, Any] = {"user": auth.current_user(request)}
    if ctx:
        data.update(ctx)
    return templates.TemplateResponse(request, name, data, status_code=status_code)


def _output_root(db_path: str) -> str:
    return models.get_setting(db_path, "output_root") or "."


# The progress bar is elapsed-driven: extraction has no mid-run signal, so the
# bar approaches (never reaches) 95% on this half-life and snaps to done when the
# job settles - it always creeps forward instead of sitting at a fixed value.
_EXTRACT_HALFLIFE_S = 40.0


def _progress(job: Dict[str, Any]) -> Dict[str, Any]:
    """Elapsed seconds -> an ever-advancing percent + a mm:ss elapsed label."""
    created = job.get("created_at")
    elapsed = 0.0
    if created:
        try:
            started = datetime.fromisoformat(str(created))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
        except ValueError:
            elapsed = 0.0
    pct = round(95 * (1 - 0.5 ** (elapsed / _EXTRACT_HALFLIFE_S)))
    minutes, seconds = divmod(int(elapsed), 60)
    return {"pct": max(4, pct), "elapsed_label": f"{minutes}:{seconds:02d}"}


def _job_ctx(job: Dict[str, Any]) -> Dict[str, Any]:
    """Presentation fields for the job card: tone + whether to keep polling."""
    state = job.get("state") or ""
    polling = state in _ACTIVE
    if state == "error":
        tone = "error"
    elif state.startswith("skipped"):
        tone = "note"
    elif state in _ACTIVE:
        tone = "info"
    else:  # done
        tone = "ok"
    ctx = {"job": job, "polling": polling, "tone": tone}
    if polling:
        ctx.update(_progress(job))
    return ctx


# --- screen ---------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def intake_page(request: Request):
    if auth.current_user(request) is None:
        return RedirectResponse("/login", status_code=303)
    return _view(request, "intake.html")


@router.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    files: List[UploadFile] = File(default=[]),
    user: dict = Depends(auth.require_role("marketing")),
):
    from engine.intake import build_combined_job, dp_candidates

    db_path = auth.db_path_for(request)

    if not files:
        return _view(
            request,
            "_intake_error.html",
            {"message": "No files were received. Drop the source PDFs to begin."},
            status_code=400,
        )

    # Stage every upload under a unique batch folder, so a drop of many files
    # (a multi-portion property with several EVMs) is kept together and cannot
    # collide with another user's concurrent upload.
    output_root = _output_root(db_path)
    _sweep_stale_trays(output_root)  # drop abandoned batches (POPIA retention)
    batch_id = uuid.uuid4().hex
    batch_dir = _tray_dir(output_root, batch_id)
    batch_dir.mkdir(parents=True, exist_ok=True)

    saved: List[Path] = []
    skipped: List[str] = []
    for upload_file in files:
        name = Path(upload_file.filename or "").name
        if not name:
            continue
        # Only PDFs are source documents. The dropzone's accept= filters the
        # browse dialog but NOT a drag-drop, so property photos dragged in with
        # the reports would otherwise be staged and fed to the PDF classifier.
        if Path(name).suffix.lower() != ".pdf":
            skipped.append(name)
            continue
        dest = batch_dir / name
        dest.write_bytes(await upload_file.read())
        saved.append(dest)

    if not saved:
        shutil.rmtree(batch_dir, ignore_errors=True)
        message = "No readable files were received. Drop the source PDFs to begin."
        if skipped:
            message = (
                "Only PDF documents can be used here (" + ", ".join(skipped[:4])
                + (" and others" if len(skipped) > 4 else "")
                + " were not PDFs). Drop the Lightstone EVM and the Property "
                "Report; property photos are added later, on the photos step."
            )
        return _view(
            request, "_intake_error.html", {"message": message}, status_code=400,
        )

    # One combined property record from all the files. Three cases, and they must
    # be kept apart: exactly one DP -> proceed; NO DP (a farm portion named
    # "PTN 6 of Farm 7.pdf") -> ask the user to type one; SEVERAL DPs -> refuse,
    # because filing one property's documents under the other's DP would make
    # extraction synthesise the two into a single chimera record.
    found = dp_candidates(saved)
    if len(found) > 1:
        # Ask rather than refuse. ``parse_dp`` reads any leading number, so more
        # than one candidate does NOT prove more than one property: a valuer's
        # report named for the street ("40 Topham Road.pdf"), a scanner's date
        # stamp, or sub-lot files (3035 / 3035.1) all land here legitimately.
        # Refusing outright would dead-end those drops with no way through, so
        # name the numbers found and let the marketer say which property this is
        # (and tell her to upload separately if they really are two).
        return _view(
            request,
            "_intake_need_dp.html",
            {
                "batch_id": batch_id,
                "files": [p.name for p in saved],
                "count": len(saved),
                "candidates": found,
                "error": (
                    "These files are named for "
                    + str(len(found))
                    + " different numbers ("
                    + ", ".join("DP" + d for d in found)
                    + "). If they are two separate properties, start over and "
                    "upload one at a time. If they are all one property, enter "
                    "its DP number below."
                ),
            },
        )

    job = build_combined_job(saved)
    if not job.dp:
        return _view(
            request,
            "_intake_need_dp.html",
            {"batch_id": batch_id, "files": [p.name for p in saved], "count": len(saved)},
        )
    return _finalize_intake(request, db_path, output_root, job, batch_dir)


@router.post("/finalize", response_class=HTMLResponse)
async def finalize(
    request: Request,
    batch_id: str = Form(...),
    dp: str = Form(...),
    user: dict = Depends(auth.require_role("marketing")),
):
    """Second step of a no-DP upload: the user supplied the DP; proceed with it."""
    from engine.intake import build_combined_job, dp_candidates

    db_path = auth.db_path_for(request)
    output_root = _output_root(db_path)

    batch_dir = _tray_dir(output_root, batch_id)
    # Every staged file, not just lower-case ".pdf": upload stages whatever was
    # dropped, so a scanner-named "3060 REPORT.PDF" must be found here too or it
    # would be reported missing and then destroyed with the tray.
    staged = sorted(p for p in batch_dir.iterdir() if p.is_file()) if (batch_dir and batch_dir.is_dir()) else []
    if not staged:
        return _view(
            request,
            "_intake_error.html",
            {"message": "The staged files could not be found. Please upload them again."},
            status_code=400,
        )

    clean = _normalize_dp_input(dp)
    if not clean:
        return _view(
            request,
            "_intake_need_dp.html",
            {
                "batch_id": batch_id,
                "files": [p.name for p in staged],
                "count": len(staged),
                "error": "Enter a DP number like 3060 or 3035.1.",
            },
            status_code=400,
        )

    # The typed DP wins deliberately, even when a filename carries a different
    # number: the prompt that sent the user here already listed every number it
    # found and asked her to confirm which property this is (or to upload the
    # properties separately). Blocking here as well would dead-end the legitimate
    # cases - a street-numbered valuer's report, a scanner date stamp, sub-lots -
    # with no way through.
    job = build_combined_job(staged, dp=clean)
    return _finalize_intake(request, db_path, output_root, job, batch_dir)


def _finalize_intake(request: Request, db_path, output_root: str, job, batch_dir: Optional[Path]):
    """Relocate a combined job's files, create the record, enqueue extraction.

    Shared by the direct upload path and the DP-prompt finalize path. All of the
    property's EVMs, Property Reports and valuations are moved into its uploads
    folder and passed to extraction as lists (one combined record).
    """
    from engine.schema import PropertyRecord
    from engine.store import RecordStore

    dp = job.dp
    uploads_dir = Path(output_root) / f"DP{dp}" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    def _relocate(src: Path) -> str:
        target = uploads_dir / src.name
        if src.resolve() != target.resolve():
            target.write_bytes(src.read_bytes())
        return str(target)

    lightstones = [_relocate(p) for p in job.lightstone_evms]
    property_reports = [_relocate(p) for p in job.property_reports]
    valuations = [_relocate(p) for p in job.valuation_reports]  # optional (D35)
    for extra in job.unknown:  # keep unclassified files with the property too
        _relocate(extra)

    # The staged batch is now copied into the property folder; drop the tray.
    if batch_dir is not None:
        shutil.rmtree(batch_dir, ignore_errors=True)

    # Create the base record only if this DP is new; never overwrite an
    # already-extracted record's JSON with an empty shell.
    store = RecordStore(models.resolve_db_path(db_path))
    try:
        if store.get(dp) is None:
            base = PropertyRecord(dp=dp, parent_dp=job.parent_dp)
            store.upsert(base, state="intake")
    finally:
        store.close()

    payload = {
        "dp": dp,
        # parent_dp travels with the job: extraction rebuilds the record from the
        # sources and would otherwise null a sub-property's parent link (3035.1).
        "parent_dp": job.parent_dp,
        "lightstones": lightstones,
        "property_reports": property_reports,
        "valuations": valuations,
        "output_root": output_root,
    }
    job_id = jobs.enqueue(db_path, "extract", dp, payload=payload)

    row = models.get_job(db_path, job_id)
    ctx = _job_ctx(row)
    ctx["missing"] = job.missing  # e.g. no Property Report among the files
    return _view(request, "_intake_job.html", ctx)


@router.get("/job/{job_id}", response_class=HTMLResponse)
def job_status(request: Request, job_id: int):
    if auth.current_user(request) is None:
        return RedirectResponse("/login", status_code=303)
    row = models.get_job(auth.db_path_for(request), job_id)
    if row is None:
        return _view(
            request,
            "_intake_error.html",
            {"message": "That job could not be found."},
            status_code=404,
        )
    return _view(request, "_intake_job.html", _job_ctx(row))
