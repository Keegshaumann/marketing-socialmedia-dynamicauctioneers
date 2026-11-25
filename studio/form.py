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
           "viewing", "caption", "listing", "master_ref", "phone", "email", "qr_link")
LINES = ("board_lines", "board_place", "terms", "best_use")
PARAGRAPHS = ("description",)
ROWS = ("overview", "rates", "condition", "highlights")
PORTION_KEYS = ("label", "legal_description", "extent", "municipality", "zoning", "title_deed")
FEATURE_SLOTS = {"features": 6, "shared_features": 2}
# Grouped lists: a heading line ending in ":" then one "icon | text | note" per line (note optional).
GROUPS = ("improvements", "extra_units")


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
            lines += [" | ".join(x for x in (it.get("icon", ""), it.get("text", ""), it.get("note", "")) if x)
                      if it.get("note") else f"{it.get('icon', '')} | {it.get('text', '')}" for it in g.get("items") or []]
            lines.append("")
        v[k] = "\n".join(lines).strip()
    for k, n in FEATURE_SLOTS.items():
        items = [dict({"note": ""}, **it) for it in (facts.get(k) or [])]
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
    for k, n in FEATURE_SLOTS.items():
        if f"{k}_text_0" in form:
            items = []
            for i in range(n):
                text = (form.get(f"{k}_text_{i}") or "").strip()
                icon = (form.get(f"{k}_icon_{i}") or "home").strip()
                note = (form.get(f"{k}_note_{i}") or "").strip()
                if text:
                    item = {"icon": icon if icon in ICONS else "home", "text": text}
                    if note:
                        item["note"] = note
                    items.append(item)
            out[k] = items
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
        icon, item_text, note = (cells + ["", ""])[:3]
        item = {"icon": icon.lower() if icon.lower() in ICONS else "home", "text": item_text}
        if note:
            item["note"] = note
        groups[-1]["items"].append(item)
    return groups


def changed_keys(before: dict, after: dict) -> set[str]:
    return {k for k, v in after.items() if before.get(k) != v}
