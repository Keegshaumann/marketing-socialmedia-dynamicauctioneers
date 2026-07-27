"""Verification-gate tests (M3, Phase 2D).

All offline, no API key. Exercises the deterministic checks on the golden
DP3060 record (the garage block flag and the flatlet awareness note both fire
unprompted), the memo's block-vs-note separation, the human sign-off gate
(refused while a block flag is unresolved, allowed once overridden with a
reason), and the offline shape of the market-research request.

The golden record is committed (``DP3060/record.json``); tests skip cleanly via
the ``golden_record_path`` fixture if it is ever absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine import MODEL
from engine.schema import PropertyRecord
from engine.store import RecordStore
from engine.verify import (
    Flag,
    SignOffRefused,
    build_memo,
    build_research_request,
    deterministic_checks,
    research_market,
    sign_off,
    verify,
)

# Occupant PII carried on the golden record; must never leak into a memo or a
# research prompt. Kept here as the poison marker for this module.
GOLDEN_PII = "Tahera Kader"


@pytest.fixture
def golden_record(golden_record_path: Path) -> PropertyRecord:
    return PropertyRecord.model_validate_json(
        golden_record_path.read_text(encoding="utf-8")
    )


# --- deterministic checks ------------------------------------------------

def test_deterministic_checks_surface_garage_block_and_flatlet_note(golden_record):
    flags = deterministic_checks(golden_record)
    by_code = {f.code: f for f in flags}

    # The garage disagreement is now a structured PHYSICAL_CONFLICT (D35).
    assert "PHYSICAL_CONFLICT" in by_code
    assert by_code["PHYSICAL_CONFLICT"].severity == "block"
    assert by_code["PHYSICAL_CONFLICT"].field == "garages"

    assert "FLATLET_INSPECTION_ONLY" in by_code
    assert by_code["FLATLET_INSPECTION_ONLY"].severity == "note"


def test_deterministic_checks_order_block_flags_first(golden_record):
    flags = deterministic_checks(golden_record)
    severities = [f.severity for f in flags]
    # Every block flag precedes every note flag.
    first_note = next((i for i, s in enumerate(severities) if s == "note"), len(severities))
    assert all(s == "block" for s in severities[:first_note])


def test_deterministic_checks_are_pure_no_key_needed(golden_record):
    # Nothing here touches a model or the network; it just runs.
    flags = deterministic_checks(golden_record)
    assert flags
    assert all(isinstance(f, Flag) for f in flags)


def test_recorded_physical_conflicts_each_raise_a_block_flag(golden_record):
    # D32/D35 (seen live on Erf 2035): the EVM and the inspection disagreed on
    # the bedroom count and the building size; each recorded disagreement must
    # gate sign-off, and each names every source's value.
    from engine.schema import PhysicalConflict

    rec = golden_record.model_copy(deep=True)
    rec.physical.conflicts = [
        PhysicalConflict(field="bedrooms", label="Bedrooms", lightstone="2", property_report="4"),
        PhysicalConflict(field="unit_size_m2", label="Building size", lightstone="106", property_report="310"),
    ]
    flags = deterministic_checks(rec)
    conflicts = [f for f in flags if f.code == "PHYSICAL_CONFLICT"]
    assert len(conflicts) == 2
    assert all(f.severity == "block" for f in conflicts)
    assert "Lightstone says 2" in conflicts[0].evidence
    assert "Property Report says 4" in conflicts[0].evidence


def test_professional_valuation_outside_evm_range_raises_note(golden_record):
    from engine.schema import ProfessionalValuation

    rec = golden_record.model_copy(deep=True)
    rec.valuation.evm_range = [2060000.0, 2910000.0]
    rec.valuation.professional = ProfessionalValuation(
        market_value=3400000, forced_sale_value=2380000
    )
    flags = deterministic_checks(rec)
    div = [f for f in flags if f.code == "VALUATION_DIVERGENCE"]
    assert len(div) == 1
    assert div[0].severity == "note"

    # Inside the range: no flag.
    rec.valuation.professional.market_value = 2500000
    assert not [
        f for f in deterministic_checks(rec) if f.code == "VALUATION_DIVERGENCE"
    ]


# --- memo ----------------------------------------------------------------

def test_memo_distinguishes_block_from_note(golden_record):
    flags = deterministic_checks(golden_record)
    memo = build_memo(golden_record, flags)

    assert "[BLOCK]" in memo
    assert "[NOTE]" in memo
    # Block flags are numbered ahead of notes.
    assert memo.index("[BLOCK]") < memo.index("[NOTE]")


def test_memo_omits_market_section_without_research(golden_record):
    flags = deterministic_checks(golden_record)
    memo = build_memo(golden_record, flags, research=None)
    assert "Market context" not in memo


def test_memo_includes_market_section_when_research_supplied(golden_record):
    flags = deterministic_checks(golden_record)
    research = {"summary": "Address resolves; comps are in range.", "sources": ["x: http://x"]}
    memo = build_memo(golden_record, flags, research=research)
    assert "Market context" in memo
    assert "Address resolves" in memo


def test_memo_never_contains_occupant_pii(golden_record):
    flags = deterministic_checks(golden_record)
    memo = build_memo(golden_record, flags)
    assert GOLDEN_PII not in memo


# --- research request shape (offline) ------------------------------------

def test_build_research_request_shape_offline(golden_record):
    req = build_research_request(golden_record)

    assert req["model"] == MODEL
    assert req["tools"][0]["type"] == "web_search_20260209"
    assert req["tools"][0]["name"] == "web_search"
    assert req["messages"][0]["role"] == "user"
    assert isinstance(req["messages"][0]["content"], str)


def test_build_research_request_carries_no_pii(golden_record):
    # The request is built from public_view only; occupant PII must not appear.
    req = build_research_request(golden_record)
    assert GOLDEN_PII not in json.dumps(req)


def test_research_market_returns_none_without_key(golden_record, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert research_market(golden_record, client=None) is None


def test_research_market_keeps_findings_drops_narration(golden_record):
    # A live tool-using turn interleaves narration between searches; the memo
    # must carry only the final findings (text after the last tool block) while
    # sources still come from every search-result block.
    from types import SimpleNamespace as NS

    class _Client:
        class messages:  # noqa: N801 - mimics the SDK surface
            @staticmethod
            def create(**kwargs):
                return NS(content=[
                    NS(type="text", text="I'll research this listing for you."),
                    NS(type="server_tool_use", name="web_search"),
                    NS(type="web_search_tool_result", content=[
                        NS(url="https://p24.example/1", title="Listing A"),
                    ]),
                    NS(type="text", text="Let me refine the search."),
                    NS(type="server_tool_use", name="web_search"),
                    NS(type="web_search_tool_result", content=[
                        NS(url="https://pp.example/2", title="Listing B"),
                    ]),
                    NS(type="text", text="Findings: address confirmed; range looks sane."),
                ])

    out = research_market(golden_record, client=_Client())
    assert out["summary"] == "Findings: address confirmed; range looks sane."
    assert "I'll research" not in out["summary"]      # narration dropped
    assert len(out["sources"]) == 2                    # sources from every search


def test_research_market_no_tools_keeps_all_text(golden_record):
    from types import SimpleNamespace as NS

    class _Client:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                return NS(content=[NS(type="text", text="No search was needed; area is known.")])

    out = research_market(golden_record, client=_Client())
    assert out["summary"] == "No search was needed; area is known."


# --- sign-off gate -------------------------------------------------------

def test_sign_off_refused_then_allowed_with_override(golden_record, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = RecordStore(db_path=":memory:")
    try:
        store.upsert(golden_record, state="extracted")

        memo_path, flags = verify("3060", store, output_root=str(tmp_path))
        assert Path(memo_path).exists()
        assert store.get_state("3060") == "flags_raised"
        assert any(f.severity == "block" for f in flags)

        # Refused while the garage block flag is unresolved and not overridden.
        with pytest.raises(SignOffRefused):
            sign_off("3060", store, user="gerrie@dynamicauctioneers.co.za")
        assert store.get_state("3060") == "flags_raised"

        # Allowed once the block flag is overridden with a written reason.
        sign_off(
            "3060",
            store,
            user="gerrie@dynamicauctioneers.co.za",
            override_notes={
                "PHYSICAL_CONFLICT": "Agent confirmed no garages; guest parking only."
            },
        )
        assert store.get_state("3060") == "verified"
        signed = store.get("3060")
        assert signed.verification.human_signoff
    finally:
        store.close()


def test_no_draft_without_sign_off(golden_record, tmp_path):
    # verified is the only state the store lets reach drafted, and sign_off is
    # the only path to verified; so a record cannot be drafted unsigned.
    store = RecordStore(db_path=":memory:")
    try:
        store.upsert(golden_record, state="extracted")
        verify("3060", store, output_root=str(tmp_path))
        # No sign-off yet: the record is not verified, so it cannot be drafted.
        assert store.get_state("3060") != "verified"
        assert store.get("3060").verification.human_signoff is None
    finally:
        store.close()
