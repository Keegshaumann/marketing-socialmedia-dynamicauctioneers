"""Canonical property-record schema (Pydantic v2).

This is the Phase 1 formalisation of the record shape that ``DP3060/record.json``
established by hand in Phase 0 (SPEC.md 4.2). Extraction (M2) produces a
``PropertyRecord``; the record store (M4) persists it; renderers (M5, Phase 3)
consume ``public_view()`` — never the model directly.

Design rules baked in here:
- Every field is Optional and defaults to ``None`` so a fact the source docs
  don't contain becomes ``null`` rather than a hallucination (SPEC M2 criterion).
- ``financials_internal`` and ``sale_process.viewing.contact_internal_only``
  are the POPIA internal layer: owner name/ID, bond, arrears, occupant contact.
  ``public_view()`` physically removes them (SPEC 4.4). Public templates cannot
  reach PII because the projection never contains it.
- ``extra="forbid"`` => the generated JSON Schema carries
  ``additionalProperties: false``, which structured outputs require, and which
  makes a stray hallucinated field a validation error rather than silent data.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- sources -------------------------------------------------------------

class LightstoneSource(_Base):
    file: Optional[str] = None
    report_id: Optional[str] = None
    report_date: Optional[str] = None
    purchased_by: Optional[str] = None


class PropertyReportSource(_Base):
    file: Optional[str] = None
    prepared_by: Optional[str] = None
    figures_as_at: Optional[str] = None


class Sources(_Base):
    lightstone_evm: Optional[LightstoneSource] = None
    property_report: Optional[PropertyReportSource] = None


# --- identity (Lightstone deeds data wins) -------------------------------

class Identity(_Base):
    title_type: Optional[str] = None  # "sectional" | "freehold"
    scheme: Optional[str] = None  # sectional title only
    unit: Optional[int] = None  # sectional title only
    erf: Optional[str] = None  # freehold only (DP3040-style)
    legal_description: Optional[str] = None
    street_address: Optional[str] = None
    suburb: Optional[str] = None
    municipality: Optional[str] = None
    municipality_note: Optional[str] = None
    province: Optional[str] = None
    gps: Optional[List[float]] = None  # [lat, lon]
    title_deed_no: Optional[str] = None
    mandate_ref: Optional[str] = None  # DA mandate/instruction no. ("MASTER REF" on ads); shared by sub-lots (DP3035.1/.2). null until sourced.


# --- physical (Property Report / inspection wins) ------------------------

class Flatlet(_Base):
    present: Optional[bool] = None
    bedrooms: Optional[int] = None
    ensuite: Optional[bool] = None
    kitchen: Optional[bool] = None
    lounge: Optional[bool] = None
    patio: Optional[bool] = None
    note: Optional[str] = None


class Physical(_Base):
    unit_size_m2: Optional[float] = None
    mother_erf_m2: Optional[float] = None
    zoning: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms_main_unit: Optional[int] = None
    separate_toilet: Optional[bool] = None
    garages: Optional[int] = None
    garages_conflict: Optional[str] = None  # set when sources disagree
    # One line per cross-source disagreement on any OTHER physical fact (bed
    # count, sizes...). The inspection's value stands in the field itself per
    # the merge rule, but the disagreement must be recorded, never silently
    # picked (seen live on Erf 2035: EVM said 2 bedrooms / 106 m2, inspection
    # said 4 / 310 m2, and only the garage half of that line was noted). Each
    # entry raises a blocking PHYSICAL_CONFLICT verification flag (D32).
    conflicts: Optional[List[str]] = None
    flatlet: Optional[Flatlet] = None
    features_main: Optional[List[str]] = None
    features_complex: Optional[List[str]] = None


# --- valuation (Lightstone EVM/comps wins) -------------------------------

class SuburbBands(_Base):
    low: Optional[float] = None
    mid: Optional[float] = None
    high: Optional[float] = None


class SameSchemeSale(_Base):
    unit: Optional[int] = None
    scheme: Optional[str] = None
    size_m2: Optional[float] = None
    price: Optional[float] = None
    terms: Optional[str] = None
    sale_date: Optional[str] = None
    rand_per_m2: Optional[float] = None


class ProfessionalValuation(_Base):
    """A registered valuer's figures, when a valuation report is a source.

    Sale-strategy data, not ad material: ``public_view()`` strips this whole
    block (a published "forced sale value" would undercut the sale), so no
    renderer or copy model ever sees it. It reaches the verification memo and
    the internal gate screens only (D32).
    """

    market_value: Optional[float] = None
    forced_sale_value: Optional[float] = None
    valuation_date: Optional[str] = None
    valuer: Optional[str] = None
    note: Optional[str] = None


class Valuation(_Base):
    evm_range: Optional[List[float]] = None  # [low, high]
    suburb_bands: Optional[SuburbBands] = None
    municipal_valuation: Optional[float] = None
    municipal_valuation_year: Optional[int] = None
    estimated_monthly_rates: Optional[float] = None
    comparables_avg_sales_price: Optional[float] = None
    same_scheme_sale: Optional[SameSchemeSale] = None
    professional: Optional[ProfessionalValuation] = None


# --- financials_internal (POPIA internal layer — never rendered) ---------

class Owner(_Base):
    """Owner PII. Lives only here; stripped by ``public_view()``."""

    name: Optional[str] = None
    id_number: Optional[str] = None


class LastSale(_Base):
    price: Optional[float] = None
    date: Optional[str] = None
    deed: Optional[str] = None


class Bond(_Base):
    amount: Optional[float] = None
    institution: Optional[str] = None
    bond_no: Optional[str] = None


class FinancialsInternal(_Base):
    owner: Optional[Owner] = None
    last_sale: Optional[LastSale] = None
    bond: Optional[Bond] = None
    outstanding_rates_taxes_water: Optional[float] = None
    outstanding_levies: Optional[float] = None
    as_at: Optional[str] = None
    note: Optional[str] = None


# --- sale process (Property Report wins) ---------------------------------

class Viewing(_Base):
    by_appointment: Optional[bool] = None
    contact_public: Optional[str] = None  # Dynamic's number (safe to render)
    contact_internal_only: Optional[str] = None  # occupant cell — POPIA, stripped


class SaleProcess(_Base):
    method: Optional[str] = None  # "offers_invited" | "auction"
    terms: Optional[List[str]] = None
    viewing: Optional[Viewing] = None


# --- marketing (system + human edits) ------------------------------------

class ChannelRouting(_Base):
    property24: Optional[bool] = None
    own_website: Optional[bool] = None
    facebook: Optional[bool] = None
    email_list: Optional[bool] = None
    jamesedition: Optional[bool] = None
    jamesedition_reason: Optional[str] = None
    private_property: Optional[bool] = None
    private_property_reason: Optional[str] = None


class Marketing(_Base):
    headline: Optional[str] = None
    price_display: Optional[str] = None
    channel_routing: Optional[ChannelRouting] = None
    hero_photo: Optional[str] = None
    gallery: Optional[List[str]] = None


# --- compliance / verification -------------------------------------------

class Compliance(_Base):
    owner_pii_redacted: Optional[bool] = None
    notes: Optional[str] = None


class Verification(_Base):
    status: Optional[str] = None  # "flags_raised" | "verified" | ...
    memo: Optional[str] = None
    human_signoff: Optional[str] = None


# --- human overrides (POPIA-guarded editor layer) ------------------------
# A marketer may correct a public fact on an already-sourced record (e.g. a
# suburb typo, a changed sale method). The correction is stored as a top-level
# ``human_overrides`` map of dotted public-view path -> replacement value and
# applied last by ``public_view()``, so the sourced Lightstone / Property-Report
# layer stays pristine (SPEC hard rule 3: every field still traces to a source)
# and the edit survives a re-extraction. An override can never reach a POPIA
# path: the guard below rejects any key that would recreate a stripped field.

_OVERRIDE_FORBIDDEN_PREFIXES = ("financials_internal",)
_OVERRIDE_FORBIDDEN_PATHS = ("sale_process.viewing.contact_internal_only",)


def override_key_allowed(path: str) -> bool:
    """Whether ``path`` may be set as a ``human_overrides`` key (POPIA guard).

    Rejects the whole ``financials_internal`` group and the occupant's private
    cell, so an override can never resurrect a field ``public_view()`` strips.
    A key is refused when it is a forbidden path, an **ancestor** of one (a
    whole-dict override that would carry the stripped field back in), or a
    **descendant** under one.
    """
    if not path:
        return False
    head = path.split(".", 1)[0]
    if head in _OVERRIDE_FORBIDDEN_PREFIXES:
        return False
    for forbidden in _OVERRIDE_FORBIDDEN_PATHS:
        if path == forbidden or forbidden.startswith(path + ".") or path.startswith(forbidden + "."):
            return False
    return True


def _strip_pii(data: dict) -> None:
    """Remove the POPIA internal layer from a projection dict, in place."""
    data.pop("financials_internal", None)
    sale = data.get("sale_process")
    if isinstance(sale, dict):
        viewing = sale.get("viewing")
        if isinstance(viewing, dict):
            viewing.pop("contact_internal_only", None)


def _strip_internal_strategy(data: dict) -> None:
    """Remove sale-strategy figures from a projection dict, in place.

    Not POPIA, but commercially sensitive: the copy model writes ads from the
    public view, and a professional valuation's market or forced-sale value in
    an ad would anchor buyers or undercut the sale. The whole block stays
    internal (memo and gate screens) until a logged decision opens it (D32).
    """
    valuation = data.get("valuation")
    if isinstance(valuation, dict):
        valuation.pop("professional", None)


def _apply_overrides(data: dict, overrides: dict) -> None:
    """Apply a dotted-path override map onto the already-stripped ``data`` dict.

    Walks each dotted key, creating intermediate dicts as needed, and does a
    whole-value leaf replacement. Runs on the PII-stripped projection and skips
    any forbidden key, so an override can never re-add a POPIA field.
    """
    for path, value in (overrides or {}).items():
        if not path or not override_key_allowed(path):
            continue
        parts = path.split(".")
        node = data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value


# --- top-level record ----------------------------------------------------

class PropertyRecord(_Base):
    dp: str
    parent_dp: Optional[str] = None
    status: Optional[str] = None
    record_created: Optional[str] = None
    sources: Optional[Sources] = None
    identity: Optional[Identity] = None
    physical: Optional[Physical] = None
    valuation: Optional[Valuation] = None
    financials_internal: Optional[FinancialsInternal] = None
    sale_process: Optional[SaleProcess] = None
    marketing: Optional[Marketing] = None
    compliance: Optional[Compliance] = None
    verification: Optional[Verification] = None
    # Dotted public-view path -> human replacement value; applied last by
    # public_view(). Never a POPIA path (guarded in _apply_overrides / on write).
    human_overrides: Optional[Dict[str, Any]] = None

    def public_view(self) -> dict:
        """Return a projection safe for public renderers (SPEC 4.4).

        Physically removes the POPIA internal layer: the entire
        ``financials_internal`` group and the occupant's private cell in
        ``sale_process.viewing.contact_internal_only``. A renderer handed this
        dict cannot leak PII because the PII is not present in it. The
        professional valuation block (``valuation.professional``) is removed
        for the same structural reason: sale-strategy figures must never reach
        a renderer or the copy model (D32).

        Human edits stored in ``human_overrides`` are applied last, after the
        PII strip, and cannot recreate a stripped POPIA path (see
        ``_apply_overrides``). The sourced record fields are left untouched.
        """
        data = self.model_dump(mode="json")
        _strip_pii(data)
        _strip_internal_strategy(data)
        overrides = data.pop("human_overrides", None) or {}
        _apply_overrides(data, overrides)
        # Defence in depth: overrides are applied last and are guarded on write,
        # but strip again so no override shape can leave a POPIA or strategy
        # field behind.
        _strip_pii(data)
        _strip_internal_strategy(data)
        return data
