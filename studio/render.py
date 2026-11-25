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
    video: bool = False         # an MP4 (the reel), REEL_SECONDS long at REEL_FPS


FORMATS = {
    "ad": Format("ad", "Ad", 1080, 1350),
    "ad2": Format("ad2", "Ad 2, big photo", 1080, 1350),   # the team's other layout (DP3100, DP3078.1)
    "saia": Format("saia", "SAIA banner", 952, 239, modes=("auction",)),
    "icon": Format("icon", "App icon", 1280, 960, modes=("auction",)),
    "board": Format("board", "Board", 1200, 1600, pdf=True, pdf_size=(900, 1200)),
    "billboard": Format("billboard", "Billboard", 3000, 2000, pdf=True, pdf_size=(2250, 1500)),   # 3 m x 2 m
    "infopack": Format("infopack", "Info pack", 794, 1123, pdf=True),
    "reel": Format("reel", "Reel", 1080, 1350, video=True),   # AD 2 animated, as the team's Canva reels
}
REEL_SECONDS, REEL_FPS = 10, 30


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


QR_DEFAULT = "https://dynamicauctioneers.co.za/"


def _qr(link: str) -> tuple[int, list[tuple[int, int]]]:
    """The code's size in modules and its dark modules, the three corner squares
    left out (they are drawn rounded, the way the team's GoHighLevel codes look)."""
    import segno

    q = segno.make(link or QR_DEFAULT, error="h")
    rows = [list(r) for r in q.matrix]
    n = len(rows)
    corners = ((0, 0), (n - 7, 0), (0, n - 7))

    def in_corner(x: int, y: int) -> bool:
        return any(cx <= x < cx + 7 and cy <= y < cy + 7 for cx, cy in corners)

    dots = [(x, y) for y, row in enumerate(rows) for x, v in enumerate(row) if v & 1 and not in_corner(x, y)]
    return n, dots


def _corners(n: int) -> tuple[tuple[int, int], ...]:
    return ((0, 0), (n - 7, 0), (0, n - 7))


def qr_svg(link: str, dark: str = "#000") -> Markup:
    n, dots = _qr(link)
    path = "".join(f"M{x},{y}h1v1h-1z" for x, y in dots)
    eyes = "".join(
        f'<path fill="{dark}" fill-rule="evenodd" d="M{cx + 2},{cy}h3a2,2 0 0 1 2,2v3a2,2 0 0 1 -2,2h-3a2,2 0 0 1 -2,-2v-3a2,2 0 0 1 2,-2z'
        f'M{cx + 2.3},{cy + 1}h2.4a1.3,1.3 0 0 1 1.3,1.3v2.4a1.3,1.3 0 0 1 -1.3,1.3h-2.4a1.3,1.3 0 0 1 -1.3,-1.3v-2.4a1.3,1.3 0 0 1 1.3,-1.3z"/>'
        f'<rect x="{cx + 2}" y="{cy + 2}" width="3" height="3" rx=".9" fill="{dark}"/>'
        for cx, cy in _corners(n))
    return Markup(f'<svg viewBox="0 0 {n} {n}" xmlns="http://www.w3.org/2000/svg" '
                  f'style="width:100%;height:100%;display:block">'
                  f'<path d="{path}" fill="{dark}" shape-rendering="crispEdges"/>{eyes}</svg>')


def qr_png(link: str, white: bool) -> bytes:
    """The QR code files the team keeps beside the ads: rounded corner squares and
    the DS mark on a rounded plate in the middle, black on clear or white on clear."""
    from PIL import Image, ImageDraw

    n, dots = _qr(link)
    big, border = 96, 2                       # drawn 4x, then scaled down smooth
    side = (n + 2 * border) * big
    ink = (255, 255, 255, 255) if white else (0, 0, 0, 255)
    clear = (0, 0, 0, 0)
    im = Image.new("RGBA", (side, side), clear)
    d = ImageDraw.Draw(im)
    o = border * big
    for x, y in dots:
        d.rectangle((o + x * big, o + y * big, o + (x + 1) * big - 1, o + (y + 1) * big - 1), fill=ink)
    for cx, cy in _corners(n):
        x0, y0 = o + cx * big, o + cy * big
        d.rounded_rectangle((x0, y0, x0 + 7 * big - 1, y0 + 7 * big - 1), radius=int(2 * big), fill=ink)
        d.rounded_rectangle((x0 + big, y0 + big, x0 + 6 * big - 1, y0 + 6 * big - 1), radius=int(1.3 * big), fill=clear)
        d.rounded_rectangle((x0 + 2 * big, y0 + 2 * big, x0 + 5 * big - 1, y0 + 5 * big - 1), radius=int(.9 * big), fill=ink)
    im = im.resize((side // 4, side // 4), Image.LANCZOS)
    mark = Image.open(ASSETS / "logos" / ("logo-auctioneers-on-dark.png" if white else "logo-auctioneers-on-light.png")).convert("RGBA")
    plate_side = int(im.width * 0.24)
    plate = Image.new("RGBA", (plate_side, plate_side), clear)
    ImageDraw.Draw(plate).rounded_rectangle((0, 0, plate_side - 1, plate_side - 1), radius=plate_side // 6,
                                            fill=(0, 0, 0, 255) if white else (255, 255, 255, 255))
    mark.thumbnail((int(plate_side * 0.84), int(plate_side * 0.84)))
    plate.alpha_composite(mark, ((plate_side - mark.width) // 2, (plate_side - mark.height) // 2))
    im.alpha_composite(plate, ((im.width - plate_side) // 2, (im.height - plate_side) // 2))
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

SLOTS = ("hero", "photo1", "photo2", "aerial")


def photo_picks(photos: dict, all_photos: list[Item]) -> dict:
    """What she placed herself, then what Claude chose when it read the folder
    (``photos["claude"]``, studio.photopick), then the studio's own rules for
    anything still open."""
    photos = photos or {}
    by_id = {p.item_id: p for p in all_photos}
    picks = {k: photos[k] for k in SLOTS if photos.get(k) in by_id}
    claude = photos.get("claude") or {}
    for k in SLOTS:
        if k not in picks and claude.get(k) in by_id:
            picks[k] = claude[k]
    # Aerials: named as such, or kept in the ad folder's IMAGES / AV IMAGES, which is
    # where the team saves the Google Earth captures with the red boundary drawn on.
    aerials = [p for p in all_photos if re.search(r"AERIA?E?L|AV IMAGE|GOOGLE|SATELLITE|BOUNDARY|MAP|^ADVERTISEMENTS?/[^/]*IMAGES/",
                                                   p.rel.upper())]
    ordinary = [p for p in all_photos if p not in aerials]
    from studio.catalog import is_asset_photo
    pool = [p for p in ordinary if not is_asset_photo(p)] or ordinary or all_photos
    if "aerial" not in picks and (aerials or all_photos):
        picks["aerial"] = (aerials or all_photos)[0].item_id
    taken = set(picks.values())
    # Claude's walk-through order (its gallery) is a better source of "the next
    # good photo" than folder order, which can put a cracked wall first.
    curated = [by_id[g] for g in claude.get("gallery", []) if g in by_id]
    for slot in ("hero", "photo1", "photo2", "photo3"):
        if slot not in picks:
            nxt = next((p for p in curated + pool if p.item_id not in taken), None)
            if slot == "photo1" and aerials and not curated:
                nxt = aerials[0]
            if nxt:
                picks[slot] = nxt.item_id
                taken.add(nxt.item_id)
    if "gallery" in photos:                      # she has added or taken out a photo
        gallery = [g for g in photos["gallery"] if g in by_id]
    else:
        gallery = [g for g in claude.get("gallery", []) if g in by_id]
        if not gallery:
            gallery = [p.item_id for p in ordinary if not is_asset_photo(p)][:24]
    picks["gallery"] = gallery
    return picks


def ad_photo_ids(picks: dict, auction: bool) -> tuple[Optional[str], list[str]]:
    """The ad's background and the (up to) three photos in the block beside the
    features, all different."""
    background = (picks.get("aerial") or picks.get("hero")) if auction else (picks.get("hero") or picks.get("aerial"))
    extras: list[str] = []
    for item_id in [picks.get("photo1"), picks.get("photo2"), picks.get("photo3"),
                    *picks.get("gallery", []), picks.get("hero"), picks.get("aerial")]:
        if item_id and item_id != background and item_id not in extras:
            extras.append(item_id)
    return background, extras[:3]


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
    _, extra_ids = ad_photo_ids(picks, auction)
    return {
        "ref": f"DP{prop.number}", "f": f, "mode": mode, "auction": auction,
        "hero": src("hero"), "photo1": src("photo1"), "photo2": src("photo2"), "photo3": src("photo3"),
        "aerial": src("aerial") or src("hero"), "gallery": gallery,
        "extras": [photo_src(i, "view") for i in extra_ids],
        "auction_line": " | ".join(x for x in (f"{f.get('auction_kind') or 'ONLINE'} AUCTION", when) if x),
        **_pack_photos(picks, photo_src, pack_pages(f, gallery, auction)),
    }


# Photos offered to fill the room a pack page has left; the template's script shows as many
# rows as the room takes (a pair, or one wide over pairs) and drops the lot when it is too
# little. The overview page shows the aerial instead.
FILL = {"description": 5, "details": 5, "improvements": 5, "movables": 5, "use": 5, "highlights": 5}


def _pack_photos(picks: dict, photo_src: Callable[[str, str], str], pages: list[dict]) -> dict:
    """Photos for the pack's spare room: the ad's photos, then the gallery, each page
    taking the next ones so no two pages show the same pair."""
    ids: list[str] = []
    for i in [picks.get("photo1"), picks.get("photo2"), picks.get("photo3"), picks.get("hero"),
              *picks.get("gallery", [])]:
        if i and i not in ids and i != picks.get("aerial"):
            ids.append(i)
    pool = [photo_src(i, "gallery") for i in ids]
    n = 0

    def take(k: int) -> list[str]:
        nonlocal n
        if not pool:
            return []
        got = [pool[(n + j) % len(pool)] for j in range(min(k, len(pool)))]
        n += k
        return got

    contents = take(5)
    for pg in pages:
        pg["fill"] = take(FILL.get(pg["kind"], 0))
    return {"pack_pages": pages, "contents_fill": contents, "back_photo": (take(1) or [""])[0]}


MOVABLES_PER_PAGE = 36      # two columns of 18 rows


def _improvement_pages(groups: list[dict]) -> list[list[dict]]:
    """The improvement cards, two side by side, as many rows of them as fit a page."""
    pages, cur, used = [], [], 0
    for i in range(0, len(groups), 2):
        pair = groups[i:i + 2]
        need = 64 + 40 * max(min(len(g.get("items") or []), 10) for g in pair)
        if cur and used + need > 840:
            pages.append(cur)
            cur, used = [], 0
        cur += pair
        used += need
    if cur:
        pages.append(cur)
    return pages


# Gallery pages take turns: one big photo over four, an even six, a tall one beside two.
_GALLERY_TURNS = (("big", 5), ("grid", 6), ("side", 5))


def gallery_pages(gallery: list[str]) -> list[tuple[str, list[str]]]:
    out, i, turn = [], 0, 0
    while i < len(gallery):
        layout, n = _GALLERY_TURNS[turn % len(_GALLERY_TURNS)]
        photos = gallery[i:i + n]
        out.append((layout if len(photos) == n else f"n{len(photos)}", photos))
        i += n
        turn += 1
    return out


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
    for k, groups in enumerate(_improvement_pages(f.get("improvements") or [])):
        add("improvements", "Main Improvements", groups=groups, first=k == 0)
    movables = f.get("movables") or []
    for i in range(0, len(movables), MOVABLES_PER_PAGE):
        add("movables", "Movable Assets Included", items=movables[i:i + MOVABLES_PER_PAGE], start=i + 1,
            first=i == 0)
    if f.get("best_use") or f.get("condition"):
        add("use", "Highest & Best Use")
    for k, (layout, photos) in enumerate(gallery_pages(gallery)):
        add("gallery", "Photographic Overview", photos=photos, layout=layout, first=k == 0)
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

    def video(self, kind: str, markup: str, seconds: float = REEL_SECONDS, fps: int = REEL_FPS) -> bytes:
        """Step every animation on the page frame by frame and encode the frames as an
        H.264 MP4 (silent, yuv420p, faststart: plays everywhere, posts everywhere)."""
        import subprocess
        import tempfile

        import imageio_ffmpeg

        fmt = FORMATS[kind]
        page = self._page(kind, markup)
        try:
            page.evaluate("() => document.getAnimations().forEach(a => a.pause())")
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "reel.mp4")
                enc = subprocess.Popen(
                    [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "image2pipe",
                     "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                     "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
                    stdin=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    for n in range(int(seconds * fps)):
                        page.evaluate("t => document.getAnimations().forEach(a => { a.currentTime = t; })",
                                      n * 1000.0 / fps)
                        enc.stdin.write(page.screenshot(type="jpeg", quality=92,
                                                        clip={"x": 0, "y": 0, "width": fmt.width, "height": fmt.height}))
                    enc.stdin.close()
                    err = enc.stderr.read().decode(errors="replace")
                    if enc.wait() != 0:
                        raise RuntimeError(f"ffmpeg failed: {err[-400:]}")
                finally:
                    if enc.poll() is None:
                        enc.kill()
                with open(out, "rb") as fh:
                    return fh.read()
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
          kinds: Optional[list[str]] = None, with_qr: Optional[bool] = None) -> dict[str, bytes]:
    """The finished files, named the way the team names them. ``with_qr``: add the
    two QR code files (an auction's whole set does, by default)."""
    ctx = context(prop, facts, picks, mode, inline_photos(prop, items_by_id))
    whole = kinds is None
    kinds = kinds or [f.kind for f in formats_for(mode)]
    out: dict[str, bytes] = {}
    with Renderer() as r:
        for kind in kinds:
            fmt = FORMATS[kind]
            markup = html(kind, ctx)
            out[F.name_for(prop, kind, mode)] = (r.video(kind, markup) if fmt.video else
                                                 r.pdf(kind, markup) if fmt.pdf else r.png(kind, markup))
    if (whole if with_qr is None else with_qr) and mode == "auction":
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
