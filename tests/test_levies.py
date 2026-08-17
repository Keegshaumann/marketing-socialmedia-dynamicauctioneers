"""Reading the monthly levy off a managing agent's statement (engine/levies.py).

The client's words: "all levy statements are different". The five samples bear
that out - a "LEVY STATEMENT" with CHARGES/PAYMENTS columns, a Crystal Reports
tax invoice, and three "CustomerStatement" exports, two of them nested inside a
wider layout. So the reader does not recognise layouts; it works off the one
thing they share, dated rows carrying a description and an amount.

These run against the real statements where they are present, and skip when they
are not (they are client documents, not test data).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.levies import LeviesUnreadable, monthly_levy, read_statement

SAMPLES = Path("/Users/keegshaumann/Dev/claudecode/Dynamic/Levies")

# What each real statement should yield. Worked out by reading the documents:
# the levy components of the most recent complete month, excluding interest,
# arrears and one-off backdated corrections.
EXPECTED = {
    "BPMW00051D.pdf": 1283.12,                                     # 6 components
    "Crystal Reports - Tax Invoice & Statement (Standard).pdf": 1075.93,
    "CustomerStatement-1776837465.pdf": 1651.36,                   # 1268 + 368 + 15.36
    "CustomerStatement-1776866067.pdf": 2170.72,
    "CustomerStatement-1776929405.pdf": None,                      # interest only
}


def _sample(name: str) -> Path:
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext (poppler) not installed")
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"levy sample not present: {name}")
    return path


@pytest.mark.parametrize("name, expected", sorted(EXPECTED.items()))
def test_reads_each_real_statement(name, expected):
    """Four different layouts, one reader."""
    assert monthly_levy(_sample(name)) == expected


def test_components_are_shown_not_just_a_total():
    """A figure on a buyer pack has to be checkable: the marketer sees
    "1268.00 Levies + 368.00 Reserve Fund + 15.36 CSOS", not just a total."""
    result = read_statement(_sample("CustomerStatement-1776837465.pdf"))

    amounts = sorted(c["amount"] for c in result["components"])
    assert amounts == [15.36, 368.00, 1268.00]
    labels = " ".join(c["label"].lower() for c in result["components"])
    assert "levies" in labels and "reserve fund" in labels and "csos" in labels
    assert result["month"] == "2026-04"
    assert round(sum(amounts), 2) == result["monthly_total"]


def test_interest_and_arrears_are_never_counted_as_levy():
    """The lines that would inflate the figure most are the ones a statement is
    full of. One sample carries nothing but interest journals and must read as
    no levy at all rather than as its arrears."""
    assert monthly_levy(_sample("CustomerStatement-1776929405.pdf")) is None

    for name in EXPECTED:
        if EXPECTED[name] is None:
            continue
        result = read_statement(_sample(name))
        for component in result["components"]:
            label = component["label"].lower()
            assert "interest" not in label
            assert "arrear" not in label
            assert "balance" not in label


def test_one_off_backdated_corrections_are_excluded():
    """Backdated increases and decreases are corrections, not a month's levy.

    Sample 1776837465 carries three of them, and counting them read R2 606.92
    where the monthly levy is R1 651.36. They also make that month the FULLEST
    month, so an unguarded "most complete month" rule actively prefers it.
    """
    result = read_statement(_sample("CustomerStatement-1776837465.pdf"))

    assert result["monthly_total"] == 1651.36
    assert not any("backdated" in c["label"].lower() for c in result["components"])


def test_a_scan_is_refused_rather_than_guessed(tmp_path):
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext (poppler) not installed")
    empty = tmp_path / "scan.pdf"
    empty.write_bytes(b"%PDF-1.4\n%%EOF\n")
    with pytest.raises(LeviesUnreadable):
        read_statement(empty)
