"""Turns a property's facts and photo picks into the team's files.

Each output is one HTML template at the exact size of the team's own file
(``FORMATS``). The same HTML serves the live preview in the browser (photos by
URL) and the final file (photos inlined), so what Nikki sees is what she gets.
Headless Chromium (Playwright) makes the PNGs and PDFs.

Text that is longer than the design expected shrinks to fit (``data-fit``) instead
of running off the canvas, and numbers never split from their units across a line.
"""

from __future__ import annotations

import base64
import io
import math
import os
import re
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from studio import facts as F
from studio.catalog import Item, Property
from studio.sharepoint import store

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"


@dataclass(frozen=True)
class Format:
    kind: str
    label: str
    width: int
    height: int
    pdf: bool = False
    pdf_size: tuple = ()        # points, when the PDF page differs from the px canvas
    modes: tuple = ("forsale", "auction")


FORMATS = {
    "ad": Format("ad", "Ad", 1080, 1350),
    "saia": Format("saia", "SAIA banner", 952, 239, modes=("auction",)),
    "icon": Format("icon", "App icon", 1280, 960, modes=("auction",)),
    "board": Format("board", "Board", 1200, 1600, pdf=True, pdf_size=(900, 1200)),
    "infopack": Format("infopack", "Info pack", 794, 1123, pdf=True),
}


def formats_for(mode: str) -> list[Format]:
    return [f for f in FORMATS.values() if mode in f.modes]


# --- template helpers ---------------------------------------------------------

def nb(value) -> Markup:
    """Bind numbers to their units and thousands groups with no-break spaces."""
    s = str(value or "")
    s = re.sub(r"(?<=\d) (?=\d{3}\b)", " ", s)
    s = re.sub(r"(\d) ?(m²|m2|ha|HA|Ha|HECTARES?|hectares?|km)\b", lambda m: f"{m.group(1)} {m.group(2)}", s)
    s = re.sub(r"(±) ?(?=\d)", "± ", s) if "± " in s else s
    s = re.sub(r"\bR (?=\d)", "R ", s)
    return Markup.escape(s)


_ICON_CACHE: dict[str, str] = {}


def icon(name: str, size: int = 0, height: int = 0, cls: str = "ic") -> Markup:
    """An icon ``size`` px square (or ``size`` x ``height``). "m2" and "ha" are the
    boxed unit marks the team's ads use; the rest are filled Material Symbols."""
    name = (name or "").strip().lower()
    h = height or size
    style = f' style="width:{size}px;height:{h}px;font-size:{h}px"' if size else ""
    if name in ("m2", "m²", "size"):
        return Markup(f'<span class="{cls} unit"{style}><span>m²</span></span>')
    if name in ("ha", "hectare", "land"):
        return Markup(f'<span class="{cls} unit"{style}><span>HA</span></span>')
    if name not in _ICON_CACHE:
        f = ASSETS / "icons" / f"{name}.svg"
        if not f.exists():
            f = ASSETS / "icons" / "home.svg"
        svg = f.read_text()
        svg = re.sub(r'\swidth="\d+"|\sheight="\d+"', "", svg, count=2)
        _ICON_CACHE[name] = svg.replace("<svg ", '<svg fill="currentColor" ', 1)
    return Markup(f'<span class="{cls}"{style}>{_ICON_CACHE[name]}</span>')


_MON = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}


def weekday(text: str) -> str:
    """'28 JAN 2027' -> 'THURSDAY' (the SAIA banner names the day)."""
    from datetime import date

    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\s+(\d{4})", text or "")
    if not m or m.group(2).upper() not in _MON:
        return ""
    try:
        return date(int(m.group(3)), _MON[m.group(2).upper()], int(m.group(1))).strftime("%A").upper()
    except ValueError:
        return ""


def _data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


_LOGOS: dict[str, str] = {}


def logo(which: str) -> str:
    """auctioneers-on-light | auctioneers-on-dark | realestate-on-light | realestate-on-dark"""
    if which not in _LOGOS:
        _LOGOS[which] = _data_uri(ASSETS / "logos" / f"logo-{which}.png", "image/png")
    return _LOGOS[which]


_FONT_CSS: Optional[str] = None


def font_css() -> Markup:
    global _FONT_CSS
    if _FONT_CSS is None:
        rules = []
        for weight, f in ((600, "Oswald-SemiBold.ttf"), (700, "Oswald-Bold.ttf")):
            rules.append(f"@font-face{{font-family:'Oswald Studio';font-weight:{weight};"
                         f"src:url({_data_uri(ASSETS / 'fonts' / f, 'font/ttf')}) format('truetype');}}")
        _FONT_CSS = "\n".join(rules)
    return Markup(_FONT_CSS)


def qr_svg(link: str, dark: str = "#000") -> Markup:
    import segno

    q = segno.make(link or "https://dynamicauctioneers.co.za/", error="h")
    svg = q.svg_inline(border=0, dark=dark, light=None, omitsize=True)
    return Markup(svg.replace("<svg ", '<svg style="width:100%;height:100%;display:block" ', 1))


def qr_png(link: str, white: bool) -> bytes:
    """The QR code files the team keeps beside the ads, the DS mark in the middle."""
    import segno
    from PIL import Image

    q = segno.make(link or "https://dynamicauctioneers.co.za/", error="h")
    buf = io.BytesIO()
    q.save(buf, kind="png", scale=24, border=2, dark="#ffffff" if white else "#000000", light=None)
    im = Image.open(io.BytesIO(buf.getvalue())).convert("RGBA")
    mark = Image.open(ASSETS / "logos" / ("logo-auctioneers-on-dark.png" if white else "logo-auctioneers-on-light.png")).convert("RGBA")
    side = int(im.width * 0.26)
    plate = Image.new("RGBA", (side, side), (0, 0, 0, 255) if white else (255, 255, 255, 255))
    mark.thumbnail((int(side * 0.9), int(side * 0.9)))
    plate.alpha_composite(mark, ((side - mark.width) // 2, (side - mark.height) // 2))
    im.alpha_composite(plate, ((im.width - side) // 2, (im.height - side) // 2))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


_env: Optional[Environment] = None


def env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(loader=FileSystemLoader(str(HERE / "templates")),
                           autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)
        _env.filters["nb"] = nb
        _env.globals.update(icon=icon, logo=logo, font_css=font_css, qr_svg=qr_svg, weekday=weekday)
    return _env


# --- the context a template sees ----------------------------------------------

def photo_picks(photos: dict, all_photos: list[Item]) -> dict:
    """The saved picks, with sensible picks for anything not chosen yet."""
    by_id = {p.item_id: p for p in all_photos}
    picks = {k: v for k, v in (photos or {}).items() if k != "gallery" and v in by_id}
    # Aerials: named as such, or kept in the ad folder's IMAGES / AV IMAGES, which is
    # where the team saves the Google Earth captures with the red boundary drawn on.
    aerials = [p for p in all_photos if re.search(r"AERIAL|AV IMAGE|GOOGLE|SATELLITE|BOUNDARY|MAP|^ADVERTISEMENTS?/[^/]*IMAGES/",
                                                   p.rel.upper())]
    ordinary = [p for p in all_photos if p not in aerials]
    from studio.catalog import is_asset_photo
    pool = [p for p in ordinary if not is_asset_photo(p)] or ordinary or all_photos
    if "aerial" not in picks and (aerials or all_photos):
        picks["aerial"] = (aerials or all_photos)[0].item_id
    taken = set(picks.values())
    for slot in ("hero", "photo1", "photo2"):
        if slot not in picks:
            nxt = next((p for p in pool if p.item_id not in taken), None)
            if slot == "photo1" and aerials:
                nxt = aerials[0]
            if nxt:
                picks[slot] = nxt.item_id
                taken.add(nxt.item_id)
    gallery = [g for g in (photos or {}).get("gallery", []) if g in by_id]
    if not gallery and "gallery" not in (photos or {}):
        from studio.catalog import is_asset_photo
        gallery = [p.item_id for p in ordinary if not is_asset_photo(p)][:18]
    picks["gallery"] = gallery
    return picks


def context(prop: Property, facts: dict, picks: dict, mode: str, photo_src: Callable[[str, str], str]) -> dict:
    f = dict(F.blank())
    f.update({k: v for k, v in (facts or {}).items() if v not in (None,)})

    def src(slot: str, size: str = "view") -> str:
        item_id = picks.get(slot)
        return photo_src(item_id, size) if item_id else ""

    # "gallery": small in the live preview, full size in the finished pack.
    gallery = [photo_src(i, "gallery") for i in picks.get("gallery", [])]
    auction = mode == "auction"
    when = " | ".join(x for x in (f.get("auction_date"), f.get("auction_time")) if x)
    return {
        "ref": f"DP{prop.number}", "f": f, "mode": mode, "auction": auction,
        "hero": src("hero"), "photo1": src("photo1"), "photo2": src("photo2"),
        "aerial": src("aerial") or src("hero"), "gallery": gallery,
        "auction_line": " | ".join(x for x in (f"{f.get('auction_kind') or 'ONLINE'} AUCTION", when) if x),
        "pack_pages": pack_pages(f, gallery, auction),
    }


def pack_pages(f: dict, gallery: list[str], auction: bool) -> list[dict]:
    """The info pack's page plan, so the contents page can print page numbers."""
    pages: list[dict] = []
    n = 0

    def add(kind: str, title: str = "", **kw):
        nonlocal n
        n += 1
        pages.append({"kind": kind, "title": title, "no": n, **kw})

    add("overview", "Property & Sale Overview")
    if f.get("description"):
        add("description", "Property Description")
    if f.get("portions"):
        add("details", "Property Details")
    groups = f.get("improvements") or []
    for i in range(0, len(groups), 4):
        add("improvements", "Main Improvements", groups=groups[i:i + 4], first=i == 0)
    if f.get("best_use") or f.get("condition"):
        add("use", "Highest & Best Use")
    for i in range(0, len(gallery), 6):
        add("gallery", "Photographic Overview", photos=gallery[i:i + 6], first=i == 0)
    if f.get("highlights"):
        add("highlights", "Investment Highlights")
    # Number the sections in order of first appearance, as DP2987 does.
    sec, seen = 0, {}
    for p in pages:
        if p["title"] not in seen:
            sec += 1
            seen[p["title"]] = sec
        p["section"] = seen[p["title"]]
    return pages


def toc(pages: list[dict]) -> list[tuple[int, str, str]]:
    out, seen = [], {}
    for p in pages:
        seen.setdefault(p["title"], []).append(p["no"])
    for title, nos in seen.items():
        first = next(p for p in pages if p["title"] == title)["section"]
        out.append((first, title, f"{nos[0]}" if len(nos) == 1 else f"{nos[0]}-{nos[-1]}"))
    return out


def html(kind: str, ctx: dict) -> str:
    fmt = FORMATS[kind]
    return env().get_template(f"{kind}.html.j2").render(fmt=fmt, toc=toc(ctx["pack_pages"]), **ctx)


# --- the files ----------------------------------------------------------------

_browser_lock = threading.Lock()


class Renderer:
    """One headless Chromium for a batch of files."""

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        _browser_lock.acquire()
        try:
            self._pw = sync_playwright().start()
            try:
                self._browser = self._pw.chromium.launch()
            except Exception:
                # Development Macs without Playwright's own Chromium use Chrome.
                self._browser = self._pw.chromium.launch(channel="chrome")
        except Exception:
            _browser_lock.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            self._browser.close()
            self._pw.stop()
        finally:
            _browser_lock.release()

    def _page(self, kind: str, markup: str):
        fmt = FORMATS[kind]
        page = self._browser.new_page(viewport={"width": fmt.width, "height": fmt.height}, device_scale_factor=1)
        page.set_content(markup, wait_until="load")
        page.wait_for_function("document.body.dataset.ready === '1'", timeout=20000)
        return page

    def png(self, kind: str, markup: str) -> bytes:
        fmt = FORMATS[kind]
        page = self._page(kind, markup)
        try:
            return page.screenshot(clip={"x": 0, "y": 0, "width": fmt.width, "height": fmt.height}, type="png")
        finally:
            page.close()

    def pdf(self, kind: str, markup: str) -> bytes:
        page = self._page(kind, markup)
        try:
            page.emulate_media(media="print")
            return page.pdf(prefer_css_page_size=True, print_background=True)
        finally:
            page.close()


def inline_photos(prop: Property, items_by_id: dict[str, Item]) -> Callable[[str, str], str]:
    def src(item_id: str, size: str) -> str:
        item = items_by_id.get(item_id)
        if not item:
            return ""
        size = {"view": "print", "gallery": "view"}.get(size, size)
        return "data:image/jpeg;base64," + base64.b64encode(store().image(item, size)).decode()
    return src


def build(prop: Property, facts: dict, picks: dict, mode: str, items_by_id: dict[str, Item],
          kinds: Optional[list[str]] = None) -> dict[str, bytes]:
    """The finished files, named the way the team names them."""
    ctx = context(prop, facts, picks, mode, inline_photos(prop, items_by_id))
    kinds = kinds or [f.kind for f in formats_for(mode)]
    out: dict[str, bytes] = {}
    with Renderer() as r:
        for kind in kinds:
            fmt = FORMATS[kind]
            markup = html(kind, ctx)
            out[F.name_for(prop, kind, mode)] = r.pdf(kind, markup) if fmt.pdf else r.png(kind, markup)
    if kinds == [f.kind for f in formats_for(mode)] and mode == "auction":
        link = facts.get("qr_link") or ""
        out[F.name_for(prop, "qr_black", mode)] = qr_png(link, white=False)
        out[F.name_for(prop, "qr_white", mode)] = qr_png(link, white=True)
    return out


def zip_files(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()
