"""Channel routing rules (M6, SPEC 5.6).

The hard-coded routing matrix that decides which channels a property is
distributed to. This is pure code: no external calls, no credentials, fully
deterministic and testable. The downstream posting modules
(``engine.distribute.ghl`` and ``.packs``) consult this matrix to
decide what to push and what to park as a manual pack.

The rules (SPEC 5.6 "M6 - Distribution", D5):

- **Every property** goes to Property24, the own website, Facebook and the
  email list.
- **>= R10m** additionally goes to JamesEdition (the luxury portal).
- **Industrial or commercial** additionally goes to the commercial portals (the
  specific portals are TBD, open question #4 for the parser side; the routing
  flag is stable regardless).
- **Private Property is always excluded** by policy (D5) - never routed, whatever
  the value or type.

The property's value is read from the record sensibly: an explicit numeric
``marketing.price_display`` wins, then the Lightstone EVM estimate, then the
municipal valuation, then comparable sales. A record like DP3060 whose price
display is textual ("Offers invited") falls through to the valuation figures.
The type is read from ``physical.zoning`` (and the identity legal description as
a backstop), matched case-insensitively for industrial/commercial keywords.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from engine.schema import PropertyRecord


# The luxury-portal threshold (D5): properties valued at or above this route to
# JamesEdition in addition to the standard channels.
JAMESEDITION_THRESHOLD_ZAR: float = 10_000_000.0

# Zoning / description tokens that mark a property as industrial or commercial.
# Matched case-insensitively as whole words so "commercial" is caught but
# "residential" is not accidentally matched on a substring.
_COMMERCIAL_TOKENS = (
    "industrial",
    "commercial",
    "warehouse",
    "factory",
    "retail",
    "office",
    "business",
)
_COMMERCIAL_RE = re.compile(
    r"\b(" + "|".join(_COMMERCIAL_TOKENS) + r")\b", re.IGNORECASE
)


def _parse_price_display(price_display: Optional[str]) -> Optional[float]:
    """Return the rand amount embedded in ``price_display``, or ``None``.

    Handles displays like ``"R 12 500 000"`` or ``"R12,500,000"``. A textual
    display with no digits ("Offers invited") returns ``None`` so the caller
    falls through to the valuation figures. Requires at least four digits to
    avoid treating a stray number (a bedroom count leaking in) as a price.
    """
    if not price_display:
        return None
    # Take the FIRST contiguous numeric token (thousands separators allowed) and
    # stop at a decimal point or any trailing text, so "R950 000 (was R1 050 000)"
    # reads as 950000, not a concatenation, and cents do not inflate the value.
    match = re.search(r"\d[\d ,]*(?:\.\d+)?", price_display)
    if not match:
        return None
    integer_part = match.group(0).split(".")[0]
    digits = re.sub(r"[^0-9]", "", integer_part)
    if len(digits) < 4:
        return None
    try:
        return float(digits)
    except ValueError:  # pragma: no cover - re guarantees digits only
        return None


def property_value(record: PropertyRecord) -> Optional[float]:
    """Return a representative rand value for ``record``, or ``None``.

    Priority (most property-specific first): an explicit numeric price display,
    then the high end of the Lightstone EVM range, then the municipal
    valuation, then the comparable average sales price. Used only to test the
    JamesEdition threshold; it is not a valuation in itself.
    """
    marketing = record.marketing
    if marketing is not None:
        parsed = _parse_price_display(marketing.price_display)
        if parsed is not None:
            return parsed

    valuation = record.valuation
    if valuation is not None:
        if valuation.evm_range:
            # The high end: a property whose estimate tops the threshold qualifies.
            return float(max(valuation.evm_range))
        if valuation.municipal_valuation is not None:
            return float(valuation.municipal_valuation)
        if valuation.comparables_avg_sales_price is not None:
            return float(valuation.comparables_avg_sales_price)

    return None


def is_commercial(record: PropertyRecord) -> bool:
    """Whether ``record`` is an industrial or commercial property.

    Reads ``physical.zoning`` first, then the identity legal description as a
    backstop, matching industrial/commercial keywords case-insensitively. A
    residential record (DP3060, zoning "Residential") returns ``False``.
    """
    # Zoning is authoritative when present: an explicit "Residential" is not
    # overridden by an incidental keyword in the legal description. The legal
    # description is only a backstop when zoning is absent.
    physical = record.physical
    if physical is not None and physical.zoning:
        return bool(_COMMERCIAL_RE.search(physical.zoning))

    identity = record.identity
    if identity is not None and identity.legal_description:
        if _COMMERCIAL_RE.search(identity.legal_description):
            return True

    return False


def channel_matrix(record: PropertyRecord) -> Dict[str, bool]:
    """Return the channel routing decision for ``record`` (SPEC 5.6, D5).

    Keys are channel identifiers; values are whether the property routes there.
    The five standard channels are always ``True``; JamesEdition depends on the
    R10m threshold; the commercial portals depend on the property type; Private
    Property is always ``False`` (policy exclusion, D5).
    """
    value = property_value(record)
    over_threshold = value is not None and value >= JAMESEDITION_THRESHOLD_ZAR

    return {
        # Every property (SPEC 5.6 row 1). The three connected GHL socials
        # (Facebook, Instagram, LinkedIn) all route; TikTok/Pinterest are out
        # (D24/D26), X is not connected.
        "property24": True,
        "own_website": True,
        "facebook": True,
        "instagram": True,
        "linkedin": True,
        "email_list": True,
        # >= R10m (SPEC 5.6 row 2).
        "jamesedition": over_threshold,
        # Industrial / commercial (SPEC 5.6 row 3; specific portals TBD, oq#4).
        # PLACEHOLDER(open-question#4): the concrete commercial portals are not
        # yet chosen; this flag routes to them once selected.
        "commercial_portals": is_commercial(record),
        # Excluded by policy (SPEC 5.6 row 4, D5) - never routed.
        "private_property": False,
    }
