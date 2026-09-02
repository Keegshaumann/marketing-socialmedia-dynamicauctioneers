"""Re-encoding oversized lossless photographs before they reach an artifact (D102).

A real property's pack came to 56MB, and one 4.5MB PNG aerial was most of it.
The danger is not the photographs - it is converting something that is NOT one,
because text and fine lines are what JPEG handles worst and transparency it
cannot carry at all. These tests pin what is converted and, more importantly,
what is left alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.render.photo_opt import optimised, should_convert

REPO = Path(__file__).resolve().parent.parent


def _photo(tmp_path, name, size=(1600, 1200), mode="RGB", noise=True):
    """A lossless image big enough to be a candidate."""
    from PIL import Image
    import random

    im = Image.new(mode, size, (30, 90, 140) if mode == "RGB" else (30, 90, 140, 255))
    if noise:
        px = im.load()
        rnd = random.Random(7)
        for x in range(0, size[0], 2):
            for y in range(0, size[1], 2):
                px[x, y] = ((rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                            + ((255,) if mode == "RGBA" else ()))
    path = tmp_path / name
    im.save(path)
    return path


def test_a_big_lossless_photograph_is_converted(tmp_path):
    src = _photo(tmp_path, "aerial.png")
    assert should_convert(src)
    out = optimised(src, tmp_path)
    assert out != src and out.suffix == ".jpg"
    assert out.stat().st_size < src.stat().st_size
    # The original is never touched: a wrong call must cost a setting, not a file.
    assert src.is_file() and src.stat().st_size > 0


def test_transparency_is_never_converted(tmp_path):
    """A logo or QR with an alpha channel would come out on a solid background,
    which on the dark advert reads as broken."""
    src = _photo(tmp_path, "logo.png", mode="RGBA")
    assert not should_convert(src)
    assert optimised(src, tmp_path) == src


def test_small_images_are_left_alone(tmp_path):
    """Thumbnails, marks and page extracts: the saving is irrelevant and these
    are the files most likely to be diagrams rather than photographs."""
    src = _photo(tmp_path, "thumb.png", size=(276, 207))
    assert not should_convert(src)
    assert optimised(src, tmp_path) == src


def test_a_jpeg_is_never_re_encoded(tmp_path):
    """Re-encoding an already-compressed photo adds generational loss for no
    gain - and most of the team's photographs are already JPEGs."""
    from PIL import Image

    src = tmp_path / "already.jpg"
    Image.new("RGB", (1600, 1200), (10, 20, 30)).save(src, "JPEG", quality=90)
    assert not should_convert(src)
    assert optimised(src, tmp_path) == src


def test_the_copy_is_reused_not_re_encoded(tmp_path):
    src = _photo(tmp_path, "aerial.png")
    first = optimised(src, tmp_path)
    stamp = first.stat().st_mtime_ns
    second = optimised(src, tmp_path)
    assert second == first and second.stat().st_mtime_ns == stamp


def test_replacing_the_photo_invalidates_the_copy(tmp_path):
    """The cache key carries the source's size and mtime, so a replaced
    photograph is re-encoded rather than serving the previous picture."""
    src = _photo(tmp_path, "aerial.png")
    first = optimised(src, tmp_path)
    src.unlink()
    _photo(tmp_path, "aerial.png", size=(1700, 1300))
    assert optimised(src, tmp_path) != first


def test_a_failure_returns_the_original(tmp_path):
    """Never crash a render over an image: a corrupt file must still produce an
    artifact, with the original in place."""
    src = tmp_path / "broken.png"
    src.write_bytes(b"not a png" * 200_000)
    assert optimised(src, tmp_path) == src


@pytest.mark.skipif(not (REPO / "DP3060" / "photos").is_dir(), reason="no golden photos")
def test_the_real_document_extracts_are_left_alone():
    """Measured on the actual files: the letterhead and the locality map are the
    two that would visibly suffer, and neither is a candidate."""
    photos = REPO / "DP3060" / "photos"
    for name in ("p1_img01_1241x303.png", "p1_img02_601x424.png"):
        path = photos / name
        if path.is_file():
            assert not should_convert(path), f"{name} would have been re-encoded"
