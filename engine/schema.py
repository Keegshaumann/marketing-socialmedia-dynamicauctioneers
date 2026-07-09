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

from typing import List, Optional

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


class Valuation(_Base):
    evm_range: Optional[List[float]] = None  # [low, high]
    suburb_bands: Optional[SuburbBands] = None
    municipal_valuation: Optional[float] = None
    municipal_valuation_year: Optional[int] = None
    estimated_monthly_rates: Optional[float] = None
    comparables_avg_sales_price: Optional[float] = None
    same_scheme_sale: Optional[SameSchemeSale] = None


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
    whatsapp_broadcast: Optional[bool] = None
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

    def public_view(self) -> dict:
        """Return a projection safe for public renderers (SPEC 4.4).

        Physically removes the POPIA internal layer: the entire
        ``financials_internal`` group and the occupant's private cell in
        ``sale_process.viewing.contact_internal_only``. A renderer handed this
        dict cannot leak PII because the PII is not present in it.
        """
        data = self.model_dump(mode="json")
        data.pop("financials_internal", None)
        sale = data.get("sale_process")
        if isinstance(sale, dict):
            viewing = sale.get("viewing")
            if isinstance(viewing, dict):
                viewing.pop("contact_internal_only", None)
        return data
