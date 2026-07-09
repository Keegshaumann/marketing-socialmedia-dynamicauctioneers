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

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from webapp import auth, jobs, models

router = APIRouter(prefix="/intake")

# Job states the worker uses that mean "still working" (keep polling).
_ACTIVE = {"queued", "running"}


def _view(request: Request, name: str, ctx: Optional[dict] = None, status_code: int = 200):
    templates = request.app.state.templates
    data: Dict[str, Any] = {"user": auth.current_user(request)}
    if ctx:
        data.update(ctx)
    return templates.TemplateResponse(request, name, data, status_code=status_code)


def _output_root(db_path: str) -> str:
    return models.get_setting(db_path, "output_root") or "."


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
    return {"job": job, "polling": polling, "tone": tone}


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
    from engine.intake import build_jobs
    from engine.schema import PropertyRecord
    from engine.store import RecordStore

    db_path = auth.db_path_for(request)

    if not files:
        return _view(
            request,
            "_intake_error.html",
            {"message": "No files were received. Drop the two source PDFs to begin."},
            status_code=400,
        )

    # Save each upload to a temporary tray, then let build_jobs read the DP
    # number from the filenames and classify each PDF.
    output_root = _output_root(db_path)
    tray = Path(output_root) / "_intake_tray"
    tray.mkdir(parents=True, exist_ok=True)

    saved: List[Path] = []
    for upload_file in files:
        name = Path(upload_file.filename or "").name
        if not name:
            continue
        dest = tray / name
        dest.write_bytes(await upload_file.read())
        saved.append(dest)

    intake_jobs = build_jobs(saved)
    if not intake_jobs:
        return _view(
            request,
            "_intake_error.html",
            {
                "message": "Could not read a DP number from the filenames. "
                "Name each file with its DP number, for example "
                "'3060 - PROPERTY REPORT.pdf'.",
            },
            status_code=400,
        )

    # Prefer a complete pair; otherwise take the first job and note the gap.
    job = next((j for j in intake_jobs if j.is_complete), intake_jobs[0])
    dp = job.dp

    # Move the pair into the property's own uploads folder.
    uploads_dir = Path(output_root) / f"DP{dp}" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    def _relocate(src: Optional[Path]) -> Optional[str]:
        if src is None:
            return None
        target = uploads_dir / src.name
        if src.resolve() != target.resolve():
            target.write_bytes(src.read_bytes())
        return str(target)

    lightstone = _relocate(job.lightstone_evm)
    property_report = _relocate(job.property_report)

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
        "lightstone": lightstone,
        "property_report": property_report,
        "output_root": output_root,
    }
    job_id = jobs.enqueue(db_path, "extract", dp, payload=payload)

    missing = job.missing  # e.g. one document could not be classified
    row = models.get_job(db_path, job_id)
    ctx = _job_ctx(row)
    ctx["missing"] = missing
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
