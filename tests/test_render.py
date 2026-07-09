"""Rendering + swappable-backend tests (M5, Phase 3C).

All offline, no API key. Covers backend resolution (default html, the
ENGINE_RENDERER override, and an unconfigured Canva scaffold degrading rather
than crashing), a real-facts demo-ad render, the per-backend poison-marker PII
guarantee, and a price re-render preserving a human copy edit. The Canva
scaffold is exercised only through the registry here; its no-credential
behaviour lives in ``tests/test_canva.py`` so deleting the scaffold keeps this
suite green.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

from engine import MODEL
from engine.render import get_backend, list_backends
from engine.render.copy import CopyBundle, build_copy_request
from engine.render.service import render_all, render_one, set_price
from engine.schema import Owner, PropertyRecord
from engine.store import RecordStore

# Poison markers: distinctive strings placed in the POPIA internal layer. None of
# them may surface in any rendered artifact (they live only where public_view
# strips them).
POISON_OWNER = "ZZOWNERPOISON_DoNotPublish"
POISON_ID = "ZZID_9999999999"
POISON_CELL = "ZZCELL_0820001111"
POISON_MARKERS = (POISON_OWNER, POISON_ID, POISON_CELL)

# Backends registered at import time. Deletion-safe: removing the Canva scaffold
# simply drops it from this list, so the parametrised PII test still passes.
BACKENDS: List[str] = sorted(list_backends().keys())


@pytest.fixture
def golden_record(golden_record_path: Path) -> PropertyRecord:
    return PropertyRecord.model_validate_json(
        golden_record_path.read_text(encoding="utf-8")
    )


def _poison(record: PropertyRecord) -> PropertyRecord:
    """Plant poison markers in the internal-only layer of ``record``."""
    if record.financials_internal is not None:
        record.financials_internal.owner = Owner(name=POISON_OWNER, id_number=POISON_ID)
    if record.sale_process is not None and record.sale_process.viewing is not None:
        record.sale_process.viewing.contact_internal_only = POISON_CELL
    return record


def _store_with(record: PropertyRecord, state: str = "extracted") -> RecordStore:
    store = RecordStore(db_path=":memory:")
    store.upsert(record, state=state)
    return store


# --- backend resolution --------------------------------------------------

def test_get_backend_defaults_to_html(monkeypatch):
    monkeypatch.delenv("ENGINE_RENDERER", raising=False)
    assert get_backend().name == "html"


def test_engine_renderer_env_selects_canva_scaffold(monkeypatch):
    if "canva" not in list_backends():
        pytest.skip("canva scaffold not registered")
    monkeypatch.setenv("ENGINE_RENDERER", "canva")
    assert get_backend().name == "canva"


def test_explicit_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("ENGINE_RENDERER", "canva")
    assert get_backend("html").name == "html"


def test_unconfigured_canva_degrades_without_crashing(monkeypatch):
    if "canva" not in list_backends():
        pytest.skip("canva scaffold not registered")
    for var in ("CANVA_CLIENT_ID", "CANVA_CLIENT_SECRET", "CANVA_REFRESH_TOKEN", "CANVA_TEMPLATE_MAP"):
        monkeypatch.delenv(var, raising=False)
    ok, reason = get_backend("canva").available()
    assert ok is False
    assert reason  # a human-readable reason, not a crash


# --- demo ad renders real facts + brand tokens ---------------------------

def test_demo_ad_renders_real_facts_and_brand_tokens(golden_record, tmp_path):
    store = _store_with(golden_record)
    try:
        artifact = render_one(
            "3060", store, "demo_ad", backend="html", output_root=str(tmp_path)
        )
        html = Path(artifact.path).read_text(encoding="utf-8")
    finally:
        store.close()

    assert artifact.mime == "text/html"
    # Real facts from the record.
    assert "185" in html  # unit size
    assert "Pelham North" in html  # suburb
    # Real brand tokens / chrome.
    assert "086 155 2288" in html  # brand phone
    assert "Montserrat" in html  # brand font stack
    assert "DYNAMIC" in html  # brand letterhead


# --- per-backend poison-marker PII test ----------------------------------

@pytest.mark.parametrize("backend_name", BACKENDS)
def test_poison_marker_pii_absent_from_every_artifact(backend_name, golden_record, tmp_path):
    be = get_backend(backend_name)
    ok, reason = be.available()
    if not ok:
        # An unconfigured backend (Canva offline) renders nothing to leak; its
        # public_view-only contract is checked in its own test module.
        pytest.skip(f"{backend_name} backend unavailable offline: {reason}")

    store = _store_with(_poison(golden_record))
    try:
        artifacts = render_all(
            "3060", store, backend=backend_name, output_root=str(tmp_path)
        )
    finally:
        store.close()

    assert artifacts
    art_dir = tmp_path / "DP3060" / "artifacts"
    rendered = list(art_dir.iterdir())
    assert rendered
    for path in rendered:
        blob = path.read_bytes()
        for marker in POISON_MARKERS:
            assert marker.encode("utf-8") not in blob, f"{marker} leaked into {path.name}"


# --- price re-render preserves human copy edits --------------------------

def test_price_rerender_preserves_human_copy_edit(golden_record, tmp_path):
    human_headline = "Rare riverside flatlet home, ready to move in"
    golden_record.marketing.headline = human_headline

    # Take the record live so set_price emits the live -> updated re-engagement move.
    store = RecordStore(db_path=":memory:")
    try:
        store.upsert(golden_record, state="assets_built")
        store.transition("3060", "live")

        change = set_price("3060", store, 2500000, backend="html", output_root=str(tmp_path))
        assert change.new == "R2 500 000"
        assert store.get_state("3060") == "updated"

        portal = (tmp_path / "DP3060" / "artifacts" / "portal_listing.md").read_text(
            encoding="utf-8"
        )
        # The human headline survives the re-render.
        assert human_headline in portal
        # The new price is reflected.
        assert "R2 500 000" in portal
    finally:
        store.close()


# --- copy request shape (offline) ----------------------------------------

def test_build_copy_request_shape_offline(golden_record):
    req = build_copy_request(golden_record)

    assert req["model"] == MODEL
    assert req["output_format"] is CopyBundle
    assert req["system"][0]["cache_control"]["type"] == "ephemeral"
    assert req["messages"][0]["role"] == "user"

    # Built from public_view only: occupant PII must not be in the payload sent.
    poisoned = _poison(golden_record)
    req2 = build_copy_request(poisoned)
    payload = json.dumps(req2["messages"]) + json.dumps(req2["system"])
    for marker in POISON_MARKERS:
        assert marker not in payload
