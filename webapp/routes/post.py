"""Post and change screen (M8 screen 7, Phase 4).

After gate 3 (client approval) a property is distributed. This screen triggers
that distribution and handles the one operational reality SPEC section 12 step 8
calls out: a change to a live post is a regenerate-and-repost, and a live
Instagram post can only be removed by hand.

Two actions:

- **Trigger distribution.** When ``engine.distribute`` is present it is used to
  build the ready-to-post pack and (token permitting) fire the GHL Social Planner
  post; the per-channel outcome is logged to ``channel_status`` (Proof of
  Marketing) and the record advances toward ``live``. Live posting itself is a
  PLACEHOLDER until a GHL Private Integration token is configured (Phase 5, Q12):
  without a token the channels are logged as ``ready`` (a ready-to-post pack) and
  nothing is called out. When the distribute package is absent, a ``post`` job is
  enqueued for the worker instead.
- **Request changes.** Re-renders the artifacts (a ``render`` job) and returns the
  property to gate 2 for re-review, in one flow. From ``live`` this is the legal
  ``live -> updated`` re-engagement path (the REDUCED update); from earlier states
  the change request is recorded on the audit trail without an illegal move.

POPIA: the only property facts rendered here come from ``record.public_view()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from webapp import auth, jobs, models
from webapp.routes.artifacts import _load_manifest, _output_root, _status_board
from webapp.routes.settings import CHANNELS, channel_enabled

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# The GHL Social Planner channels (D11); everything else is a manual/pack channel.
_SOCIAL = {"facebook", "instagram", "linkedin", "x"}

# SPEC section 12 step 8 reality, surfaced verbatim so the delete limit is no
# surprise. Single source of truth: import the leaf constant from ghl (gates.py
# already imports it at top level, so it is always available) instead of keeping
# a copy here that can drift.
from engine.distribute.ghl import DELETE_CAVEAT as _DELETE_CAVEAT


# --- distribute package (guarded; Phase 5 may still be a scaffold) ---------

def _load_distribute():
    """Import engine.distribute if available, else None.  # PLACEHOLDER(Phase 5)."""
    try:
        from engine import distribute  # type: ignore
        return distribute
    except Exception:
        return None


# --- helpers --------------------------------------------------------------

def _ctx(request: Request, **extra: Any) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"request": request, "user": auth.current_user(request)}
    ctx.update(extra)
    return ctx


def _enabled_channels(db_path: str) -> List[str]:
    return [key for key, _label, default in CHANNELS if channel_enabled(db_path, key, default)]


def _pack_version(db_path: str, dp: str) -> int:
    versions = [int(a.get("version", 1) or 1) for a in _load_manifest(db_path, dp)]
    return max(versions) if versions else 1


def _matrix_for(dp: str, db_path: str, distribute) -> Dict[str, bool]:
    """Routing matrix for the DP: the distribute package's matrix when available,
    else the settings toggles. Never raises."""
    if distribute is not None:
        try:
            from engine.store import RecordStore

            store = RecordStore(db_path)
            try:
                record = store.get(dp)
            finally:
                store.close()
            if record is not None:
                return distribute.channel_matrix(record)
        except Exception:
            pass
    return {key: channel_enabled(db_path, key, default) for key, _l, default in CHANNELS}


def _advance_to_live(store, dp: str) -> Optional[str]:
    """Step a post-gate-3 record forward to ``live`` through legal moves only.

    Only the client_approved -> assets_built -> live chain (and updated -> live)
    is walked; gate 3 itself is never auto-signed. Any illegal move stops the
    walk cleanly. Returns the resulting state.
    """
    from engine.store import IllegalTransition

    nexts = {
        "client_approved": "assets_built",
        "assets_built": "live",
        "updated": "live",
    }
    for _ in range(4):
        state = store.get_state(dp)
        target = nexts.get(state)
        if target is None:  # already live, or not yet past gate 3
            break
        try:
            store.transition(dp, target, note="distribution triggered")
        except IllegalTransition:
            break
    return store.get_state(dp)


# --- routes ---------------------------------------------------------------

@router.get("/post/{dp}")
def post_page(
    request: Request,
    dp: str,
    user: dict = Depends(auth.require_login),
):
    """Render the post-and-change screen for one property."""
    db_path = auth.db_path_for(request)
    distribute = _load_distribute()

    state = None
    try:
        from engine.store import RecordStore

        store = RecordStore(db_path)
        try:
            state = store.get_state(dp)
        finally:
            store.close()
    except Exception:
        pass

    matrix = _matrix_for(dp, db_path, distribute)
    enabled = set(_enabled_channels(db_path))
    channels = [
        {
            "channel": key,
            "routed": bool(matrix.get(key)),
            "enabled": key in enabled,
            "will_post": bool(matrix.get(key)) and key in enabled,
        }
        for key, _label, _d in CHANNELS
    ]

    return templates.TemplateResponse(
        request,
        "post.html",
        _ctx(
            request,
            dp=dp,
            state=state,
            channels=channels,
            has_artifacts=bool(_load_manifest(db_path, dp)),
            distribute_available=distribute is not None,
            delete_caveat=_DELETE_CAVEAT,
            board=_status_board(db_path, dp),
        ),
    )


def _blocked(request, db_path: str, dp: str, state, summary: str, title: str = "Blocked"):
    """A 409 block response for the post screen: nothing was distributed."""
    return templates.TemplateResponse(
        request,
        "_post_result.html",
        _ctx(
            request,
            dp=dp,
            state=state,
            live=False,
            summary=summary,
            pack_path=None,
            notes=[],
            board=_status_board(db_path, dp),
            tone="block",
            title=title,
        ),
        status_code=409,
    )


@router.post("/post/{dp}/distribute")
async def trigger_distribution(
    request: Request,
    dp: str,
    user: dict = Depends(auth.require_role("marketing")),
):
    """Trigger distribution after gate 3: build the pack, log per channel, go live."""
    db_path = auth.db_path_for(request)
    form = await request.form()

    # Gate guard: nothing is distributed (no manual pack, no live GHL post, no
    # channel_status) until the record is postable. A first listing needs client
    # approval (gate 3). A small-edit repost (state "updated") instead needs one
    # internal approval recorded since its last edit, and an explicit
    # confirmation that the old Instagram posts were deleted by hand (no delete
    # API, SPEC 12 step 8).
    _POSTABLE = {"client_approved", "assets_built", "live", "updated"}
    from engine.store import RecordStore as _RS

    _guard_store = _RS(db_path)
    try:
        _current = _guard_store.get_state(dp)
        _repost_approved = _guard_store.internally_approved_since_last_edit(dp)
    finally:
        _guard_store.close()

    if _current not in _POSTABLE:
        return _blocked(
            request, db_path, dp, _current,
            "Cannot distribute: client approval (gate 3) has not been recorded "
            "for this property yet.",
        )
    if _current == "updated":
        if not _repost_approved:
            return _blocked(
                request, db_path, dp, _current,
                "This update needs internal approval before it can repost. "
                "Approve the adverts at gate 2 first.",
            )
        if not form.get("deleted_old_posts"):
            return _blocked(
                request, db_path, dp, _current,
                "Confirm you have manually deleted the old Instagram posts "
                "before reposting (Instagram has no delete API).",
            )

    distribute = _load_distribute()
    output_root = _output_root(db_path)
    version = _pack_version(db_path, dp)
    artifacts = _load_manifest(db_path, dp)
    matrix = _matrix_for(dp, db_path, distribute)
    enabled = _enabled_channels(db_path)
    channels = [c for c in enabled if matrix.get(c)]

    token = models.get_setting(db_path, "ghl_token")
    posted_social = False
    pack_path: Optional[str] = None
    notes: List[str] = []

    if distribute is not None:
        # Build the ready-to-post pack for the manual channels.
        try:
            pack_path = distribute.build_manual_pack(dp, artifacts, output_root=output_root)
        except Exception as exc:  # never let pack building break the flow
            notes.append(f"pack build skipped ({type(exc).__name__})")

        # Attempt the social post (parks a ready-to-post pack when no token).
        try:
            result = distribute.post_to_planner(dp, artifacts, matrix, token=token)
            posted_social = bool(getattr(result, "posted", False))
        except Exception as exc:
            notes.append(f"social post skipped ({type(exc).__name__})")
    else:
        # Phase 5 not present: hand it to the worker's token-gated post handler.
        jobs.enqueue(db_path, "post", dp, payload={"channels": channels, "version": version})
        notes.append("engine.distribute not available; a post job was queued (Phase 5).")

    # Proof of Marketing: one status row per channel that routes and is enabled.
    for channel in channels:
        status = "posted" if (channel in _SOCIAL and posted_social) else "ready"
        models.log_channel_status(db_path, dp, channel, version, status)

    # Advance the record toward live so the board reflects the go-live.
    state = None
    try:
        from engine.store import RecordStore

        store = RecordStore(db_path)
        try:
            state = _advance_to_live(store, dp)
        finally:
            store.close()
    except Exception:
        pass

    live = state == "live"
    if token:
        summary = (
            "Distribution triggered. Social channels posted via GoHighLevel; "
            "manual channels are in the ready-to-post pack."
        )
    else:
        summary = (
            "Distribution prepared. No GHL token configured, so every channel is "
            "logged as ready-to-post pending live posting (Phase 5 placeholder)."
        )

    return templates.TemplateResponse(
        request,
        "_post_result.html",
        _ctx(
            request,
            dp=dp,
            state=state,
            live=live,
            summary=summary,
            pack_path=pack_path,
            notes=notes,
            board=_status_board(db_path, dp),
            tone="ok" if live else "note",
            title="Posted" if live else "Ready to post",
        ),
    )


@router.post("/post/{dp}/change-request")
async def request_changes(
    request: Request,
    dp: str,
    user: dict = Depends(auth.require_role("marketing")),
):
    """Re-render and return the property to gate 2 for re-review, in one flow."""
    db_path = auth.db_path_for(request)
    form = await request.form()
    reason = (form.get("reason") or "").strip() or "no reason given"

    # Re-render the artifacts (key-free template path when no API key).
    jobs.enqueue(db_path, "render", dp, payload={"output_root": _output_root(db_path)})

    note = f"change requested: {reason}"
    state = None
    moved = False
    try:
        from engine.store import IllegalTransition, RecordStore

        store = RecordStore(db_path)
        try:
            current = store.get_state(dp)
            if current == "live":
                # Legal REDUCED path: live -> updated, then gate 2 re-approves.
                try:
                    store.transition(dp, "updated", note=note)
                    moved = True
                except IllegalTransition:
                    store.record_signoff(dp, gate="2", user=user["email"], note=note)
            else:
                # No legal backward jump; log the change request for gate 2 pickup.
                store.record_signoff(dp, gate="2", user=user["email"], note=note)
            state = store.get_state(dp)
        finally:
            store.close()
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "_post_result.html",
            _ctx(
                request,
                dp=dp,
                state=None,
                live=False,
                summary=f"Could not record the change request: {type(exc).__name__}.",
                notes=[],
                board=_status_board(db_path, dp),
                tone="block",
                title="Change request failed",
            ),
        )

    summary = (
        "Change requested. The artifacts are re-rendering and the property has "
        "returned to gate 2 for re-review."
    )
    if moved:
        summary += " The live listing moves to a REDUCED update on repost."

    return templates.TemplateResponse(
        request,
        "_post_result.html",
        _ctx(
            request,
            dp=dp,
            state=state,
            live=False,
            summary=summary,
            notes=[],
            board=_status_board(db_path, dp),
            tone="note",
            title="Returned to gate 2",
        ),
    )
