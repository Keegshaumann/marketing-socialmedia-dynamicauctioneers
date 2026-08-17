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
import re
import shutil
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
    assert "ho-hero" in html  # the default Hero-overlay design's branded chrome (D49)
    # The tight ad contact bar carries only the primary enquiries email; the
    # second (admin) mailbox is shown on the roomy documents, not the ad.
    assert "properties@dynamicauctioneers.co.za" in html
    assert "properties.admin@dynamicauctioneers.co.za" not in html


# --- multi-portion extent is summed in code (multi-file intake) ----------

def test_view_model_sums_portion_extents():
    """A multi-portion property's size is the SUM of its portions (code, D-multi)."""
    from engine.render.base import RenderRequest
    from engine.render.html_backend import HtmlBackend, _fmt_num

    pub = {
        "physical": {
            "unit_size_m2": 185.0,  # ignored once portions are present
            "portions": [
                {"label": "Portion 6", "size_m2": 21000.0},
                {"label": "Portion 7", "size_m2": 18500.0},
            ],
        }
    }
    vm = HtmlBackend()._view_model(
        RenderRequest(dp="2918.1", fmt="demo_ad", public_record=pub)
    )
    assert vm["size_str"] == _fmt_num(39500.0)
    assert [p["label"] for p in vm["portions"]] == ["Portion 6", "Portion 7"]


def test_view_model_size_falls_back_to_unit_size_without_portions():
    from engine.render.base import RenderRequest
    from engine.render.html_backend import HtmlBackend, _fmt_num

    pub = {"physical": {"unit_size_m2": 185.0}}
    vm = HtmlBackend()._view_model(
        RenderRequest(dp="3060", fmt="demo_ad", public_record=pub)
    )
    assert vm["size_str"] == _fmt_num(185.0)
    assert vm["portions"] == []


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
    # A pick other than the default is honoured: Stats-first renders its own
    # chrome, not the default Hero-overlay design.
    golden_record.marketing.template_set = "stats_first"
    store = _store_with(golden_record)
    try:
        art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
        html = Path(art.path).read_text(encoding="utf-8")
    finally:
        store.close()
    assert "sf-hero" in html  # the Stats-first design's hero banner
    assert "ho-hero" not in html  # and not the default Hero-overlay design
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
        "collage": "dark",        # the dark canvas, logo direct on it
        "feature_list": "light",  # logo sits in a white shield
        "stats_first": "light",   # logo sits in a white shield
        "hero_overlay": "light",  # logo sits in a white shield
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


# --- info pack: playbook framing branches on auction vs sale ---------------

_LEGAL_DISCLAIMER = (
    "Whilst all reasonable care has been taken to provide accurate information, "
    "neither Dynamic Solutions 1068 (Pty) LTD Trading As Dynamic Auctioneers"
)


def _info_pack_html(record, tmp_path):
    store = _store_with(record)
    try:
        art = render_one(record.dp, store, "info_pack", backend="html", output_root=str(tmp_path))
        return Path(art.path).read_text(encoding="utf-8")
    finally:
        store.close()


def test_info_pack_sale_uses_offer_language_not_auction(golden_record, tmp_path):
    """A normal listing (offers invited) never uses auction / bid wording.

    The cover badge is the tell: the reference packs put the method there in two
    words, first word gold (playbook 5.1), and a listing that is not an auction
    must read FOR SALE.
    """
    golden_record.sale_process.method = "offers_invited"
    html = _info_pack_html(golden_record, tmp_path)

    assert "<b>For</b> Sale" in html
    assert "Offers invited" in html
    assert "How It Is Offered" in html                 # the highlights row
    assert "Enquiries and offers welcome." in html     # the closing tagline
    # No auction framing anywhere. ("Auctioneers" is in the brand name on every
    # page, so these are phrases, not the bare word.)
    assert "Auction information pack" not in html
    assert "Don't miss this auction" not in html
    assert "Register to bid" not in html
    assert "fall of the hammer" not in html
    # Disclaimer verbatim, and the enquiries mailbox on the closing page.
    assert _LEGAL_DISCLAIMER in html
    assert "properties@dynamicauctioneers.co.za" in html


def test_info_pack_auction_uses_auction_language_and_details(golden_record, tmp_path):
    """An auction record frames the pack as an auction with its specifics."""
    golden_record.sale_process.method = "auction"
    golden_record.sale_process.auction_type = "Insolvency"
    golden_record.sale_process.auction_channel = "Online"
    golden_record.sale_process.auction_date = "28 May 2026"
    golden_record.sale_process.auction_time = "10:00"
    html = _info_pack_html(golden_record, tmp_path)

    assert "<b>Online</b> Auction" in html             # the cover badge
    assert "Offered on Auction" in html                # the highlights row
    assert "Insolvency auction" in html
    assert "28 May 2026" in html and "10:00" in html
    assert "Don't miss this auction!" in html
    # No sale-only framing on an auction pack.
    assert "<b>For</b> Sale" not in html
    assert "Enquiries and offers welcome." not in html
    assert _LEGAL_DISCLAIMER in html


def test_info_pack_prints_the_conditions_once_in_the_terms_box(golden_record, tmp_path):
    """The conditions live in the bordered box on the details page (playbook 5.2).

    The reference packs carry deposit, commission, guarantee and occupation in
    one box under the fees pill, not on a page of their own. Each condition is
    printed once: a term repeated on a second page is a term a buyer can read
    two different ways.
    """
    golden_record.sale_process.terms = [
        f"Condition {i}: the purchaser accepts the property voetstoots and "
        f"acknowledges that all measurements are approximate."
        for i in range(1, 13)
    ]
    html = _info_pack_html(golden_record, tmp_path)

    box = html.split('<div class="terms">')[1].split("</div>\n          </div>")[0]
    for i in range(1, 13):
        assert html.count(f"Condition {i}:") == 1
        assert f"Condition {i}:" in box
    # The standing statement above the box, verbatim.
    assert "All Outstanding Fees, if any, to be Settled by the Seller." in html
    # With no OTP on the record the confirmation pill is OMITTED rather than
    # printed with a guessed period (D68). It used to read a hardcoded "30 days",
    # which the sample OTP shows to be wrong: that sale confirms in 7.
    assert "Confirmation By Seller" not in html


def test_info_pack_terms_come_from_the_otp_when_there_is_one(golden_record, tmp_path):
    """Deposit, commission, guarantee and confirmation follow the OTP's clauses.

    The values were literal strings in the template - one property's terms baked
    in - so a sale on a 20% deposit and a 7 day confirmation printed "10%" and
    "30 days" on its buyer pack.
    """
    from engine.schema import OtpTerms

    golden_record.sale_process.otp = OtpTerms(
        deposit_pct=20.0,
        deposit_due="on signature date",
        guarantee_days=60,
        commission_pct=6.0,
        commission_vat=True,
        commission_payable_by="seller",
        confirmation_days=7,
        outstanding_payable_by="purchaser",
    )
    html = _info_pack_html(golden_record, tmp_path)

    assert "20% deposit payable on signature date" in html
    assert "6% commission and VAT on the commission payable by the Seller" in html
    assert "Guarantee for balance within 60 days after confirmation" in html
    assert "Subject To 7 days Confirmation By Seller" in html
    assert "All Outstanding Fees, if any, to be Settled by the Purchaser." in html
    # None of the old hardcoded values survive anywhere on the pack.
    for stale in ("10% deposit", "45 days", "30 days Confirmation"):
        assert stale not in html


def _with_portions(record, n: int):
    """Give a record ``n`` land portions (the multi-portion intake case)."""
    from engine.schema import Portion

    record.physical.portions = [
        Portion(
            label=f"Portion {i} of Farm 7 Slagboom",
            erf=str(2000 + i),
            size_m2=53434.0 + i * 137,
            title_deed_no=f"T{10000 + i}/2019",
        )
        for i in range(1, n + 1)
    ]
    return record


def test_info_pack_schedule_pages_split_evenly(golden_record, tmp_path):
    """A long schedule is split into even pages, never a page holding two rows.

    23 portions at twelve to a sheet is 12 and 11, not 12, 11 and a sheet
    carrying the last row on its own.
    """
    html = _info_pack_html(_with_portions(golden_record, 23), tmp_path)

    chunks = html.split('<table class="hl sched"')[1:]
    assert len(chunks) == 2                       # two schedule pages
    rows = [c.count("of Farm 7 Slagboom") for c in chunks]
    assert sum(rows) == 23
    assert min(rows) >= 11                        # no ragged tail
    assert "Schedule of portions continued" in html


def test_info_pack_short_schedule_is_one_page_and_takes_a_band(golden_record, tmp_path):
    """Three portions do not fill a sheet, so the sheet takes a photograph band.

    The schedule cannot share the details page in this format (that page is
    composed to its own layout), so the rule is: one schedule page, rows opened
    up, and what is still empty filled with a photograph rather than left blank.
    """
    html = _info_pack_html(_with_portions(golden_record, 3), tmp_path)

    assert html.count('<table class="hl sched"') == 1
    assert "Schedule of portions continued" not in html
    sched_page = html.split("Schedule of portions")[1]
    assert 'class="band"' in sched_page


def test_info_pack_groups_large_extents_and_adds_hectares(golden_record, tmp_path):
    """A farm sized extent is grouped and given a hectare equivalent.

    An ungrouped seven digit run cannot be read at a glance in a buyer document.
    """
    html = _info_pack_html(_with_portions(golden_record, 23), tmp_path)

    assert "1 266 794 m2" in html
    assert "1266794" not in html
    assert "126.7 ha" in html
    # Per portion extents are grouped too.
    assert "53 571 m2" in html


def test_info_pack_names_one_brand_on_a_sale_pack(golden_record, tmp_path):
    """The pack is issued as Dynamic Auctioneers whatever the sale method.

    The cover and contact lockup used to switch to the Dynamic Real Estate mark
    on a non-auction pack while the running head and contact block still read
    "Dynamic Auctioneers": two brands on one document.
    """
    from engine.render.html_backend import _asset_data_uri

    golden_record.sale_process.method = "offers_invited"
    html = _info_pack_html(golden_record, tmp_path)

    auctioneers = _asset_data_uri("ads/_assets/logo-auctioneers-on-light.png")
    realestate = _asset_data_uri("ads/_assets/logo-realestate-on-light.png")
    assert auctioneers and auctioneers in html
    assert realestate not in html


def test_info_pack_band_photographs_come_from_the_record(golden_record, tmp_path):
    """A band that fills a short page uses this property's photographs only."""
    html = _info_pack_html(_with_portions(golden_record, 3), tmp_path)

    photos = [golden_record.marketing.hero_photo] + list(golden_record.marketing.gallery)
    names = {Path(p).name for p in photos if p}
    bands = re.findall(r'<div class="band".*?</div>', html, re.S)
    assert bands
    for block in bands:
        for src in re.findall(r'src="([^"]+)"', block):
            assert Path(src).name in names


def test_info_pack_without_photographs_has_no_band_or_gallery(golden_record, tmp_path):
    """No photographs on the record means no band and no gallery page.

    An empty image box, or a gallery page with nothing on it, is worse than the
    page simply not being there.
    """
    golden_record.marketing.hero_photo = None
    golden_record.marketing.gallery = []

    html = _info_pack_html(golden_record, tmp_path)

    assert 'class="band"' not in html
    assert 'class="grid3"' not in html
    assert "<img" in html                      # the logo lockups still render
    assert 'alt="Property photograph"' not in html


@pytest.mark.parametrize("design", AD_DESIGNS)
def test_no_ad_text_is_cut_off(design, golden_record, tmp_path):
    """Measured in Chromium: no run of text on an ad falls outside the canvas.

    The canvas is a locked 1080x1350 with `overflow: hidden`, and the stat rows,
    the tagline and the terms strip were `white-space: nowrap` with an ellipsis -
    so a long real value was forced onto one line and then silently truncated,
    or pushed past the edge and clipped away. Long values now wrap. This asserts
    the outcome rather than the CSS, on every design in the library, because the
    fault depends on the rendered width of real record text.
    """
    from engine.render import rasterize

    if not rasterize.available():
        pytest.skip("Playwright not installed; ad geometry cannot be measured")
    from playwright.sync_api import sync_playwright

    # Values long enough to have overflowed before: a scheme name, a wordy
    # descriptor and a terms strip are what the team's real records carry.
    golden_record.marketing.template_set = design
    golden_record.identity.scheme = "SS PAULA- EN KARIENHOF NUMBER 334 OF 1993"
    golden_record.marketing.headline = (
        "Spacious three bedroom sectional title apartment with a separate flatlet"
    )
    golden_record.sale_process.terms = [
        "10% deposit on the purchase price payable by the purchaser on submitting an offer",
        "Electrical COC, SPLUMA and all certificates for successful registration",
    ]
    store = _store_with(golden_record)
    try:
        art = render_one("3060", store, "demo_ad", backend="html", output_root=str(tmp_path))
    finally:
        store.close()

    # Measured against every CLIPPING ancestor, not just the canvas. An inner box
    # with `overflow: hidden` cuts text off while the words still sit well inside
    # the 1080x1350 frame, so a canvas-only check passes even when half a stat row
    # has been eaten (verified: it does not fire on deliberately oversized copy).
    overflow = """() => {
      const canvas = document.querySelector('.ig') || document.body;
      const clippers = el => {
        const out = [];
        for (let p = el; p && p !== document.documentElement; p = p.parentElement) {
          const o = getComputedStyle(p);
          if (o.overflowX !== 'visible' || o.overflowY !== 'visible') out.push(p);
        }
        if (!out.includes(canvas)) out.push(canvas);
        return out;
      };
      const out = [];
      const walker = document.createTreeWalker(canvas, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const text = n.textContent.trim();
        if (!text) continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        const boxes = clippers(n.parentElement);
        let hit = false;
        for (const r of range.getClientRects()) {
          if (!r.width || !r.height) continue;
          for (const box of boxes) {
            const c = box.getBoundingClientRect();
            if (r.right > c.right + 2 || r.left < c.left - 2 ||
                r.bottom > c.bottom + 2 || r.top < c.top - 2) { hit = true; break; }
          }
          if (hit) break;
        }
        if (hit) out.push(text.slice(0, 40));
      }
      return out;
    }"""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1080, "height": 1350})
            page.goto(Path(art.path).as_uri())
            spills = page.evaluate(overflow)
        finally:
            browser.close()

    assert spills == [], f"{design}: text outside the canvas: {spills}"


def test_every_visual_artifact_is_a_pdf_at_its_own_size(golden_record, tmp_path, monkeypatch):
    """The pack ships PDFs, each printed at its own canvas, text still text.

    The team downloads the pack to send to a client and to import into Canva.
    It used to hand them .html files: only the information pack was printed, and
    printing an advert as A4 would letterbox a 1080x1350 canvas onto a portrait
    sheet. Each visual format is now printed at the size of its own root element,
    and its text stays selectable text rather than a picture of an advert.
    """
    from engine.render import rasterize

    if not rasterize.available():
        pytest.skip("Playwright not installed; PDF export unavailable")
    monkeypatch.setenv("ENGINE_PDF_EXPORT", "1")
    import fitz

    store = _store_with(golden_record)
    try:
        artifacts = {a.fmt: a for a in render_all("3060", store, backend="html", output_root=str(tmp_path))}
    finally:
        store.close()

    for fmt in ("demo_ad", "info_pack", "saia_banner", "alert_mailer", "auction_board"):
        art = artifacts[fmt]
        path = Path(art.path)
        assert path.suffix == ".pdf", f"{fmt} is not a PDF ({path.name})"
        assert art.mime == "application/pdf"
        assert path.read_bytes()[:5] == b"%PDF-"
        doc = fitz.open(path)
        try:
            # Text is text (a rasterised page yields none), and the ad canvases
            # print as ONE page rather than spilling onto a second, near-empty one.
            assert doc[0].get_text().strip(), f"{fmt} has no extractable text"
            if fmt != "info_pack":
                assert doc.page_count == 1, f"{fmt} printed {doc.page_count} pages"
            if fmt == "demo_ad":                       # 1080x1350 px at 0.75 pt/px
                assert abs(doc[0].rect.width / doc[0].rect.height - 0.8) < 0.02
        finally:
            doc.close()

    # The copy formats stay text: they are pasted into portals and posts.
    for fmt in ("portal_listing", "facebook_post", "email_blast"):
        assert Path(artifacts[fmt].path).suffix == ".md"


def test_a_price_reaches_every_artifact(golden_record, tmp_path):
    """One price on the record, the same price on all nine artifacts.

    This is the engine's whole reason for existing: the price changes once and
    every piece of marketing follows. It did not hold. The Facebook post, the
    email subject and the website tile printed the sale-METHOD badge instead of
    the figure, so a property whose advert read "R1 875 000" was posted to
    Facebook as "OFFERS INVITED" - two different money lines for one property,
    live on two channels at the same time.
    """
    golden_record.marketing.price_display = "R1 875 000"
    store = _store_with(golden_record)
    try:
        artifacts = render_all("3060", store, backend="html", output_root=str(tmp_path))
    finally:
        store.close()

    missing = []
    for art in artifacts:
        path = Path(art.path)
        if path.suffix == ".pdf":                       # compressed: read its text
            import fitz

            doc = fitz.open(path)
            try:
                found = any("R1 875 000" in page.get_text() for page in doc)
            finally:
                doc.close()
        else:
            blob = path.read_bytes()
            found = any(
                marker in blob
                for marker in (b"R1 875 000", b"R1&#160;875&#160;000", b"R1\xc2\xa0875\xc2\xa0000")
            )
        if not found:
            missing.append(art.fmt)

    assert missing == [], f"the price never reached: {missing}"


def test_info_pack_every_word_is_visible(golden_record, golden_record_path, tmp_path):
    """Nothing is hidden behind the badge, and nothing is cut off by the sheet.

    Two ways a word can be on the page and unreadable. The shield badge is
    absolutely positioned over the top-right corner, so it cannot push anything
    aside: on a property viewed "by arrangement" the viewing line runs wide
    enough to reach it and printed underneath the black. And the pages are a
    fixed height with `overflow: hidden`, so anything that outgrows its sheet is
    silently clipped rather than spilling somewhere visible.

    Measured in Chromium rather than reasoned about: both faults depend on the
    text's rendered width, which no amount of arithmetic in the template can
    know. Measured on the TEXT, not on its block - a full-width heading's box
    legitimately reaches the badge while its words stop well short (that is what
    the title's right padding is for), so a Range's client rects are the ink.
    """
    from engine.render import rasterize

    if not rasterize.available():
        pytest.skip("Playwright not installed; page geometry cannot be measured")
    from playwright.sync_api import sync_playwright

    photos = golden_record_path.parent / "photos"
    if not photos.is_dir():
        pytest.skip("golden photographs not present")
    shutil.copytree(photos, tmp_path / "DP3060" / "photos")
    # The long-viewing-line case, plus a scheme and municipality that run wide.
    golden_record.sale_process.viewing.by_appointment = False
    golden_record.identity.municipality = "City of Tshwane Metropolitan Municipality"

    store = _store_with(golden_record)
    try:
        art = render_one("3060", store, "info_pack", backend="html", output_root=str(tmp_path))
    finally:
        store.close()

    overlaps = """() => {
      const out = [];
      document.querySelectorAll('.page').forEach((page, i) => {
        // The closing page carries the badge too, and its body is .close-body.
        const body = page.querySelector('.page__body') || page.querySelector('.close-body');
        if (!body) return;
        const badge = page.querySelector('.shield');
        const b = badge ? badge.getBoundingClientRect() : null;
        const p = page.getBoundingClientRect();
        const hits = (r, r2) =>
          Math.min(r.right, r2.right) - Math.max(r.left, r2.left) > 1 &&
          Math.min(r.bottom, r2.bottom) - Math.max(r.top, r2.top) > 1;
        const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
          const text = n.textContent.trim();
          if (!text) continue;
          const range = document.createRange();
          range.selectNodeContents(n);
          for (const r of range.getClientRects()) {
            if (b && hits(r, b)) { out.push(`under the badge, page ${i + 1}: ${text.slice(0, 40)}`); break; }
            // 2px of tolerance for sub-pixel rounding on the sheet edge.
            if (r.top < p.top - 2 || r.bottom > p.bottom + 2 ||
                r.left < p.left - 2 || r.right > p.right + 2) {
              out.push(`clipped by the sheet, page ${i + 1}: ${text.slice(0, 40)}`); break;
            }
          }
        }
      });
      return out;
    }"""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        try:
            page = browser.new_page()
            page.goto(Path(art.path).with_suffix(".html").as_uri())
            page.emulate_media(media="print")
            hits = page.evaluate(overlaps)
        finally:
            browser.close()

    assert hits == [], "hidden or clipped text: " + "; ".join(hits)


def test_info_pack_pages_are_full_a4_landscape_sheets(golden_record, golden_record_path, tmp_path):
    """Measured in Chromium: every sheet is a full A4 landscape, none clipped.

    The costing in the template is arithmetic on estimated block heights, so the
    only honest check is the printed page itself. Three things must hold on every
    sheet: it is exactly 210mm tall, nothing overflows it (the pages are fixed
    height, so an overrun is CLIPPED rather than spilled, which is worse), and no
    gap between blocks exceeds 30mm. The closing page is exempt from the gap
    rule: its contact block is pinned to the foot by design.
    """
    from engine.render import rasterize

    if not rasterize.available():
        pytest.skip("Playwright not installed; page geometry cannot be measured")
    from playwright.sync_api import sync_playwright

    photos = golden_record_path.parent / "photos"
    if not photos.is_dir():
        pytest.skip("golden photographs not present")
    shutil.copytree(photos, tmp_path / "DP3060" / "photos")
    store = _store_with(golden_record)
    try:
        art = render_one("3060", store, "info_pack", backend="html", output_root=str(tmp_path))
    finally:
        store.close()

    measure = """() => Array.from(document.querySelectorAll('.page')).map(el => {
      const mm = v => v / 3.7795;
      const body = el.querySelector('.page__body');
      const r = Array.from(body ? body.children : []).map(k => k.getBoundingClientRect());
      const gaps = [];
      for (let j = 1; j < r.length; j++) gaps.push(r[j].top - r[j-1].bottom);
      if (r.length) gaps.push(body.getBoundingClientRect().bottom - r[r.length-1].bottom);
      return {h: mm(el.getBoundingClientRect().height),
              clip: mm(Math.max(0, el.scrollHeight - el.clientHeight)),
              gap: mm(Math.max(0, ...gaps))};
    })"""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        try:
            page = browser.new_page()
            page.goto(Path(art.path).with_suffix(".html").as_uri())
            page.emulate_media(media="print")
            pages = page.evaluate(measure)
        finally:
            browser.close()

    assert len(pages) >= 5
    for i, p in enumerate(pages, start=1):
        assert 209 < p["h"] < 211, f"page {i} is not a landscape A4 ({p['h']:.0f}mm tall)"
        assert p["clip"] <= 2, f"page {i} clips {p['clip']:.0f}mm of content"
        assert p["gap"] <= 30, f"page {i} holds a {p['gap']:.0f}mm hole"


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


def test_badge_never_doubles_the_sale_or_auction_word():
    """The callout box takes a REASON, but a marketer may type "for sale" in it.

    The badge appends SALE!/AUCTION! itself, so a callout that already ends in
    that word must be used as written - reported live as "FOR SALE SALE!".
    """
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader("engine/render/templates"))
    badge = env.get_template("_adparts.html.j2").module.badge_text

    def text(method, callout):
        return str(badge({"method": method, "auction_type": callout})).strip()

    # The reported bug, in both methods and any casing.
    assert text("offers_invited", "for sale") == "FOR SALE!"
    assert text("offers_invited", "For Sale") == "FOR SALE!"
    assert text("offers_invited", "Insolvency Sale!") == "INSOLVENCY SALE!"
    assert text("auction", "auction") == "AUCTION!"
    assert text("auction", "Insolvency Auction") == "INSOLVENCY AUCTION!"
    # A real reason still gets its suffix.
    assert text("offers_invited", "Insolvency") == "INSOLVENCY SALE!"
    assert text("auction", "Liquidation") == "LIQUIDATION AUCTION!"
    # Blank keeps the plain badge.
    assert text("offers_invited", None) == "FOR SALE!"
    assert text("auction", "") == "ON AUCTION!"
    # Whole-word comparison: "Wholesale" is not the word "sale".
    assert text("offers_invited", "Wholesale") == "WHOLESALE SALE!"


def test_info_pack_exports_a_real_pdf(golden_record, tmp_path, monkeypatch):
    """The buyer receives a PDF, not a web page.

    The suite disables the export for speed (conftest), so this test turns it
    back on to cover the real Chromium print path.
    """
    from engine.render import rasterize

    if not rasterize.available():
        pytest.skip("Playwright not installed; PDF export unavailable")
    monkeypatch.setenv("ENGINE_PDF_EXPORT", "1")

    store = _store_with(golden_record)
    try:
        art = render_one("3060", store, "info_pack", backend="html", output_root=str(tmp_path))
    finally:
        store.close()

    path = Path(art.path)
    assert path.suffix == ".pdf"
    assert art.mime == "application/pdf"
    assert path.read_bytes()[:5] == b"%PDF-"      # a real PDF, not renamed HTML
    # A4 LANDSCAPE (playbook 2), and the pack runs to more than one page.
    import fitz

    doc = fitz.open(path)
    try:
        assert doc.page_count >= 2
        assert 820 < doc[0].rect.width < 860      # A4 long edge in points
        assert 580 < doc[0].rect.height < 610
    finally:
        doc.close()
    # The HTML print source is kept beside it.
    assert (path.parent / "info_pack.html").exists()


def test_info_pack_falls_back_to_html_without_chromium(golden_record, tmp_path, monkeypatch):
    """A host with no Chromium still gets the pack, as HTML - never a failed render."""
    from engine.render import rasterize

    monkeypatch.setenv("ENGINE_PDF_EXPORT", "1")

    def _boom(*a, **k):
        raise rasterize.RasterizeUnavailable("no chromium here")

    monkeypatch.setattr(rasterize, "html_to_pdf", _boom)
    store = _store_with(golden_record)
    try:
        art = render_one("3060", store, "info_pack", backend="html", output_root=str(tmp_path))
    finally:
        store.close()
    assert Path(art.path).suffix == ".html"
    assert art.mime == "text/html"
    assert "Buyer information pack" in Path(art.path).read_text(encoding="utf-8")


# --- the board's weekday is derived, never typed (D71) --------------------

@pytest.mark.parametrize(
    "typed, expected",
    [
        ("7 May 2026", "THURSDAY"),          # the day their own reference board prints
        ("28 May 2026", "THURSDAY"),
        ("2026-09-15", "TUESDAY"),
        ("7/5/2026", "THURSDAY"),            # SA order, day first
        ("Thursday 7 May 2026", "THURSDAY"), # a weekday already typed in
        ("7th May 2026", "THURSDAY"),        # ordinal suffix
    ],
)
def test_auction_weekday_is_derived_from_the_date(typed, expected):
    from engine.render.html_backend import _auction_weekday

    assert _auction_weekday(typed) == expected


@pytest.mark.parametrize("typed", ["next week", "TBC", "15 September", "", None])
def test_an_unreadable_date_yields_no_weekday_rather_than_a_guess(typed):
    """A wrong day on a printed board sends people to a property on the wrong
    morning, so an unparseable date (or one with no year) prints nothing."""
    from engine.render.html_backend import _auction_weekday

    assert _auction_weekday(typed) is None


def test_the_ads_auction_line_does_not_carry_the_weekday(golden_record, tmp_path):
    """The board prints the weekday; the adverts keep D42's wording.

    One shared macro serving both would have changed every advert in the library.
    """
    golden_record.sale_process.method = "auction"
    golden_record.sale_process.auction_channel = "Online"
    golden_record.sale_process.auction_date = "28 May 2026"
    golden_record.sale_process.auction_time = "10:00"
    store = _store_with(golden_record)
    try:
        ad = Path(render_one("3060", store, "demo_ad", backend="html",
                             output_root=str(tmp_path)).path).read_text(encoding="utf-8")
    finally:
        store.close()

    assert "ONLINE AUCTION | 28 MAY 2026 @ 10:00" in ad
    assert "THURSDAY" not in ad
