"""The property report record and the text the Word template prints (M11, D116, D117).

The report is the team's July 2026 Word report section for section: the cover
(legal description, address, owner, the Lightstone picture, who prepared it),
the PROPERTY DETAIL table, the description, improvements, external features,
security, rates and taxes, outstanding levies, terms, viewing and conclusion,
then the photos six to a page.

The description is not typed as prose but collected from the team's viewing
checklist (``PROPERTY REPORT TEMPLATE/REPORT CHECK LIST/VIEWING.docx``) as
fields, and only what is filled in is printed (D117). Checklist items that are
for the office rather than the trustee (keys, alarm codes, boards, cleaning) are
left out. The only value printed is the municipal valuation: no Lightstone value
range, comparables or opinion (D117).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from engine.proposal.model import date_words, pct_text, rands

# key -> (form label, heading printed above the description)
PROPERTY_TYPES: Dict[str, Tuple[str, str]] = {
    "house": ("House (freehold)", "Residential Dwelling:"),
    "unit": ("Sectional title unit", "Sectional Title Unit:"),
    "commercial": ("Commercial", "Commercial Property:"),
    "industrial": ("Industrial", "Industrial Property:"),
    "farm": ("Farm or smallholding", "Agricultural Property:"),
    "vacant": ("Vacant land", "Vacant Land:"),
}

EMAILS = (
    "properties.admin@dynamicauctioneers.co.za",
    "administration@dynamicauctioneers.co.za",
    "properties@dynamicauctioneers.co.za",
)
OFFICE_PHONE = "086 155 2288"

# The checklist's tick boxes, in its own order: key -> the line printed.
ROOMS: Dict[str, str] = {
    "entrance_hall": "Entrance hall",
    "lounge": "Lounge",
    "dining_room": "Dining room",
    "family_room": "Family room",
    "tv_room": "TV room",
    "open_plan": "Open plan living area",
    "kitchen": "Kitchen",
    "pantry": "Pantry",
    "scullery": "Separate scullery",
    "laundry": "Laundry",
    "study": "Study",
    "linen": "Linen cupboards",
}
FEATURES: Dict[str, str] = {
    "pool": "Swimming pool",
    "entertainment": "Entertainment area",
    "braai": "Built-in braai",
    "domestic": "Domestic quarters",
    "outside_toilet": "Outside toilet",
    "borehole": "Borehole",
    "garden": "Established garden",
}
FARM: Dict[str, str] = {
    "barn": "Barn or shed",
    "water": "Streams, rivers or dams",
    "stables": "Stables, kraal or pens",
    "fencing": "Fencing",
    "fields": "Fields",
    "labourers": "Labourers' quarters",
}
SECURITY: Dict[str, str] = {
    "electric_fence": "electric fence",
    "alarm": "alarm system",
    "auto_gates": "automated gates",
    "guards": "guards on site",
}

STATEMENT_REQUESTED = "We did request a statement and will provide you with same once received."
_SA_ID = re.compile(r"^\d{13}$")


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class Inspection(_Base):
    """The viewing checklist, as fields."""

    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    ensuite: Optional[int] = None
    separate_toilets: Optional[int] = None
    rooms: List[str] = Field(default_factory=list)
    floors: str = ""
    other_improvements: List[str] = Field(default_factory=list)

    garages: Optional[int] = None
    carports: Optional[int] = None
    patio: str = ""  # lapa, balcony, patio or deck, as typed
    features: List[str] = Field(default_factory=list)
    outbuildings: str = ""
    homes: Optional[int] = None
    farm: List[str] = Field(default_factory=list)
    roof: str = ""
    walls: str = ""
    ceilings: str = ""
    other_features: List[str] = Field(default_factory=list)

    security: str = ""
    security_items: List[str] = Field(default_factory=list)

    occupancy: Literal["", "vacant", "occupied"] = ""
    occupant: str = ""
    area: Literal["", "low", "medium", "high"] = ""
    impression: str = ""
    electricity: Literal["", "on", "off"] = ""
    water: Literal["", "on", "off"] = ""
    meters: str = ""
    defects: str = ""


class PropertyReport(_Base):
    dp: str
    property_type: Literal["", "house", "unit", "commercial", "industrial", "farm", "vacant"] = ""
    owner_name: str = ""
    owner_id: str = ""
    legal_description: str = ""
    known_as: str = ""
    extent: str = ""
    zoning: str = ""
    local_authority: str = ""
    municipal_valuation: Optional[Decimal] = None
    municipal_valuation_year: Optional[int] = None
    title_deed: str = ""
    gps: str = ""
    description: str = ""

    inspection_date: Optional[date] = None
    prepared_by: str = ""
    prepared_email: str = EMAILS[0]
    prepared_cell: str = OFFICE_PHONE
    inspection: Inspection = Field(default_factory=Inspection)

    rates_outstanding: Optional[Decimal] = None
    rates_as_at: Optional[date] = None
    rates_note: str = ""
    levies_outstanding: Optional[Decimal] = None
    levies_as_at: Optional[date] = None
    managing_agent: str = ""

    # The terms the team's reports print; an OTP for the DP replaces them when a report starts.
    deposit_pct: Optional[Decimal] = Decimal("10")
    commission_pct: Optional[Decimal] = Decimal("5")
    guarantee_days: Optional[int] = 30
    confirmation_days: Optional[int] = None

    viewing_contact: str = f"Dynamic Auctioneers: {OFFICE_PHONE}"
    conclusion: str = "We will market the property to attract potential offers."

    # Files under <output_root>/DP<dp>/report/, paths relative to it.
    cover_image: str = ""
    photos: List[str] = Field(default_factory=list)
    lightstone_files: List[str] = Field(default_factory=list)
    lightstone_read: List[str] = Field(default_factory=list)
    lightstone_municipal: Optional[Decimal] = None  # what Lightstone printed, for the checks
    lightstone_last_sale: Optional[Decimal] = None
    prefill_note: str = ""
    prefill_job: int = 0

    docx_file: str = ""
    generated_at: str = ""
    generated_by: str = ""


# --- small text helpers ------------------------------------------------------------------

def _lines(values: List[str]) -> List[str]:
    return [v.strip() for v in values if v and v.strip()]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _full_stop(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?" else text + "."


def _join_and(items: List[str]) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _day(value: Optional[date]) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


# --- what the report prints ----------------------------------------------------------------

def sectional(r: PropertyReport) -> bool:
    return (
        r.property_type == "unit"
        or r.title_deed.strip().upper().startswith("ST")
        or bool(re.match(r"\s*(SECTION|UNIT)\b", r.legal_description, re.I))
    )


def owner_line(r: PropertyReport) -> str:
    """``NAME ID NUMBER: ...`` for a person, ``COMPANY, REGISTRATION NUMBER: ...`` otherwise (the team's reports)."""
    name = r.owner_name.strip().upper()
    ident = r.owner_id.strip().upper()
    if not ident:
        return name
    if _SA_ID.match(ident.replace(" ", "")):
        return f"{name} ID NUMBER: {ident}"
    return f"{name}, REGISTRATION NUMBER: {ident}"


def extent_text(r: PropertyReport) -> str:
    return re.sub(r"(?<=\d)\s*m2\b|\bm2\b", " m²", r.extent.strip()).strip()


def valuation_text(r: PropertyReport) -> str:
    if r.municipal_valuation is None:
        return "TBC"
    text = rands(r.municipal_valuation)
    return f"{text} ({r.municipal_valuation_year} valuation roll)" if r.municipal_valuation_year else text


def improvements(r: PropertyReport) -> List[str]:
    i = r.inspection
    out: List[str] = []
    if i.bedrooms:
        out.append(_plural(i.bedrooms, "Bedroom"))
    if i.bathrooms:
        out.append(_plural(i.bathrooms, "Bathroom") + (f" ({i.ensuite} en-suite)" if i.ensuite else ""))
    elif i.ensuite:
        out.append(_plural(i.ensuite, "En-suite bathroom"))
    if i.separate_toilets:
        out.append(_plural(i.separate_toilets, "Separate toilet"))
    out += [label for key, label in ROOMS.items() if key in i.rooms]
    if i.floors.strip():
        out.append(f"Floors: {i.floors.strip()}")
    return out + _lines(i.other_improvements)


def features(r: PropertyReport) -> List[str]:
    i = r.inspection
    out: List[str] = []
    if i.garages:
        out.append(_plural(i.garages, "Garage"))
    if i.carports:
        out.append(_plural(i.carports, "Carport"))
    if i.patio.strip():
        out.append(_capital(i.patio.strip()))
    out += [label for key, label in FEATURES.items() if key in i.features]
    if i.outbuildings.strip():
        out.append(f"Outbuildings: {i.outbuildings.strip()}")
    if i.homes:
        out.append(_plural(i.homes, "Home") + " on the property")
    out += [label for key, label in FARM.items() if key in i.farm]
    construction = [f"{label}: {value.strip()}" for label, value in
                    (("Roof", i.roof), ("Outside walls", i.walls), ("Ceilings", i.ceilings)) if value.strip()]
    if construction:
        out.append("; ".join(construction))
    return out + _lines(i.other_features)


def security_text(r: PropertyReport) -> str:
    i = r.inspection
    parts = [_full_stop(i.security)] if i.security.strip() else []
    items = [label for key, label in SECURITY.items() if key in i.security_items]
    if items:
        parts.append(_full_stop(_capital(_join_and(items))))
    return " ".join(parts)


def condition_lines(r: PropertyReport) -> List[str]:
    i = r.inspection
    out: List[str] = []
    if i.occupancy == "vacant":
        out.append("The property is vacant.")
    elif i.occupancy == "occupied":
        out.append(_full_stop("The property is occupied" + (f" by {i.occupant.strip()}" if i.occupant.strip() else "")))
    if i.impression.strip():
        out.append(_full_stop(f"General impression: {i.impression.strip()}"))
    if i.area:
        out.append(f"Area: {i.area.capitalize()}.")
    services = [f"electricity {i.electricity}"] if i.electricity else []
    if i.water:
        services.append(f"water {i.water}")
    if i.meters.strip():
        services.append(i.meters.strip())
    if services:
        out.append(_full_stop("Services: " + ", ".join(services)))
    if i.defects.strip():
        out.append(_full_stop(f"Defects noted: {i.defects.strip()}"))
    return out


def rates_line(r: PropertyReport) -> str:
    if r.rates_outstanding is not None:
        return f"± {rands(r.rates_outstanding)}" + (f" as at {_day(r.rates_as_at)}" if r.rates_as_at else "")
    return r.rates_note.strip() or STATEMENT_REQUESTED


def show_levies(r: PropertyReport) -> bool:
    return sectional(r) or r.levies_outstanding is not None or bool(r.managing_agent.strip())


def levies_line(r: PropertyReport) -> str:
    if r.levies_outstanding is not None:
        line = f"± {rands(r.levies_outstanding)}" + (f" as at {_day(r.levies_as_at)}" if r.levies_as_at else "")
    else:
        line = STATEMENT_REQUESTED
    if r.managing_agent.strip():
        line += f"\nManaging agent: {r.managing_agent.strip()}"
    return line


def terms_lines(r: PropertyReport) -> List[str]:
    out: List[str] = []
    if r.deposit_pct is not None:
        out.append(f"{pct_text(r.deposit_pct)}% Deposit on the purchase price with submitting an offer payable by the Purchaser.")
    if r.commission_pct is not None:
        out.append(f"{pct_text(r.commission_pct)}% Commission on the purchase price, plus VAT on the commission payable by the SELLER.")
    if r.guarantee_days is not None:
        out.append(f"{r.guarantee_days} Days for guarantees of the balance of the purchase price from date of acceptance.")
    if r.confirmation_days is not None:
        out.append(f"{r.confirmation_days} Days confirmation period by the SELLER.")
    return out + [
        "Occupation on registration of transfer.",
        "Electrical COC, SPLUMA and all certificates necessary for the successful registration of the transfer "
        "for the account of the PURCHASER.",
        "Arrear rates and levies for the account of the SELLER.",
    ]


def tokens(r: PropertyReport) -> Dict[str, str]:
    return {
        "legal_description": r.legal_description.strip().upper(),
        "known_as": r.known_as.strip().upper(),
        "owner_line": owner_line(r),
        "prepared_by": r.prepared_by.strip().upper(),
        "prepared_cell": r.prepared_cell.strip(),
        "prepared_email": r.prepared_email.strip(),
        "inspection_date": date_words(r.inspection_date),
        "extent": extent_text(r) or "TBC",
        "zoning": r.zoning.strip().upper() or "TBC",
        "local_authority": r.local_authority.strip().upper() or "TBC",
        "municipal_valuation": valuation_text(r),
        "title_deed": r.title_deed.strip().upper(),
        "gps": r.gps.strip() or "TBC",
        "property_heading": PROPERTY_TYPES.get(r.property_type, ("", "Property:"))[1],
        "description": r.description.strip(),
        "security": security_text(r),
        "rates_line": rates_line(r),
        "levies_line": levies_line(r),
        "viewing_heading": "VIEWING BY APPOINTMENT TO THE UNIT:" if sectional(r) else "VIEWING BY APPOINTMENT:",
        "viewing_contact": r.viewing_contact.strip(),
        "conclusion": r.conclusion.strip(),
        "signed_by": " ".join(word.capitalize() for word in r.prepared_by.split()),
    }


def output_stem(r: PropertyReport) -> str:
    return f"{r.dp} - PROPERTY REPORT"
