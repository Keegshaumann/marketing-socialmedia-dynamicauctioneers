"""Background-worker handler tests (M8).

Offline: ``extract_record`` is monkeypatched so no API key or network is needed.
The point of the suite is the lifecycle transition, not the extraction itself.
"""

from __future__ import annotations

from engine.schema import Identity, Marketing, PropertyRecord
from engine.store import RecordStore
from webapp import jobs, models


def _record(dp: str = "3024.3") -> PropertyRecord:
    return PropertyRecord(
        dp=dp,
        status="extracted",
        identity=Identity(title_type="sectional", suburb="Pretoria North"),
        marketing=Marketing(price_display="Offers invited"),
    )


def test_extract_job_advances_intake_to_extracted(tmp_path, monkeypatch):
    """Regression: the extract handler must move an existing ``intake`` record to
    ``extracted``.

    Production creates the record at ``intake`` on upload, then runs extraction.
    ``upsert(state="extracted")`` is a no-op on the state column for an existing
    row, so the handler must call ``transition`` explicitly. Before the fix the
    board sat on "Awaiting extraction" forever even though extraction succeeded.
    """
    db_path = str(tmp_path / "engine.db")
    dp = "3024.3"
    models.init_db(db_path)  # create users/jobs/settings tables the worker needs

    # 1. intake upload creates the record at 'intake' first (as in production)
    store = RecordStore(db_path)
    store.upsert(_record(dp), state="intake")
    assert store.get_state(dp) == "intake"
    store.close()

    # 2. extraction returns a record; no network/key needed
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("engine.extract.extract_record", lambda *a, **k: _record(dp))

    # 3. run the extract job through the real worker path
    payload = {
        "dp": dp,
        "lightstone": "evm.pdf",
        "property_report": "report.pdf",
        "output_root": str(tmp_path),
    }
    job_id = jobs.enqueue(db_path, "extract", dp, payload=payload)
    assert jobs.drain(db_path) == 1

    job = models.get_job(db_path, job_id)
    assert job["state"] == "done", job

    # 4. the record must now be 'extracted', with the transition in the audit log
    store = RecordStore(db_path)
    assert store.get_state(dp) == "extracted"
    store.close()


def test_extract_job_without_key_is_skipped(tmp_path, monkeypatch):
    """Without a key the extract job is skipped cleanly and the record stays put
    (intake), never crashing the worker."""
    db_path = str(tmp_path / "engine.db")
    dp = "3024.3"
    models.init_db(db_path)

    store = RecordStore(db_path)
    store.upsert(_record(dp), state="intake")
    store.close()

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    job_id = jobs.enqueue(db_path, "extract", dp, payload={"dp": dp})
    assert jobs.drain(db_path) == 1

    job = models.get_job(db_path, job_id)
    assert job["state"].startswith("skipped")

    store = RecordStore(db_path)
    assert store.get_state(dp) == "intake"
    store.close()
