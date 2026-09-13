"""A Lightstone report into proposal fields and a deeds page (M9, D103).

Two separate jobs, so a proposal is never stuck waiting on either:

* ``deeds_image`` draws the "Subject Property Deeds Enquiry" page. On an EVM report
  it crops page 1 from "Property Details" down to the valuation (the legal
  description, land size and both maps) and stacks the "Owner Details" strip from
  page 2 under it, which is the view staff screenshot from the Lightstone site
  today. Any other Lightstone layout (a deeds search, say) gets its whole first
  page. Deterministic PyMuPDF, no key needed.
* ``read_facts`` reads the report into the cover fields with one Claude call and a
  validated structured output. Key-gated: without ``ANTHROPIC_API_KEY`` the fields
  are typed by hand. ``apply_facts`` only ever fills blank fields, so nothing a
  person typed is overwritten.

The model is told to copy, not to complete (hard rule 3): a fact the report does
not print comes back empty.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import List, Literal, Optional

import fitz
from PIL import Image
from pydantic import BaseModel, Field

from engine import MODEL
from engine.proposal.model import Erf, Proposal

_TOP = "Property Details"
_BOTTOM = "Valuation Details"
_OWNER = "Owner Details"
_OWNER_END = "Transfer History"
_PAD = 6  # points above an anchor, so its heading band is kept whole


class LightstoneUnreadable(RuntimeError):
    """The report could not be drawn or read."""


# --- deeds page ------------------------------------------------------------------

def _anchor(page, label: str):
    hits = page.search_for(label)
    return hits[0] if hits else None


def _to_image(pixmap) -> Image.Image:
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def deeds_image(pdf_path: "str | Path", out_path: "str | Path", zoom: float = 2.0) -> Path:
    out_path = Path(out_path)
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        raise LightstoneUnreadable(f"{Path(pdf_path).name} is not a readable PDF") from exc
    try:
        if doc.page_count == 0:
            raise LightstoneUnreadable(f"{Path(pdf_path).name} has no pages")
        matrix = fitz.Matrix(zoom, zoom)
        first = doc[0]
        top, bottom = _anchor(first, _TOP), _anchor(first, _BOTTOM)
        parts = []
        if top is not None and bottom is not None and bottom.y0 > top.y0:
            width = first.rect.width
            parts.append(first.get_pixmap(matrix=matrix, clip=fitz.Rect(0, top.y0 - _PAD, width, bottom.y0 - _PAD)))
            for page in list(doc)[:3]:
                owner = _anchor(page, _OWNER)
                if owner is None:
                    continue
                end = _anchor(page, _OWNER_END)
                y1 = end.y0 - _PAD if end is not None and end.y0 > owner.y0 else min(owner.y0 + 90, page.rect.height)
                parts.append(page.get_pixmap(matrix=matrix, clip=fitz.Rect(0, owner.y0 - _PAD, page.rect.width, y1)))
                break
        else:
            parts.append(first.get_pixmap(matrix=matrix))
        images = [_to_image(p) for p in parts]
    finally:
        doc.close()

    gap = int(12 * zoom)
    sheet = Image.new(
        "RGB",
        (max(i.width for i in images), sum(i.height for i in images) + gap * (len(images) - 1)),
        "white",
    )
    y = 0
    for image in images:
        sheet.paste(image, (0, y))
        y += image.height + gap
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, "PNG", optimize=True)
    return out_path


# --- cover facts ---------------------------------------------------------------------

class LightstoneFacts(BaseModel):
    owner_name: str = Field(description="Current registered owner(s) as printed, in capitals; several joined with ' AND '. Empty if not shown.")
    owner_id: str = Field(description="The owner's ID or company registration number exactly as printed. Empty if not shown.")
    title_type: Literal["freehold", "sectional", "unknown"]
    legal_description: str = Field(description="The property as a title deed describes it, in capitals. Empty if not shown.")
    street_address: str = Field(description="Street address in capitals with suburb and province. Empty if not shown.")
    title_deed: str = Field(description="The current owner's title deed number, e.g. T49195/2001. Empty if not shown.")
    extent: str = Field(description="Registered land size (freehold) or unit size (sectional) with its unit. Empty if not shown.")


SYSTEM_PROMPT = (
    "You read South African Lightstone property reports (EVM reports and deeds searches) for "
    "Dynamic Auctioneers, who are preparing an auction proposal for the property's seller. The "
    "facts go onto a document a liquidator or trustee signs off, so copy them as the report prints "
    "them. Never infer, complete or correct a fact the report does not show; leave that field as "
    "an empty string instead. If the report covers more than one property, describe the first one."
)

INSTRUCTION = (
    "Fill in the fields for the property in this report.\n\n"
    "legal_description: in capitals, including only the parts the report shows. A freehold erf "
    'reads like ERF 2188, TOWN "HELDERKRUIN EXT 21", GAUTENG. A farm portion reads like PORTION 3 '
    'OF THE FARM 300 "DRIE FONTEIN", REGISTRATION DIVISION "ES", KWAZULU-NATAL. A sectional title '
    'unit reads like SECTION 17 OF THE SCHEME "PRENORPARK" (SS 122/1985), SITUATED AT ERF 1120, '
    "PRETORIA NORTH, GAUTENG.\n"
    "street_address: e.g. 842 PHEASANT STREET, HELDERKRUIN EXT 21, GAUTENG.\n"
    "title_deed: e.g. T49195/2001 or ST65783/2017.\n"
    "extent: e.g. 2018 m2 or 568.4365 ha.\n"
    "owner_name and owner_id: the current registered owner, not a previous buyer or seller in the "
    "transfer history.\n"
    "title_type: freehold, sectional, or unknown when the report does not make it clear."
)


def build_request(pdf_path: "str | Path") -> dict:
    """The ``client.messages.parse`` arguments, factored out so a test can check them offline."""
    data = base64.standard_b64encode(Path(pdf_path).read_bytes()).decode("ascii")
    return {
        "model": MODEL,
        "max_tokens": 16000,
        "thinking": {"type": "adaptive"},
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
                    {"type": "text", "text": INSTRUCTION},
                ],
            }
        ],
        "output_format": LightstoneFacts,
    }


def _tidy(facts: LightstoneFacts) -> LightstoneFacts:
    def clean(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    owner_id = clean(facts.owner_id)
    if re.fullmatch(r"\d{12}", owner_id):  # Lightstone prints 2014/203299/07 as 201420329907
        owner_id = f"{owner_id[:4]}/{owner_id[4:10]}/{owner_id[10:]}"
    return LightstoneFacts(
        owner_name=clean(facts.owner_name).upper(),
        owner_id=owner_id,
        title_type=facts.title_type,
        legal_description=clean(facts.legal_description).upper(),
        street_address=clean(facts.street_address).upper(),
        title_deed=clean(facts.title_deed).upper().replace(" ", ""),
        extent=clean(facts.extent),
    )


def read_facts(pdf_path: "str | Path", client=None) -> LightstoneFacts:
    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    response = client.messages.parse(**build_request(pdf_path))
    if response.stop_reason == "refusal":
        raise LightstoneUnreadable(f"{Path(pdf_path).name}: the model declined to read this report")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise LightstoneUnreadable(f"{Path(pdf_path).name}: the report could not be read")
    return _tidy(response.parsed_output)


def apply_facts(proposal: Proposal, facts: List[LightstoneFacts]) -> str:
    """Fill blank fields from the reports. Returns a note for the page."""
    filled: List[str] = []
    notes: List[str] = []
    owners = {f.owner_name for f in facts if f.owner_name}
    if len(owners) > 1:
        notes.append("The reports name different owners: " + "; ".join(sorted(owners)) + ".")

    lead: Optional[LightstoneFacts] = next((f for f in facts if f.owner_name or f.owner_id), None)
    if lead is not None:
        if not proposal.seller_name.strip() and lead.owner_name:
            proposal.seller_name = lead.owner_name
            filled.append("seller")
        if not proposal.seller_id.strip() and lead.owner_id:
            proposal.seller_id = lead.owner_id
            filled.append("ID or registration number")

    for fact in facts:
        if not (fact.legal_description or fact.title_deed or fact.street_address):
            continue
        deed = fact.title_deed.replace(" ", "").upper()
        target = next((e for e in proposal.erven if deed and e.title_deed.replace(" ", "").upper() == deed), None)
        if target is None:
            target = next(
                (e for e in proposal.erven if not (e.legal_description.strip() or e.known_as.strip() or e.title_deed.strip())),
                None,
            )
        new = target is None
        if new:
            target = Erf()
        changed = False
        for field, value in (
            ("legal_description", fact.legal_description),
            ("known_as", fact.street_address),
            ("title_deed", fact.title_deed),
            ("extent", fact.extent),
        ):
            if value and not getattr(target, field).strip():
                setattr(target, field, value)
                changed = True
        if new and changed:
            proposal.erven = [*proposal.erven, target]
        if changed:
            filled.append(f"property {fact.title_deed or fact.street_address}")

    if filled:
        notes.insert(0, "Filled from the Lightstone report: " + ", ".join(filled) + ". Check each against the title deed.")
    elif facts:
        notes.insert(0, "The Lightstone report was read, but every field it covers was already filled in.")
    return " ".join(notes)
