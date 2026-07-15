"""Distribution tests (M6, Phase 5A).

All offline and credential-free. Records are built in code (no sample PDFs), so
the suite runs on a clean checkout. Covers the routing matrix for the four SPEC
5.6 cases (< R10m, >= R10m, industrial, Private Property excluded), a ready-to-post
manual pack, the per-DP/channel/version status log, a price-drop burst firing only
on a genuine decrease, and the GHL Social Planner scaffold returning a pack (never
raising) with no token configured.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.distribute.ghl import post_to_planner
from engine.distribute.packs import (
    build_manual_pack,
    list_status,
    log_posted,
    price_drop_burst,
)
from engine.distribute.routing import channel_matrix
from engine.render.base import Artifact
from engine.schema import Identity, Marketing, Physical, PropertyRecord

# The five channels every property routes to (SPEC 5.6 row 1).
STANDARD_CHANNELS = (
    "property24",
    "own_website",
    "facebook",
    "email_list",
)


# --- record builders -----------------------------------------------------

def _record(
    dp: str = "3060",
    *,
    price_display: str = "R2 500 000",
    zoning: str = "Residential",
    suburb: str = "Pelham North",
) -> PropertyRecord:
    return PropertyRecord(
        dp=dp,
        identity=Identity(title_type="sectional", suburb=suburb),
        physical=Physical(zoning=zoning),
        marketing=Marketing(price_display=price_display),
    )


def _artifact_file(tmp_path: Path, fmt: str, body: str, ext: str = "md") -> Artifact:
    """Write a real artifact file and return the Artifact pointing at it."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(exist_ok=True)
    path = src_dir / f"{fmt}.{ext}"
    path.write_text(body, encoding="utf-8")
    mime = "text/markdown" if ext == "md" else "text/plain"
    return Artifact(dp="3060", fmt=fmt, backend="html", path=str(path), mime=mime)


# --- channel_matrix (SPEC 5.6, D5) ---------------------------------------

def test_channel_matrix_standard_under_r10m():
    matrix = channel_matrix(_record(price_display="R2 500 000"))
    for channel in STANDARD_CHANNELS:
        assert matrix[channel] is True
    # Below the luxury threshold: JamesEdition off.
    assert matrix["jamesedition"] is False
    # Residential: no commercial portals.
    assert matrix["commercial_portals"] is False
    # Policy exclusion (D5).
    assert matrix["private_property"] is False


def test_channel_matrix_luxury_adds_jamesedition():
    matrix = channel_matrix(_record(price_display="R12 500 000"))
    assert matrix["jamesedition"] is True
    # The standard channels still route.
    for channel in STANDARD_CHANNELS:
        assert matrix[channel] is True
    assert matrix["private_property"] is False


def test_channel_matrix_industrial_adds_commercial_portals():
    matrix = channel_matrix(_record(zoning="Industrial", price_display="R5 000 000"))
    assert matrix["commercial_portals"] is True
    # Industrial below R10m: still no JamesEdition.
    assert matrix["jamesedition"] is False
    assert matrix["private_property"] is False


def test_channel_matrix_always_excludes_private_property():
    for rec in (
        _record(price_display="R2 500 000"),
        _record(price_display="R12 500 000"),
        _record(zoning="Industrial", price_display="R30 000 000"),
    ):
        assert channel_matrix(rec)["private_property"] is False


# --- ready-to-post manual pack -------------------------------------------

def test_build_manual_pack_writes_folder_and_checklist(tmp_path):
    artifacts = [
        _artifact_file(tmp_path, "portal_listing", "Portal copy here."),
        _artifact_file(tmp_path, "facebook_post", "Facebook copy here."),
    ]
    pack_dir = build_manual_pack("3060", artifacts, output_root=str(tmp_path))

    pack = Path(pack_dir)
    assert pack.is_dir()
    # Version folder under DP3060/packs/v1 (default version 1).
    assert pack == tmp_path / "DP3060" / "packs" / "v1"

    checklist = (pack / "checklist.md").read_text(encoding="utf-8")
    assert "ready-to-post pack" in checklist
    # Manual channels are listed as a checklist.
    assert "property24" in checklist
    # Each artifact was copied into the pack folder.
    assert (pack / "portal_listing.md").exists()
    assert (pack / "facebook_post.md").exists()
    # And referenced in the artifact table.
    assert "portal_listing" in checklist
    # No em or en dash in generated copy.
    assert "—" not in checklist
    assert "–" not in checklist


# --- Proof of Marketing status log ---------------------------------------

def test_log_posted_and_list_per_dp_channel_version(tmp_path):
    db_path = tmp_path / "engine.db"

    row = log_posted(db_path, "3060", "property24", 1, "posted")
    assert row["dp"] == "3060"
    assert row["channel"] == "property24"
    assert row["version"] == 1
    assert row["status"] == "posted"
    assert row["at"]  # timestamped

    log_posted(db_path, "3060", "facebook", 1, "posted")
    # A re-render lands a new version, logged separately.
    log_posted(db_path, "3060", "property24", 2, "posted", note="price reduced")
    # A different DP must not bleed into this DP's trail.
    log_posted(db_path, "9999", "facebook", 1, "posted")

    rows = list_status(db_path, "3060")
    assert len(rows) == 3
    triples = {(r["channel"], r["version"], r["status"]) for r in rows}
    assert triples == {
        ("property24", 1, "posted"),
        ("property24", 2, "posted"),
        ("facebook", 1, "posted"),
    }
    # The note survives on the versioned row.
    v2 = next(r for r in rows if r["channel"] == "property24" and r["version"] == 2)
    assert v2["note"] == "price reduced"


# --- price-drop re-engagement burst --------------------------------------

def test_price_drop_burst_fires_on_a_drop_only():
    before = _record(price_display="R2 500 000")
    lower = _record(price_display="R2 000 000")

    event = price_drop_burst(before, lower)
    assert event is not None
    assert event["event"] == "price_drop_burst"
    assert event["label"] == "REDUCED"
    assert event["dp"] == "3060"
    assert event["old_price"] == 2_500_000.0
    assert event["new_price"] == 2_000_000.0
    assert event["drop_amount"] == 500_000.0
    assert event["channels"] == ["facebook"]


def test_price_rise_and_unchanged_do_not_burst():
    before = _record(price_display="R2 000 000")
    higher = _record(price_display="R2 500 000")
    same = _record(price_display="R2 000 000")

    # A rise is silent maintenance, not a re-engagement event.
    assert price_drop_burst(before, higher) is None
    # An unchanged price does not burst.
    assert price_drop_burst(before, same) is None


def test_price_drop_burst_none_when_price_is_textual():
    before = _record(price_display="Offers invited")
    after = _record(price_display="Offers invited")
    # No comparable figure on either side.
    assert price_drop_burst(before, after) is None


# --- GHL Social Planner scaffold (no token) ------------------------------

def test_post_to_planner_without_token_returns_pack_and_never_raises(
    tmp_path, monkeypatch
):
    # Guarantee no ambient token so the scaffold path runs.
    monkeypatch.delenv("GHL_API_TOKEN", raising=False)

    artifacts = [
        _artifact_file(tmp_path, "facebook_post", "Come and view this property."),
    ]
    channels = channel_matrix(_record())

    result = post_to_planner("3060", artifacts, channels, token=None)

    assert result.mode == "ready_to_post_pack"
    assert result.posted is False
    # Facebook is a Social Planner channel and is enabled by the matrix.
    assert "facebook" in result.channels
    # A manual checklist and the built request shape come back, never a crash.
    assert result.checklist
    assert isinstance(result.request, dict)
    assert result.artifacts  # the artifact paths to attach
    # The reason names the missing token; no token is ever leaked into the shape.
    assert result.reason
    assert "token" in result.reason.lower()
