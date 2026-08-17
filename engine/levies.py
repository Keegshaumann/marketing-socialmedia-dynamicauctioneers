"""Read the monthly levy off a body-corporate / HOA statement (fix list 3.1).

The information pack prints a "Levies" chip beside the rates. There is no levy
figure anywhere in the Lightstone or property reports, so it comes from the
managing agent's statement - and, in the client's words, every statement is
different. The five samples prove it: a "LEVY STATEMENT" with CHARGES and
PAYMENTS columns, a Crystal Reports tax invoice, and three "CustomerStatement"
exports, two of which nest the whole table inside a wider layout.

What they have in common is the only thing worth relying on: **dated rows, each
with a description and an amount**. So the reader does not try to recognise a
layout at all. It:

1. finds every line carrying a date and at least one money amount;
2. keeps the ones whose description reads like a levy (levy, levies, CSOS,
   reserve fund, HOA) and drops interest, arrears, balances, payments and
   credits - the lines that make a statement's total larger than a month's levy;
3. groups what is left by month and takes the **most recent complete month**,
   because the newest month on a statement is often a single part-charge;
4. returns the total, the month it came from, and **every component line**, so
   the marketer sees "1457.00 Levies + 83.00 Reserve Fund + 19.14 CSOS" rather
   than a number to trust.

A statement with no levy lines at all (one sample carries only interest
journals) yields None, and the pack keeps saying "TBC". That is the honest
answer: a wrong levy on a buyer pack is a misrepresentation.
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 2025-06-01 | 01/04/2026 | 1 May 2026
_DATE = re.compile(
    r"\b(?P<iso>\d{4}-\d{2}-\d{2})\b|\b(?P<dmy>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b"
)
# "1 457.00", "1,457.00", "403.90", "19.14" - SA statements group with a space.
_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:[ ,]\d{3})*|\d+)[.,](\d{2})(?![\d])")

_LEVY_WORDS = ("levy", "levies", "csos", "reserve fund", "hoa")
# Lines that carry an amount but are not a levy charge. Order matters: an
# "Interest on arrears balance" line mentions no levy word anyway, but a
# "Levy interest" line must still be excluded.
_NOT_A_CHARGE = (
    "interest", "arrear", "balance", "b/f", "brought forward", "payment",
    "receipt", "credit", "refund", "discount", "vat on", "total", "sub-total",
    "subtotal", "opening", "closing",
    # One-off corrections, not a month's levy. A statement that carries them has
    # MORE levy lines than a normal month, so without this the "fullest month"
    # rule below actively prefers the wrong month: sample 1776837465 read
    # R2 606.92 where the monthly levy is R1 651.36.
    "backdated", "adjustment", "adjust", "correction", "reversal", "pro-rata",
    "pro rata", "prorata",
)


class LeviesUnreadable(RuntimeError):
    """The statement could not be turned into text (an image scan, or no poppler)."""


def pdf_text(path: "str | Path") -> str:
    path = Path(path)
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LeviesUnreadable(f"could not run pdftotext on {path.name}") from exc
    text = out.stdout.decode("utf-8", errors="replace")
    if len(text.strip()) < 80:
        raise LeviesUnreadable(
            f"{path.name} yielded almost no text; it is probably a scan, so the "
            "levy must be entered by hand"
        )
    return text


def _month_of(line: str) -> Optional[str]:
    """The row's month as YYYY-MM, from either date order."""
    match = _DATE.search(line)
    if not match:
        return None
    if match.group("iso"):
        return match.group("iso")[:7]
    parts = re.split(r"[/-]", match.group("dmy"))
    if len(parts) != 3:
        return None
    day, month, year = parts
    if len(year) == 2:
        year = "20" + year
    try:
        # SA statements are day-first; a "month" over 12 means the file is not.
        if int(month) > 12:
            day, month = month, day
        return f"{int(year):04d}-{int(month):02d}"
    except ValueError:
        return None


def _amounts(line: str) -> List[float]:
    out = []
    for whole, cents in _AMOUNT.findall(line):
        try:
            out.append(float(whole.replace(" ", "").replace(",", "") + "." + cents))
        except ValueError:
            continue
    return out


def _describes_a_levy(line: str) -> bool:
    lowered = line.lower()
    if not any(word in lowered for word in _LEVY_WORDS):
        return False
    return not any(word in lowered for word in _NOT_A_CHARGE)


def read_statement(pdf_path: "str | Path") -> Dict[str, object]:
    """Return the monthly levy, the month it came from, and its component lines.

    ``{}`` when the statement carries no levy charge at all.
    """
    text = pdf_text(pdf_path)
    by_month: Dict[str, List[Tuple[str, float]]] = defaultdict(list)

    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or not _describes_a_levy(line):
            continue
        month = _month_of(line)
        amounts = _amounts(line)
        if not month or not amounts:
            continue
        # The charge is the FIRST amount on the row: statements put charges
        # before payments and the running balance, and the balance is the
        # largest number on the line, which is exactly what a max() would grab.
        label = re.sub(r"\s{2,}", " ", raw).strip()
        label = re.sub(r"^\W*\d{4}-\d{2}-\d{2}\s*", "", label)
        label = re.sub(r"^\W*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s*", "", label)
        by_month[month].append((label[:80], amounts[0]))

    if not by_month:
        return {}

    months = sorted(by_month)
    # The newest month is often a single part-charge posted mid-cycle, so prefer
    # the newest month that carries as many charge lines as the fullest month.
    widest = max(len(v) for v in by_month.values())
    chosen = next(
        (m for m in reversed(months) if len(by_month[m]) >= widest), months[-1]
    )
    components = by_month[chosen]
    return {
        "monthly_total": round(sum(a for _, a in components), 2),
        "month": chosen,
        "components": [{"label": label, "amount": amount} for label, amount in components],
        "source_file": Path(pdf_path).name,
    }


def monthly_levy(pdf_path: "str | Path") -> Optional[float]:
    """Just the figure, or None. Convenience for callers that want no detail."""
    result = read_statement(pdf_path)
    total = result.get("monthly_total")
    return float(total) if total is not None else None
