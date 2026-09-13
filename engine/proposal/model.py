"""The proposal record and the formatting the Word template prints (M9, D103).

One ``Proposal`` per DP number. Every field is typeable: a Lightstone report
prefills the seller and the erven when one is uploaded, and the OTP prefills the
three sale terms the introduction pages quote, but nothing here requires either.

The liquidator sets the budget, so there is no rate card. The standard media
lines are offered with their costs blank and staff type the figures they were
given. A line whose cost is left blank is left out of the document; ``FREE`` and
``N/A`` print as words; anything else must be a rand amount.

Formatting follows the team's own proposals: upper-case facts, ``R 2 500.00``
with a space for thousands and a point for cents, dates as ``23 JULY 2026``, a
percentage with a decimal comma (``7,5``). No dashes are introduced.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

VAT_RATE = Decimal("0.15")
ONLINE_URL = "https://online.dynamicauctioneers.co.za/"

# Cover label for each kind of seller. The contract's own wording for the seller
# lives in the property's OTP, which is inserted verbatim; only the cover uses
# this.
SELLER_LABELS: Dict[str, str] = {
    "liquidation": "Liquidated Estate of",
    "insolvent": "Insolvent Estate of",
    "deceased": "Deceased Estate of",
    "matter": "The Matter of",
}

# The standard media lines, taken from the 2026 proposals with the spelling
# fixed (Instragram, TARGETD, "featured add"). Costs are blank unless the line
# has always been free; the liquidator's figures are typed per job.
DEFAULT_BUDGET: Tuple[Tuple[str, str, str, str], ...] = (
    ("Auction notice boards\n**Includes design, licensing and placement", "CUSTOM DESIGNED AUCTION NOTICE BOARDS", "N/A", ""),
    ("Government Gazette", "LEGAL B NOTICE IN GOVERNMENT GAZETTE", "N/A", ""),
    ("SAIA featured ad", "FEATURED WEB LISTING AND ONCE-OFF ALERT MAILER", "IMMEDIATELY", ""),
    ("Social media\n**Facebook, Instagram, LinkedIn, TikTok, YouTube, X", "TARGETED SOCIAL MEDIA MARKETING", "IMMEDIATELY", ""),
    ("Property24", "FEATURED LISTING", "IMMEDIATELY", ""),
    ("Google Ads", "SEO AND TARGETED ADS", "IMMEDIATELY", ""),
    ("Dynamic Auctioneers website\n**Custom landing page, featured banner, home page", "WEB LISTING", "IMMEDIATELY", "FREE"),
    ("Dynamic Auctioneers auction platform\n**Live-stream auction hosting", "AUCTION REGISTRATION AND HOSTING", "IMMEDIATELY", ""),
    ("WhatsApp", "DATABASE NOTIFICATIONS AND GROUPS", "IMMEDIATELY", "FREE"),
    ("Bulk mailer", "DATABASE NOTIFICATIONS", "IMMEDIATELY", ""),
    ("Gumtree, MyProperty, ImmoAfrica, IOL Property, Qwengo, Property Central", "WEB LISTING", "IMMEDIATELY", "FREE"),
    ("Local newspaper", "PRINT ADVERT", "N/A", ""),
    ("Bidder's info pack", "DOWNLOADABLE PDF", "IMMEDIATELY", "FREE"),
)

_WORD_COSTS = ("FREE", "N/A", "TBC")
_DP_RE = re.compile(r"^(?:DP\s*)?(\d{3,5}(?:\.\d{1,2})?)$", re.I)


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class Erf(_Base):
    """One piece of land on the cover. A dual property has two."""

    legal_description: str = ""   # ERF 2188, TOWN "HELDERKRUIN EXT 21", GAUTENG
    known_as: str = ""            # 842 PHEASANT STREET, HELDERKRUIN EXT 21, GAUTENG
    title_deed: str = ""          # T49195/2001
    extent: str = ""              # 2018 m2 (not on the cover; used by the checks)


class BudgetLine(_Base):
    media: str = ""
    description: str = ""
    placement: str = ""
    cost: str = ""  # "2500", "R 2 500.00", "FREE", "N/A" or blank (= leave out)


class Terms(_Base):
    """The sale terms the introduction pages quote, read from the OTP."""

    deposit_pct: Optional[Decimal] = None
    confirmation_days: Optional[int] = None
    commission_pct: Optional[Decimal] = None
    commission_payer: Literal["", "seller", "purchaser"] = ""
    commission_vat: Optional[bool] = None


class SharePointFile(_Base):
    item_id: str = ""
    name: str = ""
    web_url: str = ""


class Proposal(_Base):
    dp: str
    seller_capacity: Literal["", "liquidation", "insolvent", "deceased", "matter"] = ""
    seller_name: str = ""
    seller_id: str = ""           # ID number or company registration number
    masters_ref: str = ""
    erven: List[Erf] = Field(default_factory=lambda: [Erf()])

    auction_date: Optional[date] = None
    time_from: str = ""           # "10:00"
    time_to: str = ""             # "12:00", optional
    channel: Literal["", "online", "onsite"] = ""
    venue_address: str = ""       # on-site only
    administrator: str = ""

    budget: List[BudgetLine] = Field(
        default_factory=lambda: [
            BudgetLine(media=m, description=d, placement=p, cost=c) for m, d, p, c in DEFAULT_BUDGET
        ]
    )
    deadline: Optional[date] = None
    terms: Terms = Field(default_factory=Terms)

    # Files, stored under <output_root>/DP<dp>/proposal/ (paths relative to it).
    lightstone_files: List[str] = Field(default_factory=list)
    deeds_images: List[str] = Field(default_factory=list)
    advert_image: str = ""
    otp_file: str = ""
    otp_terms: Terms = Field(default_factory=Terms)   # what the OTP itself says
    otp_notes: List[str] = Field(default_factory=list)
    lightstone_read: List[str] = Field(default_factory=list)  # reports already prefilled from
    prefill_note: str = ""
    prefill_job: int = 0
    publish_job: int = 0

    # Output.
    docx_file: str = ""
    pdf_file: str = ""
    generated_at: str = ""
    generated_by: str = ""
    sharepoint_folder: str = ""
    sharepoint_docx: SharePointFile = Field(default_factory=SharePointFile)
    sharepoint_pdf: SharePointFile = Field(default_factory=SharePointFile)
    pdf_note: str = ""


# --- parsing ----------------------------------------------------------------

def normalise_dp(raw: str) -> Optional[str]:
    """``DP2990.3`` / ``2990.3`` -> ``2990.3``; anything else -> None."""
    match = _DP_RE.match((raw or "").strip())
    return match.group(1) if match else None


def base_dp(dp: str) -> str:
    """The instruction number a sub-lot belongs to: ``2990.3`` -> ``2990``."""
    return dp.split(".", 1)[0]


def parse_cost(raw: str) -> Tuple[Optional[Decimal], Optional[str]]:
    """Return ``(amount, word)``. Blank is ``(None, None)``; junk raises ValueError."""
    text = (raw or "").strip()
    if not text:
        return None, None
    if text.upper() in _WORD_COSTS:
        return None, text.upper()
    cleaned = re.sub(r"^R\s*", "", text, flags=re.I)
    cleaned = cleaned.replace(" ", "").replace(" ", "")
    # A decimal comma ("2500,50") is accepted when it is the only separator.
    if "," in cleaned and "." not in cleaned and re.fullmatch(r"\d+,\d{1,2}", cleaned):
        cleaned = cleaned.replace(",", ".")
    cleaned = cleaned.replace(",", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"{raw!r} is not a rand amount, FREE or N/A") from None
    if amount < 0:
        raise ValueError(f"{raw!r} is negative")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), None


# --- formatting -------------------------------------------------------------

def rands(amount: Decimal) -> str:
    """``Decimal('12070')`` -> ``R 12 070.00``."""
    q = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole, cents = f"{q:.2f}".split(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"R {' '.join(groups)}.{cents}"


def date_words(value: Optional[date]) -> str:
    return value.strftime("%-d %B %Y").upper() if value else ""


def pct_text(value: Optional[Decimal]) -> str:
    """``Decimal('7.5')`` -> ``7,5``; ``Decimal('10')`` -> ``10``."""
    if value is None:
        return ""
    text = f"{value.normalize():f}"
    return text.replace(".", ",")


_UNITS = ("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE",
          "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN",
          "SEVENTEEN", "EIGHTEEN", "NINETEEN")
_TENS = ("", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY")


def _int_words(n: int) -> str:
    if n < 20:
        return _UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _TENS[tens] + ("" if unit == 0 else " " + _UNITS[unit])
    if n == 100:
        return "ONE HUNDRED"
    raise ValueError("percentages above 100 are not supported")


def pct_words(value: Optional[Decimal]) -> str:
    """``7.5`` -> ``SEVEN AND A HALF``; ``10`` -> ``TEN``; ``6.25`` -> ``SIX POINT TWO FIVE``."""
    if value is None:
        return ""
    whole = int(value)
    frac = value - whole
    if frac == 0:
        return _int_words(whole)
    if frac == Decimal("0.5"):
        return f"{_int_words(whole)} AND A HALF"
    digits = f"{frac.normalize():f}".split(".")[1]
    return f"{_int_words(whole)} POINT " + " ".join(_UNITS[int(d)] for d in digits)


def seller_line(p: Proposal) -> str:
    name = p.seller_name.strip().upper()
    ident = p.seller_id.strip().upper()
    if not ident:
        return name
    label = "ID NUMBER" if re.fullmatch(r"\d{13}", ident.replace(" ", "")) else "REGISTRATION NUMBER"
    return f"{name} {label}: {ident}"


def auction_date_line(p: Proposal) -> str:
    when = date_words(p.auction_date)
    if p.time_from and p.time_to:
        when = f"{when} FROM {p.time_from} TO {p.time_to}"
    elif p.time_from:
        when = f"{when} AT {p.time_from}"
    prefix = {"online": "ONLINE", "onsite": "ON SITE"}.get(p.channel, "")
    return f"{prefix} {when}".strip()


def auction_venue_line(p: Proposal) -> str:
    if p.channel == "online":
        return f"ONLINE AT {ONLINE_URL}"
    if p.channel == "onsite":
        return f"ON SITE AT {p.venue_address.strip().upper()}"
    return ""


def budget_rows(p: Proposal) -> List[Dict[str, str]]:
    """The lines that print, with their cost text. Blank-cost lines are left out."""
    rows = []
    for line in p.budget:
        amount, word = parse_cost(line.cost)
        if amount is None and word is None:
            continue
        rows.append({
            "media": line.media.strip(),
            "description": line.description.strip().upper(),
            "placement": line.placement.strip().upper(),
            "cost": rands(amount) if amount is not None else word,
        })
    return rows


def budget_totals(p: Proposal) -> Tuple[Decimal, Decimal, Decimal]:
    total = Decimal("0.00")
    for line in p.budget:
        amount, _ = parse_cost(line.cost)
        if amount is not None:
            total += amount
    vat = (total * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return total, vat, total + vat


def tokens(p: Proposal) -> Dict[str, str]:
    """Every scalar ``{{token}}`` the two templates carry, as printed text."""
    total, vat, incl = budget_totals(p)
    t = p.terms
    return {
        "seller_label": SELLER_LABELS.get(p.seller_capacity, "The Matter of"),
        "seller_line": seller_line(p),
        "auction_date_line": auction_date_line(p),
        "auction_venue_line": auction_venue_line(p),
        "auction_starting_line": auction_date_line(p),
        "administrator": p.administrator.strip().upper(),
        "total": rands(total),
        "vat": rands(vat),
        "total_incl": rands(incl),
        "deadline": date_words(p.deadline),
        "deposit_pct": pct_text(t.deposit_pct),
        "confirmation_days": str(t.confirmation_days) if t.confirmation_days is not None else "",
        "conduct_channel": {"online": "publicly, online", "onsite": "publicly, on site"}.get(p.channel, "publicly"),
        "commission_payer": (t.commission_payer or "seller").upper(),
        "commission_pct": pct_text(t.commission_pct),
        "commission_words": pct_words(t.commission_pct),
        "commission_vat": "PLUS VAT" if t.commission_vat is not False else "VAT INCLUSIVE",
    }


def output_stem(p: Proposal) -> str:
    return f"{p.dp} - Auction proposal"


def email_text(p: Proposal) -> Tuple[str, str]:
    """Subject and body for the covering email, from the team's "Sending of Auction proposal" template."""
    seller = p.seller_name.strip().upper()
    subject = " - ".join(
        part for part in (p.dp, "AUCTION PROPOSAL", seller, p.masters_ref.strip().upper()) if part
    )
    lines = ["Good day,", "", f"{SELLER_LABELS.get(p.seller_capacity, 'The Matter of').upper()}: {seller}"]
    for erf in p.erven:
        if erf.legal_description.strip() or erf.known_as.strip():
            lines.append(
                f"PROPERTY: {erf.legal_description.strip().upper()}, BETTER KNOWN AS {erf.known_as.strip().upper()}"
            )
    lines += [
        f"AUCTION DATE: {auction_date_line(p) or 'TBC'}",
        "",
        "With reference to the above matter, kindly find attached:",
        "",
        "  • Draft advertisement",
        "  • Estimated advertising schedule (a proposed budget; we can make changes if needed)",
        "  • Conditions of sale",
        "",
        f"We would appreciate permission to proceed with the auction by {date_words(p.deadline) or 'the deadline'}, "
        "so that we can arrange the advertisements and auction boards and confirm the date.",
        "",
        "Thank you for your support and this instruction.",
        "",
        "Kind regards,",
        p.administrator.strip(),
        "Dynamic Auctioneers",
        "086 155 2288",
    ]
    return subject, "\n".join(lines)
