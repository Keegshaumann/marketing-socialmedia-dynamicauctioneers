"""Record-store and state-machine tests (M4).

All offline: an isolated SQLite file under ``tmp_path``. Covers the round-trip,
the indexed key columns, the illegal-transition guard and the audit trail.
"""

from __future__ import annotations

import pytest

from engine.schema import Identity, Marketing, PropertyRecord
from engine.store import IllegalTransition, RecordStore


def _record(dp: str = "3060") -> PropertyRecord:
    return PropertyRecord(
        dp=dp,
        status="extracted",
        identity=Identity(
            title_type="sectional",
            suburb="Pelham North",
        ),
        marketing=Marketing(price_display="Offers invited"),
    )


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "engine.db"
    s = RecordStore(db_path)
    yield s
    s.close()


def test_upsert_get_round_trip(store):
    record = _record()
    store.upsert(record, state="extracted")

    fetched = store.get("3060")
    assert fetched is not None
    assert fetched.model_dump() == record.model_dump()
    assert store.get_state("3060") == "extracted"


def test_get_missing_returns_none(store):
    assert store.get("0000") is None
    assert store.get_state("0000") is None


def test_upsert_populates_indexed_columns(store):
    store.upsert(_record(), state="extracted")

    listing = store.list_records()
    assert len(listing) == 1
    row = listing[0]
    assert row["dp"] == "3060"
    assert row["state"] == "extracted"
    assert row["suburb"] == "Pelham North"
    assert row["updated_at"] is not None


def test_initial_upsert_logs_state_event(store):
    store.upsert(_record(), state="extracted")

    events = store.conn.execute(
        "SELECT from_state, to_state FROM state_events WHERE dp = ? ORDER BY id",
        ("3060",),
    ).fetchall()
    assert len(events) == 1
    assert events[0]["from_state"] is None
    assert events[0]["to_state"] == "extracted"


def test_illegal_transition_extracted_to_live_raises(store):
    store.upsert(_record(), state="extracted")
    with pytest.raises(IllegalTransition):
        store.transition("3060", "live")
    # State is unchanged after the rejected move.
    assert store.get_state("3060") == "extracted"


def test_legal_transition_chain_and_audit_trail(store):
    store.upsert(_record(), state="extracted")

    store.transition("3060", "flags_raised", note="conflict on garages")
    store.transition("3060", "verified", note="human signoff")
    store.transition("3060", "drafted")

    assert store.get_state("3060") == "drafted"

    events = store.conn.execute(
        "SELECT from_state, to_state, note FROM state_events "
        "WHERE dp = ? ORDER BY id",
        ("3060",),
    ).fetchall()
    transitions = [(e["from_state"], e["to_state"]) for e in events]
    assert transitions == [
        (None, "extracted"),
        ("extracted", "flags_raised"),
        ("flags_raised", "verified"),
        ("verified", "drafted"),
    ]
    # Notes are recorded on the audit trail.
    assert events[1]["note"] == "conflict on garages"


def test_full_gate_chain_to_live(store):
    # The complete legal path through all three gates to live (SPEC 4.3).
    store.upsert(_record(), state="extracted")

    chain = [
        "flags_raised",
        "verified",       # gate 1
        "drafted",
        "approved",       # gate 2
        "client_approved",  # gate 3
        "assets_built",
        "live",
    ]
    for to_state in chain:
        store.transition("3060", to_state)

    assert store.get_state("3060") == "live"


def test_live_updated_and_terminal_transitions(store):
    # From live: a re-render (updated) back to live, then sold -> archived.
    store.upsert(_record(), state="live")

    store.transition("3060", "updated", note="price change")
    store.transition("3060", "live")
    store.transition("3060", "sold")
    store.transition("3060", "archived")

    assert store.get_state("3060") == "archived"
    # archived is terminal: no further move is allowed.
    with pytest.raises(IllegalTransition):
        store.transition("3060", "live")


def test_illegal_skip_within_gate_chain_raises(store):
    # verified may not jump straight to approved (drafting is skipped).
    store.upsert(_record(), state="verified")
    with pytest.raises(IllegalTransition):
        store.transition("3060", "approved")
    assert store.get_state("3060") == "verified"


def test_transition_unknown_record_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.transition("0000", "extracted")


def test_upsert_update_refreshes_without_relogging(store):
    store.upsert(_record(), state="extracted")

    record = _record()
    record.marketing.price_display = "Auction"
    store.upsert(record)  # update path

    # Still one state event (the initial one); update must not re-log.
    events = store.conn.execute(
        "SELECT id FROM state_events WHERE dp = ?", ("3060",)
    ).fetchall()
    assert len(events) == 1

    listing = store.list_records()
    assert listing[0]["state"] == "extracted"
    fetched = store.get("3060")
    assert fetched.marketing.price_display == "Auction"
