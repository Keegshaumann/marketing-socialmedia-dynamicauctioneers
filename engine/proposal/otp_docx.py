"""The sale terms a proposal quotes, read out of the property's OTP .docx (D103).

``engine.otp`` reads a PDF by clause number (3.1, 14.1, 21.1). A Word file has
no clause numbers in its text: Word draws them from list numbering at display
time. So this reader goes by the contract's own wording instead, which is the
same in every Dynamic OTP checked (the insolvency master, 2821, 2887):

* "A cash deposit of 10% (Ten Percent) of the PURCHASE PRICE"
* "within a period of 30 (THIRTY) days (the CONFIRMATION PERIOD)"
* "The commission calculated at 7,5% (SEVEN AND HALF PERCENT) of the purchase
  price plus VAT"

Like ``engine.otp`` it is deterministic and honest about what it could not
find. A figure whose words disagree with its digits (the master says
"5% (SIX PERCENT)") is returned with a note for a person to settle, never
silently resolved.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Tuple

from docx import Document
from docx.oxml.ns import qn

from engine.proposal.model import Terms, pct_words

_PCT = r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%\s*(?:\(\s*([A-Za-z ]+?)\s*\))?"
_DEPOSIT = re.compile(r"cash deposit of\s*" + _PCT, re.I)
_COMMISSION = re.compile(r"commission calculated at\s*" + _PCT + r"([^\n]*)", re.I)
_CONFIRMATION = re.compile(
    r"within a period of\s*(\d{1,3})\s*(?:\(\s*([A-Za-z ]+?)\s*\))?\s*"
    r"(?:calendar\s+|business\s+|working\s+)?days\s*\(\s*the\s+CONFIRMATION PERIOD",
    re.I,
)


def read_text(path: "str | Path") -> str:
    """Every paragraph of the document (tables included), one per line."""
    doc = Document(str(path))
    lines = []
    for p in doc.element.body.iter(qn("w:p")):
        text = "".join(t.text or "" for t in p.iter(qn("w:t")))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _number(raw: str) -> Decimal:
    return Decimal(raw.replace(",", "."))


def _norm_words(words: str) -> str:
    text = words.upper().replace("FOURTY", "FORTY").replace("PER CENT", "PERCENT")
    return re.sub(r"\b(?:AND|A|PERCENT)\b|\s+", "", text)


def _words_note(what: str, value: Decimal, words: Optional[str]) -> Optional[str]:
    if not words:
        return None
    if _norm_words(words) != _norm_words(pct_words(value)):
        shown = f"{value.normalize():f}".replace(".", ",")
        return f"The OTP gives the {what} as {shown} but writes it out as \"{words.strip()}\"."
    return None


def _first(pattern: re.Pattern, text: str, what: str, notes: List[str]):
    found = list(pattern.finditer(text))
    if not found:
        return None
    values = {m.group(1).replace(",", ".") for m in found}
    if len(values) > 1:
        notes.append(f"The OTP states more than one {what}: " + ", ".join(sorted(values)) + ".")
    return found[0]


def terms_from_text(text: str) -> Tuple[Terms, List[str]]:
    notes: List[str] = []
    terms = Terms()

    m = _first(_DEPOSIT, text, "deposit", notes)
    if m:
        terms.deposit_pct = _number(m.group(1))
        note = _words_note("deposit", terms.deposit_pct, m.group(2))
        if note:
            notes.append(note)
    else:
        notes.append('No deposit found in the usual wording ("A cash deposit of ...%").')

    m = _first(_CONFIRMATION, text, "confirmation period", notes)
    if m:
        terms.confirmation_days = int(m.group(1))
        note = _words_note("confirmation period", Decimal(m.group(1)), m.group(2))
        if note:
            notes.append(note)
    else:
        notes.append('No confirmation period found ("within a period of ... days (the CONFIRMATION PERIOD)").')

    m = _first(_COMMISSION, text, "commission", notes)
    if m:
        terms.commission_pct = _number(m.group(1))
        note = _words_note("commission", terms.commission_pct, m.group(2))
        if note:
            notes.append(note)
        tail = m.group(3) or ""
        payer = re.search(r"payable by (?:the )?(SELLER|PURCHASER|BUYER)", tail, re.I)
        if payer:
            terms.commission_payer = "seller" if payer.group(1).upper() == "SELLER" else "purchaser"
        if re.search(r"\bplus\s+VAT\b", tail, re.I):
            terms.commission_vat = True
    else:
        notes.append('No commission found ("The commission calculated at ...%").')

    return terms, notes


def read_terms(path: "str | Path") -> Tuple[Terms, List[str]]:
    return terms_from_text(read_text(path))
