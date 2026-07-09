"""Photo extraction from the Property Report PDF (M2).

The Property Report carries the inspection photos as embedded images. This
module pulls each one out at its native resolution using PyMuPDF, rebuilding
the pixel data directly from the PDF object (``fitz.Pixmap(doc, xref)``) rather
than rasterising the page, so the saved PNG matches the source quality.

Design rules baked in here:
- Rebuild from the xref so we keep the embedded resolution, not the page's
  display size. CMYK and alpha pixmaps are converted to plain RGB before the
  PNG save, since PNG cannot carry a bare CMYK buffer.
- Each xref is saved once. A single image reused across pages (letterhead,
  logos) shares one xref, so we skip repeats to avoid duplicate files.
- Trivially small images (``min(width, height) < 100``) are dropped: those are
  icons, bullets and thin rule/letterhead strips, not property photos. Using the
  smaller side keeps the tall letterhead banner (1241x303) while dropping thin
  strips (792x91), which reproduces the Phase 0 set of 26 photos for DP3060.
- Filenames encode page, a global counter and the pixel size, e.g.
  ``p6_img14_276x207.png``, so a human can eyeball the gallery without opening
  every file.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import fitz


MIN_DIMENSION = 100  # drop an image if min(width, height) is under this


def extract_photos(pdf_path: "str | Path", out_dir: "str | Path") -> List[Path]:
    """Extract embedded images from ``pdf_path`` into ``out_dir``.

    Iterates pages in order, rebuilds each embedded image from its xref at
    source resolution, converts CMYK/alpha buffers to RGB, and saves a PNG
    named ``p{page}_img{idx:02d}_{w}x{h}.png`` (page 1-based, idx a global
    1-based counter). Duplicate xrefs and images with ``min(w, h) < 100`` are
    skipped. Returns the saved paths in page order.
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    saved: List[Path] = []
    seen_xrefs: set[int] = set()
    idx = 0

    doc = fitz.open(pdf_path)
    try:
        for page_index in range(doc.page_count):
            page = doc[page_index]
            page_no = page_index + 1  # 1-based for filenames
            for image in page.get_images(full=True):
                xref = image[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)

                pix = fitz.Pixmap(doc, xref)
                try:
                    # PNG cannot hold CMYK or a stray alpha plane, so flatten
                    # anything that is not plain RGB (or grayscale) down to RGB.
                    if pix.colorspace is None or pix.n - pix.alpha > 3:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    elif pix.alpha:
                        pix = fitz.Pixmap(pix, 0)  # drop the alpha channel

                    if min(pix.width, pix.height) < MIN_DIMENSION:
                        continue

                    idx += 1
                    name = f"p{page_no}_img{idx:02d}_{pix.width}x{pix.height}.png"
                    path = out_dir / name
                    pix.save(path)
                    saved.append(path)
                finally:
                    pix = None  # release the buffer promptly
    finally:
        doc.close()

    return saved


def _dimensions(path: Path) -> "tuple[int, int]":
    """Read the ``{w}x{h}`` embedded in a filename produced by this module."""
    stem = path.stem  # e.g. "p6_img14_276x207"
    size = stem.rsplit("_", 1)[-1]
    width, height = size.split("x")
    return int(width), int(height)


def rank_photos(paths: List[Path]) -> dict:
    """Pick a hero image and order the gallery by a simple heuristic.

    Interior-page photos ranked largest-area first make the gallery. The whole
    of page one is excluded: on the Property Report it carries the letterhead
    and the cover collage (a summary panel plus street/aerial maps), which are
    branding, not the property. The interior inspection photos begin on later
    pages, matching the Phase 0 picks (hero on page 6). Returns
    ``{"hero": Path | None, "gallery": list[Path]}``.
    """
    candidates = []
    for path in paths:
        try:
            width, height = _dimensions(path)
        except (ValueError, IndexError):
            continue
        if path.name.startswith("p1_"):
            continue  # cover page: letterhead + map collage, never a property photo
        candidates.append((path, width * height))

    gallery_ranked = sorted(candidates, key=lambda c: c[1], reverse=True)
    gallery = [c[0] for c in gallery_ranked]
    hero = gallery[0] if gallery else None
    return {"hero": hero, "gallery": gallery}
