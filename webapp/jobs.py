"""Background job queue and worker thread (M8, Phase 4).

The platform runs slow work (extraction, verification, rendering, posting) off
the request path. A route enqueues a job (``enqueue``) and HTMX polls its status
(``get_job``); a single background thread (``Worker``) drains the queue one job at
a time. One worker means no locking dance around SQLite writes.

Job kinds and how each degrades without an ``ANTHROPIC_API_KEY``:

- ``extract`` -- KEY-GATED. Extraction needs the model; without a key the job is
  marked ``skipped: no API key`` and the flow continues on the intake+photos +
  template paths (the record already exists from upload/intake).
- ``verify``  -- runs the deterministic checks (key-free) and writes the memo;
  the optional market research is key-gated inside the engine and simply omitted.
- ``render``  -- fully key-free: ``render_all`` falls back to template copy when
  no key is present, so every artifact still renders.
- ``post``    -- TOKEN-GATED. Needs a GHL token in settings; without it the job
  produces a ready-to-post pack / logs intent (Phase 5 scaffold) rather than
  posting, and is marked ``skipped``.

Hard rule: the worker NEVER crashes. Every handler runs inside a guard; any
exception is recorded on the job row as ``state="error"`` with the message, and
the loop moves on.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from webapp import models
from webapp.models import get_job  # re-exported: get_job(db_path, id)

JOB_KINDS = ("extract", "verify", "render", "post")

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

def _handle_render(db_path: Optional[str], job: Dict[str, Any]) -> Tuple[str, str]:
    """Render every artifact for the DP. Key-free (template copy fallback)."""
    from engine.render.service import render_all
    from engine.store import RecordStore

    dp = job.get("dp")
    if not dp:
        return "error", "render job has no DP number."

    store = RecordStore(models.resolve_db_path(db_path))
    try:
        artifacts = render_all(dp, store, output_root=_output_root(job, db_path))
    finally:
        store.close()
    fmts = ", ".join(sorted({a.fmt for a in artifacts}))
    return "done", f"rendered {len(artifacts)} artifacts ({fmts})"


def _handle_verify(db_path: Optional[str], job: Dict[str, Any]) -> Tuple[str, str]:
    """Run deterministic verification + memo. Research is key-gated in-engine."""
    from engine.store import RecordStore
    from engine.verify import verify as run_verify

    dp = job.get("dp")
    if not dp:
        return "error", "verify job has no DP number."

    store = RecordStore(models.resolve_db_path(db_path))
    try:
        # client stays None: the deterministic checks + memo run key-free; the
        # market-research section is simply omitted without a key.
        memo_path, flags = run_verify(dp, store, client=None)
    finally:
        store.close()
    blocks = sum(1 for f in flags if getattr(f, "severity", None) == "block")
    detail = f"memo written ({memo_path}); {len(flags)} flags, {blocks} blocking"
    if not _has_api_key():
        detail += " (market research skipped: no API key)"
    return "done", detail


def _handle_extract(db_path: Optional[str], job: Dict[str, Any]) -> Tuple[str, str]:
    """Extraction. KEY-GATED: skipped cleanly without a key."""
    if not _has_api_key():
        return (
            "skipped: no API key",
            "extraction needs ANTHROPIC_API_KEY; intake + photos + template "
            "paths continue without it.",
        )

    # With a key: run extraction over the uploaded pair if paths were supplied.
    payload = job.get("payload") or {}
    dp = job.get("dp")
    lightstone = payload.get("lightstone")
    property_report = payload.get("property_report")
    if not (dp and lightstone and property_report):
        return (
            "skipped: no API key",
            "no source pair on the job payload; nothing to extract.",
        )

    from engine.extract import extract_record
    from engine.store import RecordStore

    record = extract_record(lightstone, property_report, dp=dp)
    store = RecordStore(models.resolve_db_path(db_path))
    try:
        store.upsert(record, state="extracted")
    finally:
        store.close()
    return "done", f"extracted record for DP {dp}"


def _handle_post(db_path: Optional[str], job: Dict[str, Any]) -> Tuple[str, str]:
    """Distribution. TOKEN-GATED: without a GHL token, build a ready-to-post pack.

    Phase 5 (``engine.distribute``) owns the real posting client. This handler is
    forward-compatible: if that package is present and a token is configured it
    delegates; otherwise it logs a ``ready`` channel status per configured channel
    and marks the job skipped, so the flow never stalls waiting on credentials.
    """
    dp = job.get("dp")
    if not dp:
        return "error", "post job has no DP number."

    token = models.get_setting(db_path, "ghl_token")
    payload = job.get("payload") or {}
    version = int(payload.get("version", 1))
    channels = payload.get("channels") or [
        "property24",
        "own_website",
        "facebook",
        "whatsapp_broadcast",
        "email_list",
    ]

    if not token:
        for channel in channels:
            models.log_channel_status(db_path, dp, channel, version, "ready")
        return (
            "skipped: no GHL token",
            "no GoHighLevel token configured; a ready-to-post pack was logged "
            f"for {len(channels)} channels (Phase 5 scaffold).",
        )

    # A token exists: delegate to Phase 5 if it has landed, else log intent.
    try:
        from engine.distribute.ghl import post_to_planner  # type: ignore
    except Exception:
        for channel in channels:
            models.log_channel_status(db_path, dp, channel, version, "pending")
        return (
            "skipped: distribution pending",
            "GHL token present but engine.distribute is not available yet "
            "(Phase 5); intent logged.",
        )

    result = post_to_planner(dp, payload.get("artifacts"), channels, token=token)  # type: ignore
    for channel in channels:
        models.log_channel_status(db_path, dp, channel, version, "posted")
    return "done", f"posted to {len(channels)} channels via GHL ({result!r})"


_HANDLERS: Dict[str, Callable[[Optional[str], Dict[str, Any]], Tuple[str, str]]] = {
    "extract": _handle_extract,
    "verify": _handle_verify,
    "render": _handle_render,
    "post": _handle_post,
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
