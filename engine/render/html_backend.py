"""Default rendering backend: HTML / Markdown / SVG from Jinja2 templates (M5).

``HtmlBackend`` renders every format in :data:`engine.render.base.FORMATS` from the
brand-token templates in ``engine/render/templates/``. It is the platform's default
renderer (SPEC M5, D14): no credentials, always available, deterministic, offline.

Design rules baked in here:
- Backends receive **only** ``public_record`` (``PropertyRecord.public_view()``),
  plus photo paths and an optional copy dict. The POPIA internal layer is already
  gone from ``public_record``, so no artifact this backend produces can leak owner
  or occupant PII (SPEC 4.4) — the poison-marker test relies on exactly this.
- Copy precedence: values on ``request.copy`` (generated or human-edited) override
  the deterministic defaults derived from record fields, so re-renders keep human
  edits (SPEC M5). With ``copy=None`` every artifact still renders from the record.
- Facts are only rendered when the record carries them; a missing field is omitted
  rather than invented (no hallucinated facts in a client-facing artifact, SPEC 8).
- SA English, no em or en dashes, no emojis in any rendered copy.

Text formats (portal_listing, facebook_post, whatsapp_blast, email_blast) render to
``.md`` / ``.txt``; visual formats (demo_ad, info_pack, saia_banner, alert_mailer,
auction_board) to ``.html``; webapp_icon to an ``.svg`` tile. Artifacts are written
to ``<output_root>/DP<dp>/artifacts/<fmt>.<ext>``.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader

from engine.render.base import FORMATS, Artifact, RenderBackend, RenderRequest


# Real Dynamic Auctioneers brand tokens (extracted Phase 0; see DESIGN-SYSTEM.md
# and the DP3060 letterhead). These are company-level facts, safe for any artifact.
BRAND: Dict[str, str] = {
    "name": "Dynamic Auctioneers",
    "phone": "086 155 2288",
    "email": "properties.admin@dynamicauctioneers.co.za",
    "web": "dynamicauctioneers.co.za",
    "address": "187 Gouws Avenue, Raslouw AH, Centurion",
    "reg": (
        "Dynamic Solutions 1068 (Pty) Ltd T/A Dynamic Auctioneers"
        "  ·  Reg 2018/014769/07"
        "  ·  VAT 4050206442"
        "  ·  Registered with the PPRA"
        "  ·  Member: SAIA, National Auction Association"
    ),
}

# fmt -> (template file, extension, MIME type). Keys mirror base.FORMATS exactly.
_FORMAT_SPEC: Dict[str, Tuple[str, str, str]] = {
    "portal_listing": ("portal_listing.md.j2", "md", "text/markdown"),
    "facebook_post": ("facebook_post.md.j2", "md", "text/markdown"),
    "whatsapp_blast": ("whatsapp_blast.txt.j2", "txt", "text/plain"),
    "email_blast": ("email_blast.md.j2", "md", "text/markdown"),
    "demo_ad": ("demo_ad.html.j2", "html", "text/html"),
    "info_pack": ("info_pack.html.j2", "html", "text/html"),
    "webapp_icon": ("webapp_icon.svg.j2", "svg", "image/svg+xml"),
    "saia_banner": ("saia_banner.html.j2", "html", "text/html"),
    "alert_mailer": ("alert_mailer.html.j2", "html", "text/html"),
    "auction_board": ("auction_board.html.j2", "html", "text/html"),
}

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _should_autoescape(template_name: Optional[str]) -> bool:
    """Escape markup templates (HTML, SVG); leave Markdown/text unescaped."""
    if not template_name:
        return False
    return template_name.endswith((".html.j2", ".svg.j2"))


def _fmt_num(value: object) -> Optional[str]:
    """Render a number without a trailing ``.0`` (185.0 -> ``"185"``)."""
    if value is None:
        return None
    if isinstance(value, bool):  # guard: bools are ints in Python
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class HtmlBackend(RenderBackend):
    """Render each format from bundled Jinja2 templates. Always available."""

    name = "html"

    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=_should_autoescape,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    # --- backend contract ------------------------------------------------

    def available(self) -> "tuple[bool, str]":
        return (True, "html backend renders from bundled templates; no credentials required")

    def supports(self, fmt: str) -> bool:
        return fmt in _FORMAT_SPEC

    def render(self, request: RenderRequest) -> Artifact:
        """Render ``request.fmt`` for one property and write it to disk."""
        if not self.supports(request.fmt):
            raise ValueError(
                f"html backend cannot render {request.fmt!r}. "
                f"Known formats: {', '.join(sorted(_FORMAT_SPEC))}."
            )

        template_name, ext, mime = _FORMAT_SPEC[request.fmt]
        template = self._env.get_template(template_name)
        context = self._view_model(request)
        rendered = template.render(vm=context)

        art_dir = Path(request.output_root) / f"DP{request.dp}" / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        path = art_dir / f"{request.fmt}.{ext}"
        path.write_text(rendered, encoding="utf-8")

        return Artifact(
            dp=request.dp,
            fmt=request.fmt,
            backend=self.name,
            path=str(path),
            mime=mime,
        )

    # --- view model ------------------------------------------------------

    def _view_model(self, request: RenderRequest) -> dict:
        """Build the template context from ``public_record`` (+ photos + copy).

        Everything here is derived from the POPIA-safe ``public_view`` projection,
        so PII is structurally absent. Any value supplied on ``request.copy`` wins
        over the derived default, which is how human copy edits survive re-renders.
        """
        rec = request.public_record or {}
        identity = rec.get("identity") or {}
        physical = rec.get("physical") or {}
        valuation = rec.get("valuation") or {}
        sale = rec.get("sale_process") or {}
        marketing = rec.get("marketing") or {}
        viewing = sale.get("viewing") or {}
        flatlet = physical.get("flatlet") or {}

        method = sale.get("method")
        badge_label = self._badge_label(method, marketing.get("price_display"))

        flatlet_present = bool(flatlet.get("present"))
        flatlet_beds = _fmt_num(flatlet.get("bedrooms")) if flatlet_present else None

        photos = self._photo_refs(request, marketing)

        vm: dict = {
            "dp": request.dp,
            "ref": f"DP{request.dp}",
            "headline": marketing.get("headline") or "Property for sale",
            "address": identity.get("street_address"),
            "suburb": identity.get("suburb"),
            "municipality": identity.get("municipality"),
            "province": identity.get("province"),
            "scheme": identity.get("scheme"),
            "unit": _fmt_num(identity.get("unit")),
            "erf": identity.get("erf"),
            "title_type": identity.get("title_type"),
            "title_type_label": self._title_type_label(identity.get("title_type")),
            "location_line": self._location_line(identity),
            "method": method,
            "badge_label": badge_label,
            "price_display": marketing.get("price_display") or badge_label,
            "size_str": _fmt_num(physical.get("unit_size_m2")),
            "beds": _fmt_num(physical.get("bedrooms")),
            "baths": _fmt_num(physical.get("bathrooms_main_unit")),
            "separate_toilet": bool(physical.get("separate_toilet")),
            "zoning": physical.get("zoning"),
            "flatlet_present": flatlet_present,
            "flatlet_beds": flatlet_beds,
            "features_main": list(physical.get("features_main") or []),
            "features_complex": list(physical.get("features_complex") or []),
            "terms": list(sale.get("terms") or []),
            "municipal_valuation": _fmt_num(valuation.get("municipal_valuation")),
            "viewing_by_appt": bool(viewing.get("by_appointment")),
            "contact_public": viewing.get("contact_public"),
            "photos": photos,
            "hero_src": photos[0] if photos else None,
            "stack_photos": photos[1:3],
            "gallery_photos": photos[3:7],
            "brand_name": BRAND["name"],
            "brand_phone": BRAND["phone"],
            "brand_email": BRAND["email"],
            "brand_web": BRAND["web"],
            "brand_address": BRAND["address"],
            "brand_reg": BRAND["reg"],
            "generated_note": (
                "Generated automatically from the "
                f"{request.dp} Lightstone EVM report and Property Report. E&OE."
            ),
        }

        # Copy overrides (generated or human-edited) win over derived defaults, so
        # re-renders preserve edits. Only non-null values override.
        if request.copy:
            vm.update({k: v for k, v in request.copy.items() if v is not None})

        return vm

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _badge_label(method: Optional[str], price_display: Optional[str]) -> str:
        """Sale-method framing: offers vs auction (SPEC M5)."""
        if method == "offers_invited":
            return "Offers Invited"
        if method == "auction":
            return "On Auction"
        return price_display or "Enquire"

    @staticmethod
    def _title_type_label(title_type: Optional[str]) -> Optional[str]:
        return {
            "sectional": "Sectional title",
            "freehold": "Freehold",
        }.get(title_type or "", title_type)

    @staticmethod
    def _location_line(identity: dict) -> str:
        """Truthful location line from record fields only (no invented amenities)."""
        parts = [
            identity.get("suburb"),
            identity.get("municipality"),
            identity.get("province"),
        ]
        return ", ".join(p for p in parts if p)

    @staticmethod
    def _photo_refs(request: RenderRequest, marketing: dict) -> List[str]:
        """Resolve photo paths to references relative to the artifact directory.

        The record stores photo paths relative to the DP folder (``photos/x.png``);
        artifacts live one level deeper in ``DP<dp>/artifacts/``, so a record path
        resolves to ``../photos/x.png``. Absolute paths are honoured as given.
        Record picks (hero + gallery) are preferred; ``request.photos`` is the
        fallback when the record carries none.
        """
        picks: List[str] = []
        hero = marketing.get("hero_photo")
        if hero:
            picks.append(hero)
        picks.extend(marketing.get("gallery") or [])
        if not picks:
            picks = list(request.photos or [])

        art_dir = Path(request.output_root) / f"DP{request.dp}" / "artifacts"
        refs: List[str] = []
        for raw in picks:
            if not raw:
                continue
            if os.path.isabs(raw):
                candidate = Path(raw)
            else:
                # record paths are relative to the DP folder
                candidate = Path(request.output_root) / f"DP{request.dp}" / raw
            rel = os.path.relpath(str(candidate), str(art_dir))
            refs.append(str(PurePosixPath(*Path(rel).parts)))
        return refs
