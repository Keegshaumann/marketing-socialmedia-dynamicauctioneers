"""A Lightstone report into a property report's blank fields (M11, D116).

The reading is the proposal's (``engine.proposal.lightstone.read_facts``), which
also takes the municipal facts a property report prints: the local authority, the
usage category, the municipal valuation and its year, and the coordinates. Only
blank fields are filled. What Lightstone printed as the municipal valuation and
the last sale price is kept on the record either way, so the checks can say when
a typed valuation is really the last sale price (the mistake on 3076).
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from engine.proposal.lightstone import LightstoneFacts
from engine.proposal.model import parse_cost
from engine.propertyreport.model import PropertyReport


def _money(text: str) -> Optional[Decimal]:
    try:
        return parse_cost(text)[0]
    except ValueError:
        return None


def apply_facts(report: PropertyReport, facts: List[LightstoneFacts]) -> str:
    lead = next((f for f in facts if f.legal_description or f.title_deed or f.owner_name), None)
    if lead is None:
        return "The Lightstone report was read, but it named no property."
    filled: List[str] = []

    def fill(field: str, value: str, label: str) -> None:
        if value and not str(getattr(report, field) or "").strip():
            setattr(report, field, value)
            filled.append(label)

    fill("owner_name", lead.owner_name, "owner")
    fill("owner_id", lead.owner_id, "ID or registration number")
    fill("legal_description", lead.legal_description, "legal description")
    fill("known_as", lead.street_address, "address")
    fill("title_deed", lead.title_deed, "title deed")
    fill("extent", lead.extent, "extent")
    fill("zoning", lead.usage, "zoning")
    fill("local_authority", lead.local_authority, "local authority")
    fill("gps", lead.coordinates, "GPS")

    valuation = _money(lead.municipal_valuation)
    if valuation is not None:
        report.lightstone_municipal = valuation
        if report.municipal_valuation is None:
            report.municipal_valuation = valuation
            filled.append("municipal valuation")
            if lead.municipal_valuation_year.strip().isdigit() and report.municipal_valuation_year is None:
                report.municipal_valuation_year = int(lead.municipal_valuation_year.strip())
    last_sale = _money(lead.last_sale_price)
    if last_sale is not None:
        report.lightstone_last_sale = last_sale
    if not report.property_type and lead.title_type == "sectional":
        report.property_type = "unit"
        filled.append("property type")

    notes = []
    if filled:
        notes.append("Filled from the Lightstone report: " + ", ".join(filled) + ". Check each against the title deed.")
    else:
        notes.append("The Lightstone report was read, but every field it covers was already filled in.")
    if len(facts) > 1:
        notes.append("Only the first report was used: a property report covers one property.")
    return " ".join(notes)
