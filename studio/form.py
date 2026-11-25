"""The editing form: every fact as a plain field Nikki can type in.

Lists are one item per line; tables are "LABEL | text" per line; improvements are
a heading line ending in ":" followed by "icon | text" lines. Plain text keeps the
form short and lets her paste from Word or an email.
"""

from __future__ import annotations

from typing import Any

from studio.facts import ICONS

STRINGS = ("headline", "subline", "headline2", "badge", "address", "disclaimer", "price", "main_title",
           "auction_kind", "auction_date", "auction_time", "short_title", "short_place", "sale_kind",
           "location_line", "pack_title", "pack_region", "pack_strapline", "pack_footer", "confirmation",
           "viewing", "caption", "listing", "master_ref", "phone", "email", "qr_link", "map_pin")
LINES = ("board_lines", "board_place", "terms", "best_use", "movables")
PARAGRAPHS = ("description",)
ROWS = ("overview", "rates", "condition", "highlights", "sale_info", "particulars", "conditions", "fica")
PORTION_KEYS = ("label", "legal_description", "extent", "municipality", "zoning", "title_deed")
FEATURE_SLOTS = {"features": 6, "shared_features": 2}
# Grouped lists: a heading line ending in ":" then one "icon | text | note" per line (note optional).
GROUPS = ("improvements", "extra_units")
# The pack's own sections and tables (docs/info-pack-guide.md): a heading ending in ":" then
# one line each; in a table the first line after the heading is the column headings.
SECTIONS = ("sections",)
TABLES = ("tables",)


# What each field is called on the studio's page (studio_edit.html), so a request in her
# own words ("the gold line", "the price box") reaches the right field (studio.ask).
LABELS = {
    "headline": "Gold line (ad heading line 1)", "subline": "White line (ad heading line 2)",
    "headline2": "Second gold line (ad heading line 3)", "badge": "Badge (the sale badge on the ad)",
    "address": "Address strip (for sale ad)", "features": "Features (the ad's feature list, main dwelling)",
    "main_title": "Main dwelling heading", "extra_units": "Flats and cottages (their own feature boxes)",
    "shared_features": "For the whole property (size first, then anything shared)",
    "disclaimer": "Occupation line", "price": "Price box", "auction_kind": "Online or on site",
    "auction_date": "Auction date", "auction_time": "Auction time", "master_ref": "Master ref",
    "phone": "Phone", "email": "Email", "short_title": "One-line title (banner, app icon, reel)",
    "short_place": "One-line place (banner, app icon, reel)", "sale_kind": "Sale type",
    "board_lines": "Board: what it is (2 lines)", "board_place": "Board: where (1-2 lines)",
    "location_line": "App icon: location line", "qr_link": "QR code link",
    "pack_title": "Info pack cover title", "pack_region": "Info pack cover region",
    "pack_strapline": "Info pack cover strap", "pack_footer": "Info pack cover footer",
    "viewing": "Viewing", "overview": "Info pack: property overview table",
    "rates": "Info pack: rates & taxes", "terms": "Info pack: sale overview (from the OTP)",
    "confirmation": "Info pack: confirmation line", "description": "Info pack: description",
    "portions": "Info pack: property details", "improvements": "Info pack: main improvements",
    "movables": "Info pack: movable assets in the sale",
    "best_use": "Info pack: highest & best use", "condition": "Info pack: property condition",
    "highlights": "Info pack: investment highlights", "caption": "Social caption",
    "sale_info": "Info pack: auction / sale information table", "particulars": "Info pack: property particulars & location",
    "map_pin": "Info pack: map pin (latitude, longitude)", "tables": "Info pack: breakdown tables (land use, orchards, units)",
    "sections": "Info pack: extra sections (water, infrastructure, going concern)",
    "conditions": "Info pack: summary of sale conditions", "fica": "Info pack: registration & FICA requirements",
    "listing": "Portal listing (Propdata, Property24)",
}


def _rows_text(rows: list[dict]) -> str:
    return "\n".join(f"{r.get('label', '')} | {r.get('text', '')}" for r in rows or [])


def _parse_rows(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        label, _, rest = line.partition("|")
        out.append({"label": label.strip(), "text": rest.strip()})
    return out


def to_form(facts: dict) -> dict[str, Any]:
    """Values for the form's fields."""
    v: dict[str, Any] = {k: facts.get(k, "") or "" for k in STRINGS}
    for k in LINES:
        v[k] = "\n".join(facts.get(k) or [])
    for k in PARAGRAPHS:
        v[k] = "\n\n".join(facts.get(k) or [])
    for k in ROWS:
        v[k] = _rows_text(facts.get(k))
    v["portions"] = "\n".join(" | ".join(p.get(k, "") for k in PORTION_KEYS) for p in facts.get("portions") or [])
    for k in GROUPS:
        lines = []
        for g in facts.get(k) or []:
            lines.append(f"{g.get('title', '')}:")
            lines += [f"{it.get('icon', '')} | {it.get('text', '')}" for it in g.get("items") or []]
            lines.append("")
        v[k] = "\n".join(lines).strip()
    for k in SECTIONS:
        v[k] = "\n\n".join("\n".join([f"{s.get('title', '')}:"] + list(s.get("lines") or []))
                             for s in facts.get(k) or []).strip()
    for k in TABLES:
        v[k] = "\n\n".join("\n".join([f"{t.get('title', '')}:", " | ".join(t.get("columns") or [])]
                                       + [" | ".join(r) for r in t.get("rows") or []])
                             for t in facts.get(k) or []).strip()
    for k, n in FEATURE_SLOTS.items():
        items = []
        for it in facts.get(k) or []:            # "TEXT | small note" shows as two boxes
            text, _, note = (it.get("text") or "").partition(" | ")
            items.append({"icon": it.get("icon", ""), "text": text, "note": note})
        items += [{"icon": "", "text": "", "note": ""}] * (n - len(items))
        v[k] = items[:n]
    return v


def from_form(form: dict[str, str]) -> dict:
    """Facts from a submitted form (only the keys present are returned)."""
    out: dict = {}
    for k in STRINGS:
        if k in form:
            out[k] = (form.get(k) or "").strip()
    for k in LINES:
        if k in form:
            out[k] = [x.strip() for x in (form.get(k) or "").splitlines() if x.strip()]
    for k in PARAGRAPHS:
        if k in form:
            out[k] = [p.strip().replace("\n", " ") for p in (form.get(k) or "").split("\n\n") if p.strip()]
    for k in ROWS:
        if k in form:
            out[k] = _parse_rows(form.get(k) or "")
    if "portions" in form:
        portions = []
        for line in (form.get("portions") or "").splitlines():
            if line.strip():
                cells = [c.strip() for c in line.split("|")] + [""] * 6
                portions.append(dict(zip(PORTION_KEYS, cells[:6])))
        out["portions"] = portions
    for k in GROUPS:
        if k in form:
            out[k] = _parse_groups(form.get(k) or "", "IMPROVEMENTS" if k == "improvements" else "FLAT")
    for k in SECTIONS:
        if k in form:
            out[k] = [{"title": t, "lines": body} for t, body in _blocks(form.get(k) or "") if body]
    for k in TABLES:
        if k in form:
            tables = []
            for t, body in _blocks(form.get(k) or ""):
                if len(body) >= 2:
                    cols = [c.strip() for c in body[0].split("|")]
                    rows = [([c.strip() for c in line.split("|")] + [""] * len(cols))[:len(cols)] for line in body[1:]]
                    tables.append({"title": t, "columns": cols, "rows": rows})
            out[k] = tables
    for k, n in FEATURE_SLOTS.items():
        if f"{k}_text_0" in form:
            items = []
            for i in range(n):
                text = (form.get(f"{k}_text_{i}") or "").strip()
                icon = (form.get(f"{k}_icon_{i}") or "home").strip()
                note = (form.get(f"{k}_note_{i}") or "").strip()
                if text:
                    items.append({"icon": icon if icon in ICONS else "home",
                                  "text": f"{text} | {note}" if note else text})
            out[k] = items
    return out


def _blocks(text: str) -> list[tuple[str, list[str]]]:
    """A heading line ending in ":" (no "|") then the lines under it."""
    out: list[tuple[str, list[str]]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith(":") and "|" not in line:
            out.append((line[:-1].strip(), []))
        elif out:
            out[-1][1].append(line)
        else:
            out.append(("", [line]))
    return out


def _parse_groups(text: str, first: str) -> list[dict]:
    groups: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith(":") and "|" not in line:
            groups.append({"title": line[:-1].strip(), "items": []})
            continue
        if not groups:
            groups.append({"title": first, "items": []})
        cells = [c.strip() for c in line.split("|")]
        if len(cells) == 1:
            cells = ["home"] + cells
        icon, item_text = cells[0], " | ".join(c for c in cells[1:] if c)
        groups[-1]["items"].append({"icon": icon.lower() if icon.lower() in ICONS else "home", "text": item_text})
    return groups


def changed_keys(before: dict, after: dict) -> set[str]:
    return {k for k, v in after.items() if before.get(k) != v}
