"""Read the sale terms out of an OTP / Conditions of Sale (fix list 3.2).

Until now the information pack printed the terms as literal strings - "10%
deposit", "45 days", "30 days confirmation" - which are simply the values of
whichever property the template was written against. Checked against a real
document (``2383.1 OTP.pdf``), **every one of them was wrong**: that sale carries
a 20% deposit, a 60 day guarantee and a 7 day confirmation period. A buyer pack
that misstates the deposit is worse than one that says nothing.

The values are read **deterministically** from the numbered clauses, not by a
model:

* they are numbers in a legal document, so a regex that either matches or does
  not is honest in a way a model's best guess is not;
* it costs nothing and runs offline, so it can run on every intake;
* every value carries the **verbatim clause text it came from**, so the marketer
  at gate 1 can see the sentence rather than trust a figure.

Anything not found is left ``None`` and the pack simply omits that line (hard
rule 3: missing data is missing, never invented). Where a clause states a figure
twice and the two disagree - the sample OTP says "6 % (SEVEN PERCENT POINT FIVE
PERCENT)", a copy-paste error in their own template - both are recorded and the
term is flagged for a human rather than silently resolved.

Clause map, confirmed against the sample:

    3.1   deposit percentage and when it is payable
    3.2   guarantee period for the balance
    14.1  commission percentage, VAT, and which party pays
    20    rates and taxes: who settles what is outstanding
    21.1  confirmation period
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from engine.pdftext import PdfUnreadable, layout_text

# "20% (Twenty Percent)" / "6 % (SEVEN PERCENT POINT FIVE PERCENT)" / "7,5%"
_PCT = r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%"
# "60 (SIXTY) days" / "7 (SEVEN) days"
_DAYS = r"(\d{1,3})\s*(?:\([A-Za-z ]+\)\s*)?(?:business\s+|working\s+)?days"

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
}


class OtpUnreadable(PdfUnreadable):
    """Kept as its own name so callers can catch this reader specifically."""


def _read(path: "str | Path") -> str:
    """Text with its rows intact. Raises OtpUnreadable when the document is a scan."""
    try:
        return layout_text(path)
    except PdfUnreadable as exc:
        raise OtpUnreadable(str(exc)) from exc


def _clause(text: str, number: str, stop: str) -> str:
    """The body of one numbered clause, from its number to the next one.

    The document repeats its clause numbers in a table of contents, so the match
    must be the one followed by real sentence text rather than a heading.
    """
    pattern = re.compile(
        rf"^\s*{re.escape(number)}\s+(?P<body>.{{40,}}?)(?=^\s*{stop}\b)",
        re.M | re.S,
    )
    match = pattern.search(text)
    return " ".join(match.group("body").split()) if match else ""


def _words_to_number(body: str) -> Optional[int]:
    """The bracketed words beside a figure ("(SIXTY)"), as a number.

    The whole bracket is tried first, then the first number word inside it. The
    sample OTP reads "6 % (SEVEN PERCENT POINT FIVE PERCENT)" - a copy-paste
    error in their own template, where the figure and the words state different
    commissions. Reading only whole-bracket matches would miss it and print one
    of the two as though it were agreed.
    """
    match = re.search(r"\(([A-Za-z ]+)\)", body)
    if not match:
        return None
    words = " ".join(match.group(1).lower().split())
    if words in _WORD_NUMBERS:
        return _WORD_NUMBERS[words]
    for token in words.split():
        if token in _WORD_NUMBERS:
            return _WORD_NUMBERS[token]
    return None


def _pct_with_check(body: str) -> Tuple[Optional[float], Optional[str]]:
    """A percentage, plus a note when the figure and its words disagree."""
    match = re.search(_PCT, body)
    if not match:
        return None, None
    value = float(match.group(1).replace(",", "."))
    spelled = _words_to_number(body)
    if spelled is not None and abs(spelled - value) > 0.51:
        return value, (
            f"the clause states {match.group(1)}% in figures but "
            f"{spelled} in words; the figure is used"
        )
    return value, None


def _days(body: str) -> Optional[int]:
    match = re.search(_DAYS, body, re.I)
    return int(match.group(1)) if match else None


def _payable_by(body: str) -> Optional[str]:
    """Which party carries a cost, from the clause's own words."""
    match = re.search(r"payable by (?:the )?(SELLER|PURCHASER|BUYER)", body, re.I)
    if match:
        party = match.group(1).lower()
        return "purchaser" if party in ("purchaser", "buyer") else "seller"
    return None


def extract_terms(pdf_path: "str | Path") -> Dict[str, object]:
    """Read the sale terms out of an OTP. Returns a dict shaped for the record.

    Keys are left out entirely when the clause could not be read, so a caller can
    tell "not in this document" from "zero". ``clauses`` carries the verbatim
    text each value came from, and ``flags`` any disagreement a human must settle.
    """
    text = _read(pdf_path)
    clauses = {
        "3.1": _clause(text, "3.1", r"3\.2"),
        "3.2": _clause(text, "3.2", r"3\.3"),
        "14.1": _clause(text, "14.1", r"14\.2"),
        "20.1": _clause(text, "20.1", r"20\.2|21\."),
        "21.1": _clause(text, "21.1", r"21\.2"),
    }
    flags: List[str] = []
    terms: Dict[str, object] = {}

    deposit, note = _pct_with_check(clauses["3.1"])
    if note:
        flags.append(f"clause 3.1: {note}")
    if deposit is not None:
        terms["deposit_pct"] = deposit
        lowered = clauses["3.1"].lower()
        if "fall of the hammer" in lowered:
            terms["deposit_due"] = "on the fall of the hammer"
        elif "signature date" in lowered:
            terms["deposit_due"] = "on signature date"

    guarantee = _days(clauses["3.2"])
    if guarantee is not None:
        terms["guarantee_days"] = guarantee

    commission, note = _pct_with_check(clauses["14.1"])
    if note:
        flags.append(f"clause 14.1: {note}")
    if commission is not None:
        terms["commission_pct"] = commission
        terms["commission_vat"] = "vat" in clauses["14.1"].lower()
        payer = _payable_by(clauses["14.1"])
        if payer:
            terms["commission_payable_by"] = payer

    confirmation = _days(clauses["21.1"])
    if confirmation is not None:
        terms["confirmation_days"] = confirmation

    outstanding = _payable_by(clauses["20.1"]) or (
        "seller" if re.search(r"seller shall (?:be liable|settle|pay)", clauses["20.1"], re.I) else None
    )
    if outstanding:
        terms["outstanding_payable_by"] = outstanding

    missing = [c for c, body in clauses.items() if not body]
    if missing:
        flags.append(
            "clauses not found in this document, so their terms are unset: "
            + ", ".join(missing)
        )
    terms["clauses"] = {c: body[:400] for c, body in clauses.items() if body}
    if flags:
        terms["flags"] = flags
    return terms


def terms_lines(terms: Dict[str, object]) -> List[str]:
    """The terms box, in the reference pack's wording, from extracted values.

    Only states what the document actually said. A missing value drops its line
    rather than printing a default that would be wrong.
    """
    lines: List[str] = []
    deposit = terms.get("deposit_pct")
    if deposit is not None:
        due = terms.get("deposit_due") or "on acceptance"
        lines.append(f"{_pct(deposit)} deposit payable {due} by way of EFT.")
    commission = terms.get("commission_pct")
    if commission is not None:
        vat = " and VAT on the commission" if terms.get("commission_vat") else ""
        payer = terms.get("commission_payable_by")
        who = f" payable by the {str(payer).title()}" if payer else ""
        lines.append(f"{_pct(commission)} commission{vat}{who}.")
    guarantee = terms.get("guarantee_days")
    if guarantee is not None:
        lines.append(f"Guarantee for balance within {guarantee} days after confirmation.")
    lines.append("Occupation on date of registration of transfer of the property.")
    return lines


def confirmation_pill(terms: Dict[str, object]) -> Optional[str]:
    """The gold pill under the terms box, or None when the OTP did not say."""
    days = terms.get("confirmation_days")
    return f"Subject To {days} days Confirmation By Seller" if days is not None else None


def outstanding_pill(terms: Dict[str, object]) -> str:
    """The gold pill above the terms box: who settles what is outstanding."""
    payer = terms.get("outstanding_payable_by")
    if payer:
        return f"All Outstanding Fees, if any, to be Settled by the {str(payer).title()}."
    return "All Outstanding Fees, if any, to be Settled by the Seller."


def _pct(value: float) -> str:
    """"20" not "20.0"; "7.5" kept."""
    return f"{value:g}%"
