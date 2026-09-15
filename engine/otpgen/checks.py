"""What must be true before an OTP is generated (M10, D114).

``block`` stops generation; ``warn`` is shown and allowed. The rules are the
proposal's (M9) where the facts are the same, plus the OTP's own terms. Every
figure in an OTP is printed twice, as digits and as words, so each one must be a
number the words can be made from.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import List

from engine.otpgen.model import Otp
from engine.proposal.checks import Issue

_TITLE_DEED = re.compile(r"^(?:T|ST|TL|TE|G|SK)\s?\d{1,7}/\d{4}$", re.I)
_SA_ID = re.compile(r"^\d{13}$")
_CO_REG = re.compile(r"^\d{4}/\d{6}/\d{2}$")
_SECTIONAL = re.compile(r"\bSECTION\s+\d+|\bSS\s*\d|\bSCHEME\b|\bUNIT\s+\d+", re.I)


def run(o: Otp) -> List[Issue]:
    issues: List[Issue] = []

    def block(field: str, message: str) -> None:
        issues.append(Issue("block", field, message))

    def warn(field: str, message: str) -> None:
        issues.append(Issue("warn", field, message))

    if not o.seller_capacity:
        block("seller_capacity", "Choose who is selling (liquidators, trustees, practitioners, executor or a private seller).")
    if not o.seller_name.strip():
        block("seller_name", "The seller's name is missing.")
    ident = o.seller_id.replace(" ", "")
    if not ident:
        warn("seller_id", "The seller's ID or registration number is missing.")
    elif not (_SA_ID.match(ident) or _CO_REG.match(ident)):
        warn("seller_id", f'"{o.seller_id}" is neither a 13-digit ID number nor a registration number like 2014/203299/07.')

    for n, erf in enumerate(o.erven, start=1):
        which = f"Property {n}" if len(o.erven) > 1 else "The property"
        if not erf.legal_description.strip():
            block(f"erf_{n}_legal", f"{which}: the legal description is missing.")
        if not erf.known_as.strip():
            block(f"erf_{n}_known_as", f"{which}: the street address (better known as) is missing.")
        if not erf.title_deed.strip():
            block(f"erf_{n}_title_deed", f"{which}: the title deed number is missing.")
        elif not _TITLE_DEED.match(erf.title_deed.strip()):
            warn(f"erf_{n}_title_deed", f'{which}: "{erf.title_deed}" does not look like a title deed number (T12345/2001 or ST12345/2017).')
        if not erf.extent.strip():
            warn(f"erf_{n}_extent", f"{which}: the extent is missing, so the MEASURING line is left out.")
        sectional = _SECTIONAL.search(erf.legal_description) or erf.title_deed.strip().upper().startswith("ST")
        if sectional and not o.body_corporate.strip():
            warn("body_corporate", f"{which} looks like a sectional title unit. Add the body corporate if the sale is subject to its rules.")

    for field, label, value, low, high in (
        ("deposit_pct", "The deposit %", o.deposit_pct, Decimal("1"), Decimal("100")),
        ("interest_pct", "The interest %", o.interest_pct, Decimal("0"), Decimal("100")),
        ("commission_pct", "The commission %", o.commission_pct, Decimal("0.5"), Decimal("100")),
    ):
        if value is None:
            block(field, f"{label} is missing.")
        elif not low <= value <= high:
            block(field, f"{label} ({value}) is out of range.")
    for field, label, value in (
        ("guarantee_days", "The guarantee period", o.guarantee_days),
        ("confirmation_days", "The confirmation period", o.confirmation_days),
    ):
        if value is None:
            block(field, f"{label} is missing.")
        elif not 1 <= value <= 100:
            block(field, f"{label} ({value} days) must be between 1 and 100 days.")
    if o.commission_pct is not None and o.commission_pct > 15:
        warn("commission_pct", f"A commission of {o.commission_pct}% is unusually high. Check it.")

    return issues


def blocking(issues: List[Issue]) -> List[Issue]:
    return [i for i in issues if i.level == "block"]
