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
                            template_set="collage", multi_property_ad=False),
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
        assert after.marketing.multi_property_ad is False     # the tick-box answer (D112)
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


def _reaches_extraction(tmp_path, monkeypatch, dp: str, **sources) -> dict:
    """Run the extract job on ``sources`` with extraction stubbed; return the
    kwargs extraction was called with ({} when the job never got that far)."""
    from webapp import jobs

    seen = {}

    def _fake_extract(*a, **kw):
        seen.update(kw)
        seen["_args"] = a
        raise RuntimeError("stop after the gate")

    monkeypatch.setattr("engine.extract.extract_record", _fake_extract, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    payload = {"dp": dp, "lightstones": [], "property_reports": [], "valuations": [],
               "output_root": str(tmp_path)}
    payload.update(sources)
    try:
        state, detail = jobs._handle_extract(str(tmp_path / "t.db"), {"dp": dp, "payload": payload})
        assert not (state or "").startswith("skipped"), detail
    except RuntimeError as exc:
        assert "stop after the gate" in str(exc)
    return seen


# --- no property is refused for a missing document (D122) ------------------

def test_a_lightstone_alone_is_extracted(tmp_path, monkeypatch):
    """Reported from production: DP 3078.1 had only a Lightstone, the job was
    skipped as "incomplete sources", and the board showed "Awaiting extraction"
    with nothing to click. The team often has one document and nothing else."""
    seen = _reaches_extraction(tmp_path, monkeypatch, "3078.1",
                               lightstones=[str(tmp_path / "ls.pdf")])
    assert seen, "extraction was never called: a Lightstone alone is still refused"
    assert "ls.pdf" in str(seen["_args"][0])


def test_a_property_report_alone_is_extracted(tmp_path, monkeypatch):
    seen = _reaches_extraction(tmp_path, monkeypatch, "3079",
                               property_reports=[str(tmp_path / "pr.pdf")])
    assert seen, "extraction was never called: a Property Report alone is refused"


def test_no_recognised_document_says_what_to_upload(tmp_path, monkeypatch):
    """The one intake with nothing to read. The message names the documents
    that would do and where to upload them."""
    from webapp import jobs

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    state, detail = jobs._handle_extract(
        str(tmp_path / "t.db"),
        {"dp": "2677", "payload": {
            "dp": "2677", "lightstones": [], "property_reports": [], "valuations": [],
            "output_root": str(tmp_path),
        }},
    )
    assert state == "skipped: no documents"
    assert "Lightstone" in detail and "Property Report" in detail and "valuation" in detail
    assert "Intake" in detail


def test_the_directive_names_only_the_documents_supplied():
    """With a Lightstone alone the model must not be told about Property
    Reports that do not exist, and must leave room counts null."""
    from engine.extract import _docs_line

    alone = _docs_line(1, 0, 0)
    assert "Property Report(s)" not in alone and "0 " not in alone
    assert "no physical inspection" in alone
    # The ordinary pair is unchanged.
    assert _docs_line(1, 1, 0).startswith("The documents above are the Lightstone EVM report (first)")
    assert "no physical inspection" not in _docs_line(2, 1, 0)


def test_a_lightstone_alone_never_claims_zero_rooms():
    """Live on DP 3078.1: a Lightstone alone, a house on its aerial photograph,
    and the model answered 0 bedrooms, 0 bathrooms, 0 garages. Unstated is null,
    so gate 1 notes it and gate 2's Rooms box is empty for the team to fill."""
    from engine.extract import _blank_unstated_rooms
    from engine.schema import Physical, PropertyRecord

    def rec():
        return PropertyRecord(dp="3078.1", physical=Physical(bedrooms=0, bathrooms_main_unit=0, garages=2))

    alone = _blank_unstated_rooms(rec(), inspected=False).physical
    assert alone.bedrooms is None and alone.bathrooms_main_unit is None
    assert alone.garages == 2                      # a stated count is kept
    # With an inspection among the documents, a zero is the inspector's word.
    inspected = _blank_unstated_rooms(rec(), inspected=True).physical
    assert inspected.bedrooms == 0 and inspected.bathrooms_main_unit == 0


def test_our_own_dp_is_never_the_master_ref():
    """Live on DP 3078.1: the model copied the DP from its directive into the
    master ref, and the advert printed it twice under two different labels."""
    from engine.extract import normalize_record
    from engine.schema import Identity, PropertyRecord

    for ref in ("DP 3078.1", "DP3078.1", "3078.1", "dp 3078.1"):
        rec = normalize_record(PropertyRecord(dp="3078.1", identity=Identity(mandate_ref=ref)))
        assert rec.identity.mandate_ref is None, ref
    kept = normalize_record(PropertyRecord(dp="3078.1", identity=Identity(mandate_ref="B33/2025")))
    assert kept.identity.mandate_ref == "B33/2025"
    # Another property's number is not ours to drop.
    other = normalize_record(PropertyRecord(dp="3078.1", identity=Identity(mandate_ref="DP 3078.2")))
    assert other.identity.mandate_ref == "DP 3078.2"
