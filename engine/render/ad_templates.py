"""HTML ad-template library (D41).

The marketer picks an ad *design* per property from a library of HTML templates.
Every template is a self-contained Jinja2 file that fills from the SAME view
model the html backend builds (headline, price, photos, stat bar, features,
terms, contact), so a design uses whatever slots it has and ignores the rest.

Adding a design is just dropping a ``<name>.html.j2`` file into
``templates/ads/`` (optionally with a ``{# name: Nice Name #}`` first line for
the display label); it is auto-discovered here, appears in the gate-2 picker,
and gets a thumbnail. An empty/unknown pick falls back to the ``DEFAULT_ID``
design (the Hero-overlay social ad, D49).

The pick is stored on ``marketing.template_set`` (the same field the Canva
template sets used, D33) and flows to the backend on ``RenderRequest.template_set``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_ADS_DIR = _TEMPLATE_DIR / "ads"

# The default design an empty/unknown pick falls back to (D49). It is a real
# dark social ad from the library: the previous white "Classic" one-pager did
# not match the team's Canva ads, so an un-picked property now renders the
# Hero-overlay design. Legacy picks ("classic", "bold", or any removed design)
# degrade here too rather than failing the render.
DEFAULT_ID = "hero_overlay"
_DEFAULT_TEMPLATE = "ads/hero_overlay.html.j2"


_SUFFIX = ".html.j2"


def _template_id(path: Path) -> str:
    """The clean id from a template filename (Path.stem only strips .j2)."""
    name = path.name
    return name[: -len(_SUFFIX)] if name.endswith(_SUFFIX) else path.stem


def _pretty(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").strip().title()


def _display_name(path: Path) -> str:
    """A ``{# name: ... #}`` first line overrides the prettified filename."""
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return _pretty(path.stem)
    if "name:" in first:
        return first.split("name:", 1)[1].replace("#}", "").replace("#", "").strip() or _pretty(path.stem)
    return _pretty(path.stem)


def _library_paths() -> List[Path]:
    if not _ADS_DIR.is_dir():
        return []
    # Skip "_"-prefixed partials (shared macros/includes), which are not
    # standalone, pickable designs.
    return sorted(p for p in _ADS_DIR.glob("*.html.j2") if not p.name.startswith("_"))


def list_templates() -> List[Dict[str, str]]:
    """Every selectable ad design as ``{"id", "name", "template"}``, default first."""
    out: List[Dict[str, str]] = []
    for path in _library_paths():
        tid = _template_id(path)
        out.append({"id": tid, "name": _display_name(path), "template": f"ads/{path.name}"})
    # Offer the default design first; the rest follow alphabetically by name.
    out.sort(key=lambda t: (t["id"] != DEFAULT_ID, t["name"].lower()))
    return out


def template_ids() -> set:
    return {t["id"] for t in list_templates()}


def resolve(template_id: Optional[str]) -> str:
    """Return the Jinja template name for a pick, falling back to Classic.

    An unknown or empty pick (e.g. a design later removed from the library)
    degrades to the default rather than failing the render.
    """
    if not template_id:
        return _DEFAULT_TEMPLATE
    path = _ADS_DIR / f"{template_id}.html.j2"
    return f"ads/{path.name}" if path.is_file() else _DEFAULT_TEMPLATE


def variation_ids(picked: Optional[str], count: int = 2) -> List[str]:
    """The other designs to render beside ``picked`` (fix list 2.1).

    The library in order, starting after the marketer's pick and wrapping, so
    three variations are always three DIFFERENT designs and the set is stable
    for a given pick rather than shuffling on every render.
    """
    # template_ids() is a SET; the rotation has to be stable or the same pick
    # would yield different alternatives on different renders.
    ids = sorted(template_ids())
    if not ids:
        return []
    current = picked if picked in ids else DEFAULT_ID
    start = ids.index(current) if current in ids else 0
    rotated = ids[start + 1:] + ids[:start]
    return rotated[:count]
