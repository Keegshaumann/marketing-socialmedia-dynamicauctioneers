"""Canva scaffold tests (M5, D14) -- kept separate so deletion stays clean.

The Canva Connect backend is the one-move-removable scaffold. Removing it is:
delete ``engine/render/canva_backend.py``, its single line in the render
registry, and this file. Nothing else references it, so the suite stays green.

These tests run offline with no Canva credentials: ``available()`` must report
why it is unconfigured (never raise), ``supports()`` must be False with no
template map, ``render()`` must refuse cleanly, and the autofill payload the
backend would send to Canva's cloud must carry only public_view data (no PII).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.render import get_backend
from engine.render.base import RenderRequest
from engine.render.canva_backend import CanvaBackend
from engine.schema import Owner, PropertyRecord

_CANVA_VARS = (
    "CANVA_CLIENT_ID",
    "CANVA_CLIENT_SECRET",
    "CANVA_REFRESH_TOKEN",
    "CANVA_TEMPLATE_MAP",
)

POISON_OWNER = "ZZOWNERPOISON_DoNotPublish"
POISON_ID = "ZZID_9999999999"
POISON_CELL = "ZZCELL_0820001111"
POISON_MARKERS = (POISON_OWNER, POISON_ID, POISON_CELL)


@pytest.fixture(autouse=True)
def _no_canva_creds(monkeypatch):
    for var in _CANVA_VARS + ("CANVA_STATE_FILE",):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def golden_record(golden_record_path: Path) -> PropertyRecord:
    return PropertyRecord.model_validate_json(
        golden_record_path.read_text(encoding="utf-8")
    )


def test_registry_selects_canva_backend():
    assert get_backend("canva").name == "canva"


def test_available_reports_missing_credentials():
    ok, reason = CanvaBackend().available()
    assert ok is False
    # Names at least one missing credential rather than crashing.
    assert "CANVA_CLIENT_ID" in reason


def test_supports_is_false_without_template_map():
    assert CanvaBackend().supports("demo_ad") is False


def test_render_refuses_when_unconfigured(golden_record):
    request = RenderRequest(
        dp="3060",
        fmt="demo_ad",
        public_record=golden_record.public_view(),
        photos=[],
        copy=None,
    )
    with pytest.raises(RuntimeError):
        CanvaBackend().render(request)


def test_autofill_payload_carries_only_public_view(golden_record):
    # Plant PII in the internal layer, then confirm the autofill payload the
    # backend builds from public_view + copy contains none of it.
    if golden_record.financials_internal is not None:
        golden_record.financials_internal.owner = Owner(
            name=POISON_OWNER, id_number=POISON_ID
        )
    if golden_record.sale_process is not None and golden_record.sale_process.viewing is not None:
        golden_record.sale_process.viewing.contact_internal_only = POISON_CELL

    request = RenderRequest(
        dp="3060",
        fmt="demo_ad",
        public_record=golden_record.public_view(),
        photos=[],
        copy={"headline": "Public headline", "price_display": "Offers invited"},
    )
    data = CanvaBackend()._autofill_data(request, asset_ids=[])
    blob = json.dumps(data)
    for marker in POISON_MARKERS:
        assert marker not in blob
