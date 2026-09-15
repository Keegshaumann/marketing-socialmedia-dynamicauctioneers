"""What must be true before a property report is generated (M11, D116).

``block`` stops generation; ``warn`` is shown and allowed. Beside the missing
facts, three warnings come straight from mistakes in the team's own reports:
a sectional title written up as a house or the other way round (3065 called a
freehold erf "UNIT 32"), a vacant stand with rooms (3000 described "the
dwelling"), and a municipal valuation that is really the last sale price (3076
printed R3 000 000 where Lightstone and the municipality say R3 002 000).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from engine.propertyreport.model import PROPERTY_TYPES, PropertyReport, improvements, sectional
from engine.proposal.checks import Issue
from engine.proposal.model import rands

_TITLE_DEED = re.compile(r"^(?:T|ST|TL|TE|G|SK)\s?\d{1,7}/\d{4}$", re.I)
_SA_ID = re.compile(r"^\d{13}$")
_CO_REG = re.compile(r"^\d{4}/\d{6}/\d{2}$")


def run(r: PropertyReport, files_root: Optional[Path] = None) -> List[Issue]:
    issues: List[Issue] = []

    def block(field: str, message: str) -> None:
        issues.append(Issue("block", field, message))

    def warn(field: str, message: str) -> None:
        issues.append(Issue("warn", field, message))

    if not r.property_type:
        block("property_type", "Choose the kind of property (house, sectional title unit, commercial, industrial, farm or vacant land).")
    if not r.owner_name.strip():
        block("owner_name", "The owner's name is missing.")
    ident = r.owner_id.replace(" ", "")
    if not ident:
        warn("owner_id", "The owner's ID or registration number is missing.")
    elif not (_SA_ID.match(ident) or _CO_REG.match(ident)):
        warn("owner_id", f'"{r.owner_id}" is neither a 13-digit ID number nor a registration number like 2014/203299/07.')
    for field, label in (("legal_description", "legal description"), ("known_as", "street address"), ("extent", "extent")):
        if not getattr(r, field).strip():
            block(field, f"The {label} is missing.")
    if not r.title_deed.strip():
        block("title_deed", "The title deed number is missing.")
    elif not _TITLE_DEED.match(r.title_deed.strip()):
        warn("title_deed", f'"{r.title_deed}" does not look like a title deed number (T12345/2001 or ST12345/2017).')
    if not r.prepared_by.strip():
        block("prepared_by", "Who prepared the report is missing.")

    for field, label in (("zoning", "zoning"), ("local_authority", "local authority"), ("gps", "GPS")):
        if not getattr(r, field).strip():
            warn(field, f"The {label} is missing, so the table prints TBC.")
    if r.municipal_valuation is None:
        warn("municipal_valuation", "The municipal valuation is missing, so the table prints TBC.")
    if not r.inspection_date:
        warn("inspection_date", "The inspection date is missing, so the cover leaves that line out.")
    if not r.description.strip():
        warn("description", "The one-line description under the heading is empty.")
    if not r.cover_image:
        warn("cover_image", "There is no Lightstone picture for the cover. Upload the Lightstone report or a screenshot.")
    if not r.photos:
        warn("photos", "There are no photos.")
    if r.rates_outstanding is None and not r.rates_note.strip():
        warn("rates_outstanding", "No outstanding rates amount, so the report says a statement was requested.")
    if sectional(r) and r.levies_outstanding is None:
        warn("levies_outstanding", "No outstanding levies amount, so the report says a statement was requested.")
    for field, label in (("deposit_pct", "deposit %"), ("commission_pct", "commission %"), ("guarantee_days", "guarantee period")):
        if getattr(r, field) is None:
            warn(field, f"The {label} is empty, so its terms line is left out.")

    # Mistakes seen in the team's own reports --------------------------------------------
    kind = PROPERTY_TYPES.get(r.property_type, ("",))[0].lower()
    deed = r.title_deed.strip().upper()
    if r.property_type and r.property_type != "unit" and (deed.startswith("ST") or re.match(r"\s*SECTION\b", r.legal_description, re.I)):
        warn("property_type", f"The title deed or legal description is a sectional title, but the property is set as {kind}.")
    if r.property_type == "unit" and deed and not deed.startswith("ST"):
        warn("property_type", "The property is set as a sectional title unit, but the title deed is not an ST deed.")
    if r.property_type == "vacant" and improvements(r):
        warn("property_type", "The property is set as vacant land, but rooms or improvements are filled in.")
    if r.municipal_valuation is not None:
        if r.lightstone_last_sale is not None and r.municipal_valuation == r.lightstone_last_sale and (
            r.lightstone_municipal is None or r.lightstone_municipal != r.municipal_valuation
        ):
            warn("municipal_valuation", f"The municipal valuation equals the last sale price on the Lightstone report ({rands(r.lightstone_last_sale)}). Check it is the valuation roll figure.")
        elif r.lightstone_municipal is not None and r.municipal_valuation != r.lightstone_municipal:
            warn("municipal_valuation", f"Lightstone gives the municipal valuation as {rands(r.lightstone_municipal)}.")

    if files_root is not None:
        missing = [p for p in ([r.cover_image] if r.cover_image else []) + r.photos if not (files_root / p).is_file()]
        if missing:
            block("photos", "These pictures are no longer on the server: " + ", ".join(Path(p).name for p in missing) + ". Remove and upload them again.")
    return issues


def blocking(issues: List[Issue]) -> List[Issue]:
    return [i for i in issues if i.level == "block"]
