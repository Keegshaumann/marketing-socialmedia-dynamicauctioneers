"""The advert's glyph set: stroked line icons, one source of truth (D95).

The pack has its own set (``engine.render.pack_icons``) drawn as FILLED shapes,
because that is how the team's information packs draw them. The advert's are
STROKED, because that is how the team's advertisements draw them - proved by
rendering an advert on the pack's glyphs, where a filled kitchen beside a
stroked bed read as a mistake.

Two sets is therefore deliberate. What was NOT deliberate is that the advert's
lived as fourteen Jinja macros inside a template while the pack's lived in
Python, so:

* the picker could not show them (a webapp template cannot reach an engine
  macro), which is why choosing an icon was a dropdown of words; and
* the two sets drifted - D87 taught the pack a farm's vocabulary and the advert
  learned nothing, so a warehouse's advert had bed, bath and swimming pool to
  choose from and nothing for a workshop, a reception or a loading yard.

Both are fixed by moving the drawings here, where the renderer and the picker
read the same dict.

Every glyph is drawn in a ``0 0 24 24`` box with no fill: the advert sets the
stroke colour, so one glyph works on the dark canvas and on a white shield.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# --- the drawings --------------------------------------------------------

ICONS: Dict[str, str] = {
    # --- rooms a home has ---
    "bed": '<path d="M3 7v11m0-4h18m0 4v-7a2 2 0 0 0-2-2h-7v5"/><circle cx="7" cy="10" r="1.6"/>',
    "bath": '<path d="M4 12h16v3a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4z"/><path d="M6 12V6a2 2 0 0 1 2-2 1.5 1.5 0 0 1 1.5 1.5"/>',
    "kitchen": '<path d="M3 15h18v2a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M3 15h18"/><path d="M7 15v-3a2 2 0 0 1 2-2h1"/><path d="M10 10V6h4"/>',
    "openplan": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M12 5v3M12 16v3"/>',
    "sofa": '<path d="M5 11V8a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v3"/><rect x="3" y="11" width="18" height="6" rx="2"/><path d="M6 17v2M18 17v2"/>',
    "dining": '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="3"/>',
    "study": '<rect x="4" y="4" width="16" height="10" rx="1"/><path d="M9 18h6M12 14v4"/>',
    # --- outside ---
    "patio": '<path d="M12 4v16"/><path d="M4 11a8 8 0 0 1 16 0z"/>',
    "pool": ('<path d="M3 13c1.4 0 1.4 1.4 2.8 1.4S7.2 13 8.6 13 10 14.4 11.4 14.4 12.8 13 14.2 13'
             's1.4 1.4 2.8 1.4S18.4 13 19.8 13 21 14.4 21 14.4"/>'
             '<path d="M3 18c1.4 0 1.4 1.4 2.8 1.4S7.2 18 8.6 18 10 19.4 11.4 19.4 12.8 18 14.2 18'
             's1.4 1.4 2.8 1.4S18.4 18 19.8 18"/><path d="M8 13V5a2 2 0 0 1 4 0"/>'),
    "garden": '<path d="M12 21v-7"/><path d="M12 14c-3.2 0-5.2-2-5.2-5.2C10 8.8 12 10.8 12 14z"/><path d="M12 14c3.2 0 5.2-2 5.2-5.2C14 8.8 12 10.8 12 14z"/><path d="M4 21h16"/>',
    "bar": '<path d="M5 4h14l-7 8z"/><path d="M12 12v6M8 21h8"/>',
    "garage": '<path d="M3 20v-8l9-5 9 5v8"/><path d="M6 20v-5h12v5"/><path d="M6 15h12"/>',
    # --- what a commercial or industrial property is made of (D95) ---
    # A warehouse advert had none of these and had to choose between a bed and a
    # swimming pool for "Workshop with industrial sliding doors".
    "building": '<path d="M3 21V8l7-4 7 4v13"/><path d="M17 21V11h4v10"/><path d="M7 12h2M7 16h2M12 12h2M12 16h2"/>',
    "warehouse": '<path d="M3 21V10l9-5 9 5v11"/><path d="M7 21v-7h10v7"/><path d="M7 17h10"/>',
    "workshop": '<path d="M14.5 5.5a3.5 3.5 0 0 0 4.7 4.7L21 12l-9 9-3-3 9-9z"/><path d="M6 6l3 3"/><path d="M3 9l6-6"/>',
    "storeroom": '<rect x="3" y="7" width="18" height="13" rx="1.5"/><path d="M3 11h18"/><path d="M10 7V4h4v3"/>',
    "office": '<rect x="3" y="9" width="18" height="11" rx="1.5"/><path d="M8 9V5h8v4"/><path d="M3 14h18"/>',
    "parking": '<rect x="3.5" y="3.5" width="17" height="17" rx="2.5"/><path d="M9.5 17V8h3.2a2.8 2.8 0 0 1 0 5.6H9.5"/>',
    "security": '<path d="M12 3l8 3v6c0 4.4-3.2 7.8-8 9-4.8-1.2-8-4.6-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "land": '<path d="M3 20h18"/><path d="M5 20V9l7-5 7 5v11"/><path d="M9 20v-5h6v5"/>',
    "power": '<path d="M13 2L5 14h6l-1 8 8-12h-6z"/>',
    "water": '<path d="M12 3s6 6.5 6 10.5A6 6 0 0 1 6 13.5C6 9.5 12 3 12 3z"/>',
    # --- generic ---
    "size": '<rect x="3.5" y="3.5" width="17" height="17" rx="1.5"/><path d="M20 8l-4-4M20 5v3h-3M4 16l4 4M4 19v-3h3"/>',
    "feature": '<path d="M12 3l2.4 6.2L21 11l-6.6 1.8L12 19l-2.4-6.2L3 11l6.6-1.8z"/>',
}


def svg(name: str, extra_class: str = "") -> str:
    """One glyph as a complete ``<svg>``. Unknown names draw the generic mark."""
    inner = ICONS.get(name) or ICONS["feature"]
    cls = f' class="{extra_class}"' if extra_class else ""
    return (
        f'<svg{cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true" focusable="false">{inner}</svg>'
    )


# What the picker offers, grouped so a marketer scanning it finds the right
# family first. Order is the display order.
CHOICES: List[Tuple[str, str, str]] = [
    ("", "Automatic", "Let the wording choose"),
    ("bed", "Bedroom", "Home"),
    ("bath", "Bathroom", "Home"),
    ("kitchen", "Kitchen", "Home"),
    ("openplan", "Open-plan", "Home"),
    ("sofa", "Lounge", "Home"),
    ("dining", "Dining", "Home"),
    ("study", "Study", "Home"),
    ("patio", "Patio", "Outside"),
    ("pool", "Pool", "Outside"),
    ("garden", "Garden", "Outside"),
    ("bar", "Braai", "Outside"),
    ("garage", "Garage", "Parking"),
    ("parking", "Parking", "Parking"),
    ("warehouse", "Warehouse", "Commercial"),
    ("workshop", "Workshop", "Commercial"),
    ("office", "Office", "Commercial"),
    ("storeroom", "Storage", "Commercial"),
    ("building", "Building", "Commercial"),
    ("land", "Land / yard", "Land"),
    ("security", "Security", "Services"),
    ("power", "Power", "Services"),
    ("water", "Water", "Services"),
    ("size", "Extent", "Other"),
    ("feature", "Plain mark", "Other"),
]

NAMES = {name for name, _, _ in CHOICES if name}

# Keyword rules, first match wins. Commercial terms are tested BEFORE the
# residential ones they would otherwise collide with ("reception office" must
# not become a study; a "storage warehouse" must not become a storeroom).
_RULES: List[Tuple[str, str]] = [
    (r"open.?plan", "openplan"),
    # Workshop before warehouse: "Workshop with industrial sliding doors" is a
    # workshop, and "industrial" in the warehouse rule was claiming it first.
    (r"\bworkshop|\bworks\b", "workshop"),
    (r"\bwarehouse|\bfactory\b|industrial", "warehouse"),
    (r"\breception|\boffice", "office"),
    (r"\bstorage|storeroom|\bstore\b", "storeroom"),
    (r"\bparking|\bcarport|\bbay\b", "parking"),
    (r"\bgarage", "garage"),
    (r"\bsecurity|\balarm|\bfenc|\bguard|access control", "security"),
    (r"\bpower\b|electric|eskom|three.?phase|generator|solar", "power"),
    (r"\bwater\b|borehole|\btank\b|irrigation|\bdam\b", "water"),
    (r"\bkitchen|kitchenette|scullery|pantry", "kitchen"),
    (r"\bstudy\b", "study"),
    (r"\bpatio|balcon|veranda|\bdeck\b|\bstoep", "patio"),
    (r"\bpool\b|swimming", "pool"),
    (r"\bgarden|\blawn\b|landscap|\byard\b", "garden"),
    (r"\bbraai|\bbar\b|\bbbq\b|entertain|\bboma|\blapa", "bar"),
    (r"\blounge|\bliving|tv room|family room", "sofa"),
    (r"\bdining", "dining"),
    (r"\bbedroom|\bbeds?\b", "bed"),
    (r"\bbathroom|\bbath\b|en.?suite|ablution|shower|toilet", "bath"),
    (r"\bbuilding|\bshed\b|outbuilding|\bbarn\b", "building"),
    (r"\bhectare|\bstand\b|\berf\b|\bextent|\bm2\b|\bm²", "size"),
    (r"\bland\b|\bplot\b|\bsite\b|delivery space|working area", "land"),
]
_COMPILED = [(re.compile(p, re.I), n) for p, n in _RULES]


def icon_for(text: str, picks: Optional[Dict[str, str]] = None) -> str:
    """The glyph for a feature line: the marketer's pick, else the rules.

    Ranked on the line's SUBJECT (its opening words) before the whole line, so
    "Balcony leading from the lounge" is a balcony rather than a sofa - the same
    rule the advert's running order uses (D92).
    """
    pick = (picks or {}).get(text)
    if pick and pick in ICONS:
        return pick
    head = " ".join(str(text or "").split()[:5])
    for pattern, name in _COMPILED:
        if pattern.search(head):
            return name
    for pattern, name in _COMPILED:
        if pattern.search(str(text or "")):
            return name
    return "feature"
