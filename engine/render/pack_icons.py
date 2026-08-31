"""Glyph library for the buyer information pack (docs/INFO-PACK-PLAYBOOK.md §3).

The team's own packs carry a black pictogram beside every feature line - a bed, a
bath, a garage, a pool - which is most of why the pages read as theirs. Those are
Canva elements we do not have, so these are drawn here: flat black shapes on a
24x24 grid, built from rectangles, circles and polygons so they stay legible at the
~9mm the pack prints them at.

Two jobs live in this module:

* ``ICONS`` - the drawings, as the inner markup of an SVG. ``svg()`` wraps one in
  its element. ``currentColor`` throughout, so a glyph inherits the ink of wherever
  it sits (black on a feature row, white on the closing page's contact rows).
* ``icon_for()`` - choosing the glyph for a line of record text. The record says
  "3 bedrooms, main with en-suite (bath, toilet, basin)"; the pack shows a bed.
  Matching is keyword-based and ORDERED: the first rule that hits wins, so
  "swimming pool and playground" is a pool rather than a playground, and a line
  that matches nothing gets the neutral mark rather than a wrong picture.

Nothing here invents facts: the glyph is decoration chosen from wording the record
already holds, and ``split_label`` only reshapes that wording (it never adds
words).
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# --- the drawings --------------------------------------------------------
# Every glyph is drawn inside viewBox="0 0 24 24" and fills with currentColor.

ICONS: Dict[str, str] = {
    # --- rooms ---
    "bed": (
        '<rect x="1" y="8" width="2.4" height="11"/>'
        '<rect x="20.6" y="12" width="2.4" height="7"/>'
        '<rect x="1" y="14.4" width="22" height="3"/>'
        '<rect x="5" y="10.4" width="6" height="4" rx="1.4"/>'
        '<path d="M12 10.4h7.2a1.4 1.4 0 0 1 1.4 1.4v2.6H12z"/>'
    ),
    "bath": (
        '<path d="M2 11h20v2.2a5 5 0 0 1-5 5H7a5 5 0 0 1-5-5z"/>'
        '<path d="M5 11V6.6A2.6 2.6 0 0 1 7.6 4a2.6 2.6 0 0 1 2.5 1.9l-1.9.6A.7.7 0 0 0 7.6 6a.6.6 0 0 0-.6.6V11z"/>'
        '<rect x="5" y="18.6" width="1.8" height="2.4"/>'
        '<rect x="17.2" y="18.6" width="1.8" height="2.4"/>'
    ),
    "shower": (
        '<path d="M12 3a6 6 0 0 1 6 6H6a6 6 0 0 1 6-6z"/>'
        '<rect x="11" y="1" width="2" height="3"/>'
        '<circle cx="8" cy="13" r="1.1"/><circle cx="12" cy="15.4" r="1.1"/>'
        '<circle cx="16" cy="13" r="1.1"/><circle cx="10" cy="18.4" r="1.1"/>'
        '<circle cx="14.4" cy="19.6" r="1.1"/>'
    ),
    "toilet": (
        '<circle cx="7" cy="4.4" r="2.4"/>'
        '<path d="M4.4 8h5.2l1.6 6.4H8.8V21H5.2v-6.6H2.8z"/>'
        '<circle cx="17" cy="4.4" r="2.4"/>'
        '<path d="M17 8a3 3 0 0 1 3 3v4h-1.6v6h-2.8v-6H14v-4a3 3 0 0 1 3-3z"/>'
    ),
    "kitchen": (
        '<rect x="2" y="4" width="20" height="2.4"/>'
        '<rect x="2" y="7.6" width="8.4" height="12.4"/>'
        '<rect x="13.6" y="7.6" width="8.4" height="12.4"/>'
        '<rect x="4.6" y="10" width="3.2" height="1.4" fill="#fff"/>'
        '<rect x="16.2" y="10" width="3.2" height="1.4" fill="#fff"/>'
        '<rect x="4.6" y="14" width="3.2" height="4" fill="#fff"/>'
        '<rect x="16.2" y="14" width="3.2" height="4" fill="#fff"/>'
    ),
    "scullery": (
        '<path d="M2 10h20v3a5 5 0 0 1-5 5H7a5 5 0 0 1-5-5z"/>'
        '<rect x="11" y="2" width="1.8" height="6"/>'
        '<path d="M12.8 2h4.4a2 2 0 0 1 2 2v4h-2V4.6h-4.4z"/>'
        '<circle cx="12" cy="13.6" r="2" fill="#fff"/>'
    ),
    "lounge": (
        '<path d="M3 11.6a2.6 2.6 0 0 1 5.2 0v1.6H3z"/>'
        '<path d="M15.8 11.6a2.6 2.6 0 0 1 5.2 0V16H3v-2.8h18"/>'
        '<rect x="2" y="13" width="20" height="4.4" rx="1.4"/>'
        '<rect x="4" y="17.4" width="2" height="2.6"/>'
        '<rect x="18" y="17.4" width="2" height="2.6"/>'
        '<path d="M6.6 8.6h10.8a2 2 0 0 1 2 2V13H4.6v-2.4a2 2 0 0 1 2-2z"/>'
    ),
    "dining": (
        '<rect x="2" y="9.6" width="20" height="2.4"/>'
        '<rect x="4" y="12" width="1.8" height="8"/>'
        '<rect x="18.2" y="12" width="1.8" height="8"/>'
        '<rect x="7.4" y="4" width="1.6" height="5.2"/>'
        '<rect x="10.4" y="4" width="1.6" height="5.2"/>'
        '<path d="M15 4h1.7v2.6a1.7 1.7 0 0 1-1.7 1.7z"/>'
        '<rect x="15" y="7.4" width="1.7" height="2"/>'
    ),
    "study": (
        '<rect x="2" y="14.6" width="20" height="2.2"/>'
        '<rect x="3.6" y="16.8" width="1.8" height="4"/>'
        '<rect x="18.6" y="16.8" width="1.8" height="4"/>'
        '<rect x="6.6" y="5" width="11" height="7.6" rx="1"/>'
        '<rect x="8.4" y="6.8" width="7.4" height="4" fill="#fff"/>'
        '<rect x="5" y="12.6" width="14.2" height="2"/>'
    ),
    "laundry": (
        '<rect x="3.4" y="2.6" width="17.2" height="18.8" rx="2"/>'
        '<circle cx="12" cy="14" r="5" fill="#fff"/>'
        '<circle cx="12" cy="14" r="2.2"/>'
        '<circle cx="7" cy="6.2" r="1.3" fill="#fff"/>'
        '<rect x="10.6" y="5" width="7.4" height="2.4" fill="#fff"/>'
    ),
    "fire": (
        '<path d="M12 2c2.6 3.2 1 5.4 0 6.6-1.4 1.8-2.6 3-2.6 5A5.4 5.4 0 0 0 12 19a5.4 5.4 0 0 0 2.6-5.4c0-1.4-.6-2.6-1.4-3.6 2.4.8 4.8 3.4 4.8 6.4A6 6 0 0 1 12 22a6 6 0 0 1-6-5.6c0-4.4 4.4-6.6 6-8.4 1-1.2 1.4-3 0-6z"/>'
    ),
    # --- outside ---
    "pool": (
        '<path d="M2 15.4c1.7 0 1.7 1.4 3.3 1.4s1.7-1.4 3.4-1.4 1.7 1.4 3.3 1.4 1.7-1.4 3.4-1.4 1.7 1.4 3.3 1.4S20.4 15.4 22 15.4V18c-1.7 0-1.7 1.4-3.3 1.4s-1.7-1.4-3.4-1.4-1.7 1.4-3.3 1.4-1.7-1.4-3.4-1.4-1.7 1.4-3.3 1.4S3.7 18 2 18z"/>'
        '<rect x="8" y="4" width="1.9" height="10"/>'
        '<rect x="14.4" y="4" width="1.9" height="10"/>'
        '<rect x="9.9" y="6.6" width="4.5" height="1.8"/>'
        '<rect x="9.9" y="10.2" width="4.5" height="1.8"/>'
    ),
    "braai": (
        '<path d="M3.4 8h17.2l-1.8 5.6a6 6 0 0 1-5.7 4.2h-2.2a6 6 0 0 1-5.7-4.2z"/>'
        '<rect x="6.4" y="17" width="1.8" height="4.6" transform="rotate(20 7.3 19.3)"/>'
        '<rect x="15.8" y="17" width="1.8" height="4.6" transform="rotate(-20 16.7 19.3)"/>'
        '<path d="M9 2.4c1.4 1.4.6 2.4 0 3.2h-1.6c-.6-1.4.2-2.4 1.6-3.2z"/>'
        '<path d="M13 1.6c1.6 1.8.8 3 0 4h-1.7c-.7-1.7.2-2.9 1.7-4z"/>'
        '<path d="M16.8 2.4c1.4 1.4.6 2.4 0 3.2h-1.6c-.6-1.4.2-2.4 1.6-3.2z"/>'
    ),
    "lapa": (
        '<path d="M12 3 23 11H1z"/>'
        '<path d="M3.6 11h16.8l-1.6 2.2H5.2z"/>'
        '<rect x="4.4" y="13.4" width="1.9" height="7.6"/>'
        '<rect x="17.7" y="13.4" width="1.9" height="7.6"/>'
    ),
    "patio": (
        '<path d="M12 2c5.5 0 10 3.6 10 8H2c0-4.4 4.5-8 10-8z"/>'
        '<rect x="11.1" y="10" width="1.8" height="10"/>'
        '<path d="M8.6 20h6.8a3.4 3.4 0 0 1-6.8 0z"/>'
    ),
    "garden": (
        '<path d="M12 2c3.4 0 6 2.6 6 5.8 0 3.4-2.6 6.2-6 6.2s-6-2.8-6-6.2C6 4.6 8.6 2 12 2z"/>'
        '<rect x="11" y="13" width="2" height="8"/>'
        '<path d="M11 17c-2.6 0-4.4-1.4-5-3.4 2.6-.6 4.6.6 5 3.4z"/>'
        '<path d="M13 18.4c2.6 0 4.4-1.4 5-3.4-2.6-.6-4.6.6-5 3.4z"/>'
    ),
    "view": (
        '<path d="M2 20 9 8l4.4 7.4L16 11l6 9z"/>'
        '<circle cx="17.6" cy="5.4" r="2.6"/>'
    ),
    # --- parking ---
    "garage": (
        '<path d="M12 2 22.4 7.4V21h-3.2V9.6H4.8V21H1.6V7.4z"/>'
        '<rect x="6.4" y="11.4" width="11.2" height="2"/>'
        '<rect x="6.4" y="14.8" width="11.2" height="2"/>'
        '<rect x="6.4" y="18.2" width="11.2" height="2.8"/>'
    ),
    "carport": (
        '<path d="M12 2 22.6 8H1.4z"/>'
        '<rect x="2.6" y="8.4" width="1.8" height="12.6"/>'
        '<rect x="19.6" y="8.4" width="1.8" height="12.6"/>'
        '<path d="M7.8 13.6h8.4l1.4 3.2v3H6.4v-3z"/>'
        '<circle cx="8.6" cy="19.8" r="1.4"/><circle cx="15.4" cy="19.8" r="1.4"/>'
    ),
    "parking": (
        '<path d="M5.6 9.6h12.8l2 5v5h-2.6v-2H6.2v2H3.6v-5z"/>'
        '<path d="M7 5h10l1.6 4H5.4z"/>'
        '<circle cx="7.4" cy="16" r="1.4" fill="#fff"/>'
        '<circle cx="16.6" cy="16" r="1.4" fill="#fff"/>'
    ),
    # --- services / security ---
    "security": (
        '<path d="M12 1.6 21 5v7.4c0 5-3.8 8.6-9 10-5.2-1.4-9-5-9-10V5z"/>'
        '<path d="m10.8 15.4-3.4-3.4 1.8-1.8 1.6 1.6 4.2-4.2 1.8 1.8z" fill="#fff"/>'
    ),
    "camera": (
        '<path d="M2.6 6.4 19 3.2l1 5-16.4 3.2z"/>'
        '<path d="M6 11.6h4.4l-1.6 4.8H4.4z"/>'
        '<rect x="11" y="10.6" width="1.8" height="6.4"/>'
        '<rect x="6.6" y="17" width="10.6" height="1.8"/>'
    ),
    "gate": (
        '<rect x="1.6" y="4" width="2.2" height="17"/>'
        '<rect x="20.2" y="4" width="2.2" height="17"/>'
        '<rect x="4.4" y="7.6" width="15.2" height="1.8"/>'
        '<rect x="4.4" y="12" width="15.2" height="1.8"/>'
        '<rect x="4.4" y="16.4" width="15.2" height="1.8"/>'
        '<rect x="11" y="6" width="2" height="15"/>'
    ),
    "power": (
        '<path d="M13.6 1.6 5 13.4h5.2L9.2 22.4 19 10h-5.6z"/>'
    ),
    "water": (
        '<path d="M12 2.6c3.4 4.4 6 7.4 6 10.6A6 6 0 0 1 12 19a6 6 0 0 1-6-5.8c0-3.2 2.6-6.2 6-10.6z"/>'
        '<rect x="4" y="20" width="16" height="1.8"/>'
    ),
    "solar": (
        '<path d="M4.6 5h14.8l3 11.4H1.6z"/>'
        '<rect x="6.6" y="7" width="10.8" height="1.6" fill="#fff"/>'
        '<rect x="5.6" y="10.4" width="12.8" height="1.6" fill="#fff"/>'
        '<rect x="4.6" y="13.8" width="14.8" height="1.6" fill="#fff"/>'
        '<rect x="11" y="17.6" width="2" height="3.4"/>'
    ),
    "aircon": (
        '<rect x="2" y="4.6" width="20" height="8" rx="1.6"/>'
        '<rect x="4" y="9.6" width="16" height="1.4" fill="#fff"/>'
        '<path d="M6.4 15.4c1.6 1 1.6 2.6 0 4"/>'
        '<path d="M6.4 15.4c1.8 1.2 1.8 3 0 4.4l-1.4-1.2c.8-.7.8-1.3 0-2z"/>'
        '<path d="M12 15.4c1.8 1.2 1.8 3 0 4.4l-1.4-1.2c.8-.7.8-1.3 0-2z"/>'
        '<path d="M17.6 15.4c1.8 1.2 1.8 3 0 4.4l-1.4-1.2c.8-.7.8-1.3 0-2z"/>'
    ),
    # --- buildings ---
    "house": (
        '<path d="M12 2.4 22.6 11h-3v10h-15V11h-3z"/>'
        '<rect x="10.4" y="13.6" width="3.2" height="7.4" fill="#fff"/>'
    ),
    "flatlet": (
        '<path d="M12 3.6 21.4 11h-2.6v9.4H5.2V11H2.6z"/>'
        '<rect x="7.4" y="13" width="3" height="3" fill="#fff"/>'
        '<rect x="11.6" y="13" width="5" height="7.4" fill="#fff"/>'
    ),
    "building": (
        '<rect x="3" y="2.6" width="11" height="18.4"/>'
        '<rect x="14.6" y="8.6" width="6.4" height="12.4"/>'
        '<rect x="5.4" y="5" width="2.4" height="2.4" fill="#fff"/>'
        '<rect x="9.2" y="5" width="2.4" height="2.4" fill="#fff"/>'
        '<rect x="5.4" y="9.4" width="2.4" height="2.4" fill="#fff"/>'
        '<rect x="9.2" y="9.4" width="2.4" height="2.4" fill="#fff"/>'
        '<rect x="5.4" y="13.8" width="2.4" height="2.4" fill="#fff"/>'
        '<rect x="9.2" y="13.8" width="2.4" height="2.4" fill="#fff"/>'
        '<rect x="16.6" y="11.4" width="2.4" height="2.4" fill="#fff"/>'
        '<rect x="16.6" y="15.6" width="2.4" height="2.4" fill="#fff"/>'
    ),
    "workshop": (
        '<path d="M2 21V9.6l6.4 3.4V9.6l6.4 3.4V9.6L21.2 13V4.4H23V21z"/>'
        '<rect x="4.6" y="15.4" width="3.4" height="5.6" fill="#fff"/>'
        '<rect x="11" y="15.4" width="3.4" height="5.6" fill="#fff"/>'
        '<rect x="17.4" y="15.4" width="3.4" height="5.6" fill="#fff"/>'
    ),
    "storeroom": (
        '<rect x="2" y="3" width="20" height="2.2"/>'
        '<rect x="2" y="10.4" width="20" height="2.2"/>'
        '<rect x="2" y="17.8" width="20" height="2.2"/>'
        '<rect x="4.4" y="6" width="4" height="4.4"/>'
        '<rect x="10" y="7" width="3.4" height="3.4"/>'
        '<rect x="15.4" y="6" width="4.2" height="4.4"/>'
        '<rect x="5" y="13.4" width="4.6" height="4.4"/>'
        '<rect x="11.6" y="14.4" width="3.4" height="3.4"/>'
    ),
    "canopy": (
        '<path d="M1.4 8 12 3l10.6 5v2.4H1.4z"/>'
        '<rect x="2.6" y="10.8" width="1.8" height="10.2"/>'
        '<rect x="19.6" y="10.8" width="1.8" height="10.2"/>'
        '<rect x="2.6" y="12.8" width="18.8" height="1.6"/>'
    ),
    "office": (
        '<circle cx="8.4" cy="5.6" r="3"/>'
        '<path d="M3 15.4a5.4 5.4 0 0 1 10.8 0v.6H3z"/>'
        '<rect x="2" y="16.8" width="20" height="2.2"/>'
        '<rect x="14.6" y="9" width="7" height="6.4" rx=".8"/>'
        '<rect x="16.2" y="10.6" width="3.8" height="3.2" fill="#fff"/>'
    ),
    "staff": (
        '<rect x="11" y="2" width="2" height="12"/>'
        '<path d="M7.4 14h9.2l1.4 7.4H6z"/>'
        '<rect x="7.4" y="16.4" width="9.2" height="1.6" fill="#fff"/>'
    ),
    # --- land / measure ---
    "area": (
        '<path d="M3 3h18v18H3z" fill="none" stroke="currentColor" stroke-width="2"/>'
        '<path d="M7.4 15.6V9.4h1.5l1.5 2.6 1.5-2.6h1.5v6.2h-1.5v-3.4l-1.5 2.5-1.5-2.5v3.4z"/>'
        '<path d="M15 11.6c0-.9.7-1.4 1.5-1.4s1.5.5 1.5 1.4c0 .6-.4 1-1 1.5l-.9.8h1.9v1.1H15v-.9l1.6-1.4c.3-.3.5-.5.5-.8s-.2-.5-.6-.5-.6.2-.6.6z"/>'
    ),
    "land": (
        '<path d="M2 7 9 4l6 2.6L22 4v13l-7 3-6-2.6L2 20z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>'
        '<path d="M9 4v13M15 6.6v13" stroke="currentColor" stroke-width="2"/>'
    ),
    "doc": (
        '<path d="M4.6 2h9.6L19.4 7v15H4.6z"/>'
        '<path d="M13.8 2.6V7.4h4.8" fill="#fff"/>'
        '<rect x="7" y="11" width="9.4" height="1.6" fill="#fff"/>'
        '<rect x="7" y="14.2" width="9.4" height="1.6" fill="#fff"/>'
        '<rect x="7" y="17.4" width="6" height="1.6" fill="#fff"/>'
    ),
    # --- contact / chrome ---
    "pin": (
        '<path d="M12 1.6a7.4 7.4 0 0 1 7.4 7.4c0 5.4-7.4 13.4-7.4 13.4S4.6 14.4 4.6 9A7.4 7.4 0 0 1 12 1.6z"/>'
        '<circle cx="12" cy="9" r="2.8" fill="#fff"/>'
    ),
    "globe": (
        '<path d="M12 1.6A10.4 10.4 0 1 0 22.4 12 10.4 10.4 0 0 0 12 1.6zm0 2.2c1.3 0 2.9 2.6 3.4 6.9H8.6C9.1 6.4 10.7 3.8 12 3.8zM6.4 10.7H4.1A8.2 8.2 0 0 1 8.6 4.6a17 17 0 0 0-2.2 6.1zm0 2.6a17 17 0 0 0 2.2 6.1 8.2 8.2 0 0 1-4.5-6.1zm2.2 0h6.8c-.5 4.3-2.1 6.9-3.4 6.9s-2.9-2.6-3.4-6.9zm9 0h2.3a8.2 8.2 0 0 1-4.5 6.1 17 17 0 0 0 2.2-6.1zm0-2.6a17 17 0 0 0-2.2-6.1 8.2 8.2 0 0 1 4.5 6.1z"/>'
    ),
    "mail": (
        '<path d="M2 4.6h20v14.8H2z"/>'
        '<path d="M2.9 6 12 12.6 21.1 6" fill="none" stroke="#fff" stroke-width="2"/>'
    ),
    "phone": (
        '<path d="M6.6 2.4c1 0 1.6.4 2 1.4l1.4 3.4c.4 1 .2 1.7-.6 2.3l-1.3 1c1 2.3 2.8 4.1 5.1 5.2l1-1.3c.6-.8 1.3-1 2.3-.6l3.4 1.4c1 .4 1.4 1 1.4 2v2.2c0 1.4-1 2.4-2.4 2.4C10.2 21.8 2.2 13.8 2.2 4.8c0-1.4 1-2.4 2.4-2.4z"/>'
    ),
    # --- fallback ---
    "mark": (
        '<path d="M12 2.6 15 9l6.4.6-4.8 4.3 1.4 6.3L12 16.8 5.9 20.2l1.4-6.3L2.6 9.6 9 9z"/>'
    ),
}

# --- choosing a glyph ----------------------------------------------------
# ORDERED: first match wins, so the more specific phrase is listed first.
# Every pattern is matched against the lower-cased feature line.

_RULES: List[Tuple[str, str]] = [
    (r"\bflatlet|granny|cottage|(?:second|third|fourth|additional) dwelling", "flatlet"),
    (r"\bstaff quarter|staff accommodation|staff building|domestic quarter|servant", "staff"),
    (r"\bswimming pool|\bpool\b|splash", "pool"),
    (r"\bbraai|barbecue|boma", "braai"),
    (r"\blapa|thatch", "lapa"),
    (r"\bpatio|veranda|verandah|stoep|deck\b|balcon|porch|entertainment area", "patio"),
    (r"\bgarage", "garage"),
    (r"\bcarport|covered parking", "carport"),
    (r"\bparking|\bbay\b", "parking"),
    (r"\bcanopy|canopies", "canopy"),
    (r"\bworkshop|factory|industrial|packhouse|pack ?house|packing|produce", "workshop"),
    (r"\bstoreroom|storage|shelv", "storeroom"),
    (r"\boffice|reception", "office"),
    (r"\boutbuilding|shed\b|barn", "building"),
    # Agricultural lines, placed BEFORE the rules that would otherwise catch
    # them for the wrong reason (D87): a greenhouse tunnel is described with
    # "temperature/climate control" and was drawing an air conditioner, and
    # "natural grazing (2 camps with water points)" was drawing a water drop.
    (r"\bgreenhouse|\btunnels?\b|nursery|hydroponic|shade ?net", "garden"),
    (r"\bgrazing|pasture|\bveld\b|arable|\bsoils?\b|drainage|\bslopes?\b|topograph|contour", "land"),
    (r"\bbedroom|\bbed\b|\bsleeper", "bed"),
    (r"\bbathroom|\bbath\b|en-suite|ensuite|ablution", "bath"),
    (r"\bshower", "shower"),
    (r"\btoilet|\bwc\b|cloakroom|guest loo", "toilet"),
    # Kitchen before scullery: "kitchen with separate scullery" is a kitchen
    # line with a qualifier, and the reference pack draws the kitchen.
    (r"\bkitchen|kitchenette", "kitchen"),
    (r"\bscullery|\bsink\b", "scullery"),
    (r"\blaundry|washing", "laundry"),
    (r"\bstudy|\boffice nook|home office", "study"),
    # Lounge before dining: "open-plan living and dining room" is one living
    # space and takes the sofa, not a dining table.
    (r"\blounge|living|family room|tv room|open.?plan", "lounge"),
    (r"\bdining", "dining"),
    (r"\bfire ?place|\bfire pit|sunken fire|\bboiler", "fire"),
    (r"\bair.?con|climate|cold room|cold storage|refrigerat|freezer", "aircon"),
    (r"\bsolar|inverter|\bpv\b", "solar"),
    (r"\bborehole|\bwater\b|jojo|tank\b|well\b|irrigation|\bdams?\b|\bpump", "water"),
    (r"\belectric|\bpower\b|eskom|prepaid|generator|three.?phase", "power"),
    (r"\bcamera|cctv|surveillance", "camera"),
    (r"\bsecurity|alarm|guard|access control|beams|burglar", "security"),
    (r"\bgate|\bfenc|walled|palisade|boom", "gate"),
    (r"\bgarden|lawn|tree|landscap|orchard|bird|courtyard|\byard\b", "garden"),
    (r"\bview\b|mountain|sea facing|north facing", "view"),
    (r"\bstand\b|\berf\b|\bland\b|\bhectare|\bha\b|smallholding|portion", "land"),
    (r"\bm2\b|\bm²|square met|\bsize|\bextent", "area"),
    (r"\brates|levie|levy|munic", "doc"),
    (r"\bhouse|dwelling|residence|home\b|unit\b|apartment|flat\b", "house"),
]

_COMPILED = [(re.compile(pattern), name) for pattern, name in _RULES]


def icon_for(text: str) -> str:
    """Return the glyph name for a line of record text ("mark" if nothing fits)."""
    lowered = (text or "").lower()
    for pattern, name in _COMPILED:
        if pattern.search(lowered):
            return name
    return "mark"


def svg(name: str, size: str = "9mm", extra_class: str = "") -> str:
    """Return one glyph as a complete SVG element, inheriting ``currentColor``."""
    inner = ICONS.get(name) or ICONS["mark"]
    cls = f' class="{extra_class}"' if extra_class else ""
    return (
        f'<svg{cls} viewBox="0 0 24 24" width="{size}" height="{size}" '
        f'fill="currentColor" aria-hidden="true" focusable="false">{inner}</svg>'
    )


# --- shaping a feature line ----------------------------------------------

# The pack prints a bold label with a smaller qualifier under it
# ("2 BATHROOMS" / "MAIN EN-SUITE"). Record lines carry the qualifier after a
# comma, a bracket, a dash or the words "with"/"plus", so the split is on the
# FIRST of those - and only when what follows is long enough to be worth a
# second line.
# A comma BETWEEN DIGITS is a thousands separator, not a list separator, so it
# is not a place to break a line (D82). Found on a live client pack: the feature
# "Boreholes: 14,000 L (drinking water), 40,000 L (irrigation)..." split at the
# comma inside 14,000 and printed the headline "BOREHOLES: 14" - telling a buyer
# the farm has fourteen boreholes when the number is a water capacity. The
# lookarounds also protect a decimal comma ("7,5"), which SA keyboards produce.
#
# "not between digits" needs both alternatives: ``(?<!\d),(?!\d)`` alone would
# also refuse to split "Erf 1234, 1 250 m2", where the comma follows a number
# but is a list separator. A comma is protected only when a digit sits on BOTH
# sides of it.
_SPLIT = re.compile(
    r"\s*(?:(?<!\d),|,(?!\d)|\(|\bwith\b|\bplus\b|\bincluding\b|\s-\s)\s*", re.I
)


def split_label(text: str) -> Tuple[str, str]:
    """Split a feature line into (label, sub-line). The sub-line may be empty.

    Never adds a word: both halves come out of ``text``. A line short enough to
    print whole is returned as the label with no sub-line, because a one word
    qualifier under a two word label reads as a mistake rather than a detail.
    """
    line = (text or "").strip().rstrip(".")
    if len(line) <= 26:
        return line.upper(), ""
    parts = _SPLIT.split(line, maxsplit=1)
    head = parts[0].strip()
    # Brackets are dropped rather than kept: splitting inside "(bath, toilet,
    # basin)" would otherwise leave the opening bracket orphaned on the sub-line.
    tail = parts[1].replace("(", "").replace(")", "").strip() if len(parts) > 1 else ""
    if not tail or len(head) < 3:
        return line.upper(), ""
    return head.upper(), tail.upper()
