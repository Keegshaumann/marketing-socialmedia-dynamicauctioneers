"""Ad-template library (D41): registry + backend selection."""

from __future__ import annotations

from engine.render import ad_templates


def test_registry_lists_the_default_design_first_and_only_faithful_designs():
    tpls = ad_templates.list_templates()
    ids = [t["id"] for t in tpls]
    assert ids[0] == ad_templates.DEFAULT_ID == "hero_overlay"  # default offered first
    # The off-brand starter designs were retired (D49): only the real dark
    # social-ad designs remain in the picker.
    assert "classic" not in ids
    assert "bold" not in ids
    assert set(ids) == {"hero_overlay", "collage", "feature_list", "stats_first"}
    names = {t["id"]: t["name"] for t in tpls}
    assert names["feature_list"] == "Feature list"  # from the {# name: #} comment


def test_ids_round_trip_through_resolve():
    for t in ad_templates.list_templates():
        assert ad_templates.resolve(t["id"]) == t["template"]


def test_resolve_default_and_legacy_picks_fall_back_to_hero_overlay():
    default = "ads/hero_overlay.html.j2"
    assert ad_templates.resolve(None) == default
    assert ad_templates.resolve("") == default
    assert ad_templates.resolve("hero_overlay") == default
    # Legacy / removed picks degrade to the default rather than failing.
    assert ad_templates.resolve("classic") == default
    assert ad_templates.resolve("bold") == default
    assert ad_templates.resolve("was-removed") == default


def test_template_ids_matches_list():
    assert ad_templates.template_ids() == {t["id"] for t in ad_templates.list_templates()}
