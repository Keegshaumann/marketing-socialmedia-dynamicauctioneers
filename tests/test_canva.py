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
    # A dataset covering every field the composer can emit, so the poison
    # scan exercises the full payload (stat bar + tagline + ref included).
    dataset = {
        "headline": "text", "price": "text", "body": "text",
        "address": "text", "suburb": "text", "dp": "text",
        "property_ref": "text", "beds": "text", "baths": "text",
        "garages": "text", "size": "text", "features": "text",
    }
    data = CanvaBackend()._autofill_data(
        request, asset_ids=[], dataset=dataset, image_slots=[]
    )
    blob = json.dumps(data)
    for marker in POISON_MARKERS:
        assert marker not in blob


# --- named template sets (D33) --------------------------------------------

def _set_all_creds(monkeypatch, template_map: str):
    for var in ("CANVA_CLIENT_ID", "CANVA_CLIENT_SECRET", "CANVA_REFRESH_TOKEN"):
        monkeypatch.setenv(var, "x")
    monkeypatch.setenv("CANVA_TEMPLATE_MAP", template_map)


def test_flat_map_files_under_default_set(monkeypatch):
    monkeypatch.setenv("CANVA_TEMPLATE_MAP", '{"demo_ad": "TPL_A"}')
    backend = CanvaBackend()
    assert backend._load_template_sets() == {"Default": {"demo_ad": "TPL_A"}}
    assert backend.supports("demo_ad") is True
    assert backend.supports("info_pack") is False


def test_named_sets_supports_is_the_default_sets_universe(monkeypatch):
    monkeypatch.setenv(
        "CANVA_TEMPLATE_MAP",
        '{"Classic gold": {"demo_ad": "TPL_A", "info_pack": "TPL_P"},'
        ' "Modern dark": {"demo_ad": "TPL_B", "auction_board": "TPL_C"}}',
    )
    backend = CanvaBackend()
    # supports() answers for the DEFAULT (first) set only: it must never claim
    # a format that _resolve_template cannot resolve for a default-set record
    # (a union answer crashed a canva-only render pass in review).
    assert backend.supports("demo_ad") is True
    assert backend.supports("info_pack") is True
    assert backend.supports("auction_board") is False  # only in a later set
    assert backend.supports("saia_banner") is False
    # Names come back in declared order; the first is the default.
    from engine.render.canva_backend import template_set_names

    assert template_set_names() == ["Classic gold", "Modern dark"]


def test_mixed_shapes_are_a_config_error(monkeypatch):
    _set_all_creds(
        monkeypatch, '{"demo_ad": "TPL_A", "Modern dark": {"demo_ad": "TPL_B"}}'
    )
    ok, reason = CanvaBackend().available()
    assert ok is False
    assert "CANVA_TEMPLATE_MAP" in reason


def test_flat_map_with_nested_value_is_a_loud_config_error(monkeypatch):
    # {"demo_ad": {...}} passes an all-values-are-dicts sniff, which would
    # silently become a named set called "demo_ad" and turn a malformed flat
    # map into Canva quietly rendering nothing. It must refuse instead.
    _set_all_creds(monkeypatch, '{"demo_ad": {"nested": "TPL_A"}}')
    ok, reason = CanvaBackend().available()
    assert ok is False
    assert "demo_ad" in reason and "collide" in reason


def test_empty_default_set_is_unavailable(monkeypatch):
    # The first set defines the renderable formats; empty means the backend
    # can render nothing and must say so rather than claim availability.
    _set_all_creds(
        monkeypatch, '{"Classic gold": {}, "Modern dark": {"demo_ad": "TPL_B"}}'
    )
    ok, reason = CanvaBackend().available()
    assert ok is False
    assert "default" in reason.lower()


def test_resolve_template_overlay_and_fallbacks(monkeypatch):
    monkeypatch.setenv(
        "CANVA_TEMPLATE_MAP",
        '{"Classic gold": {"demo_ad": "TPL_A", "info_pack": "TPL_P"},'
        ' "Modern dark": {"demo_ad": "TPL_B"}}',
    )
    backend = CanvaBackend()
    # The chosen set wins where it maps the format...
    assert backend._resolve_template("demo_ad", "Modern dark") == "TPL_B"
    # ...and overlays the default set where it does not.
    assert backend._resolve_template("info_pack", "Modern dark") == "TPL_P"
    # No pick / a stale pick on a record degrades to the default set.
    assert backend._resolve_template("demo_ad", None) == "TPL_A"
    assert backend._resolve_template("demo_ad", "Deleted set") == "TPL_A"
    # A format no set maps resolves to None (render refuses cleanly).
    assert backend._resolve_template("saia_banner", "Modern dark") is None


def test_template_set_names_never_raises(monkeypatch):
    from engine.render.canva_backend import template_set_names

    monkeypatch.delenv("CANVA_TEMPLATE_MAP", raising=False)
    assert template_set_names() == []
    monkeypatch.setenv("CANVA_TEMPLATE_MAP", "{not json")
    assert template_set_names() == []
