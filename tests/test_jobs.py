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


def test_identical_documents_reuse_the_cached_extraction(tmp_path, monkeypatch):
    """Re-intaking the same PDFs must not pay for extraction twice.

    Extraction is the most expensive call in the system (six calls carrying both
    source PDFs). Deleting a property and dropping the same documents again - a
    marketer fixing a mistake, or a tester repeating the flow - used to re-run
    all six. It is a pure function of the documents, so the second run is served
    from cache.
    """
    db_path = str(tmp_path / "engine.db")
    models.init_db(db_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ENGINE_AI_CACHE", "1")

    evm = tmp_path / "3070 - evm.pdf"; evm.write_bytes(b"%PDF-evm-bytes")
    rep = tmp_path / "3070 - report.pdf"; rep.write_bytes(b"%PDF-report-bytes")

    calls = []
    monkeypatch.setattr(
        "engine.extract.extract_record",
        lambda *a, **k: calls.append(1) or _record("3070"),
    )

    def run(dp):
        jobs.enqueue(db_path, "extract", dp, payload={
            "dp": dp, "lightstones": [str(evm)], "property_reports": [str(rep)],
            "output_root": str(tmp_path),
        })
        jobs.drain(db_path)

    run("3070")
    assert len(calls) == 1                       # first property: extracted

    # Same documents again (the delete-and-re-intake cycle) -> served from cache.
    run("3070")
    assert len(calls) == 1, "identical documents must not re-run extraction"

    # The same documents under a DIFFERENT DP also hit, and are re-stamped.
    run("3071")
    assert len(calls) == 1
    store = RecordStore(db_path)
    try:
        assert store.get("3071").dp == "3071"
    finally:
        store.close()

    # Different bytes -> a genuinely new property still extracts.
    evm.write_bytes(b"%PDF-a-different-property")
    run("3072")
    assert len(calls) == 2


# --- a valuation satisfies the inspection half (D88) -----------------------

def test_a_lightstone_plus_a_valuation_is_enough_to_extract(tmp_path, monkeypatch):
    """Reported from production: intake job 18 (DP 2677) was SKIPPED after a
    Lightstone EVM and a professional valuer's report were uploaded, because the
    gate demanded a Property Report specifically.

    D35 ranks a valuation ABOVE a property report for physical facts, so that
    combination is a BETTER pair than the one the gate insisted on.
    """
    from webapp import jobs

    seen = {}

    def _fake_extract(*a, **kw):
        seen.update(kw)
        raise RuntimeError("stop after the gate")

    monkeypatch.setattr("engine.extract.extract_record", _fake_extract, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")

    # Reaching the stub at all proves the gate let it through; the stub raises
    # so no real extraction is attempted.
    try:
        state, detail = jobs._handle_extract(
            str(tmp_path / "t.db"),
            {"dp": "2677", "payload": {
                "dp": "2677",
                "lightstones": [str(tmp_path / "ls.pdf")],
                "property_reports": [],
                "valuations": [str(tmp_path / "val.pdf")],
                "output_root": str(tmp_path),
            }},
        )
        assert "incomplete sources" not in (state or ""), detail
    except RuntimeError as exc:
        assert "stop after the gate" in str(exc)

    # And the valuation reached extraction as the valuer's source.
    assert seen, "extraction was never called: the gate still refuses the pair"
    assert "val.pdf" in str(seen.get("valuation_pdf"))


def test_the_skip_message_says_what_is_missing(tmp_path, monkeypatch):
    """The old message read "no source pair on the job payload" under a heading
    that said "Pair received" - it contradicted itself and named neither the
    document that was missing nor what to do about it."""
    from webapp import jobs

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    state, detail = jobs._handle_extract(
        str(tmp_path / "t.db"),
        {"dp": "2677", "payload": {
            "dp": "2677", "lightstones": [], "property_reports": [],
            "valuations": [str(tmp_path / "val.pdf")],
            "output_root": str(tmp_path),
        }},
    )
    assert "incomplete sources" in state
    assert "Lightstone EVM" in detail          # names what is missing
    assert "valuation" in detail               # names what did arrive
    assert "uploads folder" in detail          # says what to do next
