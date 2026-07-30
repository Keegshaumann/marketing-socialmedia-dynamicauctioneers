"""Small-edits-on-live-listings tests (D-log: human_overrides editor layer).

All offline / key-free. Covers:
- ``human_overrides`` applies in ``public_view()`` and leaves the sourced field
  pristine (SPEC hard rule 3);
- the POPIA guard: an override can never recreate a stripped PII path, whether
  applied through the projection or written via ``apply_edits``;
- ``apply_edits`` routing (price -> its field + format, headline -> copy field,
  other public facts -> ``human_overrides``) and single-render behaviour;
- the board index reflects an overridden suburb/price;
- the one-internal-approval-per-repost enforcement invalidates on re-edit.
"""

from __future__ import annotations

import pytest

from engine.render.service import _format_price, apply_edits, apply_photos
from engine.schema import PropertyRecord, override_key_allowed
from engine.store import RecordStore


# --- public_view overlay + POPIA guard (no store / render needed) ---------

def test_override_applies_and_source_stays_pristine():
    rec = PropertyRecord(
        dp="9001",
        identity={"suburb": "Old", "street_address": "1 Old Rd"},
        sale_process={"method": "offers_invited"},
    )
    rec.human_overrides = {
        "identity.suburb": "Newville",
        "sale_process.terms": ["Auction 20 Aug 2026", "Deposit 10%"],
    }
    pv = rec.public_view()
    assert pv["identity"]["suburb"] == "Newville"
    assert pv["sale_process"]["terms"] == ["Auction 20 Aug 2026", "Deposit 10%"]
    # Untouched field still present; sourced value on the model is unchanged.
    assert pv["identity"]["street_address"] == "1 Old Rd"
    assert rec.identity.suburb == "Old"


def test_override_creates_null_intermediate_path():
    rec = PropertyRecord(dp="9002")  # sale_process is None
    rec.human_overrides = {"sale_process.method": "auction"}
    assert rec.public_view()["sale_process"]["method"] == "auction"


def test_override_key_guard_rejects_popia_paths():
    assert override_key_allowed("identity.suburb")
    assert override_key_allowed("sale_process.method")
    assert override_key_allowed("valuation.municipal_valuation")  # public figure stays editable
    assert not override_key_allowed("financials_internal.owner.name")
    assert not override_key_allowed("financials_internal.bond.amount")
    assert not override_key_allowed("sale_process.viewing.contact_internal_only")
    # Commercially-sensitive / internal fields are now blocked at write time too
    # (D44 review), covering the same set public_view() strips.
    assert not override_key_allowed("valuation.professional")
    assert not override_key_allowed("valuation.professional.forced_sale_value")
    assert not override_key_allowed("valuation")  # whole-block override would carry professional
    assert not override_key_allowed("physical.conflicts")


def test_override_cannot_resurrect_stripped_pii_in_projection():
    rec = PropertyRecord(dp="9003")
    # Even if a forbidden override is somehow present, public_view drops it.
    rec.human_overrides = {
        "financials_internal.owner.name": "ZZOWNER",
        "sale_process.viewing.contact_internal_only": "0820000000",
    }
    pv = rec.public_view()
    assert "financials_internal" not in pv
    assert (pv.get("sale_process") or {}).get("viewing") is None


# --- apply_edits routing + render (store + html backend) ------------------

@pytest.fixture
def live_store(tmp_path):
    """A record walked to ``updated`` (mimics a reopened live listing)."""
    store = RecordStore(db_path=":memory:")
    rec = PropertyRecord(
        dp="9100",
        identity={"suburb": "Old", "street_address": "1 Old Rd", "title_type": "freehold"},
        marketing={"price_display": "R1 000 000", "headline": "Nice home"},
        sale_process={"method": "offers_invited"},
    )
    store.upsert(rec, state="extracted")
    for s in ["verified", "drafted", "approved", "client_approved", "assets_built", "live", "updated"]:
        store.transition("9100", s)
    yield store
    store.close()


def test_apply_edits_routes_and_formats(live_store, tmp_path):
    res = apply_edits(
        "9100",
        live_store,
        {
            "marketing.price_display": "900000",       # -> formatted, own field
            "marketing.headline": "Reduced riverside home",  # -> copy field
            "identity.street_address": "2 New Rd",       # -> human_overrides
            "identity.suburb": "Newville",               # -> human_overrides
            "sale_process.method": "auction",            # -> human_overrides
            "sale_process.terms": ["Auction 20 Aug 2026"],
        },
        user="nikki@da",
        output_root=str(tmp_path),
    )
    assert res.changes["marketing.price_display"] == "R900 000"  # typed number formatted
    assert res.artifacts, "a single render should produce artifacts"

    stored = live_store.get("9100")
    pv = stored.public_view()
    assert pv["marketing"]["price_display"] == "R900 000"
    assert pv["marketing"]["headline"] == "Reduced riverside home"
    assert pv["identity"]["street_address"] == "2 New Rd"
    assert pv["sale_process"]["method"] == "auction"
    # Facts ride human_overrides; the sourced address is untouched.
    assert stored.identity.street_address == "1 Old Rd"
    assert stored.human_overrides["identity.street_address"] == "2 New Rd"
    # Price/headline keep their dedicated homes (not overrides).
    assert "marketing.price_display" not in stored.human_overrides
    assert "marketing.headline" not in stored.human_overrides


def test_apply_edits_refuses_popia_field(live_store, tmp_path):
    with pytest.raises(ValueError):
        apply_edits(
            "9100",
            live_store,
            {"financials_internal.owner.name": "ZZOWNER"},
            user="nikki@da",
            output_root=str(tmp_path),
        )


def test_edit_lands_on_rendered_artifact(live_store, tmp_path):
    # An overridden address is copy-derived from public_view, so it lands on the
    # generated portal listing (proving overrides reach the copy layer, not just
    # the html templates).
    apply_edits(
        "9100", live_store, {"identity.street_address": "17 Kingfisher Lane"},
        user="nikki@da", output_root=str(tmp_path),
    )
    portal = (tmp_path / "DP9100" / "artifacts" / "portal_listing.md").read_text(encoding="utf-8")
    assert "17 Kingfisher Lane" in portal
    assert "1 Old Rd" not in portal  # the sourced address no longer shows


def test_board_index_reflects_override(live_store, tmp_path):
    apply_edits(
        "9100", live_store,
        {"identity.suburb": "Riverbend", "marketing.price_display": "900000"},
        user="nikki@da", output_root=str(tmp_path),
    )
    row = next(r for r in live_store.list_records() if r["dp"] == "9100")
    assert row["suburb"] == "Riverbend"


# --- one-internal-approval-per-repost enforcement -------------------------

def test_internal_approval_invalidated_by_later_edit(live_store, tmp_path):
    apply_edits("9100", live_store, {"identity.suburb": "Riverbend"}, user="nikki@da", output_root=str(tmp_path))
    assert live_store.internally_approved_since_last_edit("9100") is False

    live_store.record_signoff("9100", gate="repost", user="ron@da", note="internal approval for repost")
    assert live_store.internally_approved_since_last_edit("9100") is True

    # A further edit invalidates the prior approval.
    apply_edits("9100", live_store, {"identity.suburb": "Riverbend West"}, user="nikki@da", output_root=str(tmp_path))
    assert live_store.internally_approved_since_last_edit("9100") is False


def test_change_request_is_not_an_approval(live_store, tmp_path):
    apply_edits("9100", live_store, {"identity.suburb": "Riverbend"}, user="nikki@da", output_root=str(tmp_path))
    # A gate-2 change request must not count as the repost approval.
    live_store.record_signoff("9100", gate="2", user="ron@da", note="changes requested: fix suburb")
    assert live_store.internally_approved_since_last_edit("9100") is False


def test_reopen_invalidates_prior_repost_approval():
    store = RecordStore(db_path=":memory:")
    store.upsert(PropertyRecord(dp="9200"), state="extracted")
    for s in ["verified", "drafted", "approved", "client_approved", "assets_built", "live"]:
        store.transition("9200", s)
    # cycle 1: reopen -> approve -> repost
    store.transition("9200", "updated", note="reopened for edit")
    store.record_signoff("9200", gate="repost", user="ron", note="internal approval for repost")
    assert store.internally_approved_since_last_edit("9200") is True
    store.transition("9200", "live", note="reposted")
    # cycle 2: a fresh reopen (no new approval) invalidates the prior one.
    store.transition("9200", "updated", note="reopened for edit")
    assert store.internally_approved_since_last_edit("9200") is False
    store.close()


# --- POPIA guard: ancestors/descendants + defence in depth ----------------

def test_override_key_guard_rejects_ancestors_and_descendants():
    # ancestor of a stripped path (whole-dict override would carry PII back)
    assert not override_key_allowed("sale_process.viewing")
    assert not override_key_allowed("sale_process")
    # descendant under a stripped path
    assert not override_key_allowed("sale_process.viewing.contact_internal_only.x")
    assert not override_key_allowed("financials_internal.bond.amount")
    # siblings and unrelated paths are still allowed
    assert override_key_allowed("sale_process.method")
    assert override_key_allowed("sale_process.viewing.contact_public")


def test_public_view_restrips_even_if_ancestor_override_slips_through():
    # Belt-and-braces: a forbidden ancestor override set directly on the model
    # must not surface the occupant cell in the projection.
    rec = PropertyRecord(dp="9300", sale_process={"viewing": {"contact_public": "086 155 2288"}})
    rec.human_overrides = {"sale_process.viewing": {"contact_public": "x", "contact_internal_only": "0820000000"}}
    viewing = (rec.public_view().get("sale_process") or {}).get("viewing") or {}
    assert "contact_internal_only" not in viewing


def test_apply_edits_refuses_ancestor_popia_key(live_store, tmp_path):
    with pytest.raises(ValueError):
        apply_edits("9100", live_store, {"sale_process.viewing": {"contact_internal_only": "x"}},
                    user="nikki@da", output_root=str(tmp_path))


def test_apply_edits_no_partial_audit_on_reject(live_store, tmp_path):
    before = len(live_store.list_events("9100"))
    with pytest.raises(ValueError):
        apply_edits(
            "9100", live_store,
            {"identity.suburb": "Riverbend", "financials_internal.owner.name": "ZZ"},
            user="nikki@da", output_root=str(tmp_path),
        )
    # The good field in the same batch must NOT have been applied or audited.
    assert len(live_store.list_events("9100")) == before
    stored = live_store.get("9100")
    assert not (stored.human_overrides or {}).get("identity.suburb")


# --- price formatting -----------------------------------------------------

def test_format_price_handles_decimals_and_phrases():
    assert _format_price("900000") == "R900 000"
    assert _format_price("900000.50") == "R900 000"      # cents dropped, not concatenated
    assert _format_price(900000) == "R900 000"
    assert _format_price("R2 500 000") == "R2 500 000"
    assert _format_price("Offers invited") == "Offers invited"


# --- photos ---------------------------------------------------------------

def test_apply_photos_writes_canonical_hero_gallery_and_rerenders(live_store, tmp_path):
    res = apply_photos(
        "9100", live_store, "photos/front.png", ["photos/side.png", "photos/kitchen.png"],
        user="nikki@da", output_root=str(tmp_path),
    )
    rec = live_store.get("9100")
    # written to the canonical marketing fields (so Canva uploads them too), not overrides
    assert rec.marketing.hero_photo == "photos/front.png"
    assert rec.marketing.gallery == ["photos/side.png", "photos/kitchen.png"]
    assert not (rec.human_overrides or {})
    assert res.artifacts  # a single render happened


def test_apply_photos_clears_when_empty(live_store, tmp_path):
    apply_photos("9100", live_store, "photos/a.png", [], user="nikki@da", output_root=str(tmp_path))
    apply_photos("9100", live_store, None, [], user="nikki@da", output_root=str(tmp_path))
    rec = live_store.get("9100")
    assert rec.marketing.hero_photo is None
    assert rec.marketing.gallery == []
