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


def test_re_extraction_is_refused_once_gate_1_is_signed_off(tmp_path, monkeypatch):
    """Re-extracting a signed-off property must not rewrite its facts.

    Extraction replaces the whole sourced layer (identity, physical, valuation
    and the gate-1 conflict resolutions). Past gate 1 those facts underpin a
    verification memo, a drafted advert and possibly a live listing, and the
    state machine has no backward move to re-run the gates, so the job refuses
    rather than leaving a sign-off that vouches for facts nobody checked.
    """
    from engine.schema import Verification

    db_path = str(tmp_path / "engine.db")
    dp = "3050.2"
    models.init_db(db_path)

    worked = PropertyRecord(
        dp=dp, parent_dp="3050", status="drafted",
        identity=Identity(title_type="sectional", suburb="Pelham North"),
        marketing=Marketing(headline="A tidy unit", hero_photo="photos/front.png"),
        verification=Verification(status="verified", human_signoff="nikki@example.com"),
    )
    store = RecordStore(db_path)
    store.upsert(worked, state="drafted")
    store.close()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    called = []
    monkeypatch.setattr(
        "engine.extract.extract_record",
        lambda *a, **k: called.append(1) or PropertyRecord(dp=dp, status="extracted"),
    )

    job_id = jobs.enqueue(db_path, "extract", dp, payload={
        "dp": dp, "lightstones": ["evm.pdf"], "property_reports": ["report.pdf"],
        "output_root": str(tmp_path),
    })
    assert jobs.drain(db_path) == 1

    assert models.get_job(db_path, job_id)["state"] == "skipped: already signed off"
    assert not called, "extraction must not run over a signed-off record"

    store = RecordStore(db_path)
    try:
        after = store.get(dp)
        assert after.marketing.headline == "A tidy unit"     # untouched
        assert after.verification.human_signoff == "nikki@example.com"
        assert store.get_state(dp) == "drafted"
    finally:
        store.close()


def test_re_extraction_before_gate_1_keeps_photos_but_not_the_memo(tmp_path, monkeypatch):
    """A retry before sign-off is safe: photo picks survive, the memo does not."""
    from engine.schema import Verification

    db_path = str(tmp_path / "engine.db")
    dp = "3051.1"
    models.init_db(db_path)

    earlier = PropertyRecord(
        dp=dp, parent_dp="3051", status="extracted",
        identity=Identity(title_type="sectional", suburb="Pelham North"),
        marketing=Marketing(hero_photo="photos/front.png", gallery=["photos/kitchen.png"],
                            template_set="collage"),
        verification=Verification(status="flags_raised", memo="old memo"),
        human_overrides={"identity.suburb": "Pelham"},
    )
    store = RecordStore(db_path)
    store.upsert(earlier, state="extracted")
    store.close()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "engine.extract.extract_record",
        lambda *a, **k: PropertyRecord(
            dp=dp, status="extracted",
            identity=Identity(title_type="sectional", suburb="Pelham North"),
        ),
    )

    jobs.enqueue(db_path, "extract", dp, payload={
        "dp": dp, "parent_dp": "3051",
        "lightstones": ["evm.pdf"], "property_reports": ["report.pdf"],
        "output_root": str(tmp_path),
    })
    assert jobs.drain(db_path) == 1

    store = RecordStore(db_path)
    try:
        after = store.get(dp)
        # Human-owned, fact-independent work survives.
        assert after.marketing.hero_photo == "photos/front.png"
        assert after.marketing.gallery == ["photos/kitchen.png"]
        assert after.marketing.template_set == "collage"
        assert after.human_overrides == {"identity.suburb": "Pelham"}
        assert after.parent_dp == "3051"
        # The memo described the OLD facts; it must be re-run, not inherited.
        assert after.verification is None
    finally:
        store.close()


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
