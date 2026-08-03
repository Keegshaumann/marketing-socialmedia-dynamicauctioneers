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
from engine.render import ad_templates, get_backend, list_backends
from engine.render.copy import (
    CopyBundle,
    build_copy_request,
    build_headline_request,
    generate_headline,
)
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

# Every pickable ad design (Classic + the library). The whole set is exercised
# for PII safety and the property ref below, so a new design added to the
# library is covered automatically.
AD_DESIGNS: List[str] = sorted(ad_templates.template_ids())


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


def test_mixed_env_resolves_get_backend_to_html(monkeypatch):
    # "mixed" is a per-format render MODE, not a single backend; get_backend()
    # must resolve it to the default rather than raise (regression: it raised
    # ValueError, which 500'd every gate page that asks for "a backend", D40).
    monkeypatch.setenv("ENGINE_RENDERER", "mixed")
    assert get_backend().name == "html"


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
    assert "lh-logo" in html  # brand letterhead now carries the real logo image


# --- the DP shows as PROPERTY REF on ads (D42), never on the board tile ---

def test_dp_shown_on_ads_but_never_on_the_board_tile(golden_record, tmp_path):
    store = _store_with(golden_record)
    try:
        tile = Path(
            render_one("3060", store, "webapp_icon", backend="html", output_root=str(tmp_path)).path
        ).read_text(encoding="utf-8")
        ad = Path(
            render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path)).path
        ).read_text(encoding="utf-8")
        golden_record.marketing.template_set = "collage"
        store2 = _store_with(golden_record)
        try:
            collage_ad = Path(
                render_one("3060", store2, "demo_ad", backend="html", output_root=str(tmp_path)).path
            ).read_text(encoding="utf-8")
        finally:
            store2.close()
    finally:
        store.close()

    # The internal board tile still leads with the suburb, never the DP (D37).
    assert "Pelham North" in tile
    assert "DP3060" not in tile
    # The ads now carry PROPERTY REF: DP<n>, matching the team's real ads (D42).
    assert "DP3060" in ad
    assert "DP3060" in collage_ad
    assert "PROPERTY REF: DP3060" in collage_ad


# --- ad-template library (D41) -------------------------------------------

def test_demo_ad_renders_the_picked_template(golden_record, tmp_path):
    golden_record.marketing.template_set = "bold"
    store = _store_with(golden_record)
    try:
        art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
        html = Path(art.path).read_text(encoding="utf-8")
    finally:
        store.close()
    assert "#0E0C0A" in html  # the Bold Dark design's dark background
    assert "Pelham North" in html  # still fills the real property facts


def test_collage_is_method_aware(golden_record, tmp_path):
    # For Sale -> Real Estate brand + "For Sale" badge; Auction -> Auctioneers
    # brand + "On Auction" badge. Same record, only the method differs (D41).
    from engine.render.html_backend import _asset_data_uri

    # Collage is on the dark canvas, so it uses the on-dark (gold-only) logo.
    auct_logo = _asset_data_uri("ads/_assets/logo-auctioneers-on-dark.png")
    re_logo = _asset_data_uri("ads/_assets/logo-realestate-on-dark.png")
    assert auct_logo and re_logo and auct_logo != re_logo  # the two brands differ

    for method, badge, want, other in [
        ("offers_invited", "FOR SALE", re_logo, auct_logo),
        ("auction", "ON AUCTION", auct_logo, re_logo),
    ]:
        golden_record.marketing.template_set = "collage"
        golden_record.sale_process.method = method
        store = _store_with(golden_record)
        try:
            art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
            html = Path(art.path).read_text(encoding="utf-8")
        finally:
            store.close()
        assert badge in html
        assert want in html  # the correct brand logo is embedded...
        assert other not in html  # ...and only that one


def test_logo_matches_its_background_in_every_design(golden_record, tmp_path):
    # Guard against the "you can't see half the logo" bug: every placement must
    # use the logo built for the surface it sits on. on-light = the full
    # black+gold "DS DYNAMIC" lockup (legible on white); on-dark = the gold-only
    # mark (legible on a dark canvas; the black lockup would vanish there).
    # Renders each design and asserts the right variant is embedded and the wrong
    # one is not, so a future edit that swaps them fails here.
    from engine.render.html_backend import _asset_data_uri

    # The golden record is offers_invited -> Real Estate brand.
    on_light = _asset_data_uri("ads/_assets/logo-realestate-on-light.png")
    on_dark = _asset_data_uri("ads/_assets/logo-realestate-on-dark.png")
    assert on_light and on_dark and on_light != on_dark

    # design -> the surface colour its logo sits on
    surface = {
        "classic": "light",       # the white letterhead sheet
        "collage": "dark",        # the dark canvas, logo direct on it
        "feature_list": "light",  # logo sits in a white box
        "stats_first": "light",   # logo sits in a white box
        "hero_overlay": "light",  # logo sits in a white box
    }
    for design, bg in surface.items():
        golden_record.marketing.template_set = design
        store = _store_with(golden_record)
        try:
            art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
            html = Path(art.path).read_text(encoding="utf-8")
        finally:
            store.close()
        want, other = (on_light, on_dark) if bg == "light" else (on_dark, on_light)
        assert want in html, f"{design}: expected the on-{bg} logo for its background"
        assert other not in html, f"{design}: a logo for the wrong background leaked in"


def test_every_ad_design_exports_at_instagram_4x5(golden_record, tmp_path):
    # Hard guarantee for the client's requirement: every ad design rasterises to
    # the Instagram post canvas, exactly 1080x1350 (4:5), captured at 2x device
    # scale -> 2160x2700. A design that drifts off-ratio would be cropped when
    # posted, so this fails the moment any canvas changes size.
    import pytest

    from engine.render import ad_templates, rasterize

    if not rasterize.available():
        pytest.skip("Playwright not installed; rasteriser unavailable")
    from PIL import Image

    for design in ad_templates.template_ids():
        golden_record.marketing.template_set = "" if design == "classic" else design
        store = _store_with(golden_record)
        try:
            art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
        finally:
            store.close()
        png = tmp_path / f"{design}.png"
        rasterize.html_to_png(str(art.path), str(png))
        assert Image.open(png).size == (2160, 2700), f"{design} is not 1080x1350 (4:5)"


def test_collage_renders_auction_specifics(golden_record, tmp_path):
    # An auction record renders the type badge, the channel/date/time line
    # (instead of a price), and the terms strip (D42).
    golden_record.marketing.template_set = "collage"
    golden_record.sale_process.method = "auction"
    golden_record.sale_process.auction_type = "Insolvency"
    golden_record.sale_process.auction_channel = "Online"
    golden_record.sale_process.auction_date = "28 May 2026"
    golden_record.sale_process.auction_time = "10:00"
    golden_record.sale_process.terms = ["Vacant occupation cannot be guaranteed"]
    store = _store_with(golden_record)
    try:
        art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
        html = Path(art.path).read_text(encoding="utf-8")
    finally:
        store.close()
    assert "INSOLVENCY AUCTION!" in html  # type-aware badge (Jinja upper)
    assert "ONLINE AUCTION | 28 MAY 2026 @ 10:00" in html  # the auction line
    assert "Vacant occupation cannot be guaranteed" in html  # terms strip (CSS uppercases)
    assert 'class="ig-price"' not in html  # auction shows the auction line, not a price bar


@pytest.mark.parametrize("template", ["feature_list", "stats_first"])
def test_new_ad_designs_render_place_and_descriptor(template, golden_record, tmp_path):
    # AD 2 / AD 3 lead with the locality + a concise descriptor and carry the
    # PROPERTY REF (D43). They fill from the same record as the Collage.
    golden_record.marketing.template_set = template
    store = _store_with(golden_record)
    try:
        art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
        html = Path(art.path).read_text(encoding="utf-8")
    finally:
        store.close()
    assert "Pelham North" in html  # the place line
    # "3 Bedroom Apartment" isolates the descriptor bar; the feature bullets
    # render "3 Bedrooms" (plural), so this substring proves descriptor_line ran.
    assert "3 Bedroom Apartment" in html
    assert "PROPERTY REF: DP3060" in html


def test_badge_says_sale_with_type_on_a_non_auction(golden_record, tmp_path):
    # The callout type applies to a sale too: "INSOLVENCY SALE!" (real ads),
    # not "... AUCTION!" (D43).
    golden_record.marketing.template_set = "feature_list"
    golden_record.sale_process.method = "offers_invited"
    golden_record.sale_process.auction_type = "Insolvency"
    store = _store_with(golden_record)
    try:
        art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
        html = Path(art.path).read_text(encoding="utf-8")
    finally:
        store.close()
    assert "INSOLVENCY SALE!" in html  # SALE, not AUCTION, on an offers property
    assert "INSOLVENCY AUCTION" not in html


def test_partials_are_not_offered_as_pickable_designs():
    from engine.render import ad_templates

    ids = ad_templates.template_ids()
    assert "_adparts" not in ids  # the shared macro file is not a design
    assert {"collage", "feature_list", "stats_first"} <= ids


@pytest.mark.parametrize("template", AD_DESIGNS)
@pytest.mark.parametrize("method", ["offers_invited", "auction"])
def test_every_ad_design_is_pii_safe_and_carries_the_ref(template, method, golden_record, tmp_path):
    # POPIA: no ad design, in either sale method, may leak an internal-layer
    # field. And every ad carries the PROPERTY REF (the DP, D42). This covers
    # the whole library, so a newly added design is checked automatically.
    rec = _poison(golden_record)
    rec.marketing.template_set = template
    rec.sale_process.method = method
    rec.sale_process.auction_type = "Insolvency"
    rec.sale_process.auction_channel = "Online"
    rec.sale_process.auction_date = "28 May 2026"
    rec.sale_process.auction_time = "10:00"
    store = _store_with(rec)
    try:
        art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
        html = Path(art.path).read_text(encoding="utf-8")
    finally:
        store.close()
    for marker in POISON_MARKERS:
        assert marker not in html, f"{marker} leaked into {template}/{method}"
    assert "DP3060" in html  # the property ref is on every ad (D42)


def test_ad_designs_render_with_missing_data(golden_record, tmp_path):
    # A sparse record (no photos, beds, baths, price or terms) must still render
    # every design without error, showing only what the record supports.
    golden_record.marketing.hero_photo = None
    golden_record.marketing.gallery = None
    golden_record.marketing.price_display = None
    golden_record.physical.bedrooms = None
    golden_record.physical.bathrooms_main_unit = None
    golden_record.sale_process.terms = None
    for template in ("collage", "feature_list", "stats_first", "hero_overlay", "bold"):
        golden_record.marketing.template_set = template
        store = _store_with(golden_record)
        try:
            art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
            html = Path(art.path).read_text(encoding="utf-8")
        finally:
            store.close()
        assert "Pelham North" in html  # still fills the locality it does have
        assert "DP3060" in html


def test_auction_line_reads_on_site(golden_record, tmp_path):
    golden_record.marketing.template_set = "stats_first"
    golden_record.sale_process.method = "auction"
    golden_record.sale_process.auction_channel = "On-site"
    golden_record.sale_process.auction_date = "3 June 2026"
    golden_record.sale_process.auction_time = "11:30"
    store = _store_with(golden_record)
    try:
        art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
        html = Path(art.path).read_text(encoding="utf-8")
    finally:
        store.close()
    assert "ON-SITE AUCTION | 3 JUNE 2026 @ 11:30" in html


def test_badge_defaults_without_a_callout_type(golden_record, tmp_path):
    # No callout type -> the plain badge, method-aware.
    golden_record.marketing.template_set = "collage"
    golden_record.sale_process.auction_type = None
    for method, expect in [("auction", "ON AUCTION!"), ("offers_invited", "FOR SALE!")]:
        golden_record.sale_process.method = method
        store = _store_with(golden_record)
        try:
            art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
            html = Path(art.path).read_text(encoding="utf-8")
        finally:
            store.close()
        assert expect in html


def test_bold_never_fabricates_a_flatlet_bed_count(golden_record, tmp_path):
    # A flatlet present with an unknown bedroom count must NOT render "1 Bed
    # flatlet" (hard rule 3: no invented facts, D44 review).
    from engine.schema import Flatlet

    if golden_record.physical.flatlet is None:
        golden_record.physical.flatlet = Flatlet()
    golden_record.physical.flatlet.present = True
    golden_record.physical.flatlet.bedrooms = None
    golden_record.marketing.template_set = "bold"
    store = _store_with(golden_record)
    try:
        html = Path(
            render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path)).path
        ).read_text(encoding="utf-8")
    finally:
        store.close()
    assert "Bed flatlet" not in html  # the stat is dropped, not defaulted to 1

    # With a real count it shows honestly.
    golden_record.physical.flatlet.bedrooms = 2
    store = _store_with(golden_record)
    try:
        html = Path(
            render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path)).path
        ).read_text(encoding="utf-8")
    finally:
        store.close()
    assert "Bed flatlet" in html
    assert ">2</div><div class=\"l\">Bed flatlet" in html


def test_collage_stat_bar_shows_the_garage_count(golden_record, tmp_path):
    # Regression: the garage cell used to render "Garage" with no number (D44).
    golden_record.marketing.template_set = "collage"
    golden_record.physical.garages = 2
    store = _store_with(golden_record)
    try:
        html = Path(
            render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path)).path
        ).read_text(encoding="utf-8")
    finally:
        store.close()
    assert '2 <span class="u">Garages</span>' in html  # count + pluralised label


def test_classic_shows_the_money_line(golden_record, tmp_path):
    # Classic (the default one-pager) must show the auction line on an auction
    # and the price on a sale, not just the badge (D44 review).
    golden_record.marketing.template_set = "classic"
    golden_record.sale_process.method = "auction"
    golden_record.sale_process.auction_channel = "Online"
    golden_record.sale_process.auction_date = "28 May 2026"
    golden_record.sale_process.auction_time = "10:00"
    store = _store_with(golden_record)
    try:
        html = Path(
            render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path)).path
        ).read_text(encoding="utf-8")
    finally:
        store.close()
    assert "ONLINE AUCTION | 28 MAY 2026 @ 10:00" in html

    golden_record.sale_process.method = "offers_invited"
    golden_record.marketing.price_display = "R1 250 000"
    store = _store_with(golden_record)
    try:
        html = Path(
            render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path)).path
        ).read_text(encoding="utf-8")
    finally:
        store.close()
    assert "R1 250 000" in html


def test_split3_balances_a_headline():
    from engine.render.html_backend import _split3

    assert _split3("3 Bedroom Home with Separate Flatlet in Pelham North") == [
        "3 Bedroom Home",
        "with Separate Flatlet",
        "in Pelham North",
    ]
    assert _split3("Loft") == ["Loft"]  # short headlines stay one line


# --- ad-first render split (D39) -----------------------------------------

def test_render_all_subset_renders_only_requested(golden_record, tmp_path):
    store = _store_with(golden_record)
    try:
        ad = render_all("3060", store, output_root=str(tmp_path), formats=["demo_ad"])
        assert sorted(a.fmt for a in ad) == ["demo_ad"]
    finally:
        store.close()


def test_render_all_rejects_unknown_format(golden_record, tmp_path):
    store = _store_with(golden_record)
    try:
        with pytest.raises(ValueError):
            render_all("3060", store, output_root=str(tmp_path), formats=["bogus"])
    finally:
        store.close()


# --- AI headline generation (gate-2 auto-generate) -----------------------

def test_generate_headline_offline_returns_deterministic(golden_record):
    # No API key in the hermetic test env -> the deterministic fallback.
    headline = generate_headline(golden_record)
    assert headline
    assert "Pelham North" in headline  # built from the record's own facts


def test_build_headline_request_shape_and_no_pii(golden_record):
    rec = _poison(golden_record)
    req = build_headline_request(rec)
    assert req["model"] == MODEL
    assert req["output_format"].__name__ == "HeadlineSuggestion"
    # public_view only -> no owner PII in the prompt.
    assert POISON_OWNER not in json.dumps(req["messages"], default=str)


# --- per-backend poison-marker PII test ----------------------------------

@pytest.mark.parametrize("backend_name", BACKENDS)
def test_poison_marker_pii_absent_from_every_artifact(backend_name, golden_record, tmp_path):
    be = get_backend(backend_name)
    if not be.renders_locally:
        # Remote-rendering backends (Canva) produce artifact bytes off-machine, so
        # there is nothing local to scan here; their PII contract is enforced on
        # the outbound payload and checked offline in tests/test_canva.py.
        pytest.skip(f"{backend_name} renders remotely; payload PII checked in test_canva.py")
    ok, reason = be.available()
    if not ok:
        # An unconfigured backend renders nothing to leak; its public_view-only
        # contract is checked in its own test module.
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
