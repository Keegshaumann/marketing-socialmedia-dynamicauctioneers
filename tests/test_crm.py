"""Buyer CRM seed tests (M7, Phase 7).

All offline and credential-free. A file-backed SQLite database under ``tmp_path``
is shared by the record store and the CRM tables (``record_enquiry`` looks the
DP's record up through its own ``RecordStore``), so a real db file is used rather
than ``:memory:``. Covers enquiry tagging (DP + derived category / area / band),
the matched-buyer query on seeded contacts, and the broadcast line (correct
wording, hyphen not em dash).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.crm import broadcast_text, matched_buyers, record_enquiry
from engine.schema import Identity, Marketing, Physical, PropertyRecord
from engine.store import RecordStore


# --- record builders / seeding -------------------------------------------

def _residential(dp: str = "3060") -> PropertyRecord:
    return PropertyRecord(
        dp=dp,
        identity=Identity(title_type="sectional", suburb="Pelham North"),
        physical=Physical(zoning="Residential"),
        marketing=Marketing(price_display="R1 500 000"),
    )


def _industrial(dp: str = "4000") -> PropertyRecord:
    return PropertyRecord(
        dp=dp,
        identity=Identity(title_type="freehold", suburb="Mkondeni"),
        physical=Physical(zoning="Industrial"),
        marketing=Marketing(price_display="R8 000 000"),
    )


def _seed(db_path: Path, record: PropertyRecord) -> None:
    """Persist a record so the CRM can derive tags from it."""
    store = RecordStore(db_path)
    try:
        store.upsert(record, state="verified")
    finally:
        store.close()


# --- enquiry tagging -----------------------------------------------------

def test_record_enquiry_tags_dp_and_category(tmp_path):
    db_path = tmp_path / "engine.db"
    _seed(db_path, _residential("3060"))

    contact = record_enquiry(
        db_path,
        source="sms",
        raw="reply 3060, keen buyer, jane@example.com",
    )

    assert contact.dp == "3060"
    assert contact.category == "residential"
    assert contact.area == "Pelham North"
    # R1 500 000 falls in the R1m to R2m band.
    assert contact.budget_band == "R1m_R2m"
    # Deduplicated on the email handle.
    assert contact.handle == "jane@example.com"


def test_record_enquiry_industrial_category(tmp_path):
    db_path = tmp_path / "engine.db"
    _seed(db_path, _industrial("4000"))

    contact = record_enquiry(
        db_path,
        source="facebook",
        raw="?dp=4000 needs warehouse space, mail sam@example.com",
    )

    assert contact.dp == "4000"
    assert contact.category == "industrial"
    assert contact.area == "Mkondeni"
    assert contact.budget_band == "R5m_plus"


def test_record_enquiry_same_handle_updates_in_place(tmp_path):
    db_path = tmp_path / "engine.db"
    _seed(db_path, _residential("3060"))

    first = record_enquiry(db_path, "email", "reply 3060 from bob@example.com")
    second = record_enquiry(db_path, "email", "reply 3060 again from bob@example.com")

    # Same contact row updated, not duplicated.
    assert second.id == first.id


# --- matched buyers ------------------------------------------------------

def test_matched_buyers_returns_matches(tmp_path):
    db_path = tmp_path / "engine.db"
    industrial = _industrial("4000")
    _seed(db_path, industrial)
    _seed(db_path, _residential("3060"))

    # Two buyers enquire about the industrial listing (tagged industrial /
    # Mkondeni / R5m_plus).
    record_enquiry(db_path, "sms", "reply 4000, amy@example.com")
    record_enquiry(db_path, "facebook", "lead=99001 ?dp=4000")
    # An unrelated residential buyer must not match.
    record_enquiry(db_path, "email", "reply 3060, tom@example.com")

    matched = matched_buyers(db_path, industrial)
    assert len(matched) == 2
    handles = {c.handle for c in matched}
    assert "amy@example.com" in handles
    assert "fb:99001" in handles
    for contact in matched:
        assert contact.category == "industrial"
        assert contact.area == "Mkondeni"
        assert contact.budget_band == "R5m_plus"


def test_matched_buyers_empty_when_no_contacts(tmp_path):
    db_path = tmp_path / "engine.db"
    industrial = _industrial("4000")
    _seed(db_path, industrial)
    assert matched_buyers(db_path, industrial) == []


# --- broadcast line ------------------------------------------------------

def test_broadcast_text_reads_correctly_and_has_no_em_dash(tmp_path):
    db_path = tmp_path / "engine.db"
    industrial = _industrial("4000")
    _seed(db_path, industrial)

    record_enquiry(db_path, "sms", "reply 4000, amy@example.com")
    record_enquiry(db_path, "facebook", "lead=99001 ?dp=4000")
    matched = matched_buyers(db_path, industrial)

    line = broadcast_text(industrial, matched)
    assert line == "new industrial property in Mkondeni - 2 matched buyers"
    # A hyphen, never an em or en dash.
    assert "—" not in line
    assert "–" not in line


def test_broadcast_text_singular_buyer(tmp_path):
    db_path = tmp_path / "engine.db"
    industrial = _industrial("4000")
    _seed(db_path, industrial)

    record_enquiry(db_path, "sms", "reply 4000, amy@example.com")
    matched = matched_buyers(db_path, industrial)

    line = broadcast_text(industrial, matched)
    assert line == "new industrial property in Mkondeni - 1 matched buyer"
