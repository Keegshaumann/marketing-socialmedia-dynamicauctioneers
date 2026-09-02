"""Re-encode oversized lossless photographs before they go into an artifact (D102).

A real property's pack came to **56MB**, and one file was most of it: a 4.5MB
PNG aerial photograph, embedded at full resolution to be displayed 307 points
wide. JPEG photographs pass through cheaply (a 1600x1200 WhatsApp picture costs
about 400KB); a PHOTOGRAPH SAVED AS PNG cannot compress, so it goes in whole.
At 56MB the pack cannot be emailed and "Email the ad" is unusable.

The risk in converting is not the photographs - measured on the real aerial,
quality 90 costs an average error of 3.1 per channel out of 255 and saves 83%.
The risk is converting something that is NOT a photograph: text and fine lines
are exactly what JPEG handles worst, and a logo or QR code with transparency
would come out on a solid background.

So this does not try to tell a photograph from a diagram. Colour counts do not
separate them honestly - the DA letterhead has 11,663 distinct colours and a
room photograph has 16,227 - and a classifier that is wrong once puts a fuzzy
site plan in front of a buyer. Instead it converts only what is unambiguously
a big photographic file and demonstrably better off converted:

* **never** when the image has an alpha channel (a logo, a QR, a cut-out)
* **never** below ``_MIN_PIXELS`` - small images are thumbnails, marks and
  document extracts, and the saving would be irrelevant anyway
* **never** below ``_MIN_BYTES`` - if it is already small it is not the problem
* **never** if the JPEG does not actually come out smaller

The original file is never touched or replaced: the copy lives beside it in a
cache folder, so a wrong call costs one setting rather than a re-upload, and
the marketer's upload remains the master.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

# A photograph big enough for this to matter. Below these, a file is a
# thumbnail, an icon, a QR or a page extract - none of which we touch.
_MIN_PIXELS = 1_200_000        # ~1.2 megapixels (a 1600x1200 photo is 1.92)
_MIN_BYTES = 900 * 1024        # under this, the saving is not worth the risk
_QUALITY = 90                  # measured: avg error 3.1/255 on the real aerial

# Only lossless formats are candidates. A JPEG is already compressed and
# re-encoding it would add generational loss for no gain.
_LOSSLESS = {".png", ".bmp", ".tif", ".tiff"}

CACHE_DIR = ".photo-cache"


def _cache_name(path: Path) -> str:
    """A name that changes when the source does, so a replaced photo re-encodes."""
    stat = path.stat()
    key = f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"{path.stem}-{digest}.jpg"


def should_convert(path: Path) -> bool:
    """Whether this file is a big lossless photograph worth re-encoding."""
    if path.suffix.lower() not in _LOSSLESS:
        return False
    try:
        if path.stat().st_size < _MIN_BYTES:
            return False
    except OSError:
        return False
    try:
        from PIL import Image

        with Image.open(path) as im:
            # Transparency is the one property JPEG cannot carry at all.
            if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
                return False
            return (im.size[0] * im.size[1]) >= _MIN_PIXELS
    except Exception:
        return False


def optimised(path: "str | Path", cache_root: "str | Path") -> Path:
    """Return the file to embed: a cached JPEG copy, or the original unchanged.

    Never raises and never modifies the source. Any failure - Pillow missing, an
    unreadable file, a directory that cannot be written - returns the original,
    so a render always has a photograph to place.
    """
    src = Path(path)
    if not should_convert(src):
        return src

    try:
        cache = Path(cache_root) / CACHE_DIR
        cache.mkdir(parents=True, exist_ok=True)
        dst = cache / _cache_name(src)
        if dst.is_file():
            return dst

        from PIL import Image

        with Image.open(src) as im:
            im.convert("RGB").save(
                dst, "JPEG", quality=_QUALITY, optimize=True, progressive=True
            )
        # Only worth it if it is actually smaller: a screenshot-like PNG can
        # encode LARGER as a JPEG, and shipping that would be worse than doing
        # nothing at all.
        if dst.stat().st_size >= src.stat().st_size:
            dst.unlink(missing_ok=True)
            return src
        return dst
    except Exception:
        return src


def savings(path: "str | Path", cache_root: "str | Path") -> Optional[tuple]:
    """``(before, after)`` in bytes when a file was converted, else ``None``.
    Reporting only - the render path does not need it."""
    src = Path(path)
    out = optimised(src, cache_root)
    if out == src:
        return None
    return (src.stat().st_size, out.stat().st_size)
