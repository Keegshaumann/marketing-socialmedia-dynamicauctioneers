"""Intake tests (M1): DP parsing, content classification and pairing.

The pure DP-parsing and job-pairing logic runs offline. Classification by
document content needs the real sample PDFs and is skipped with a clear reason
when they are absent.
"""

from __future__ import annotations

import pytest

from engine.intake import (
    build_combined_job,
    build_jobs,
    classify_pdf,
    find_dp,
    parse_dp,
)


# --- parse_dp ------------------------------------------------------------

def test_parse_dp_plain_property():
    assert parse_dp("3060 - EVM_Report_40_Topham_Road.pdf") == ("3060", None, None)


def test_parse_dp_sub_property():
    assert parse_dp("3035.1 - EVM_Report_52_Fame_street.pdf") == ("3035.1", "3035", 1)


def test_parse_dp_with_dp_prefix():
    assert parse_dp("DP3060") == ("3060", None, None)
    assert parse_dp("3060") == ("3060", None, None)


def test_parse_dp_ignores_address_number():
    # The leading DP token wins; an address number later in the name must not.
    dp, parent, lot = parse_dp("3060 - EVM_Report_40_Topham_Road.pdf")
    assert dp == "3060"


def test_parse_dp_raises_without_dp():
    with pytest.raises(ValueError):
        parse_dp("EVM_Report_no_number.pdf")


# --- classify_pdf (real documents) ---------------------------------------

def test_classify_lightstone_evm(lightstone_3060):
    assert classify_pdf(lightstone_3060) == "lightstone_evm"


def test_classify_property_report(property_report_3060):
    assert classify_pdf(property_report_3060) == "property_report"


# --- build_jobs / pairing ------------------------------------------------

def test_build_jobs_lone_lightstone_is_incomplete(lightstone_3060):
    """A lone Lightstone doc yields a job that waits and flags the gap."""
    jobs = build_jobs([lightstone_3060])
    assert len(jobs) == 1
    job = jobs[0]
    assert job.dp == "3060"
    assert job.is_complete is False
    assert job.missing == ["property_report"]


def test_build_jobs_pair_is_complete(lightstone_3060, property_report_3060):
    """Both documents present -> a single complete job for DP3060."""
    jobs = build_jobs([lightstone_3060, property_report_3060])
    assert len(jobs) == 1
    job = jobs[0]
    assert job.dp == "3060"
    assert job.is_complete is True
    assert job.missing == []
    assert job.lightstone_evm == lightstone_3060
    assert job.property_report == property_report_3060


def test_build_jobs_groups_distinct_dps(
    lightstone_3060, property_report_3060, lightstone_3035_1
):
    """Files for different DP numbers land in separate jobs, DP-ordered."""
    jobs = build_jobs([property_report_3060, lightstone_3035_1, lightstone_3060])
    by_dp = {job.dp: job for job in jobs}

    assert set(by_dp) == {"3060", "3035.1"}
    assert by_dp["3060"].is_complete is True
    # 3035.1 has only its Lightstone doc here, so it waits and flags.
    assert by_dp["3035.1"].is_complete is False
    assert by_dp["3035.1"].missing == ["property_report"]
    assert by_dp["3035.1"].parent_dp == "3035"
    assert by_dp["3035.1"].lot == 1


# --- multi-file / multi-portion intake (offline: filename-hint classify) ---
#
# Fake PDF bytes are unreadable to PyMuPDF, so classification falls back to the
# filename hint. Naming each file lets these run offline without the real docs.

def _fake_pdf(tmp_path, name: str):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 not a real pdf")
    return path


def test_find_dp_agrees_or_prompts(tmp_path):
    evm = _fake_pdf(tmp_path, "2918 - EVM lightstone.pdf")
    report = _fake_pdf(tmp_path, "2918 - property report.pdf")
    # All files agree on one DP -> that DP.
    assert find_dp([evm, report]) == "2918"
    # Different DPs -> None (ask the user, never guess).
    other = _fake_pdf(tmp_path, "3060 - EVM lightstone.pdf")
    assert find_dp([evm, other]) is None
    # No DP in any name (farm portions) -> None.
    a = _fake_pdf(tmp_path, "PTN 6 of Farm 7 lightstone evm.pdf")
    b = _fake_pdf(tmp_path, "PTN 7 of Farm 7 lightstone evm.pdf")
    assert find_dp([a, b]) is None


def test_build_combined_job_keeps_every_evm_as_one_property(tmp_path):
    """Several EVMs under one DP become ONE job holding all of them."""
    evm1 = _fake_pdf(tmp_path, "2918 - EVM lightstone portion 1.pdf")
    evm2 = _fake_pdf(tmp_path, "2918 - EVM lightstone portion 2.pdf")
    report = _fake_pdf(tmp_path, "2918 - property report.pdf")

    job = build_combined_job([evm1, evm2, report])
    assert job.dp == "2918"
    assert job.lightstone_evms == [evm1, evm2]
    assert job.property_reports == [report]
    assert job.is_complete is True
    # Back-compat singular accessor returns the first EVM.
    assert job.lightstone_evm == evm1
    assert set(job.all_sources) == {evm1, evm2, report}


def test_build_combined_job_without_dp_leaves_key_blank(tmp_path):
    """Farm-portion files with no DP -> job.dp is empty; the caller must prompt."""
    a = _fake_pdf(tmp_path, "PTN 6 of Farm 7 lightstone evm.pdf")
    b = _fake_pdf(tmp_path, "PTN 7 of Farm 7 lightstone evm.pdf")
    job = build_combined_job([a, b])
    assert job.dp == ""
    assert job.lightstone_evms == [a, b]

    # Supplying the DP explicitly (the user typed it) fills the key + parentage.
    job2 = build_combined_job([a, b], dp="2918.1")
    assert job2.dp == "2918.1"
    assert job2.parent_dp == "2918"
    assert job2.lot == 1
