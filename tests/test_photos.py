"""Photo-extraction tests (M2).

Runs the real extractor against the real DP3060 Property Report and checks that
it yields source-quality PNGs with the agreed filename shape. Skipped with a
clear reason when the sample PDF is absent.
"""

from __future__ import annotations

import re

from engine.photos import extract_photos, rank_photos


# p{page}_img{idx:02d}_{w}x{h}.png, page 1-based, idx zero-padded 2-digit.
NAME_RE = re.compile(r"^p(\d+)_img(\d{2})_(\d+)x(\d+)\.png$")


def test_extract_photos_yields_source_quality_pngs(property_report_3060, tmp_path):
    out_dir = tmp_path / "photos"
    paths = extract_photos(property_report_3060, out_dir)

    count = len(paths)
    print(f"\nextract_photos(DP3060 Property Report) -> {count} PNG(s)")

    assert count > 0
    # Every returned path exists, is a PNG, and follows the naming pattern.
    for path in paths:
        assert path.exists()
        assert path.parent == out_dir
        match = NAME_RE.match(path.name)
        assert match is not None, f"bad filename: {path.name}"
        width, height = int(match.group(3)), int(match.group(4))
        # The extractor drops trivially small images (< 100 on the long edge).
        assert max(width, height) >= 100


def test_rank_photos_picks_a_hero(property_report_3060, tmp_path):
    paths = extract_photos(property_report_3060, tmp_path / "photos")
    ranked = rank_photos(paths)

    assert set(ranked) == {"hero", "gallery"}
    assert ranked["hero"] is not None
    assert ranked["hero"] in ranked["gallery"]
    # The hero is the largest-area non-banner image, so it heads the gallery.
    assert ranked["gallery"][0] == ranked["hero"]
