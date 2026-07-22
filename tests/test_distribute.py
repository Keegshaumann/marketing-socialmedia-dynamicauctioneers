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

from engine.distribute.ghl import build_planner_request, post_to_planner
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


def test_channel_matrix_routes_the_three_connected_socials():
    # Facebook, Instagram and LinkedIn all route (TikTok/Pinterest are out).
    matrix = channel_matrix(_record())
    assert matrix["facebook"] is True
    assert matrix["instagram"] is True
    assert matrix["linkedin"] is True


def test_caption_strips_markdown_title_but_keeps_hashtags(tmp_path):
    from engine.distribute.ghl import _caption_for, _strip_leading_heading

    raw = "# Facebook Post DP3060\n\nPELHAM NORTH | OFFERS INVITED\n3 bed home.\n#Auction #Pmb"
    stripped = _strip_leading_heading(raw)
    assert stripped.startswith("PELHAM NORTH")   # title gone
    assert "# Facebook Post" not in stripped
    assert "#Auction" in stripped                # hashtags (no space) preserved

    art = _artifact_file(tmp_path, "facebook_post", raw)
    assert _caption_for([art], "3060").startswith("PELHAM NORTH")


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


# --- userId + status wiring (GHL create-post requires a userId) -----------


def test_build_request_includes_user_id_and_status_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GHL_USER_ID", "USER123")
    monkeypatch.setenv("GHL_POST_STATUS", "draft")
    channels = channel_matrix(_record())
    req = build_planner_request("3060", [], channels)
    assert req["json"]["userId"] == "USER123"
    assert req["json"]["status"] == "draft"
    assert req["meta"]["has_user_id"] is True
    assert req["meta"]["status"] == "draft"


def test_build_request_defaults_published_and_no_user_id(tmp_path):
    # Hermetic env (no GHL_USER_ID / GHL_POST_STATUS): publish, and userId absent
    # so a caller can tell it is unconfigured rather than sending a bad body.
    req = build_planner_request("3060", [], channel_matrix(_record()))
    assert req["json"]["status"] == "published"
    assert "userId" not in req["json"]
    assert req["meta"]["has_user_id"] is False


def test_post_to_planner_packs_when_token_but_no_user_id(tmp_path, monkeypatch):
    monkeypatch.delenv("GHL_USER_ID", raising=False)
    artifacts = [_artifact_file(tmp_path, "facebook_post", "View this property.")]
    channels = channel_matrix(_record())
    result = post_to_planner("3060", artifacts, channels, token="TESTTOKEN")
    assert result.mode == "ready_to_post_pack"
    assert result.posted is False
    assert "user id" in result.reason.lower()


class _FakeHTTP:
    """Captures the outgoing request instead of hitting the network.

    Dispatches on URL: the media upload endpoint returns a hosted URL, the
    create-post endpoint returns a draft - so one fake serves the whole
    upload-then-post flow.
    """

    def __init__(self, status_code=201, payload=None, media_status=200, media_fail=()):
        self._status = status_code
        self._payload = payload or {"_id": "post123", "status": "draft"}
        self._media_status = media_status
        self._media_fail = set(media_fail)  # filenames whose upload 500s
        self.calls: list = []
        self.media_uploads: list = []

    def request(self, method, url, headers=None, json=None, timeout=None, **kwargs):
        if "/medias/upload-file" in url:
            name = None
            files = kwargs.get("files") or {}
            if "file" in files:
                name = files["file"][0]
            self.media_uploads.append(name)
            if name in self._media_fail:
                status, payload = 500, {}
            else:
                status, payload = self._media_status, {"url": f"https://cdn.test/{name}"}
        else:
            self.calls.append({"method": method, "url": url, "json": json})
            status, payload = self._status, self._payload

        class _Resp:
            status_code = status
            text = ""

            def json(self):
                return payload

        return _Resp()

    def close(self):
        pass


def test_env_lock_forces_draft_over_requested_status(monkeypatch):
    # GHL_POST_STATUS is a guard rail: it overrides even an explicit "post now".
    monkeypatch.setenv("GHL_POST_STATUS", "draft")
    req = build_planner_request(
        "3060", [], channel_matrix(_record()),
        status="published", schedule_date="2026-07-24T17:00:00",
    )
    assert req["json"]["status"] == "draft"
    assert "scheduleDate" not in req["json"]  # a draft is never scheduled
    assert req["meta"]["status_overridden"] is True
    assert req["meta"]["requested_status"] == "published"


def test_env_lock_is_case_insensitive_and_trimmed(monkeypatch):
    # A non-canonical value ("Draft" with whitespace, as a human might type in
    # .env) must still enforce the guard rail exactly as the UI reports it.
    monkeypatch.setenv("GHL_POST_STATUS", "  Draft ")
    req = build_planner_request(
        "3060", [], channel_matrix(_record()),
        status="published", schedule_date="2026-07-24T17:00:00",
    )
    assert req["json"]["status"] == "draft"        # normalized, lock enforced
    assert "scheduleDate" not in req["json"]        # a draft is never scheduled
    assert req["meta"]["status_overridden"] is True


def test_schedule_sets_scheduled_status_and_date():
    req = build_planner_request(
        "3060", [], channel_matrix(_record()),
        status="scheduled", schedule_date="2026-07-24T17:00:00",
    )
    assert req["json"]["status"] == "scheduled"
    assert req["json"]["scheduleDate"] == "2026-07-24T17:00:00"
    assert req["meta"]["status_overridden"] is False


def test_scheduled_without_a_date_falls_back_to_publish():
    req = build_planner_request(
        "3060", [], channel_matrix(_record()), status="scheduled", schedule_date=None
    )
    assert req["json"]["status"] == "published"
    assert "scheduleDate" not in req["json"]


def test_post_now_publishes_when_no_lock():
    req = build_planner_request("3060", [], channel_matrix(_record()), status="published")
    assert req["json"]["status"] == "published"
    assert req["meta"]["status_overridden"] is False


def test_post_to_planner_posts_draft_via_injected_client(tmp_path):
    artifacts = [_artifact_file(tmp_path, "facebook_post", "View this property.")]
    channels = channel_matrix(_record())
    http = _FakeHTTP()
    result = post_to_planner(
        "3060", artifacts, channels,
        token="TESTTOKEN", user_id="USER123", status="draft", client=http,
    )
    assert result.mode == "posted" and result.posted is True
    # Exactly one call went out, carrying the userId and the draft status.
    assert len(http.calls) == 1
    sent = http.calls[0]["json"]
    assert sent["userId"] == "USER123"
    assert sent["status"] == "draft"
    # The token is attached to the live headers, never stored on result.request.
    assert result.request["headers"]["Authorization"] == "Bearer <token>"


# --- media hosting (GHL Media Library -> public URLs on the post) ---------


def _png_file(tmp_path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return str(path)


def test_postable_images_orders_photos_first_and_excludes_vectors(tmp_path):
    from engine.distribute.ghl import _postable_images
    from engine.render.base import Artifact

    hero = _png_file(tmp_path, "hero.png")
    demo = _png_file(tmp_path, "demo_ad.png")
    art_png = Artifact(dp="3060", fmt="demo_ad", backend="canva", path=demo, mime="image/png")
    art_svg = Artifact(dp="3060", fmt="webapp_icon", backend="html",
                       path=str(tmp_path / "icon.svg"), mime="image/svg+xml")
    art_html = Artifact(dp="3060", fmt="saia_banner", backend="html",
                        path=str(tmp_path / "banner.html"), mime="text/html")

    images, notes = _postable_images([art_png, art_svg, art_html], [hero, str(tmp_path / "gone.png")])
    assert images == [hero, demo]  # photo first, then the raster artifact
    joined = " ".join(notes)
    assert "vector" in joined            # SVG excluded with a note, never attached
    assert "not found" in joined         # missing photo skipped with a note


def test_postable_images_caps_at_four(tmp_path):
    from engine.distribute.ghl import MAX_POST_IMAGES, _postable_images

    photos = [_png_file(tmp_path, f"p{i}.png") for i in range(6)]
    images, notes = _postable_images([], photos)
    assert len(images) == MAX_POST_IMAGES
    assert any("attaching the first" in n for n in notes)


def test_upload_media_returns_hosted_url_and_none_on_failure(tmp_path):
    from engine.distribute.ghl import upload_media

    png = _png_file(tmp_path, "hero.png")
    ok = _FakeHTTP()
    assert upload_media(png, "TOKEN", client=ok) == "https://cdn.test/hero.png"
    bad = _FakeHTTP(media_status=500)
    assert upload_media(png, "TOKEN", client=bad) is None
    # Non-image and missing files never upload.
    assert upload_media(str(tmp_path / "x.svg"), "TOKEN", client=ok) is None


def test_post_carries_hosted_urls_never_local_paths(tmp_path):
    artifacts = [
        _artifact_file(tmp_path, "facebook_post", "View this property."),
    ]
    hero = _png_file(tmp_path, "hero.png")
    http = _FakeHTTP()
    result = post_to_planner(
        "3060", artifacts, channel_matrix(_record()),
        token="TESTTOKEN", user_id="USER123", status="draft",
        photo_paths=[hero], client=http,
    )
    assert result.posted is True
    assert http.media_uploads == ["hero.png"]        # the photo was hosted first
    sent = http.calls[-1]["json"]
    assert sent["media"] == [{"url": "https://cdn.test/hero.png", "type": "image"}]
    assert all("cdn.test" in m["url"] for m in sent["media"])  # no local paths


def test_failed_upload_still_posts_text_first(tmp_path):
    artifacts = [_artifact_file(tmp_path, "facebook_post", "View this property.")]
    hero = _png_file(tmp_path, "hero.png")
    http = _FakeHTTP(media_status=500)  # hosting down, posting up
    result = post_to_planner(
        "3060", artifacts, channel_matrix(_record()),
        token="TESTTOKEN", user_id="USER123", status="draft",
        photo_paths=[hero], client=http,
    )
    assert result.posted is True                      # the post still went out
    assert http.calls[-1]["json"]["media"] == []      # just without the image
    assert any("could not be hosted" in n for n in result.request["meta"]["media_notes"])


def test_partial_upload_failure_note_survives_the_rebuild(tmp_path):
    # One image hosts, one fails: the post goes out with the hosted image AND
    # the returned request still records what was dropped (the rebuild used to
    # discard the note, so the audit read as a clean fully-imaged post).
    artifacts = [_artifact_file(tmp_path, "facebook_post", "View this property.")]
    hero = _png_file(tmp_path, "hero.png")
    garden = _png_file(tmp_path, "garden.png")
    http = _FakeHTTP(media_fail={"garden.png"})
    result = post_to_planner(
        "3060", artifacts, channel_matrix(_record()),
        token="TESTTOKEN", user_id="USER123", status="draft",
        photo_paths=[hero, garden], client=http,
    )
    assert result.posted is True
    sent = http.calls[-1]["json"]
    assert sent["media"] == [{"url": "https://cdn.test/hero.png", "type": "image"}]
    notes = result.request["meta"]["media_notes"]
    assert any("garden.png could not be hosted" in n for n in notes)
    # The draft guard input also survives the rebuild.
    assert sent["status"] == "draft"


def test_missing_raster_artifact_is_noted_not_silent(tmp_path):
    from engine.distribute.ghl import _postable_images
    from engine.render.base import Artifact

    gone = Artifact(dp="3060", fmt="demo_ad", backend="canva",
                    path=str(tmp_path / "gone.png"), mime="image/png")
    images, notes = _postable_images([gone], [])
    assert images == []
    assert any("not found on disk" in n for n in notes)


def test_checklist_lists_post_images_and_labels_reference_artifacts(tmp_path):
    from engine.distribute.ghl import build_planner_checklist

    art = _artifact_file(tmp_path, "facebook_post", "Copy here.")
    hero = _png_file(tmp_path, "hero.png")
    steps = build_planner_checklist(
        "3060", ["facebook"], [art], "no token", postable_images=[hero],
    )
    joined = "\n".join(steps)
    assert f"Attach image: {hero}" in joined
    assert "Copy/reference [facebook_post] (not post media)" in joined
    assert "Attach artifact" not in joined  # the old misleading instruction is gone
