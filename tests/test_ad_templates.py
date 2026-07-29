"""Ad-template library (D41): registry + backend selection."""

from __future__ import annotations

from engine.render import ad_templates


def test_registry_lists_classic_first_and_variants():
    tpls = ad_templates.list_templates()
    ids = [t["id"] for t in tpls]
    assert ids[0] == "classic"  # the built-in default is always offered first
    assert "bold" in ids  # the starter variant is auto-discovered
    names = {t["id"]: t["name"] for t in tpls}
    assert names["classic"] == "Classic"
    assert names["bold"] == "Bold Dark"  # from the {# name: #} comment


def test_ids_round_trip_through_resolve():
    for t in ad_templates.list_templates():
        assert ad_templates.resolve(t["id"]) == t["template"]


def test_resolve_default_and_unknown_fall_back_to_classic():
    assert ad_templates.resolve(None) == "demo_ad.html.j2"
    assert ad_templates.resolve("") == "demo_ad.html.j2"
    assert ad_templates.resolve("classic") == "demo_ad.html.j2"
    assert ad_templates.resolve("bold") == "ads/bold.html.j2"
    assert ad_templates.resolve("was-removed") == "demo_ad.html.j2"


def test_template_ids_matches_list():
    assert ad_templates.template_ids() == {t["id"] for t in ad_templates.list_templates()}
