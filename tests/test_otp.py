"""Reading sale terms out of an OTP / Conditions of Sale (engine/otp.py, D68).

The parser is checked against a REAL document where one is available
(``2383.1 OTP.pdf``), because the whole point of the change is that the terms
printed on a buyer pack must be this property's, not the template author's. That
document carries a 20% deposit, a 60 day guarantee and a 7 day confirmation
period, so every value the pack used to hardcode was wrong for it.

The sample lives outside the repo (it is a client contract, not test data), so
these tests skip when it is absent - the same rule the source-document tests use.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.otp import (
    OtpUnreadable,
    confirmation_pill,
    extract_terms,
    outstanding_pill,
    terms_lines,
)

SAMPLE = Path("/Users/keegshaumann/Dev/claudecode/Dynamic/OTP/2383.1 OTP.pdf")


def _require_sample() -> Path:
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext (poppler) not installed")
    if not SAMPLE.exists():
        pytest.skip(f"OTP sample not present: {SAMPLE}")
    return SAMPLE


def test_reads_every_clause_off_the_real_otp():
    terms = extract_terms(_require_sample())

    assert terms["deposit_pct"] == 20.0            # clause 3.1, not the old 10%
    assert terms["deposit_due"] == "on signature date"
    assert terms["guarantee_days"] == 60           # clause 3.2, not the old 45
    assert terms["commission_pct"] == 6.0          # clause 14.1
    assert terms["commission_vat"] is True
    assert terms["commission_payable_by"] == "seller"
    assert terms["confirmation_days"] == 7         # clause 21.1, not the old 30


def test_every_value_carries_the_clause_it_came_from():
    """A figure a human cannot trace is a figure they have to take on faith."""
    terms = extract_terms(_require_sample())

    clauses = terms["clauses"]
    assert "20%" in clauses["3.1"] and "deposit" in clauses["3.1"].lower()
    assert "60" in clauses["3.2"]
    assert "commission" in clauses["14.1"].lower()
    assert "7" in clauses["21.1"]


def test_flags_the_documents_own_contradiction():
    """Clause 14.1 reads "6 % (SEVEN PERCENT POINT FIVE PERCENT)".

    Their template contradicts itself. The figure is used and the disagreement is
    raised for a human, rather than one of the two being picked silently.
    """
    terms = extract_terms(_require_sample())

    flags = " ".join(terms.get("flags") or [])
    assert "14.1" in flags
    assert "6% in figures" in flags and "7 in words" in flags


def test_a_missing_clause_leaves_its_term_unset_and_says_so():
    """No clause, no value: the pack omits the line rather than inventing it."""
    terms = extract_terms(_require_sample())

    # Clause 20 has no body in this document.
    assert "outstanding_payable_by" not in terms
    assert any("20.1" in f for f in terms.get("flags") or [])


def test_the_wording_matches_the_reference_terms_box():
    terms = extract_terms(_require_sample())

    lines = terms_lines(terms)
    assert lines[0] == "20% deposit payable on signature date by way of EFT."
    assert lines[1] == "6% commission and VAT on the commission payable by the Seller."
    assert lines[2] == "Guarantee for balance within 60 days after confirmation."
    assert lines[-1] == "Occupation on date of registration of transfer of the property."
    assert confirmation_pill(terms) == "Subject To 7 days Confirmation By Seller"
    # Clause 20 was unreadable here, so the pill keeps the standing wording.
    assert outstanding_pill(terms).endswith("Settled by the Seller.")


def test_no_terms_at_all_rather_than_invented_ones():
    """An empty document yields nothing: no line is better than a wrong line."""
    assert terms_lines({}) == [
        "Occupation on date of registration of transfer of the property."
    ]
    assert confirmation_pill({}) is None


def test_a_scanned_document_is_refused_not_guessed(tmp_path):
    """An image-only PDF must raise, so intake can ask for a text one."""
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext (poppler) not installed")
    empty = tmp_path / "scan.pdf"
    empty.write_bytes(b"%PDF-1.4\n%%EOF\n")
    with pytest.raises(OtpUnreadable):
        extract_terms(empty)
