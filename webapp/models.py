"""Webapp persistence layer (M8, Phase 4).

The engine's ``RecordStore`` owns the ``records`` and ``state_events`` tables and
is the source of truth for property records and their lifecycle. This module
adds the *platform's own* tables on the same SQLite database, so the web app and
the engine share one file (env ``ENGINE_DB`` or ``./engine.db``):

- ``users``          -- login accounts (email, bcrypt hash, role)
- ``jobs``           -- the background work queue the worker thread drains
- ``settings``       -- key/value platform config (GHL token, Canva creds, ...)
- ``channel_status`` -- proof-of-marketing log (per DP, per channel, per version)
- ``used_tokens``    -- single-use nonce ledger for approve-by-email links
- ``app_secret``     -- one persisted secret shared by sessions + tokens

Design rules baked in here:
- ``init_db`` is idempotent (``CREATE TABLE IF NOT EXISTS``) and never touches the
  engine's tables beyond letting ``RecordStore`` create them, so wiring the two
  layers together cannot corrupt records.
- Every helper opens and closes its own short-lived connection. That keeps the
  helpers safe to call from the worker thread and from request handlers without
  sharing a connection across threads (SQLite forbids that).
- ``jobs.payload`` is an additive JSON column (nullable). It carries per-job
  arguments (output_root, source paths, ...) that the named columns in the wiring
  contract do not model; every contract column is present and unchanged.
- No PII lives in any table here: property PII stays inside the engine record and
  is stripped by ``public_view()`` before any artifact is produced.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# --- connection + time helpers -------------------------------------------

def resolve_db_path(db_path: Optional[str | Path] = None) -> str:
    """Resolve the database path: arg, else ``ENGINE_DB``, else ``./engine.db``.

    Matches ``engine.store._resolve_db_path`` so the web app and the engine open
    exactly the same file.
    """
    if db_path is not None:
        return str(db_path)
    return os.environ.get("ENGINE_DB", "engine.db")


def _connect(db_path: Optional[str | Path]) -> sqlite3.Connection:
    conn = sqlite3.connect(resolve_db_path(db_path))
    conn.row_factory = sqlite3.Row
    # The single worker thread and the request handlers all write this file.
    # WAL lets readers and a writer coexist; a busy_timeout makes a brief lock
    # wait instead of raising "database is locked" under concurrency.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now() -> str:
    """UTC timestamp in ISO 8601, seconds precision (matches engine.store)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- schema ---------------------------------------------------------------

def init_db(db_path: Optional[str | Path] = None) -> str:
    """Create the webapp tables (idempotent). Returns the resolved db path.

    Also instantiates ``engine.store.RecordStore`` once so the engine's
    ``records``/``state_events`` tables exist on the same file. Safe to call on
    every startup.
    """
    resolved = resolve_db_path(db_path)

    # Let the engine create its own tables on this file (records, state_events).
    from engine.store import RecordStore

    store = RecordStore(resolved)
    store.close()

    conn = _connect(resolved)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                email      TEXT PRIMARY KEY,
                pw_hash    TEXT NOT NULL,
                role       TEXT NOT NULL,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                dp         TEXT,
                kind       TEXT NOT NULL,
                state      TEXT NOT NULL,
                detail     TEXT,
                payload    TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS channel_status (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                dp      TEXT,
                channel TEXT,
                version INTEGER,
                status  TEXT,
                at      TEXT
            );

            CREATE TABLE IF NOT EXISTS used_tokens (
                jti     TEXT PRIMARY KEY,
                used_at TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Ensure the shared secret exists so sessions + tokens are stable across boots.
    get_or_create_secret(resolved)
    return resolved


# --- settings -------------------------------------------------------------

def get_setting(
    db_path: Optional[str | Path],
    key: str,
    default: Optional[str] = None,
) -> Optional[str]:
    """Return the stored value for ``key``, or ``default`` if unset."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row is not None else default
    finally:
        conn.close()


def set_setting(db_path: Optional[str | Path], key: str, value: str) -> None:
    """Insert or update a single setting."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def all_settings(db_path: Optional[str | Path] = None) -> Dict[str, str]:
    """Return every setting as a plain dict."""
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        conn.close()


def get_or_create_secret(db_path: Optional[str | Path] = None) -> str:
    """Return the platform secret, generating and persisting one on first call.

    Prefers the ``APP_SECRET`` env var (so a deployment can pin it); otherwise a
    random secret is generated once and stored in ``settings`` under
    ``app_secret``. Used by both the session cookie (main.py) and the
    approve-by-email token signer (tokens.py) so they share one key.
    """
    env = os.environ.get("APP_SECRET")
    if env:
        return env
    existing = get_setting(db_path, "app_secret")
    if existing:
        return existing
    generated = secrets.token_urlsafe(48)
    set_setting(db_path, "app_secret", generated)
    return generated


# --- users ----------------------------------------------------------------

def create_user(
    db_path: Optional[str | Path],
    email: str,
    pw_hash: str,
    role: str,
) -> None:
    """Insert a user. Raises ``sqlite3.IntegrityError`` if the email exists."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO users (email, pw_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (email.strip().lower(), pw_hash, role, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def get_user(db_path: Optional[str | Path], email: str) -> Optional[Dict[str, Any]]:
    """Return the user row as a dict (incl. ``pw_hash``), or ``None``."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT email, pw_hash, role, created_at FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_users(db_path: Optional[str | Path] = None) -> List[Dict[str, Any]]:
    """Return all users (without the password hash), ordered by email."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT email, role, created_at FROM users ORDER BY email"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_users(db_path: Optional[str | Path] = None) -> int:
    """Return the number of registered users (used to gate first-run seeding)."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])
    finally:
        conn.close()


def set_password(db_path: Optional[str | Path], email: str, pw_hash: str) -> None:
    """Replace a user's password hash (used for the forced-reset dev flow)."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE users SET pw_hash = ? WHERE email = ?",
            (pw_hash, email.strip().lower()),
        )
        conn.commit()
    finally:
        conn.close()


# --- jobs -----------------------------------------------------------------

def insert_job(
    db_path: Optional[str | Path],
    kind: str,
    dp: Optional[str],
    detail: Optional[str] = None,
    payload: Optional[dict] = None,
    state: str = "queued",
) -> int:
    """Insert a job row and return its new id."""
    conn = _connect(db_path)
    try:
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO jobs (dp, kind, state, detail, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dp,
                kind,
                state,
                detail,
                json.dumps(payload) if payload is not None else None,
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _job_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    raw = data.get("payload")
    if raw:
        try:
            data["payload"] = json.loads(raw)
        except (ValueError, TypeError):
            data["payload"] = None
    else:
        data["payload"] = None
    return data


def get_job(db_path: Optional[str | Path], job_id: int) -> Optional[Dict[str, Any]]:
    """Return a job row as a dict (``payload`` parsed to a dict), or ``None``."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _job_row_to_dict(row) if row is not None else None
    finally:
        conn.close()


def update_job(
    db_path: Optional[str | Path],
    job_id: int,
    state: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    """Update a job's ``state`` and/or ``detail`` and bump ``updated_at``."""
    sets = ["updated_at = ?"]
    params: List[Any] = [_now()]
    if state is not None:
        sets.append("state = ?")
        params.append(state)
    if detail is not None:
        sets.append("detail = ?")
        params.append(detail)
    params.append(job_id)

    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def list_jobs(
    db_path: Optional[str | Path] = None,
    dp: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Return jobs newest-first, optionally filtered to one DP."""
    conn = _connect(db_path)
    try:
        if dp is not None:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE dp = ? ORDER BY id DESC LIMIT ?",
                (dp, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_job_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def delete_jobs_for_dp(db_path: Optional[str | Path], dp: str) -> int:
    """Delete every job row for ``dp`` (used when a record is deleted). Count."""
    conn = _connect(db_path)
    try:
        cur = conn.execute("DELETE FROM jobs WHERE dp = ?", (dp,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# --- channel status (proof of marketing) ---------------------------------

def log_channel_status(
    db_path: Optional[str | Path],
    dp: str,
    channel: str,
    version: int,
    status: str,
) -> int:
    """Append a per-DP, per-channel, per-version posting record. Returns its id."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO channel_status (dp, channel, version, status, at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (dp, channel, version, status, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_channel_status(
    db_path: Optional[str | Path] = None,
    dp: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return channel-status rows newest-first, optionally filtered to one DP."""
    conn = _connect(db_path)
    try:
        if dp is not None:
            rows = conn.execute(
                "SELECT * FROM channel_status WHERE dp = ? ORDER BY id DESC",
                (dp,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM channel_status ORDER BY id DESC"
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# --- single-use token ledger ---------------------------------------------

def mark_token_used(db_path: Optional[str | Path], jti: str) -> bool:
    """Atomically claim a token nonce. Returns ``True`` if newly claimed, else
    ``False`` when the nonce was already spent (a reuse attempt).
    """
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO used_tokens (jti, used_at) VALUES (?, ?)",
            (jti, _now()),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def is_token_used(db_path: Optional[str | Path], jti: str) -> bool:
    """Return whether ``jti`` has already been spent (non-consuming)."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM used_tokens WHERE jti = ?", (jti,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()
