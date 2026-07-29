"""Thumbnail previews for the ad-template picker (D41).

Renders an ad template with fixed SAMPLE data (no real property) and rasterises
it to a small PNG so the gate-2 gallery can show what each design looks like.
Cached per template, regenerated when the template file is newer. Returns None if
the rasteriser (headless Chromium) is unavailable, so the picker degrades to
name-only tiles rather than crashing.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from engine.render import ad_templates
from engine.render.html_backend import BRAND, _asset_data_uri, _split3
from engine.render.rasterize import available, html_to_png

_TEMPLATE_DIR = ad_templates._TEMPLATE_DIR
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "svg", "j2"]),
)
_env.globals["asset_uri"] = _asset_data_uri
_env.filters["split3"] = _split3

_placeholder_cache: Optional[str] = None


def _placeholder_photo() -> str:
    """A small solid warm-grey JPEG as a data URI, standing in for a photo."""
    global _placeholder_cache
    if _placeholder_cache is None:
        from PIL import Image

        im = Image.new("RGB", (240, 180), (116, 111, 100))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=60)
        _placeholder_cache = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return _placeholder_cache


def _sample_vm() -> dict:
    photo = _placeholder_photo()
    return {
        "headline": "3 Bedroom Family Home in Sandton",
        "place_line": "Sandton, Johannesburg",
        "descriptor_line": "3 Bedroom Home",
        "public_ref": "T1234/2024",
        "property_ref": "DP0000",
        "auction_type": "Insolvency",
        "auction_channel": "Online",
        "auction_date": "20 Aug 2026",
        "auction_time": "10:00",
        "address": "12 Example Road, Sandton",
        "suburb": "Sandton",
        "municipality": "City of Johannesburg",
        "province": "Gauteng",
        "location_line": "Sandton, City of Johannesburg, Gauteng",
        "method": "offers_invited",
        "badge_label": "OFFERS INVITED",
        "price_display": "R2 950 000",
        "size_str": "240",
        "beds": "3",
        "baths": "2",
        "garages": "2",
        "scheme": "Sandton Estate",
        "separate_toilet": True,
        "zoning": "Residential",
        "title_type": "freehold",
        "title_type_label": "Freehold",
        "flatlet_present": True,
        "flatlet_beds": "1",
        "features_main": [
            "Open-plan living and dining area",
            "Modern kitchen with gas hob",
            "Main bedroom with en-suite",
            "North-facing covered patio",
        ],
        "features_complex": [
            "24-hour estate security",
            "Communal swimming pool",
            "Landscaped communal gardens",
        ],
        "terms": [
            "10% deposit on the fall of the hammer",
            "R50 000 refundable registration deposit",
        ],
        "municipal_valuation": "2 400 000",
        "viewing_by_appt": True,
        "contact_public": BRAND["phone"],
        "hero_src": photo,
        "stack_photos": [photo, photo],
        "gallery_photos": [photo, photo, photo, photo],
        "photos": [photo] * 7,
        "brand_name": BRAND["name"],
        "brand_phone": BRAND["phone"],
        "brand_email": BRAND["email"],
        "brand_web": BRAND["web"],
        "brand_address": BRAND["address"],
        "brand_reg": BRAND["reg"],
        "generated_note": "Design preview - sample data.",
    }


def thumbnail(template_id: str, cache_root: str) -> Optional[Path]:
    """Return a cached thumbnail PNG path for ``template_id`` (generating it if
    missing/stale), or None if the rasteriser is unavailable."""
    template_name = ad_templates.resolve(template_id)
    template_file = _TEMPLATE_DIR / template_name
    cache = Path(cache_root) / ".ad-thumbs"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{template_id}.png"

    if out.exists() and template_file.exists() and out.stat().st_mtime >= template_file.stat().st_mtime:
        return out
    if not available():
        return None

    html = _env.get_template(template_name).render(vm=_sample_vm())
    tmp = cache / f"{template_id}.sample.html"
    tmp.write_text(html, encoding="utf-8")
    try:
        html_to_png(tmp, out)  # default viewport fits both A4-ish and IG-format ads
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return out if out.exists() else None
