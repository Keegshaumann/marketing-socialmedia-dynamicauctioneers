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
