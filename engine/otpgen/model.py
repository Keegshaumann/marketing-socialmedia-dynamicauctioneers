"""The OTP record and the text the Word template prints (M10, D114).

The terms default to what the team's recent OTPs use (120 sampled in September
2026): a 10% deposit on signature, guarantees within 45 days, 12% interest from
occupation and a 30-day confirmation period. Commission has no default because
it genuinely varies (5, 6 and 7.5% are all common). The seller's name, number and
the properties are typed, read from a Lightstone report, or copied from the
marketing record for the DP.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from engine.proposal.model import Erf, pct_text, pct_words

# key -> (label on the form, heading printed above the seller's name)
SELLER_HEADINGS: Dict[str, Tuple[str, str]] = {
    "liquidation": ("Liquidation (joint liquidators)", "THE JOINT LIQUIDATORS OF:"),
    "insolvent": ("Insolvent estate (joint trustees)", "THE JOINT TRUSTEES OF INSOLVENT ESTATE:"),
    "brp": ("Business rescue (practitioners)", "THE BUSINESS RESCUE PRACTITIONERS OF:"),
    "deceased": ("Deceased estate (executor)", "THE EXECUTOR OF:"),
    "private": ("Private seller", "THE SELLER:"),
}

_SA_ID = re.compile(r"^\d{13}$")


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class Otp(_Base):
    dp: str
    seller_capacity: Literal["", "liquidation", "insolvent", "brp", "deceased", "private"] = ""
    seller_name: str = ""
    seller_id: str = ""
    masters_ref: str = ""
    erven: List[Erf] = Field(default_factory=lambda: [Erf()])

    deposit_pct: Optional[Decimal] = Decimal("10")
    guarantee_days: Optional[int] = 45
    interest_pct: Optional[Decimal] = Decimal("12")
    confirmation_days: Optional[int] = 30
    commission_pct: Optional[Decimal] = None
    commission_payer: Literal["seller", "purchaser"] = "seller"
    commission_vat: bool = True

    body_corporate: str = ""
    extra_conditions: List[str] = Field(default_factory=list)

    lightstone_files: List[str] = Field(default_factory=list)
    lightstone_read: List[str] = Field(default_factory=list)
    prefill_note: str = ""
    prefill_job: int = 0

    docx_file: str = ""
    generated_at: str = ""
    generated_by: str = ""


def seller_line(o: Otp) -> str:
    """``TESTCO (PTY) LTD, REGISTRATION NUMBER 2014/203299/07``, the master's own style."""
    name = o.seller_name.strip().upper()
    ident = o.seller_id.strip().upper()
    if not ident:
        return name
    label = "ID NUMBER" if _SA_ID.match(ident.replace(" ", "")) else "REGISTRATION NUMBER"
    return f"{name}, {label} {ident}"


def _title_words(value: Decimal) -> str:
    """``10`` -> ``Ten Percent``; ``7.5`` -> ``Seven and a Half Percent`` (the deposit clause's casing)."""
    words = [w.lower() if w in ("AND", "A", "POINT") else w.capitalize() for w in pct_words(value).split()]
    return " ".join(words + ["Percent"])


def conditions(o: Otp) -> List[str]:
    """The special conditions printed in place of the master's body corporate line."""
    out = []
    name = re.sub(r"\s*body\s+corporate\s*$", "", o.body_corporate.strip(), flags=re.I)
    if name:
        out.append(f"The sale is subject to the rules and regulations of the {name.upper()} Body Corporate.")
    out += [line.strip() for line in o.extra_conditions if line.strip()]
    return out


def _days(value: Optional[int]) -> Tuple[str, str]:
    if value is None:
        return "", ""
    return str(value), pct_words(Decimal(value))


def tokens(o: Otp) -> Dict[str, str]:
    guarantee, guarantee_words = _days(o.guarantee_days)
    confirmation, confirmation_words = _days(o.confirmation_days)
    return {
        "seller_heading": SELLER_HEADINGS.get(o.seller_capacity, SELLER_HEADINGS["private"])[1],
        "seller_line": seller_line(o),
        "masters_ref": o.masters_ref.strip().upper(),
        "deposit_pct": pct_text(o.deposit_pct),
        "deposit_words": _title_words(o.deposit_pct) if o.deposit_pct is not None else "",
        "guarantee_days": guarantee,
        "guarantee_words": guarantee_words,
        "interest_pct": pct_text(o.interest_pct),
        "interest_words": pct_words(o.interest_pct),
        "confirmation_days": confirmation,
        "confirmation_words": confirmation_words,
        "commission_pct": pct_text(o.commission_pct),
        "commission_words": pct_words(o.commission_pct),
        "commission_payer": o.commission_payer.upper(),
        "commission_vat": "plus VAT" if o.commission_vat else "inclusive of VAT",
    }


def output_stem(o: Otp) -> str:
    return f"{o.dp} - OTP"
