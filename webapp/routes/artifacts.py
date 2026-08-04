"""Artifact pack + Proof-of-Marketing status board (M8 screen 6, Phase 4).

Two things live on this screen, both per DP:

1. The **artifact pack** - every rendered format for the property (from the
   render service's ``artifacts/manifest.json``), shown as a gallery with a
   per-format view and a single download-all zip.
2. The **Proof of Marketing** board - the posted / not-posted status for every
   channel, read from the ``channel_status`` log (``webapp.models``). This is the
   audit trail SPEC M6 asks for: what actually went out, per channel, per version.

POPIA: this screen only ever surfaces ``record.public_view()`` fields (suburb,
price framing, headline) and the rendered artifacts, which are themselves built
from ``public_view`` by the render backends. Owner and occupant PII is not read
into any context here, so the poison-marker PII test passes against the output.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from webapp import auth, models
from webapp.routes.settings import CHANNELS, channel_enabled

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# Human labels + a coarse kind per format, for the gallery tiles.
FORMAT_META: Dict[str, Dict[str, str]] = {
    "portal_listing": {"label": "Portal listing", "kind": "text"},
    "facebook_post": {"label": "Facebook post", "kind": "text"},
    "email_blast": {"label": "Email blast", "kind": "text"},
    "demo_ad": {"label": "Demo advert", "kind": "page"},
    "info_pack": {"label": "Info pack", "kind": "page"},
    "webapp_icon": {"label": "Website tile", "kind": "image"},
    "saia_banner": {"label": "SAIA banner", "kind": "page"},
    "alert_mailer": {"label": "Alert mailer", "kind": "page"},
    "auction_board": {"label": "Auction board", "kind": "page"},
}

# Channel labels, keyed by the identifiers used across routing + the status log.
_CHANNEL_LABELS = {key: label for key, label, _default in CHANNELS}
_CHANNEL_LABELS.setdefault("meta_paid_boost", "Meta paid boost")
_CHANNEL_LABELS.setdefault("auction_boards", "Auction boards")
_CHANNEL_LABELS.setdefault("commercial_portals", "Commercial portals")

# status string -> (badge tone, whether it counts as "posted").
_STATUS_TONE = {
    "posted": ("ok", True),
    "ready": ("note", False),
    "pending": ("info", False),
    "manual": ("info", False),
    "failed": ("block", False),
    "skipped": ("block", False),
}


# --- helpers --------------------------------------------------------------

def _ctx(request: Request, **extra: Any) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"request": request, "user": auth.current_user(request)}
    ctx.update(extra)
    return ctx


def _output_root(db_path: str) -> str:
    return models.get_setting(db_path, "output_root") or "."


def _artifacts_dir(db_path: str, dp: str) -> Path:
    return Path(_output_root(db_path)) / f"DP{dp}" / "artifacts"


def _load_manifest(db_path: str, dp: str) -> List[Dict[str, Any]]:
    """Read the render manifest for a DP, or an empty list when nothing rendered."""
    manifest = _artifacts_dir(db_path, dp) / "manifest.json"
    if not manifest.exists():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def _resolve_artifact(db_path: str, dp: str, entry: Dict[str, Any]) -> Optional[Path]:
    """Resolve a manifest entry to a path inside this DP's artifacts folder.

    Returns None when the entry points outside that folder (no traversal). The
    file is not required to exist: callers decide what a missing file means.
    """
    raw = entry.get("path") or ""
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(_output_root(db_path)) / raw
    candidate = candidate.resolve()
    art_dir = _artifacts_dir(db_path, dp).resolve()
    if art_dir not in candidate.parents and candidate.parent != art_dir:
        return None
    return candidate


def _text_preview(path: Optional[Path], lines: int = 10, limit: int = 900) -> str:
    """The first few lines of a text artifact, for the tile preview.

    Safe to show: every text artifact is rendered from ``public_view``, so it
    carries no owner or occupant PII. Reads only the head of the file.
    """
    if path is None or not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(4000)  # bounded: never pull a whole file into the page
    except OSError:
        return ""
    return "\n".join(head.splitlines()[:lines]).strip()[:limit]


def _artifact_view(db_path: str, dp: str) -> List[Dict[str, Any]]:
    """Shape the manifest entries for the gallery (label, kind, view url).

    ``kind`` prefers the artifact's actual mime over the static per-format
    table: with mixed rendering the same format can be html one pass and a
    Canva-exported PNG the next, and an image must tile as an image.

    Every tile carries a real preview: images show themselves, text artifacts
    carry their first lines inline, and page artifacts (html/pdf) get a
    ``thumb_url`` pointing at the cached rasterised thumbnail. ``thumb_url`` is
    only set when a thumbnail exists or can plausibly be made, so a host with no
    Playwright falls straight back to the icon tile instead of firing requests
    that can only fail. Nothing is rasterised here - generation happens in the
    thumbnail route, off the page's critical path.
    """
    from engine.render import artifact_thumbs

    art_dir = _artifacts_dir(db_path, dp)
    out: List[Dict[str, Any]] = []
    for art in _load_manifest(db_path, dp):
        fmt = art.get("fmt", "artifact")
        meta = FORMAT_META.get(fmt, {"label": fmt.replace("_", " ").title(), "kind": "text"})
        mime = art.get("mime", "") or ""
        if mime.startswith("image/"):
            kind = "image"
        elif mime == "application/pdf":
            kind = "page"
        else:
            kind = meta["kind"]

        path = _resolve_artifact(db_path, dp, art)
        preview = _text_preview(path) if kind == "text" else ""
        thumb_url = None
        if kind == "page" and path is not None:
            try:
                if artifact_thumbs.possible(art_dir, fmt, path, mime):
                    thumb_url = f"/artifacts/{dp}/thumb/{fmt}"
            except Exception:
                thumb_url = None  # a preview must never break the page

        out.append(
            {
                "fmt": fmt,
                "label": meta["label"],
                "kind": kind,
                "mime": mime,
                "version": art.get("version", 1),
                "url": f"/artifacts/{dp}/file/{fmt}",
                "thumb_url": thumb_url,
                "preview": preview,
                "edit_url": art.get("edit_url"),
            }
        )
    return out


def _status_board(db_path: str, dp: str) -> List[Dict[str, Any]]:
    """Latest posted/not-posted status per channel (Proof of Marketing).

    The board covers every channel that is either enabled in settings or already
    has a status row logged, so a channel that has never been posted shows as
    "Not posted" rather than being invisible.
    """
    rows = models.list_channel_status(db_path, dp)  # newest-first

    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        channel = row.get("channel")
        if channel and channel not in latest:  # first seen == newest
            latest[channel] = row

    channels: List[str] = [key for key, _label, _d in CHANNELS if channel_enabled(db_path, key, _d)]
    for channel in latest:
        if channel not in channels:
            channels.append(channel)

    board: List[Dict[str, Any]] = []
    for channel in channels:
        row = latest.get(channel)
        if row is None:
            board.append(
                {
                    "channel": channel,
                    "label": _CHANNEL_LABELS.get(channel, channel.replace("_", " ").title()),
                    "status": "not posted",
                    "tone": "neutral",
                    "posted": False,
                    "version": None,
                    "at": None,
                }
            )
            continue
        status = (row.get("status") or "").lower()
        tone, posted = _STATUS_TONE.get(status.split(":")[0], ("info", False))
        board.append(
            {
                "channel": channel,
                "label": _CHANNEL_LABELS.get(channel, channel.replace("_", " ").title()),
                "status": row.get("status"),
                "tone": tone,
                "posted": posted,
                "version": row.get("version"),
                "at": row.get("at"),
            }
        )
    return board


def _property_header(db_path: str, dp: str) -> Dict[str, Any]:
    """Public-only header facts for a DP (never owner/occupant PII)."""
    header: Dict[str, Any] = {"dp": dp, "suburb": None, "price": None, "state": None}
    try:
        from engine.store import RecordStore

        store = RecordStore(db_path)
        try:
            header["state"] = store.get_state(dp)
            record = store.get(dp)
            if record is not None:
                public = record.public_view()  # POPIA: stripped projection only
                marketing = public.get("marketing") or {}
                identity = public.get("identity") or {}
                header["suburb"] = identity.get("suburb") or public.get("suburb")
                header["price"] = marketing.get("price_display")
                header["headline"] = marketing.get("headline")
        finally:
            store.close()
    except Exception:
        # A missing record must not break the artifacts page.
        pass
    return header


# --- routes ---------------------------------------------------------------

@router.get("/artifacts/{dp}")
def artifacts_page(
    request: Request,
    dp: str,
    user: dict = Depends(auth.require_login),
):
    """The artifact pack + Proof-of-Marketing board for one property."""
    db_path = auth.db_path_for(request)
    artifacts = _artifact_view(db_path, dp)
    return templates.TemplateResponse(
        request,
        "artifacts.html",
        _ctx(
            request,
            dp=dp,
            header=_property_header(db_path, dp),
            artifacts=artifacts,
            board=_status_board(db_path, dp),
        ),
    )


@router.get("/artifacts/{dp}/status")
def status_partial(
    request: Request,
    dp: str,
    user: dict = Depends(auth.require_login),
):
    """The status board on its own, for HTMX polling after a post."""
    db_path = auth.db_path_for(request)
    return templates.TemplateResponse(
        request,
        "_artifact_status.html",
        _ctx(request, dp=dp, board=_status_board(db_path, dp)),
    )


@router.get("/artifacts/{dp}/file/{fmt}")
def artifact_file(
    request: Request,
    dp: str,
    fmt: str,
    user: dict = Depends(auth.require_login),
):
    """Serve one rendered artifact file (inline where the browser can show it)."""
    db_path = auth.db_path_for(request)

    entry = next((a for a in _load_manifest(db_path, dp) if a.get("fmt") == fmt), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such artifact for this DP.")

    # Contain the served file to this DP's artifacts folder (no traversal).
    candidate = _resolve_artifact(db_path, dp, entry)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Artifact path is out of bounds.")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Artifact file is missing on disk.")

    return FileResponse(str(candidate), media_type=entry.get("mime") or "application/octet-stream")


@router.get("/artifacts/{dp}/thumb/{fmt}")
def artifact_thumb(
    request: Request,
    dp: str,
    fmt: str,
    user: dict = Depends(auth.require_login),
):
    """Serve the cached thumbnail image of an html/pdf artifact (auth-gated).

    Generated on first request and cached on disk next to the artifact, so a
    given artifact version is rasterised once. Answers 503 (not 500, never a
    hang) when the rasteriser is unavailable or the render fails; the tile then
    shows its icon fallback.
    """
    from engine.render import artifact_thumbs

    db_path = auth.db_path_for(request)
    entry = next((a for a in _load_manifest(db_path, dp) if a.get("fmt") == fmt), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such artifact for this DP.")

    source = _resolve_artifact(db_path, dp, entry)
    if source is None or not source.exists():
        raise HTTPException(status_code=404, detail="Artifact file is missing on disk.")

    try:
        png = artifact_thumbs.thumbnail(
            _artifacts_dir(db_path, dp), fmt, source, entry.get("mime") or ""
        )
    except Exception:  # belt and braces: the module already swallows failures
        png = None
    if png is None:
        raise HTTPException(status_code=503, detail="Preview unavailable.")

    # no-cache so a re-rendered artifact's new thumbnail is revalidated rather
    # than served stale from the browser cache (same rule as the ad preview).
    return FileResponse(
        str(png),
        media_type=artifact_thumbs.MEDIA_TYPE,
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/artifacts/{dp}/download")
def download_pack(
    request: Request,
    dp: str,
    user: dict = Depends(auth.require_login),
):
    """Stream every rendered artifact for the DP as a single zip."""
    db_path = auth.db_path_for(request)
    art_dir = _artifacts_dir(db_path, dp)
    if not art_dir.exists():
        raise HTTPException(status_code=404, detail="No artifacts rendered for this DP yet.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(art_dir.iterdir()):
            if path.is_file():
                archive.write(path, arcname=path.name)
    buffer.seek(0)

    headers = {"Content-Disposition": f'attachment; filename="DP{dp}-artifacts.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)
