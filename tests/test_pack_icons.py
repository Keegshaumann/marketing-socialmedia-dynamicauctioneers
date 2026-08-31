"""The info pack's glyph library (engine/render/pack_icons.py).

The pictogram beside every feature line is most of why the pack reads as the
team's own document (docs/INFO-PACK-PLAYBOOK.md §3). Two things have to hold:
the glyph chosen for a line of record wording is the right picture, and the
wording is only ever RESHAPED, never added to (hard rule 3, no invented facts).
"""

from __future__ import annotations

import re

import pytest

from engine.render.pack_icons import ICONS, icon_for, split_label, svg


# Wording taken off real records and the reference packs.
@pytest.mark.parametrize(
    "line, expected",
    [
        ("3 bedrooms, main with en-suite (bath, toilet, basin)", "bed"),
        ("Full family bathroom plus separate toilet", "bath"),
        ("Open-plan living and dining room", "lounge"),
        ("Kitchen with separate scullery", "kitchen"),
        ("Swimming pool and playground", "pool"),
        ("Communal braai area", "braai"),
        ("Large enclosed thatch lapa", "lapa"),
        ("Double garage", "garage"),
        ("4 carports", "carport"),
        ("Ample guest parking", "parking"),
        ("Remote-access estate security", "security"),
        ("Borehole", "water"),
        ("Prepaid water meter (electricity conventional)", "water"),
        ("Staff quarters", "staff"),
        ("Built-in fire place", "fire"),
        ("1 bedroom flatlet with en-suite bathroom", "flatlet"),
        ("Large steel-frame workshop", "workshop"),
        ("Storerooms", "storeroom"),
        ("5 large bird cages", "garden"),
        ("Courtyard", "garden"),
    ],
)
def test_icon_for_picks_the_right_picture(line, expected):
    assert icon_for(line) == expected


def test_icon_for_never_returns_a_missing_glyph():
    """Anything unmatched falls back to a drawn mark, never to nothing."""
    assert icon_for("something nobody has ever written on a property report") == "mark"
    assert icon_for("") == "mark"
    assert icon_for(None) == "mark"
    for name in ("mark", *ICONS):
        assert ICONS[name].strip()


def test_split_label_only_reshapes_the_record_wording():
    """The label and its qualifier both come out of the line, uppercased."""
    label, sub = split_label("3 bedrooms, main with en-suite (bath, toilet, basin)")
    assert label == "3 BEDROOMS"
    assert sub == "MAIN WITH EN-SUITE BATH, TOILET, BASIN"
    # No orphaned bracket left behind by the split.
    assert "(" not in sub and ")" not in sub
    # Every word printed is a word the line already held.
    original = set(re.findall(r"[a-z]+", "3 bedrooms, main with en-suite (bath, toilet, basin)"))
    printed = set(re.findall(r"[a-z]+", (label + " " + sub).lower()))
    assert printed <= original


def test_split_label_leaves_a_short_line_whole():
    """A short line is one label: a two word qualifier under it reads as a slip."""
    assert split_label("Kitchen") == ("KITCHEN", "")
    assert split_label("Communal braai area") == ("COMMUNAL BRAAI AREA", "")


def test_svg_is_self_contained_and_inherits_its_ink():
    """Glyphs print inside the HTML, take the surrounding colour, and scale."""
    markup = svg("bed", "9mm")
    assert markup.startswith("<svg") and markup.endswith("</svg>")
    assert 'viewBox="0 0 24 24"' in markup
    assert 'fill="currentColor"' in markup       # black on a row, white on the closing page
    assert 'width="9mm"' in markup
    assert "http" not in markup                  # nothing fetched at render time
    # An unknown name still returns a drawing rather than an empty element.
    assert svg("no-such-glyph").count("<svg") == 1


# --- a number is never broken in half (D82) -------------------------------

def test_a_thousands_comma_is_not_a_place_to_break_a_line():
    """Found on a LIVE client pack (DP2987, already client-approved).

    The feature read "Boreholes: 14,000 L (drinking water), 40,000 L
    (irrigation) and two 75,000 L". The splitter broke at the comma inside
    14,000 and printed the headline "BOREHOLES: 14" - telling a buyer the farm
    has fourteen boreholes, when the number is a water capacity. A wrong fact on
    a buyer's information pack is a misrepresentation, not a formatting nit.
    """
    label, detail = split_label(
        "Boreholes: 14,000 L (drinking water), 40,000 L (irrigation) and two 75,000 L"
    )
    assert label == "BOREHOLES: 14,000 L"
    assert "14" != label.split(": ")[-1]
    assert detail.startswith("DRINKING WATER")


def test_a_decimal_comma_survives_too():
    """SA keyboards write 1,8 m for 1.8 m; splitting there loses the number."""
    label, detail = split_label("Security fencing, electrified, 1,8 m, 16 strands")
    assert label == "SECURITY FENCING"
    assert "1,8 M" in detail


def test_a_list_comma_after_a_number_still_splits():
    """The guard must protect thousands separators without refusing every comma
    that happens to follow a digit."""
    label, detail = split_label("Erf 1234, 1,250 m2 in extent")
    assert label == "ERF 1234"
    assert detail == "1,250 M2 IN EXTENT"


# --- the icon set learns the farm vocabulary (D87) -------------------------

def test_agricultural_features_get_a_real_icon_not_the_generic_mark():
    """The glyph rules were written from RESIDENTIAL reference packs, so a farm
    fell through them: 7 of DP2987's 25 features drew the neutral mark, which
    on a buyer's pack is a row of shrugs beside the property's best assets.

    Every glyph needed already existed - this was a vocabulary gap, not missing
    artwork.
    """
    cases = {
        "Staff accommodation for approximately 75 people": "staff",
        "Produce packaging facility / packhouse": "workshop",
        "Cold room": "aircon",
        "Boiler rooms": "fire",
        "Multiple dams (cement and gravel dams)": "water",
        "Pump houses": "water",
        "Sandy to rocky soils with good drainage": "land",
        "Gradual western slope": "land",
    }
    for text, want in cases.items():
        got = icon_for(text)
        assert got == want, f"{text!r} drew {got!r}, expected {want!r}"
        assert got != "mark"


def test_a_line_is_not_matched_on_the_wrong_word():
    """Two lines were matching a later rule for the wrong reason, which is worse
    than the generic mark because it draws a confidently wrong picture:
    a greenhouse says "temperature/climate control" and drew an AIR CONDITIONER;
    "natural grazing (2 camps with water points)" drew a WATER DROP.
    """
    assert icon_for(
        "Extensive greenhouse tunnels (twelve tunnels with irrigation and "
        "temperature/climate control)"
    ) == "garden"
    assert icon_for("Natural grazing (2 camps with water points)") == "land"


def test_every_dwelling_in_the_family_draws_the_same_glyph():
    """"Second dwelling" matched; "Third dwelling" fell through to the bed rule,
    because its line also says "3 bedrooms" - so two identical rows on one pack
    drew different pictures."""
    for n in ("Second", "Third", "Fourth", "Additional"):
        assert icon_for(f"{n} dwelling: 3 bedrooms, 2 bathrooms") == "flatlet"


def test_the_residential_vocabulary_is_unchanged():
    """The rules are ordered and first-match-wins, so a new rule inserted in the
    wrong place silently re-points existing lines."""
    for text, want in {
        "3 bedrooms, main with en-suite (bath, toilet, basin)": "bed",
        "Full family bathroom plus separate toilet": "bath",
        "Open-plan living and dining room": "lounge",
        "Double lock-up garage": "garage",
        "Swimming pool and playground": "pool",
        "Air conditioning throughout": "aircon",
        "Prepaid water meter": "water",
        "Solar geyser and inverter": "solar",
    }.items():
        assert icon_for(text) == want, f"{text!r} changed to {icon_for(text)!r}"
