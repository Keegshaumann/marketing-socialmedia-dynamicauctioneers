"""Background job queue and worker thread.

Slow work for the property documents runs off the request path: a route enqueues
a job (``enqueue``) and HTMX polls its status (``get_job``); one background thread
(``Worker``) drains the queue a job at a time, so SQLite writes never contend.

Job kinds:

- ``proposal_prefill`` / ``otp_prefill`` / ``report_prefill`` -- KEY-GATED: read a
  document's new Lightstone reports into its blank fields; without
  ``ANTHROPIC_API_KEY`` the job is skipped and the fields are typed by hand.
- ``proposal_publish`` -- CREDENTIAL-GATED: save a generated proposal in
  SharePoint (dormant behind ``MS_GRAPH_*``, D105).

Hard rule: the worker NEVER crashes. Every handler runs inside a guard; any
exception is recorded on the job row as ``state="error"`` with the message, and
the loop moves on.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from webapp import models
from webapp.models import get_job  # re-exported: get_job(db_path, id)

log = logging.getLogger(__name__)

JOB_KINDS = ("proposal_prefill", "proposal_publish", "otp_prefill", "report_prefill")

_POLL_INTERVAL = 0.25  # seconds between queue polls when idle


# --- enqueue --------------------------------------------------------------

def enqueue(
    db_path: Optional[str],
    kind: str,
    dp: Optional[str],
    payload: Optional[dict] = None,
) -> int:
    """Queue a job of ``kind`` for ``dp`` and return its id.

    ``payload`` carries per-job arguments (e.g. ``output_root``, source paths).
    Raises ``ValueError`` for an unknown kind so a typo fails fast at the caller.
    """
    if kind not in JOB_KINDS:
        raise ValueError(f"Unknown job kind {kind!r}. Known: {', '.join(JOB_KINDS)}.")
    return models.insert_job(db_path, kind=kind, dp=dp, payload=payload, state="queued")


def _has_api_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _output_root(job: Dict[str, Any], db_path: Optional[str]) -> str:
    payload = job.get("payload") or {}
    if payload.get("output_root"):
        return str(payload["output_root"])
    setting = models.get_setting(db_path, "output_root")
    return setting or "."


# --- handlers -------------------------------------------------------------
# Each returns (state, detail). They may raise; the worker guard records it.
# The marketing pipeline's extract / verify / render / post jobs were removed with
# that pipeline (D127); the Ad Studio reads documents on its own thread.

def _proposal_root(job: Dict[str, Any], db_path: Optional[str], folder: str = "proposal") -> Path:
    return Path(_output_root(job, db_path)) / f"DP{job.get('dp')}" / folder


def _prefill_from_lightstone(
    db_path: Optional[str], job: Dict[str, Any], store_cls, folder: str, what: str, apply=None
) -> Tuple[str, str]:
    """Read a document's new Lightstone reports into its blank fields. KEY-GATED.

    Shared by proposals (``folder`` "proposal"), OTPs ("otp") and property
    reports ("report"): each record carries the list of reports already read, and
    ``apply`` (default: the proposal's ``apply_facts``) fills its blank fields.
    """
    from engine.proposal import lightstone

    apply = apply or lightstone.apply_facts

    dp = job.get("dp")
    if not dp:
        return "error", "prefill job has no DP number."
    if not _has_api_key():
        return "skipped: no API key", f"no ANTHROPIC_API_KEY; the {what} fields are typed by hand."
    root = _proposal_root(job, db_path, folder)
    with store_cls(models.resolve_db_path(db_path)) as store:
        doc = store.get(dp)
    if doc is None:
        return "error", f"there is no {what} for DP {dp}."
    pending = [rel for rel in doc.lightstone_files if rel not in doc.lightstone_read]
    if not pending:
        return "done", "no new Lightstone report to read."

    facts = [lightstone.read_facts(root / rel) for rel in pending]

    # Load again before writing: the page may have been saved while the model read.
    with store_cls(models.resolve_db_path(db_path)) as store:
        doc = store.get(dp)
        if doc is None:
            return "error", f"the {what} for DP {dp} was deleted while its report was read."
        note = apply(doc, facts)
        doc.lightstone_read = sorted(set(doc.lightstone_read) | set(pending))
        doc.prefill_note = note
        store.save(doc, "lightstone")
    return "done", note


def _handle_proposal_prefill(db_path: Optional[str], job: Dict[str, Any]) -> Tuple[str, str]:
    """Read a proposal's new Lightstone reports into its blank fields (M9, D103). KEY-GATED."""
    from engine.proposal.store import ProposalStore

    return _prefill_from_lightstone(db_path, job, ProposalStore, "proposal", "proposal")


def _handle_otp_prefill(db_path: Optional[str], job: Dict[str, Any]) -> Tuple[str, str]:
    """The same for an OTP (M10, D114). KEY-GATED."""
    from engine.otpgen.store import OtpStore

    return _prefill_from_lightstone(db_path, job, OtpStore, "otp", "OTP")


def _handle_report_prefill(db_path: Optional[str], job: Dict[str, Any]) -> Tuple[str, str]:
    """The same for a property report (M11, D116), which also takes the municipal facts. KEY-GATED."""
    from engine.propertyreport.prefill import apply_facts
    from engine.propertyreport.store import ReportStore

    return _prefill_from_lightstone(db_path, job, ReportStore, "report", "property report", apply=apply_facts)


def _handle_proposal_publish(db_path: Optional[str], job: Dict[str, Any]) -> Tuple[str, str]:
    """Save a generated proposal in SharePoint and fetch its PDF (M9, D103). CREDENTIAL-GATED."""
    from engine.proposal import sharepoint
    from engine.proposal.store import ProposalStore

    dp = job.get("dp")
    if not dp:
        return "error", "publish job has no DP number."
    cfg = sharepoint.config_from_env()
    if cfg is None:
        return "skipped: SharePoint not connected", "MS_GRAPH_* are not set; the Word file is ready to download."
    root = _proposal_root(job, db_path)
    with ProposalStore(models.resolve_db_path(db_path)) as store:
        proposal = store.get(dp)
    if proposal is None:
        return "error", f"there is no proposal for DP {dp}."

    try:
        published = sharepoint.publish(proposal, root, cfg)
    except Exception as exc:
        message = str(exc) if isinstance(exc, sharepoint.SharePointError) else (
            f"SharePoint could not be reached ({type(exc).__name__}). Generate again to retry."
        )
        with ProposalStore(models.resolve_db_path(db_path)) as store:
            latest = store.get(dp)
            if latest is not None:
                latest.pdf_note = message
                store.save(latest, "sharepoint")
        return "error", message

    # Only the SharePoint fields are copied onto the latest save, so edits made
    # while the upload ran are kept.
    with ProposalStore(models.resolve_db_path(db_path)) as store:
        latest = store.get(dp) or published
        for field in ("sharepoint_folder", "sharepoint_docx", "sharepoint_pdf", "pdf_file", "pdf_note"):
            setattr(latest, field, getattr(published, field))
        store.save(latest, "sharepoint")
    return "done", f"saved to {published.sharepoint_folder}"


_HANDLERS: Dict[str, Callable[[Optional[str], Dict[str, Any]], Tuple[str, str]]] = {
    "proposal_prefill": _handle_proposal_prefill,
    "proposal_publish": _handle_proposal_publish,
    "otp_prefill": _handle_otp_prefill,
    "report_prefill": _handle_report_prefill,
}


# --- single-job processing (shared by worker + tests) --------------------

def _claim_next(db_path: Optional[str]) -> Optional[Dict[str, Any]]:
    """Claim the oldest queued job by moving it to ``running``. Returns it or None.

    A single worker means the read-then-write is effectively serialised; the
    ``state='queued'`` guard on the UPDATE keeps it correct even if called twice.
    """
    resolved = models.resolve_db_path(db_path)
    import sqlite3

    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        row = conn.execute(
            "SELECT id FROM jobs WHERE state = 'queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE jobs SET state = 'running', updated_at = ? "
            "WHERE id = ? AND state = 'queued'",
            (models._now(), row["id"]),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None
        claimed = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (row["id"],)
        ).fetchone()
        return models._job_row_to_dict(claimed) if claimed is not None else None
    finally:
        conn.close()


def process_job(db_path: Optional[str], job: Dict[str, Any]) -> None:
    """Run one already-claimed job, recording the outcome. Never raises."""
    handler = _HANDLERS.get(job.get("kind"))
    if handler is None:
        models.update_job(
            db_path, job["id"], state="error",
            detail=f"no handler for kind {job.get('kind')!r}",
        )
        return
    try:
        state, detail = handler(db_path, job)
    except Exception as exc:  # the worker must never crash on a job
        models.update_job(
            db_path, job["id"], state="error", detail=f"{type(exc).__name__}: {exc}"
        )
        return
    models.update_job(db_path, job["id"], state=state, detail=detail)


def drain(db_path: Optional[str] = None, max_jobs: int = 1000) -> int:
    """Synchronously process all queued jobs (no thread). Returns the count.

    Handy for tests and for the self-check: enqueue, then ``drain`` to run the
    queue to completion in-process.
    """
    processed = 0
    while processed < max_jobs:
        job = _claim_next(db_path)
        if job is None:
            break
        process_job(db_path, job)
        processed += 1
    return processed


# --- background worker ----------------------------------------------------

class Worker:
    """A single background thread that drains the job queue.

    ``start()`` spawns a daemon thread; ``stop()`` signals it and joins. The loop
    claims one job at a time and sleeps briefly when the queue is empty.
    """

    def __init__(self, db_path: Optional[str] = None, poll_interval: float = _POLL_INTERVAL) -> None:
        self.db_path = models.resolve_db_path(db_path)
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "Worker":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="da-job-worker", daemon=True
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = _claim_next(self.db_path)
            except Exception:
                # Even a DB hiccup must not kill the loop.
                self._stop.wait(self.poll_interval)
                continue
            if job is None:
                self._stop.wait(self.poll_interval)
                continue
            try:
                process_job(self.db_path, job)
            except Exception:
                # process_job records handler failures itself; this guards the
                # rarer case where even recording the outcome raises (e.g. a DB
                # lock). The worker loop must never die.
                self._stop.wait(self.poll_interval)
