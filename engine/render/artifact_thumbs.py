"""Cached PNG thumbnails for the artifact-pack tiles (D45).

The artifact pack used to show a grey icon for everything that was not already
an image, so a marketer looking at the page could not tell whether the render
had actually produced anything. This turns any rendered artifact into a small
picture of itself:

* **HTML** artifacts (advert, info pack, SAIA banner, alert mailer, auction
  board) go through the same headless-Chromium rasteriser as the ad PNG export
  (``engine.render.rasterize``), loaded from ``file://`` so their relative photo
  paths resolve on disk.
* **PDF** artifacts are rendered from page 1 with PyMuPDF, which is already a
  dependency and does not need a browser at all.

Everything is cached on disk next to the artifact in ``<artifacts>/.thumbs/``
and regenerated only when the artifact file is newer than its thumbnail, so a
given artifact version is rasterised **once**. Generation is serialised behind a
process-wide lock: a page with nine tiles must never launch nine Chromiums at
the same time on a small VPS.

Degrades honestly and quietly. If Playwright/Chromium is missing, ``thumbnail``
returns ``None`` and the caller falls back to the icon tile. If a particular
artifact fails to rasterise, a ``.fail`` marker is written beside the cache
entry so that version is not retried on every page view.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from engine.render import rasterize

# Cache folder name, created inside the DP's artifacts directory. The leading
# dot keeps it out of the way; the pack zip only walks top-level *files*, so
# thumbnails never end up in a download.
CACHE_DIRNAME = ".thumbs"

# Thumbnails are JPEGs: a nine-tile gallery of screenshots is ~280kB this way
# against ~1.3MB as PNG, at a size where the difference is invisible.
MEDIA_TYPE = "image/jpeg"
_SUFFIX = ".jpg"
_QUALITY = 85

# Thumbnail ceiling. A tile is ~260 CSS px wide, so 600 stays crisp on a 2x
# screen while keeping a nine-tile gallery light on the wire.
_MAX_W, _MAX_H = 600, 800
# A document that rasterises much taller than it is wide (the info pack is one
# long sheet) is cropped to its top so the tile shows a readable document head
# rather than an unreadable sliver.
_TALL_RATIO = 1.6
_CROP_RATIO = 1.4

_lock = threading.Lock()


def cache_dir(artifacts_dir) -> Path:
    """The thumbnail cache folder for one DP's artifacts directory."""
    return Path(artifacts_dir) / CACHE_DIRNAME


def _paths(artifacts_dir, name: str):
    cache = cache_dir(artifacts_dir)
    return cache, cache / f"{name}.png", cache / f"{name}.fail"


def _fresh(target: Path, source: Path) -> bool:
    """True when ``target`` exists and is not older than ``source``."""
    try:
        return target.exists() and target.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        return False


def _is_pdf(source: Path, mime: str) -> bool:
    return mime == "application/pdf" or source.suffix.lower() == ".pdf"


def cached(artifacts_dir, name: str, source) -> Optional[Path]:
    """Return an already-generated, still-current thumbnail, or None.

    Cheap: pure stat calls, no rendering. Used by the page so it can decide
    whether a tile has a preview without ever blocking on a rasteriser.
    """
    source = Path(source)
    _cache, out, _fail = _paths(artifacts_dir, name)
    return out if _fresh(out, source) else None


def possible(artifacts_dir, name: str, source, mime: str = "") -> bool:
    """True when a tile can show a thumbnail: one is cached, or one can be made.

    Answers without generating anything, so the artifacts page stays fast. A
    version that already failed to rasterise, and a host with no Playwright,
    both answer False, which keeps the tile on its icon fallback instead of
    firing a request that can only 503.
    """
    source = Path(source)
    if not source.exists():
        return False
    _cache, out, fail = _paths(artifacts_dir, name)
    if _fresh(out, source):
        return True
    if _fresh(fail, source):  # this version already failed; do not retry
        return False
    if _is_pdf(source, mime):
        return True
    return rasterize.available()


def thumbnail(artifacts_dir, name: str, source, mime: str = "") -> Optional[Path]:
    """Return a current thumbnail PNG for ``source``, generating it if needed.

    Returns None (never raises) when the rasteriser is unavailable or the
    artifact cannot be previewed, so callers can fall back to an icon.
    """
    source = Path(source)
    if not source.exists():
        return None
    cache, out, fail = _paths(artifacts_dir, name)
    if _fresh(out, source):
        return out
    if _fresh(fail, source):
        return None
    if not _is_pdf(source, mime) and not rasterize.available():
        return None

    with _lock:  # one rasterisation at a time, whatever the page asked for
        # Re-check inside the lock: a queued sibling request may have just
        # generated this very thumbnail while we waited.
        if _fresh(out, source):
            return out
        if _fresh(fail, source):
            return None
        try:
            cache.mkdir(parents=True, exist_ok=True)
            if _is_pdf(source, mime):
                _pdf_to_png(source, out)
            else:
                _html_to_png(source, out)
            _shrink(out)
        except rasterize.RasterizeUnavailable:
            # Environmental, not this artifact's fault: no fail marker, so the
            # tile starts previewing as soon as Chromium is installed.
            return None
        except Exception:
            try:
                out.unlink()
            except OSError:
                pass
            try:
                fail.write_text("thumbnail generation failed\n", encoding="utf-8")
            except OSError:
                pass
            return None
    return out if out.exists() else None


# --- generation -----------------------------------------------------------

# Every artifact document's root box, so the shot is the design itself and not a
# short banner marooned in a tall empty viewport. The ad roots (.sheet/.card/.ig)
# come first, then the banner, mailer and board roots. No match anywhere still
# falls back to a full-page screenshot inside the rasteriser.
_DOC_SELECTOR = ".sheet, .card, .ig, .ad, .banner, .mail, .board"


def _html_to_png(source: Path, out: Path) -> None:
    rasterize.html_to_png(source, out, timeout_ms=20000, selector=_DOC_SELECTOR)


def _pdf_to_png(source: Path, out: Path) -> None:
    """First page of a PDF as a PNG, via PyMuPDF (no browser needed)."""
    import fitz

    doc = fitz.open(str(source))
    try:
        if doc.page_count < 1:
            raise RuntimeError("empty pdf")
        pix = doc.load_page(0).get_pixmap(dpi=96)
        pix.save(str(out))
    finally:
        doc.close()


def _shrink(out: Path) -> None:
    """Crop a very tall render to its top, then scale it into the size cap."""
    from PIL import Image

    with Image.open(out) as im:
        im = im.convert("RGB")
        w, h = im.size
        if w and h > w * _TALL_RATIO:
            im = im.crop((0, 0, w, int(w * _CROP_RATIO)))
        im.thumbnail((_MAX_W, _MAX_H), Image.LANCZOS)
        im.save(out, "PNG", optimize=True)
