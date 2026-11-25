"""What the ads and the info pack say, and where each word comes from.

Three layers, later ones winning:

1. ``from_sheets`` - what the team's own sheets already say (via the director
   mirror): the auction date and whether it is online, the listed price, the
   occupancy, the Master's reference in a correspondence file name. Instant and
   free, so a property opens with these filled before anything is read.
2. ``read_documents`` - Claude reads the Lightstone, the property report, the
   valuation, the OTP and any earlier info pack in the folder and writes every
   other field in the team's house style.
3. Whatever Nikki types. Saved per property (``state``), so a reload or a second
   visit keeps her words. A second "read the documents" never overwrites a field
   she has edited.
"""

from __future__ import annotations

import io
import logging
import os
import re
import warnings
from datetime import date


from pydantic import BaseModel, Field

from studio import catalog
from studio.catalog import Item, Property
from studio.sharepoint import StoreError, store

log = logging.getLogger("studio.facts")

MODEL = "claude-opus-5-5"
PHONE = "086 155 2288"
EMAIL = "properties.admin@dynamicauctioneers.co.za"

# The icons an ad or the pack may use (studio/assets/icons/<name>.svg, plus the
# two drawn ones: "m2" and "ha" are the boxed unit marks the team's ads use).
ICONS = ["m2", "ha", "bed", "bathtub", "garage", "local_parking", "pool", "kitchen", "deck", "yard", "fence",
         "shield", "home", "villa", "cottage", "apartment", "warehouse", "factory", "storefront", "agriculture",
         "forest", "park", "grass", "landscape", "water_drop", "bolt", "solar_power", "pets"]


class Feature(BaseModel):
    icon: str = Field(description=f"One of: {', '.join(ICONS)}")
    text: str


class Row(BaseModel):
    label: str
    text: str


class Portion(BaseModel):
    label: str = Field(description='Heading for this land parcel, e.g. "PORTION 27" or "ERF 2119"')
    legal_description: str
    extent: str
    municipality: str
    zoning: str
    title_deed: str


class Group(BaseModel):
    title: str = Field(description='e.g. "RESIDENTIAL IMPROVEMENTS", "GREENHOUSE INFRASTRUCTURE"')
    items: list[Feature]


FEATURES_RULE = (
    "4 or 5 headline features for the adverts (never fewer than 4, never more than 5), the ones a buyer "
    "decides on, most important first: the size (floor or land), then bedrooms and bathrooms for a home, "
    "then the strongest selling points the documents support, such as an income or tenant in place, a second "
    "dwelling, garages or parking, a pool, security or a gated estate, a borehole or water rights, orchards, "
    "sheds or a packhouse, a view. Never what the advert already says elsewhere (the price, the town or "
    "suburb, the sale type) and never filler a buyer doesn't choose on: \"residential property\" or "
    "\"residential stand\", freehold or sectional title, zoning, distances to schools, police stations or "
    "shops, \"established area\", \"good condition\", \"low rates\". Each feature is a concrete fact about "
    "this property from the documents, never the street address, erf or title deed number (they are printed "
    "elsewhere) and never a negative (\"no improvements\"). Zoning counts only for vacant land, where it "
    "says what may be built. When the documents are thin, use concrete things they do give (the building's "
    "size, a boundary wall, a carport, the monthly rates as an amount). If they don't give 4 real features, "
    "give only the real ones and stop: a person adds the rest. Never pad."
)


class Features(BaseModel):
    features: list[Feature] = Field(description=FEATURES_RULE)


class Facts(BaseModel):
    # --- the advert (1080x1350) ---
    headline: str = Field(description='Gold line 1. For a home in a sectional scheme the scheme name '
                                      '("SS LOS ANGELES"); otherwise "TOWN | PROVINCE" or "SUBURB | CITY" ("MAKHADO | LIMPOPO")')
    subline: str = Field(description='White line 2: what it is, e.g. "3 BEDROOM APARTMENT", "MACADAMIA & AVOCADO FARM"')
    headline2: str = Field(description='Optional gold line 3, e.g. "SUNNYSIDE | PRETORIA" when line 1 is a scheme name; else ""')
    badge: str = Field(description='Sale badge: "INSOLVENCY SALE!", "LIQUIDATION SALE!", "BUSINESS RESCUE SALE!", '
                                   '"DECEASED ESTATE SALE!", or the same with AUCTION! for an auction')
    address: str = Field(description="Street address as the public would search it, upper case, no erf numbers")
    features: list[Feature] = Field(description=FEATURES_RULE)
    disclaimer: str = Field(description='Occupation line when occupied, e.g. "PURCHASER TO DO EVICTION | VACANT '
                                        'OCCUPATION CANNOT BE GUARANTEED"; else ""')
    price: str = Field(description='e.g. "OFFERS FROM R380 000"; "" if no price is known')
    auction_kind: str = Field(description='"ONLINE" or "ON-SITE" when it goes to auction; else ""')
    auction_date: str = Field(description='e.g. "28 JAN 2027"; else ""')
    auction_time: str = Field(description='e.g. "10:00 - 12:00"; else ""')
    master_ref: str = Field(description='Master of the High Court reference for an insolvent estate or company in '
                                        'liquidation, as on the OTP\'s "MASTER REFERENCE" line or the instruction, '
                                        'e.g. "G907/2025", "T1233/26"; "" if no document gives one')
    # --- banners, icon and board ---
    short_title: str = Field(description='One line: "±38.15 HA MACADAMIA & AVOCADO FARM", "3 BEDROOM APARTMENT"')
    short_place: str = Field(description='One line: "MAKHADO, LOUIS TRICHARDT | LIMPOPO"')
    sale_kind: str = Field(description='Sale capacity for "ONLINE <kind> AUCTION": "BUSINESS RESCUE", '
                                       '"LIQUIDATION", "INSOLVENCY", "DECEASED ESTATE"')
    board_lines: list[str] = Field(description='2 short lines for the auction board, e.g. ["MACADAMIA & AVOCADO", "±38.15 HA FARM"]')
    board_place: list[str] = Field(description='1-2 lines of place for the board, e.g. ["MAKHADO", "LOUIS TRICHARDT"]')
    location_line: str = Field(description='Short legal/location line for the app icon, e.g. "FARM 242, FARM RHEDE, MAKHADO NON URBAN, LIMPOPO"')
    # --- the info pack (A4) ---
    pack_title: str = Field(description='Cover title, e.g. "± 59.96 HA FARM", "3 BEDROOM APARTMENT"')
    pack_region: str = Field(description='Cover region, e.g. "GAUTENG", "SUNNYSIDE, PRETORIA"')
    pack_strapline: str = Field(description='Cover strap, 3-4 highlights joined by " | ", e.g. '
                                            '"FLOWER PRODUCTION | GREENHOUSE TUNNELS | PACKHOUSE | WATER SECURITY"')
    pack_footer: str = Field(description='Cover footer: the land description, e.g. "PORTION 27 & PORTION 28 OF FARM 517 \'WELTEVREDEN\'"')
    overview: list[Row] = Field(description="Property overview table rows: PROPERTY NAME, SALE TYPE, EXTENT, "
                                            "PROVINCE, ZONING, SALE BASIS, VAT")
    rates: list[Row] = Field(description='Monthly rates & taxes (and levies) per portion/unit, e.g. label "Portion 27", text "± R1 645"')
    terms: list[str] = Field(description="Sale overview lines from the OTP: deposit, commission, guarantee, occupation")
    confirmation: str = Field(description='e.g. "Subject To 30 days Confirmation By Seller"; "" if not known')
    viewing: str = Field(description='e.g. "STRICTLY BY APPOINTMENT" or a date and time')
    description: list[str] = Field(description="2-4 paragraphs describing the property, factual, no hype")
    portions: list[Portion]
    improvements: list[Group] = Field(description="Improvements grouped by theme, each with icon + short text")
    best_use: list[str] = Field(description="Highest and best use bullet points")
    condition: list[Row] = Field(description='e.g. label "OVERALL CONDITION", text "Fair"')
    highlights: list[Row] = Field(description="Investment highlights: short bold label + one sentence")
    # --- posting ---
    caption: str = Field(description="Social media caption (Facebook/Instagram/LinkedIn), 60-120 words, "
                                     "ends with the phone number and 5-8 hashtags")
    listing: str = Field(description="Portal listing description for Propdata/Property24, 120-220 words, plain prose")


EDITABLE = list(Facts.model_fields)


_PHONE_ANYHOW = re.compile(r"\b0\s*8\s*6\s*[-. ]?\s*1\s*5\s*5\s*[-. ]?\s*2\s*2\s*8\s*8\b|\b086\s?1552\s?288\b")


def tidy(value):
    """The office number written the one way (086 155 2288), however it came back."""
    if isinstance(value, str):
        return _PHONE_ANYHOW.sub(PHONE, value)
    if isinstance(value, list):
        return [tidy(v) for v in value]
    if isinstance(value, dict):
        return {k: tidy(v) for k, v in value.items()}
    return value


def blank() -> dict:
    return {k: ([] if f.annotation is not str else "") for k, f in Facts.model_fields.items()} | {
        "master_ref": "", "phone": PHONE, "email": EMAIL, "qr_link": ""}


# --- 1. what the sheets already say -------------------------------------------

_MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def _rand(value: float) -> str:
    return "R" + f"{int(round(value)):,}".replace(",", " ")


def capacity(prop: Property) -> str:
    """The sale capacity, from how the team names the folder."""
    n = f" {prop.name.upper()} {prop.folder.upper()} "
    if re.search(r"BUSINESS RESCUE|[\s_(]BR[\s_)]|_BR_", n):
        return "BUSINESS RESCUE"
    if re.search(r"LATE ESTATE|ESTATE LATE|DECEASED|[\s(-]EL[\s)-]", n):
        return "DECEASED ESTATE"
    if re.search(r"LIQUIDATION|[\s(-]IL[\s)-]|I/L", n):
        return "LIQUIDATION"
    if re.search(r"INSOLVENT|INSOLVENCY|[\s(-]IE[\s)-]|I/E", n):
        return "INSOLVENCY"
    return ""


def from_sheets(prop: Property, items: list[Item], mode: str = "") -> dict:
    """``mode`` is the set being made (forsale | auction); the badge follows it."""
    auction = mode == "auction" if mode else prop.is_auction
    out: dict = {}
    if prop.listed:
        out["price"] = f"OFFERS FROM {_rand(prop.listed)}"
    d = prop.auction_date
    if d and d >= date.today():
        out["auction_date"] = f"{d.day} {_MONTHS[d.month - 1]} {d.year}"
        if prop.online is not None:
            out["auction_kind"] = "ONLINE" if prop.online else "ON-SITE"
        out["auction_time"] = "10:00 - 12:00" if prop.online is not False else "11:00"
    if re.search(r"OCCUPIED|OCCUPANT|TENANT", prop.occupancy.upper()):
        out["disclaimer"] = "PURCHASER TO DO EVICTION | VACANT OCCUPATION CANNOT BE GUARANTEED"
    cap = capacity(prop)
    if cap:
        out["sale_kind"] = cap
        out["badge"] = f"{cap} {'AUCTION' if auction else 'SALE'}!"
    ref = catalog.master_ref(items)
    if ref:
        out["master_ref"] = ref
    return out


# --- 2. Claude reads the documents ------------------------------------------

SYSTEM = """You write the marketing copy for Dynamic Auctioneers, a South African auction house that sells \
property for liquidators, trustees, business rescue practitioners and executors. You are given the documents \
in one property's folder and fill in every field of its ad set and information pack.

House style (follow exactly):
- Ad, banner, board and pack headings are UPPER CASE, except units: always lower-case "m²" and "ha", \
written exactly as the source gives them. Never convert between m² and ha.
- Round extents to at most two decimals (38.1515 ha -> ±38.15 ha; 74.4 m² -> ±74m²).
- Sizes from a deeds or valuation report are approximate: prefix them with "±" (e.g. "±74m² UNIT", "±38.15 HA" in \
headings may keep "HA" upper case only in the short_title/board/pack_title fields, as the team does).
- Money: "R380 000" (space thousands, no decimals). Prices are offers-from, never "asking".
- Ad features are short (2-5 words) and concrete: size first, then bedrooms, bathrooms, then the best \
remaining feature (garage, pool, gated access, borehole...). Farms: land area, orchards/structures, water, buildings.
- Descriptions are factual and calm: what it is, where, what is on it, what it suits. No exclamation marks, \
no "nestled", "boasting", "stunning" or "dream home".
- Only state what the documents support. If a fact is not in the documents, leave that field "" or the list \
empty rather than guess. Prefer the inspection/property report for what is physically there and the Lightstone \
or deeds data for legal description, extent and title deed.

Privacy (strict): the ads and pack are public. Never write the owner's, debtor's, seller's or estate's name, \
any person's name, ID or registration numbers, bond holders, account numbers, arrears, or the internal file \
name you are given. Legal descriptions, extents, municipality, zoning and title deed numbers are fine.

Advert features: 4 or 5, the ones a buyer decides on, most important first (see the features field). \
Never filler such as the title type, zoning, or distances to schools, police stations or shops.

Examples of the team's own adverts (Nov 2026), to match in voice and wording. headline / subline / headline2 \
are the advert's three heading lines; the features are a selection of what the team's ads list:
- Business rescue auction, farm: MAKHADO | LIMPOPO / MACADAMIA & AVOCADO FARM; features ±38.15 HA TOTAL LAND AREA, \
±13 HA ESTABLISHED MACADAMIA ORCHARDS, ±10 HA ESTABLISHED AVOCADO ORCHARDS, BUILDINGS & NURSERY INFRASTRUCTURE, \
ESTABLISHED TREES AGED 8-27 YEARS
- Liquidation auction, sectional unit let as a hotel: MORNINGSIDE | SANDTON / LUXURY 4-SUITE INVESTMENT UNIT; \
features ±213m² PROPERTY, 4 EN-SUITE SUITES, COMMUNAL KITCHEN, DINING & LIVING AREA, 2 BALCONIES, PROVEN RENTAL & \
HOSPITALITY USE
- Liquidation auction, coastal apartment: 4 BEDROOM THREE-STOREY PROPERTY / SS MIRAMAR | UMHLANGA ROCKS | KZN; \
features ±216m² PROPERTY, 4 BEDROOMS, 2.5 BATHROOMS, OCEAN VIEWS, ROOFTOP JACUZZI
- Auction, house with a flat: 3-BED FAMILY HOME WITH A GRANNY FLAT / PRIMROSE | GERMISTON; features ±788m² PROPERTY, \
3 BEDROOMS, 2.5 BATHROOMS, SWIMMING POOL, 1-BED FLATLET WITH SEPARATE ENTRANCE
- Liquidation auction, block of flats: ±74-UNIT RESIDENTIAL / WITBANK PROPERTY INVESTMENT; features ±6 620m² PROPERTY, \
±74 RESIDENTIAL UNITS, HIGH TENANT DEMAND, GATED ACCESS
- Liquidation auction, several dwellings: FERNDALE, JOHANNESBURG / MULTI-DWELLING PROPERTY; features ±4 015m² PROPERTY, \
4 BEDROOMS, 1 BACHELOR FLAT & 2-BED APARTMENT, SWIMMING POOL & JACUZZI ROOM, 2 CARPORTS
- Insolvency sale, house in an estate: HARTIES / 3-BED HOME WITH BACHELOR FLAT / IN THE ISLANDS ESTATE; features \
±1 006m² PROPERTY, 3 BEDROOMS, KITCHEN & SCULLERY, PATIO WITH BUILT-IN BRAAI, DOUBLE GARAGE
- Sale of shares, farm: HARTELUS | LIMPOPO / ±32 HA FARM; features ±32.015 HECTARE, LAND PRIMARILY USED FOR GRAIN \
CULTIVATION (no price: the price box says MAKE US AN OFFER!)
- Insolvency sale, flat in a complex: 1-BED LOFT APARTMENT / BROADACRES | SANDTON / CARA BIANCA COMPLEX; features \
±93m² UNIT, 1 BEDROOM, LOFT LIVING ROOM, 2 BALCONIES, COMPLEX POOL & CLUBHOUSE
- Smallholding: MEYERTON / ±1.41 HA PROPERTY / VALLEY SETTLEMENTS; features MAIN HOUSE + FLATLET, BOREHOLE & WATER \
TANK, FRUIT TREES, 4 GARAGE PARKINGS & 3 CARPORTS
- Insolvency sale, house in an estate: RANDBURG | GAUTENG / DOUBLE-STOREY 4-BED HOME / OLIVEWOOD ESTATE
- Liquidation sale, luxury house: LUXURY 4 BEDROOM FAMILY HOME / HELDERFONTEIN ESTATE | MIDRAND; features ±623m² \
PROPERTY, 4 EN-SUITE BEDROOMS, SWIMMING POOL, SOLAR PANELS & INVERTER, DOUBLE GARAGE & AMPLE PARKING
Note how the heading names what makes the property worth buying (WITH A GRANNY FLAT, INVESTMENT UNIT, OCEAN VIEWS) \
and the estate or complex when there is one; features are specific (KITCHEN WITH SCULLERY, not KITCHEN).

Icons: pick each feature's icon from the allowed list, the way the team's ads do: "m2" for a floor or erf \
size in m², "ha" for any area in hectares (land, orchards, fields), "park" for trees and orchards without an \
area, "home" for houses and buildings, "bed", "bathtub", "garage", "fence" for gated access or security, \
"water_drop" for boreholes and irrigation, "bolt" for power."""


def _text_pdf(raw: bytes, max_pages: int, cap: int) -> str:
    import fitz

    with fitz.open(stream=raw, filetype="pdf") as doc:
        parts = [doc[i].get_text() for i in range(min(max_pages, doc.page_count))]
    return "\n".join(parts)[:cap]


def _text_docx(raw: bytes, cap: int) -> str:
    import docx

    d = docx.Document(io.BytesIO(raw))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            cells = []
            for c in row.cells:
                if c.text.strip() and (not cells or cells[-1] != c.text.strip()):
                    cells.append(c.text.strip())
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)[:cap]


# (kind, label for Claude, how many files, page limit, character cap)
_READ = (
    ("report", "PROPERTY REPORT (inspection)", 2, 40, 60_000),
    ("lightstone", "LIGHTSTONE / DEEDS REPORT", 4, 20, 40_000),
    ("valuation", "VALUATION", 1, 25, 40_000),
    ("otp", "OFFER TO PURCHASE / CONDITIONS OF SALE", 1, 0, 60_000),
    ("infopack", "AN EARLIER INFO PACK FOR THIS PROPERTY", 1, 20, 30_000),
    ("proposal", "AUCTION PROPOSAL", 1, 0, 20_000),
)


def gather(items: list[Item]) -> list[tuple[str, str, str]]:
    """(label, file name, text) for each document worth reading."""
    docs = catalog.documents(items)
    out = []
    for kind, label, count, pages, cap in _READ:
        for item in docs[kind][:count]:
            try:
                raw = store().content(item)
                text = _text_docx(raw, cap) if item.ext == ".docx" else _text_pdf(raw, pages, cap)
            except (StoreError, Exception) as exc:  # a broken file must not stop the rest
                log.warning("could not read %s: %s", item.path, exc)
                continue
            if text.strip():
                out.append((label, item.name, text))
    for item in docs["mailer"][:1]:
        out.append(("THE INFO MAILER'S SUBJECT LINE", item.name, os.path.splitext(item.name)[0]))
    return out


def read_documents(prop: Property, items: list[Item], sheets: dict) -> tuple[dict, list[str]]:
    """Ask Claude for every field. Returns (facts, warnings)."""
    import anthropic

    docs = gather(items)
    if not docs:
        raise StoreError("There are no Lightstone, property report, valuation or OTP files in the folder to read.")

    known = "\n".join(f"- {k}: {v}" for k, v in sheets.items() if v)
    sale = "It is going to AUCTION." if prop.is_auction else "It is FOR SALE by offers (not an auction) unless the documents say otherwise."
    head = (f"Property reference: {prop.ref}. Internal file name (never print it): {prop.name}.\n{sale}\n"
            f"Already known from the team's sheets (use these as given):\n{known or '- nothing'}\n\n"
            "The documents follow.")
    content: list[dict] = [{"type": "text", "text": head}]
    for label, name, text in docs:
        content.append({"type": "text", "text": f"=== {label}: {name} ===\n{text}"})
    content.append({"type": "text", "text": "Fill in every field for this property's ads and information pack."})

    facts = ask(SYSTEM, content, Facts,
                refusal="Claude declined to describe this property; fill the fields in by hand.").model_dump()
    facts = tidy(facts)
    facts["features"] = facts.get("features", [])[:5]
    link = team_qr_link(items)
    if link:
        facts["qr_link"] = link
    return facts, privacy_warnings(prop, facts)


def pick_features(prop: Property, facts: dict) -> list[dict]:
    """The 4-5 features again, chosen from what an earlier read already found
    (no documents: a small, cheap question)."""
    import json as _json

    known = {k: facts.get(k) for k in ("headline", "subline", "short_title", "description", "overview",
                                        "improvements", "highlights", "condition", "best_use", "features")}
    content = [{"type": "text", "text": f"Property {prop.ref}. What the documents said, as already written up:\n"
                + _json.dumps(known, ensure_ascii=False, indent=1)
                + "\n\nChoose the advert's headline features from this. Keep the house style of the existing features."}]
    got = ask(SYSTEM, content, Features, max_tokens=3000, effort="low",
              refusal="Claude declined; the features stay as they were.")
    return tidy([f.model_dump() for f in got.features])[:5]


def team_qr_link(items: list[Item]) -> str:
    """The link in the team's own QR code (a GoHighLevel tracking link to the
    property's page), so the studio's board and QR files send people to the same
    place and the scans are still counted. "" when the folder has none."""
    qrs = sorted((i for i in items if not i.is_folder and i.is_image and i.top.startswith("ADVERT")
                  and "QR" in i.name.upper()), key=lambda i: -i.modified)
    for item in qrs[:3]:
        try:
            import zxingcpp
            from PIL import Image

            found = []
            with Image.open(io.BytesIO(store().content(item))) as im:
                rgba = im.convert("RGBA")
                # black on clear needs a white ground, white on clear a dark one
                for ground in ((255, 255, 255), (40, 40, 40)):
                    bg = Image.new("RGB", rgba.size, ground)
                    bg.paste(rgba, mask=rgba.split()[-1])
                    found = zxingcpp.read_barcodes(bg)
                    if found:
                        break
        except Exception as exc:  # an unreadable image just means no link from it
            log.warning("could not read the QR code %s: %s", item.rel, exc)
            continue
        for code in found:
            if code.text.startswith(("http://", "https://")):
                return code.text
    return ""


def ask(system: str, content: list[dict], schema: type[BaseModel], *, max_tokens: int = 16000,
        effort: str = "medium", refusal: str = "Claude declined; fill this in by hand.") -> BaseModel:
    """One structured answer from Claude, validated against ``schema``."""
    import anthropic

    client = anthropic.Anthropic()
    # output_format is how the SDK hands back a validated Pydantic object; it warns
    # that the field is deprecated in favour of output_config.format, which would
    # return raw JSON instead. Fine to keep until the SDK drops it.
    warnings.filterwarnings("ignore", message=".*output_format.*deprecated", category=DeprecationWarning)
    kwargs = dict(model=MODEL, max_tokens=max_tokens, system=system, output_config={"effort": effort},
                  messages=[{"role": "user", "content": content}], output_format=schema)
    try:
        # Refusal fallbacks on (a safety classifier declining a property description
        # is unlikely, but then another model finishes the same request).
        resp = client.beta.messages.parse(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except anthropic.BadRequestError as exc:
        log.warning("fallbacks refused (%s); asking without them", exc)
        resp = client.messages.parse(**kwargs)
    if resp.stop_reason == "refusal":
        raise StoreError(refusal)
    if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
        raise StoreError("Claude's answer was cut short; try again.")
    return resp.parsed_output


# --- privacy check ----------------------------------------------------------

_COMMON = set("""PTY LTD CC TRUST THE AND OF IN FOR ESTATE LATE INSOLVENT INSOLVENCY LIQUIDATION BUSINESS RESCUE
FARMING FARM FARMS PROPERTIES PROPERTY HOLDINGS INVESTMENTS INVESTMENT CONSTRUCTION GROUP SERVICES TRADING
ENTERPRISE ENTERPRISES PRIVATE SALE FAMILY BOERDERY BK HOUSING COMPANY TRANSPORT DEVELOPMENTS LIMPOPO GAUTENG
MPUMALANGA NATAL WESTERN EASTERN NORTHERN CAPE NORTH WEST FREE STATE KWAZULU""".split())


def privacy_warnings(prop: Property, facts: dict) -> list[str]:
    """Flag words from the matter name and anything shaped like an ID number."""
    name = re.sub(r"\(.*?\)|\[.*?\]", " ", prop.name.upper())
    tokens = {t for t in re.findall(r"[A-Z][A-Z'-]{3,}", name) if t not in _COMMON}
    hits: set[str] = set()

    def walk(v):
        if isinstance(v, str):
            up = v.upper()
            for t in tokens:
                if re.search(rf"\b{re.escape(t)}\b", up):
                    hits.add(t)
            if re.search(r"\b\d{13}\b", v):
                hits.add("a 13-digit number (ID?)")
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    walk(facts)
    return [f"Mentions {h} from the file name: check before it goes out." if not h.startswith("a ") else
            f"Contains {h}: check before it goes out." for h in sorted(hits)]


# --- merging --------------------------------------------------------------

def merge(current: dict, incoming: dict, edited: set[str]) -> dict:
    """Fill ``current`` from ``incoming``, never touching a field Nikki edited."""
    out = dict(current)
    for k, v in incoming.items():
        if k in edited:
            continue
        if v in ("", [], None) and out.get(k) not in ("", [], None):
            continue
        out[k] = v
    return out


def initial(prop: Property, items: list[Item]) -> dict:
    facts = blank()
    facts.update(from_sheets(prop, items))
    return facts


def mode_for(prop: Property) -> str:
    return "auction" if prop.is_auction else "forsale"


def name_for(prop: Property, kind: str, mode: str) -> str:
    n = f"DP{prop.number}"
    return {
        "ad": f"{n} {'AUCTION' if mode == 'auction' else 'FOR SALE'} AD.png",
        "saia": f"{n} SAIA BANNER.png",
        "icon": f"{n} APP ICON.png",
        "board": f"{n} {'AUCTION' if mode == 'auction' else 'FOR SALE'} BOARD.pdf",
        "infopack": f"{n} {'AUCTION' if mode == 'auction' else 'FOR SALE'} INFO PACK.pdf",
        "qr_black": f"{n} - QR CODE BLACK.png",
        "qr_white": f"{n} - QR CODE WHITE.png",
    }[kind]
