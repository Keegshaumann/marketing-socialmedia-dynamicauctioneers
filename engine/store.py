"""SQLite record store plus lifecycle state machine (M4).

A ``PropertyRecord`` (engine.schema) is persisted as its canonical JSON blob
alongside a handful of indexed key columns for cheap listing and lookup. Every
lifecycle change is validated against the allowed-transitions table (SPEC 4.3)
and appended to an audit trail, so the record's history is never lost.

Design rules baked in here:
- The record JSON is the source of truth; the indexed columns (suburb,
  title_type, price_display) are denormalised copies for querying only.
- Transitions are enforced in code. An illegal move (for example
  ``extracted -> live``, which would skip verification and drafting) raises
  ``IllegalTransition`` rather than corrupting the state.
- ``state_events`` is append-only: the first insert logs ``None -> state`` and
  every ``transition`` logs ``from_state -> to_state`` with a timestamp.

Lifecycle states (SPEC 4.3), in order through the three gates:
``intake -> extracted -> flags_raised -> verified`` (gate 1: verification
sign-off) ``-> drafted -> approved`` (gate 2: internal ad approval)
``-> client_approved -> assets_built`` (gate 3: client approval, then asset
build) ``-> live``. A live record may then move to ``updated`` (a re-render,
for example a price change), ``sold`` or ``withdrawn``; ``sold`` and
``withdrawn`` move to ``archived`` (terminal). ``extracted`` may skip straight
to ``verified`` when no flags are raised.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from engine.schema import PropertyRecord


# Allowed lifecycle transitions (SPEC 4.3). A state maps to the set of states
# it may move to next. ``archived`` is terminal (empty set).
ALLOWED_TRANSITIONS: Dict[str, Set[str]] = {
    "intake": {"extracted"},
    "extracted": {"flags_raised", "verified"},
    "flags_raised": {"verified"},
    # After gate 1 the web flow inserts an optional "add photos" step before the
    # ad is drafted; "verified -> drafted" stays legal for programmatic paths.
    "verified": {"photos", "drafted"},
    "photos": {"drafted"},
    "drafted": {"approved"},
    "approved": {"client_approved"},
    "client_approved": {"assets_built"},
    "assets_built": {"live"},
    "live": {"updated", "sold", "withdrawn"},
    "updated": {"live", "approved", "client_approved", "sold", "withdrawn"},
    "sold": {"archived"},
    "withdrawn": {"archived"},
    "archived": set(),
}


class IllegalTransition(Exception):
    """Raised when a lifecycle transition is not permitted (SPEC 4.3)."""


def _resolve_db_path(db_path: Optional[str | Path]) -> str:
    """Resolve the database path: arg, else ``ENGINE_DB``, else ``./engine.db``."""
    if db_path is not None:
        return str(db_path)
    return os.environ.get("ENGINE_DB", "engine.db")


def _now() -> str:
    """UTC timestamp in ISO 8601, seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class RecordStore:
    """SQLite-backed store of ``PropertyRecord`` rows plus a state machine."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                dp            TEXT PRIMARY KEY,
                parent_dp     TEXT,
                state         TEXT NOT NULL,
                suburb        TEXT,
                title_type    TEXT,
                price_display TEXT,
                record_json   TEXT NOT NULL,
                created_at    TEXT,
                updated_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS state_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                dp         TEXT,
                from_state TEXT,
                to_state   TEXT,
                at         TEXT,
                note       TEXT
            );
            """
        )
        self.conn.commit()

    # --- writes ----------------------------------------------------------

    def upsert(self, record: PropertyRecord, state: str = "extracted") -> None:
        """Insert or update a record, populating the indexed key columns.

        On first insert the record enters ``state`` and a ``None -> state``
        row is logged to ``state_events``. On update the JSON and indexed
        columns are refreshed; the lifecycle state is left to ``transition``.
        """
        dp = record.dp
        record_json = record.model_dump_json()
        # Index columns come from public_view() so human_overrides (e.g. a
        # corrected suburb or price) show on the board, not the sourced value.
        public = record.public_view()
        identity = public.get("identity") or {}
        marketing = public.get("marketing") or {}
        suburb = identity.get("suburb")
        title_type = identity.get("title_type")
        price_display = marketing.get("price_display")
        now = _now()

        existing = self.conn.execute(
            "SELECT dp, created_at FROM records WHERE dp = ?", (dp,)
        ).fetchone()

        if existing is None:
            self.conn.execute(
                """
                INSERT INTO records (
                    dp, parent_dp, state, suburb, title_type, price_display,
                    record_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dp,
                    record.parent_dp,
                    state,
                    suburb,
                    title_type,
                    price_display,
                    record_json,
                    now,
                    now,
                ),
            )
            self._log_event(dp, None, state, now, "initial upsert")
        else:
            self.conn.execute(
                """
                UPDATE records
                   SET parent_dp = ?, suburb = ?, title_type = ?,
                       price_display = ?, record_json = ?, updated_at = ?
                 WHERE dp = ?
                """,
                (
                    record.parent_dp,
                    suburb,
                    title_type,
                    price_display,
                    record_json,
                    now,
                    dp,
                ),
            )
        self.conn.commit()

    def transition(self, dp: str, to_state: str, note: Optional[str] = None) -> None:
        """Move ``dp`` to ``to_state`` if the transition is allowed.

        Raises ``KeyError`` if the record is unknown and ``IllegalTransition``
        if the move is not permitted by ``ALLOWED_TRANSITIONS``. On success the
        record's state is updated and the move is appended to ``state_events``.
        """
        from_state = self.get_state(dp)
        if from_state is None:
            raise KeyError(f"No record for DP {dp}")

        allowed = ALLOWED_TRANSITIONS.get(from_state, set())
        if to_state not in allowed:
            raise IllegalTransition(
                f"Cannot move DP {dp} from '{from_state}' to '{to_state}'. "
                f"Allowed next states: {sorted(allowed) or 'none (terminal)'}."
            )

        now = _now()
        self.conn.execute(
            "UPDATE records SET state = ?, updated_at = ? WHERE dp = ?",
            (to_state, now, dp),
        )
        self._log_event(dp, from_state, to_state, now, note)
        self.conn.commit()

    def delete(self, dp: str) -> bool:
        """Remove a record and its state history. Irreversible.

        Returns True if a record was removed, False if the DP did not exist. The
        board-level delete for a property intaked in error; the state machine
        does not gate deletion (it removes the whole row, not a transition).
        """
        cur = self.conn.execute("DELETE FROM records WHERE dp = ?", (dp,))
        self.conn.execute("DELETE FROM state_events WHERE dp = ?", (dp,))
        self.conn.commit()
        return cur.rowcount > 0

    def record_signoff(
        self,
        dp: str,
        gate: str,
        user: str,
        note: Optional[str] = None,
    ) -> None:
        """Record a human sign-off for ``dp`` at ``gate`` without moving state.

        Gate 1 (verification), gate 2 (internal ad approval) and gate 3 (client
        approval) each need a named human on the audit trail. This appends a
        self-referential event (``current -> current``) capturing who signed
        off and why, so the sign-off is recorded even when the transition it
        authorises is applied separately. Raises ``KeyError`` for an unknown
        record.
        """
        current = self.get_state(dp)
        if current is None:
            raise KeyError(f"No record for DP {dp}")

        detail = f"signoff gate={gate} user={user}"
        if note:
            detail = f"{detail}: {note}"
        self._log_event(dp, current, current, _now(), detail)
        self.conn.commit()

    def _log_event(
        self,
        dp: str,
        from_state: Optional[str],
        to_state: str,
        at: str,
        note: Optional[str],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO state_events (dp, from_state, to_state, at, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (dp, from_state, to_state, at, note),
        )

    # --- reads -----------------------------------------------------------

    # Fields removed from the schema after records were already stored with
    # them. ``extra="forbid"`` would refuse to load such a record outright, so
    # the loader strips these known-legacy dotted paths before validation - a
    # record written before the change stays readable (and loses the dead field
    # on its next save). D19 removed the broadcast channel.
    # D19 removed the broadcast channel; D35 removed physical.garages_conflict
    # (folded into the structured physical.conflicts list).
    _LEGACY_PATHS: tuple = (
        ("marketing", "channel_routing", "whatsapp_broadcast"),
        ("physical", "garages_conflict"),
    )

    @classmethod
    def _strip_legacy(cls, data: dict) -> dict:
        for path in cls._LEGACY_PATHS:
            node = data
            for key in path[:-1]:
                node = node.get(key) if isinstance(node, dict) else None
                if node is None:
                    break
            if isinstance(node, dict):
                node.pop(path[-1], None)
        # D35: physical.conflicts changed from a list of free-text strings to a
        # list of structured PhysicalConflict objects. A pre-D35 record carries
        # strings, which no longer validate; drop the old-shape list (it is
        # regenerated on the next extraction) so the record still loads.
        physical = data.get("physical")
        if isinstance(physical, dict):
            conflicts = physical.get("conflicts")
            if isinstance(conflicts, list) and any(not isinstance(c, dict) for c in conflicts):
                physical.pop("conflicts", None)
        return data

    def get(self, dp: str) -> Optional[PropertyRecord]:
        """Return the stored ``PropertyRecord`` for ``dp``, or ``None``.

        Tolerates records stored under an older schema: known-removed fields
        are stripped before validation (see ``_LEGACY_PATHS``).
        """
        row = self.conn.execute(
            "SELECT record_json FROM records WHERE dp = ?", (dp,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row["record_json"])
        return PropertyRecord.model_validate(self._strip_legacy(data))

    def get_state(self, dp: str) -> Optional[str]:
        """Return the current lifecycle state for ``dp``, or ``None``."""
        row = self.conn.execute(
            "SELECT state FROM records WHERE dp = ?", (dp,)
        ).fetchone()
        return row["state"] if row is not None else None

    def lot_group(self, dp: str) -> List[str]:
        """Every DP sold under the same instruction as ``dp``, itself included.

        Sub-lots share a ``parent_dp`` (DP3035.1 and DP3035.2 both point at
        DP3035), which is how a nine-unit block in one scheme is one instruction
        with nine records. The column has been carried since D50 and never
        queried; the estate board (fix list 6.4) is the first thing that needs
        it. A property with no parent is its own group of one.

        Returned in DP order so a board lists its units the way a human would.
        """
        row = self.conn.execute(
            "SELECT parent_dp FROM records WHERE dp = ?", (dp,)
        ).fetchone()
        if row is None:
            return []
        parent = row["parent_dp"]
        if not parent:
            # It may itself be the parent of a set of lots.
            kids = self.conn.execute(
                "SELECT dp FROM records WHERE parent_dp = ? ORDER BY dp", (dp,)
            ).fetchall()
            return [dp] + [k["dp"] for k in kids] if kids else [dp]
        rows = self.conn.execute(
            "SELECT dp FROM records WHERE parent_dp = ? OR dp = ? ORDER BY dp",
            (parent, parent),
        ).fetchall()
        return [r["dp"] for r in rows]

    def list_records(self) -> List[dict]:
        """Return a lightweight listing: dp, state, suburb, updated_at."""
        rows = self.conn.execute(
            """
            SELECT dp, state, suburb, updated_at
              FROM records
             ORDER BY dp
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, dp: str) -> List[dict]:
        """Return the append-only ``state_events`` rows for ``dp``, oldest first."""
        rows = self.conn.execute(
            """
            SELECT id, dp, from_state, to_state, at, note
              FROM state_events
             WHERE dp = ?
             ORDER BY id
            """,
            (dp,),
        ).fetchall()
        return [dict(row) for row in rows]

    def internally_approved_since_last_edit(self, dp: str) -> bool:
        """Whether an internal gate-2 sign-off followed the most recent field edit.

        A small-edit repost needs exactly one internal approval, and anything
        that opens a fresh edit cycle after that approval invalidates it. Two
        things open a cycle: a field edit (``gate=edit``/``gate=price`` on the
        note) and a ``live -> updated`` reopen (which may carry no edit event of
        its own, e.g. a change request or a bare re-open). ``gate=repost`` is the
        internal repost approval (distinct from a ``gate=2`` change-request, so a
        change request never counts as approval). Returns ``False`` when no
        approval followed the last cycle-opening event.
        """
        last_edit = None
        last_approval = None
        for ev in self.list_events(dp):
            note = ev.get("note") or ""
            eid = ev.get("id")
            reopened = ev.get("from_state") == "live" and ev.get("to_state") == "updated"
            if reopened or "gate=edit" in note or "gate=price" in note:
                last_edit = eid
            elif "gate=repost" in note:
                last_approval = eid
        if last_approval is None:
            return False
        return last_edit is None or last_approval > last_edit

    def close(self) -> None:
        self.conn.close()
