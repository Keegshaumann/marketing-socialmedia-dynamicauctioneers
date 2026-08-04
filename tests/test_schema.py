"""Schema and POPIA-projection tests (M2/M4).

Covers three contract obligations:
- the committed golden ``DP3060/record.json`` validates against
  ``PropertyRecord`` (the schema really mirrors the record shape);
- ``public_view()`` physically removes the POPIA internal layer;
- a structural poison-marker proof that PII placed in the internal layer never
  reaches the public projection, whatever the field values are.
"""

from __future__ import annotations

import json

from engine.schema import (
    FinancialsInternal,
    Owner,
    PropertyRecord,
    SaleProcess,
    Viewing,
)


def test_golden_record_validates(golden_record_path):
    """The hand-built golden record parses cleanly into a PropertyRecord."""
    data = json.loads(golden_record_path.read_text())
    record = PropertyRecord.model_validate(data)

    assert record.dp == "3060"
    assert record.identity is not None
    assert record.identity.suburb == "Pelham North"
    assert record.identity.title_type == "sectional"


def test_public_view_strips_internal_layer(golden_record_path):
    """public_view drops financials_internal and the private viewing cell."""
    record = PropertyRecord.model_validate_json(golden_record_path.read_text())

    full = record.model_dump(mode="json")
    assert full["financials_internal"] is not None
    assert full["sale_process"]["viewing"]["contact_internal_only"] is not None

    public = record.public_view()
    assert "financials_internal" not in public
    assert "contact_internal_only" not in public["sale_process"]["viewing"]
    # The safe public contact survives the projection.
    assert public["sale_process"]["viewing"]["contact_public"] is not None


POISON = "POPIA_POISON_MARKER_DO_NOT_LEAK_7f3a"


def test_public_view_never_leaks_pii_poison_marker():
    """A marker planted in the internal layer is absent from the public JSON.

    This is a structural proof rather than a value check: whatever a renderer
    does with ``public_view()``, it cannot surface owner PII or the occupant's
    private cell, because ``json.dumps`` of the projection does not contain the
    marker anywhere.
    """
    record = PropertyRecord(
        dp="9999",
        financials_internal=FinancialsInternal(
            owner=Owner(name=POISON, id_number=POISON),
        ),
        sale_process=SaleProcess(
            viewing=Viewing(
                by_appointment=True,
                contact_public="086 155 2288",
                contact_internal_only=POISON,
            ),
        ),
    )

    # The marker is genuinely present in the full internal dump.
    assert POISON in record.model_dump_json()

    public_json = json.dumps(record.public_view())
    assert POISON not in public_json
    # And the layer itself is gone, not merely blanked.
    assert "financials_internal" not in json.loads(public_json)
    assert (
        "contact_internal_only"
        not in json.loads(public_json)["sale_process"]["viewing"]
    )


def test_public_view_strips_professional_valuation():
    """Sale-strategy figures never reach a renderer or the copy model (D32).

    The valuer's market and forced-sale values would anchor buyers or undercut
    the sale if they appeared in an ad, so the whole ``valuation.professional``
    block is structurally absent from the public projection while the rest of
    the valuation section survives.
    """
    from engine.schema import ProfessionalValuation, Valuation

    record = PropertyRecord(
        dp="2035",
        valuation=Valuation(
            municipal_valuation=2310000,
            professional=ProfessionalValuation(
                market_value=3400000,
                forced_sale_value=2380000,
                valuation_date="2026-06-22",
                valuer=POISON,
            ),
        ),
    )

    assert POISON in record.model_dump_json()

    public = record.public_view()
    assert "professional" not in public["valuation"]
    assert POISON not in json.dumps(public)
    # The municipal valuation is stripped too (owner directive: the money line is
    # always the offers framing, never a valuation figure). Stripping it from the
    # projection - not just from the templates - is what stops the copy model
    # seeing it: given the figure it was observed writing "Offers invited.
    # Municipal valuation R960 000 (2024)." into price_display, which every ad
    # then printed.
    assert "municipal_valuation" not in public["valuation"]
    assert "2310000" not in json.dumps(public)
    # The valuation section itself survives (its publishable fields are intact).
    assert isinstance(public["valuation"], dict)
    assert "evm_range" in public["valuation"]


def test_generated_copy_is_scrubbed_of_municipal_valuation():
    """Second line of defence on the owner directive.

    The figure is stripped from ``public_view`` so the copy model cannot see it,
    but a bundle cached before that fix (or any other route to the phrase) must
    still never reach an advert: every ad renders price_display verbatim.
    """
    from engine.render.copy import _scrub_forbidden

    out = _scrub_forbidden({
        "price_display": "Offers invited. Municipal valuation R960 000 (2024).",
        "headline": "Two bedroom apartment in Pelham North",
    })
    assert out["price_display"] == "Offers invited"
    assert "municipal" not in out["price_display"].lower()
    assert out["headline"] == "Two bedroom apartment in Pelham North"  # untouched
    # A line that is ONLY the forbidden phrase becomes None rather than a stub.
    assert _scrub_forbidden({"price_display": "Municipal valuation R960 000"})["price_display"] is None


# --- multi-portion property (multi-file intake) --------------------------

def test_portions_total_m2_sums_in_code():
    """A multi-portion property's extent is the sum of its portions, in code."""
    from engine.schema import Physical, Portion, portions_total_m2

    physical = Physical(
        portions=[
            Portion(label="Portion 6 of Farm 7", size_m2=21000.0),
            Portion(label="Portion 7 of Farm 7", size_m2=18500.5),
            Portion(label="Remainder", size_m2=None),  # a portion with no stated size
        ]
    )
    assert portions_total_m2(physical) == 39500.5


def test_portions_total_m2_none_without_portions():
    """No portions (an ordinary property) or none with a size -> None, not 0."""
    from engine.schema import Physical, Portion, portions_total_m2

    assert portions_total_m2(None) is None
    assert portions_total_m2(Physical()) is None
    assert portions_total_m2(Physical(portions=[Portion(label="A", size_m2=None)])) is None


def test_public_view_carries_portions():
    """Portions are public marketing facts and survive the projection."""
    from engine.schema import Physical, Portion

    record = PropertyRecord(
        dp="2918.1",
        physical=Physical(portions=[Portion(label="Erf 15", size_m2=744.0)]),
    )
    public = record.public_view()
    assert public["physical"]["portions"][0]["label"] == "Erf 15"
