"""What Nikki has chosen and typed per property, kept in the app's own database.

One row per property: the facts (every field on the ads and pack), which fields
she edited by hand (a later "read the documents" never overwrites those), the
photo picks, whether it is a for-sale or an auction set, and the state of the
last document read.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from webapp import models

SCHEMA = """
CREATE TABLE IF NOT EXISTS studio (
    ref         TEXT PRIMARY KEY,
    facts       TEXT NOT NULL DEFAULT '{}',
    edited      TEXT NOT NULL DEFAULT '[]',
    photos      TEXT NOT NULL DEFAULT '{}',
    mode        TEXT NOT NULL DEFAULT '',
    reading     TEXT NOT NULL DEFAULT '',     -- '' | running | done | failed
    message     TEXT NOT NULL DEFAULT '',
    warnings    TEXT NOT NULL DEFAULT '[]',
    updated_at  TEXT NOT NULL DEFAULT '',
    updated_by  TEXT NOT NULL DEFAULT ''
);
"""

SLOTS = ("hero", "photo1", "photo2", "photo3", "aerial")

# Each property's set as the studio's own job sees it (D129): whether it is filed
# in SharePoint, and what the folder held when it was last read (photos_sig /
# docs_sig now, read_* when the words and photos were made), so the studio waits
# while files are still arriving and catches up when more arrive later.
SETS = """
CREATE TABLE IF NOT EXISTS studio_sets (
    ref         TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT '',     -- waiting | saving | saved | held | ready | failed | complete
    message     TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',     -- of what is in SharePoint now
    mode        TEXT NOT NULL DEFAULT '',
    cleared     TEXT NOT NULL DEFAULT '[]',   -- privacy warnings a person looked at
    folder_url  TEXT NOT NULL DEFAULT '',
    saved_at    TEXT NOT NULL DEFAULT '',
    read_at     TEXT NOT NULL DEFAULT '',     -- when the studio last read it by itself
    updated_at  TEXT NOT NULL DEFAULT ''
);
"""
_SET_COLUMNS = ("photos_sig", "docs_sig", "changed_at", "read_photos_sig", "read_docs_sig")


def _conn(db_path: Optional[str]) -> sqlite3.Connection:
    conn = models._connect(db_path)
    conn.executescript(SCHEMA)
    conn.executescript(SETS)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(studio_sets)")}
    for col in _SET_COLUMNS:
        if col not in have:
            conn.execute(f"ALTER TABLE studio_sets ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(db_path: Optional[str], ref: str) -> Optional[dict[str, Any]]:
    with _conn(db_path) as conn:
        row = conn.execute("SELECT * FROM studio WHERE ref = ?", (ref,)).fetchone()
    if not row:
        return None
    return {
        "ref": row["ref"], "facts": json.loads(row["facts"]), "edited": set(json.loads(row["edited"])),
        "photos": json.loads(row["photos"]), "mode": row["mode"], "reading": row["reading"],
        "message": row["message"], "warnings": json.loads(row["warnings"]),
        "updated_at": row["updated_at"], "updated_by": row["updated_by"],
    }


def save(db_path: Optional[str], ref: str, *, facts: Optional[dict] = None, edited: Optional[set] = None,
         photos: Optional[dict] = None, mode: Optional[str] = None, reading: Optional[str] = None,
         message: Optional[str] = None, warnings: Optional[list] = None, by: str = "") -> None:
    cols: dict[str, Any] = {}
    if facts is not None:
        cols["facts"] = json.dumps(facts)
    if edited is not None:
        cols["edited"] = json.dumps(sorted(edited))
    if photos is not None:
        cols["photos"] = json.dumps(photos)
    if mode is not None:
        cols["mode"] = mode
    if reading is not None:
        cols["reading"] = reading
    if message is not None:
        cols["message"] = message
    if warnings is not None:
        cols["warnings"] = json.dumps(warnings)
    cols["updated_at"] = _now()
    if by:
        cols["updated_by"] = by
    with _conn(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO studio (ref) VALUES (?)", (ref,))
        conn.execute(f"UPDATE studio SET {', '.join(f'{k} = ?' for k in cols)} WHERE ref = ?",
                     (*cols.values(), ref))


def all_rows(db_path: Optional[str]) -> dict[str, dict]:
    with _conn(db_path) as conn:
        rows = conn.execute("SELECT ref, mode, reading, updated_at, updated_by FROM studio").fetchall()
    return {r["ref"]: dict(r) for r in rows}


# --- the set (studio_sets) ----------------------------------------------------------

def get_set(db_path: Optional[str], ref: str) -> Optional[dict[str, Any]]:
    with _conn(db_path) as conn:
        r = conn.execute("SELECT * FROM studio_sets WHERE ref = ?", (ref,)).fetchone()
    if not r:
        return None
    return {**dict(r), "cleared": json.loads(r["cleared"])}


def all_sets(db_path: Optional[str]) -> dict[str, dict]:
    with _conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM studio_sets").fetchall()
    return {r["ref"]: {**dict(r), "cleared": json.loads(r["cleared"])} for r in rows}


def set_status(db_path: Optional[str], ref: str, **cols: Any) -> None:
    if "cleared" in cols:
        cols["cleared"] = json.dumps(cols["cleared"])
    cols["updated_at"] = _now()
    with _conn(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO studio_sets (ref) VALUES (?)", (ref,))
        conn.execute(f"UPDATE studio_sets SET {', '.join(f'{k} = ?' for k in cols)} WHERE ref = ?",
                     (*cols.values(), ref))
