"""Typing the sale terms and running costs by hand (D80).

Every figure the information pack prints for terms and monthly costs comes from
a document the marketer may simply not have been given: the OTP for the deposit,
commission, guarantee and confirmation period, the managing agent's statement
for the levy. Before this they were read or they were absent, and a marketer
holding the numbers in an email had nowhere to put them.

The fault these tests exist to prevent is subtler than "the field is missing".
A percentage is formatted with ``f"{value:g}"``, which RAISES on a string, so a
typed "20" stored verbatim would not print a slightly wrong pack - it would make
the pack unbuildable. Hence the typing tests below.
"""

from __future__ import annotations

import pytest

from webapp.routes.gates import _collect_typed_fields, _parse_number
from engine.otp import has_terms, terms_lines


class _Form(dict):
    """A submitted form: dict-like, which is all the collector asks of it."""


# --- reading what a person actually types ---------------------------------

@pytest.mark.parametrize(
    "typed, expected",
    [
        ("20", 20.0),
        ("7.5", 7.5),
        ("7,5", 7.5),            # SA keyboard: the comma IS the decimal point
        (" 10 % ", 10.0),
        ("R1 250", 1250.0),      # a money box, typed as money
        ("1 250,50", 1250.50),
        ("1,250", 1250.0),       # thousands grouping, NOT R1.25
        ("1,250.75", 1250.75),
        ("0", 0.0),
    ],
)
def test_a_figure_is_read_the_way_it_was_typed(typed, expected):
    assert _parse_number(typed, float, 0.0, 10_000_000.0) == expected


@pytest.mark.parametrize("typed", ["", "abc", "ten", "-", "1.2.3", "R", "%"])
def test_nonsense_is_refused_rather_than_guessed(typed):
    assert _parse_number(typed, float, 0.0, 100.0) is None


@pytest.mark.parametrize("typed", ["200", "2026", "-5", "101"])
def test_a_slipped_keystroke_is_refused(typed):
    """A percentage is bounded, so "200" for "20" and "2026" for "20" are
    refused rather than printed on a document a buyer relies on."""
    assert _parse_number(typed, float, 0.0, 100.0) is None


def test_day_counts_come_back_as_whole_numbers():
    assert _parse_number("45", int, 0, 365) == 45
    assert isinstance(_parse_number("45", int, 0, 365), int)
    assert _parse_number("7.6", int, 0, 365) == 8


# --- what the collector stores --------------------------------------------

def test_terms_are_stored_as_numbers_not_strings():
    """The whole reason this machinery exists: the pack formats a percentage
    with ``:g``, which raises on a string. A typed "20" stored verbatim would
    make the information pack unbuildable rather than slightly wrong."""
    rejected: list = []
    fields = _collect_typed_fields(
        _Form({"deposit_pct": "20", "guarantee_days": "60", "monthly_levy": "R1 480"}),
        full=True,
        rejected=rejected,
    )

    assert fields["sale_process.otp.deposit_pct"] == 20.0
    assert isinstance(fields["sale_process.otp.deposit_pct"], float)
    assert fields["sale_process.otp.guarantee_days"] == 60
    assert isinstance(fields["sale_process.otp.guarantee_days"], int)
    assert fields["valuation.monthly_levy"] == 1480.0
    assert not rejected

    # And the value survives the formatter that would have raised.
    assert "20% deposit" in " ".join(terms_lines({"deposit_pct": fields["sale_process.otp.deposit_pct"]}))


def test_a_refused_figure_leaves_its_field_alone_and_is_named():
    """One mistyped percentage must not discard the marketer's other edits, and
    must not silently vanish either: the field is untouched and the toast says
    which one."""
    rejected: list = []
    fields = _collect_typed_fields(
        _Form({"deposit_pct": "two hundred", "commission_pct": "6"}),
        full=True,
        rejected=rejected,
    )

    assert "sale_process.otp.deposit_pct" not in fields   # untouched, not wiped
    assert fields["sale_process.otp.commission_pct"] == 6.0
    assert rejected == ["Deposit %"]


def test_blank_clears_only_on_a_complete_form():
    """The gate-2 form posts every field, so a blank box there is a deliberate
    clear. A partial POST keeps the safer rule that blank never wipes, so a
    crafted or half-built request cannot empty a live listing's terms."""
    cleared = _collect_typed_fields(_Form({"deposit_pct": ""}), full=True, rejected=[])
    assert cleared["sale_process.otp.deposit_pct"] is None

    partial = _collect_typed_fields(_Form({"deposit_pct": ""}), full=False, rejected=[])
    assert partial == {}


def test_choices_are_validated_and_vat_keeps_three_states():
    fields = _collect_typed_fields(
        _Form({"commission_payable_by": "purchaser", "commission_vat": "yes"}),
        full=True,
        rejected=[],
    )
    assert fields["sale_process.otp.commission_payable_by"] == "purchaser"
    assert fields["sale_process.otp.commission_vat"] is True

    # An off-list value cannot land on the record.
    crafted = _collect_typed_fields(
        _Form({"commission_payable_by": "the neighbour"}), full=True, rejected=[]
    )
    assert crafted == {}

    # "Not stated" is a third state, distinct from "no VAT".
    unstated = _collect_typed_fields(_Form({"commission_vat": ""}), full=True, rejected=[])
    assert unstated["sale_process.otp.commission_vat"] is None
    no_vat = _collect_typed_fields(_Form({"commission_vat": "no"}), full=True, rejected=[])
    assert no_vat["sale_process.otp.commission_vat"] is False


# --- what the pack does with a hand-typed term -----------------------------

def test_typed_terms_print_even_without_a_deposit():
    """The old guard was "print the box if there is a deposit percentage",
    which is the usual FIRST value on an OTP but not the only one a marketer
    may know. Typing a commission and a confirmation period and getting an
    empty terms box would look exactly like the app ignoring the entry."""
    assert has_terms({"commission_pct": 6.0}) is True
    assert has_terms({"confirmation_days": 7}) is True
    assert "6% commission" in " ".join(terms_lines({"commission_pct": 6.0}))


def test_no_terms_at_all_prints_no_terms_box():
    """``terms_lines`` always appends the fixed occupation line, so without this
    guard a property with no OTP would print a lone "Occupation on date of
    registration" as though it were the sale terms."""
    assert has_terms({}) is False
    assert has_terms(None) is False
    # Provenance alone is not a term.
    assert has_terms({"source_file": "OTP.pdf", "clauses": {"3.1": "..."}}) is False
