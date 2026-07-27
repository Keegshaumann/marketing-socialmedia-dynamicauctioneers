"""Physical-fact source precedence + the optional valuation source (D35).

Offline: PDFs are generated locally with PyMuPDF so classification runs on real
document text without any committed fixture.
"""

from __future__ import annotations

import fitz  # PyMuPDF

from engine.intake import build_jobs, classify_pdf
from engine.schema import (
    Physical,
    PhysicalConflict,
    PropertyRecord,
    resolve_physical_conflicts,
)


def _write_pdf(path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _conflict_record(**source_values) -> PropertyRecord:
    return PropertyRecord(
        dp="1",
        physical=Physical(
            garages=99,  # a deliberately wrong seed the resolver must overwrite
            conflicts=[PhysicalConflict(field="garages", label="Garages", **source_values)],
        ),
    )


# --- precedence resolution -----------------------------------------------

def test_valuation_wins_over_property_report_and_lightstone():
    rec = _conflict_record(lightstone="2", property_report="1", valuation="3")
    resolve_physical_conflicts(rec)
    assert rec.physical.garages == 3
    assert rec.physical.conflicts[0].resolved_source == "valuation"


def test_property_report_wins_when_no_valuation():
    rec = _conflict_record(lightstone="2", property_report="1")
    resolve_physical_conflicts(rec)
    assert rec.physical.garages == 1
    assert rec.physical.conflicts[0].resolved_source == "property_report"


def test_lightstone_used_only_when_it_is_the_sole_source():
    rec = _conflict_record(lightstone="2")
    resolve_physical_conflicts(rec)
    assert rec.physical.garages == 2
    assert rec.physical.conflicts[0].resolved_source == "lightstone"


def test_human_override_source_is_honoured():
    # A human picked Lightstone at gate 1 even though the valuer's report exists.
    rec = _conflict_record(lightstone="2", property_report="1", valuation="3")
    rec.physical.conflicts[0].resolved_source = "lightstone"
    resolve_physical_conflicts(rec)
    assert rec.physical.garages == 2  # the human's pick stands, not precedence


def test_none_word_coerces_to_zero():
    rec = _conflict_record(lightstone="3", property_report="none")
    resolve_physical_conflicts(rec)
    assert rec.physical.garages == 0  # property report ("none") wins over lightstone


def test_conflicts_are_stripped_from_public_view():
    rec = _conflict_record(lightstone="2", property_report="1")
    resolve_physical_conflicts(rec)
    physical = rec.public_view().get("physical", {})
    assert "conflicts" not in physical
    assert physical.get("garages") == 1  # the resolved value still shows


# --- the optional valuation source ---------------------------------------

def test_classify_valuation_report_by_content(tmp_path):
    p = tmp_path / "3060 - Report.pdf"
    _write_pdf(
        p,
        "Valuation report prepared by a registered valuer. Market value "
        "R3 400 000. Forced sale value R2 380 000. SACPVP registration.",
    )
    assert classify_pdf(p) == "valuation_report"


def test_evm_not_misread_as_valuation(tmp_path):
    # The EVM mentions valuation figures but its Lightstone markers dominate.
    p = tmp_path / "3060 - EVM.pdf"
    _write_pdf(
        p,
        "Lightstone EVM report. Estimated value (AVM). Comparable sales. "
        "Report id 12345. Municipal valuation and mother erf.",
    )
    assert classify_pdf(p) == "lightstone_evm"


def test_build_jobs_slots_three_docs_and_stays_complete_on_the_pair(tmp_path):
    evm = tmp_path / "3060 - EVM_Report.pdf"
    report = tmp_path / "3060 - PROPERTY REPORT.pdf"
    valuation = tmp_path / "3060 - Valuation.pdf"
    _write_pdf(evm, "Lightstone EVM report. AVM estimated value. Report id 1. Comparable sales.")
    _write_pdf(report, "Property Report. Dynamic Auctioneers. Prepared by Gerrie. Inspection. Viewing.")
    _write_pdf(valuation, "Valuation report by a registered valuer. Forced sale value. SACPVP. Market value.")

    jobs = build_jobs([evm, report, valuation])
    assert len(jobs) == 1
    job = jobs[0]
    assert job.lightstone_evm == evm
    assert job.property_report == report
    assert job.valuation_report == valuation
    assert job.is_complete  # the pair is present; the valuation is a bonus


def test_build_jobs_complete_without_the_valuation(tmp_path):
    evm = tmp_path / "3060 - EVM_Report.pdf"
    report = tmp_path / "3060 - PROPERTY REPORT.pdf"
    _write_pdf(evm, "Lightstone EVM report. AVM estimated value. Report id 1. Comparable sales.")
    _write_pdf(report, "Property Report. Dynamic Auctioneers. Prepared by Gerrie. Inspection. Viewing.")

    job = build_jobs([evm, report])[0]
    assert job.is_complete
    assert job.valuation_report is None
