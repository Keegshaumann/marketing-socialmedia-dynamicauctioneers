"""Ad-template library (D41): registry + backend selection."""

from __future__ import annotations

import pytest

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


# --- every design previews (D97) ------------------------------------------

def test_every_ad_design_renders_a_thumbnail(tmp_path):
    """Three of the four design previews were broken images on the live site.

    Not Chromium, not the cache: ``ad_thumbs`` builds its OWN Jinja environment,
    and D95's new ``ad_glyph``/``ad_icon_for`` globals were registered on the
    render backend's environment only. Collage survived because it is the one
    design that does not draw feature icons.

    This renders every design through the thumbnail path, so a global added to
    one environment and forgotten in the other fails here rather than on a
    marketer's screen.
    """
    from engine.render import ad_templates
    from engine.render.ad_thumbs import thumbnail
    from engine.render.rasterize import available

    if not available():
        pytest.skip("no Chromium for rasterising")

    ids = ad_templates.template_ids()
    assert ids, "no ad designs registered"
    for tid in ids:
        png = thumbnail(tid, str(tmp_path))
        assert png is not None and png.is_file(), f"{tid} produced no thumbnail"
        assert png.stat().st_size > 5_000, f"{tid} thumbnail is suspiciously small"


def test_both_template_environments_are_configured_the_same(tmp_path):
    """The root cause, asserted directly: two environments load these templates
    and must know the same globals and filters."""
    from engine.render.ad_thumbs import _env as thumb_env
    from engine.render.html_backend import HtmlBackend

    backend_env = HtmlBackend()._env
    missing_globals = set(backend_env.globals) - set(thumb_env.globals)
    missing_filters = set(backend_env.filters) - set(thumb_env.filters)
    assert not missing_globals, f"the thumbnail env is missing: {missing_globals}"
    assert not missing_filters, f"the thumbnail env is missing filters: {missing_filters}"
