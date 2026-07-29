"""HTML ad-template library (D41).

The marketer picks an ad *design* per property from a library of HTML templates.
Every template is a self-contained Jinja2 file that fills from the SAME view
model the html backend builds (headline, price, photos, stat bar, features,
terms, contact), so a design uses whatever slots it has and ignores the rest.

Adding a design is just dropping a ``<name>.html.j2`` file into
``templates/ads/`` (optionally with a ``{# name: Nice Name #}`` first line for
the display label); it is auto-discovered here, appears in the gate-2 picker,
and gets a thumbnail. The current one-pager (``demo_ad.html.j2``) is the built-in
default, shown as "Classic".

The pick is stored on ``marketing.template_set`` (the same field the Canva
template sets used, D33) and flows to the backend on ``RenderRequest.template_set``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_ADS_DIR = _TEMPLATE_DIR / "ads"

# The built-in default design: the original one-pager, kept in place (not moved
# into ads/) so nothing that references it breaks. It is always offered as
# "Classic" and is what an empty/unknown pick falls back to.
DEFAULT_ID = "classic"
_DEFAULT_TEMPLATE = "demo_ad.html.j2"


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
    return sorted(_ADS_DIR.glob("*.html.j2"))


def list_templates() -> List[Dict[str, str]]:
    """Every selectable ad design as ``{"id", "name", "template"}``, Classic first."""
    out: List[Dict[str, str]] = [
        {"id": DEFAULT_ID, "name": "Classic", "template": _DEFAULT_TEMPLATE}
    ]
    for path in _library_paths():
        tid = _template_id(path)
        if tid == DEFAULT_ID:
            continue  # never shadow the built-in default
        out.append({"id": tid, "name": _display_name(path), "template": f"ads/{path.name}"})
    return out


def template_ids() -> set:
    return {t["id"] for t in list_templates()}


def resolve(template_id: Optional[str]) -> str:
    """Return the Jinja template name for a pick, falling back to Classic.

    An unknown or empty pick (e.g. a design later removed from the library)
    degrades to the default rather than failing the render.
    """
    if not template_id or template_id == DEFAULT_ID:
        return _DEFAULT_TEMPLATE
    path = _ADS_DIR / f"{template_id}.html.j2"
    return f"ads/{path.name}" if path.is_file() else _DEFAULT_TEMPLATE
