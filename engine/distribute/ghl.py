"""GoHighLevel Social Planner posting scaffold (M6, D11).

Decision D11 routes v1 social posting (Facebook, Instagram, LinkedIn, TikTok, X)
through GoHighLevel's Social Planner API: one call posts to every connected page
after gate 3. This module builds that call and fires it via ``httpx`` when a GHL
Private Integration token is configured, and degrades to a "ready-to-post pack"
result (artifacts plus a manual checklist) when it is not.

Config-gated by design. In an environment without a token this module never
calls out, never hangs and never raises: it returns a pack result and logs the
intent, leaving a human to complete the post. The token, location (sub-account)
and per-channel social-account ids come from platform settings.

# PLACEHOLDER(Q10/Q13, D11): needs a GHL Private Integration token + the Dynamic
# Solutions sub-account (location id) + the connected social-account ids, plus a
# hosting URL for rendered media (GHL Social Planner requires publicly reachable
# media URLs). Live calls are therefore unverified against the real endpoint.

SPEC section 12 step 8 reality (encoded in the checklist and the delete caveat):
GHL "delete" only cleans the Social Planner queue. It does not remove posts that
are already live on the connected pages, and Instagram and TikTok expose no
delete API at all. So a changed live post is handled by regenerate-and-repost (a
"REDUCED" update) rather than a silent edit, and removing an already-live IG or
TikTok post stays a manual moment. The approval gates are what prevent the need
to recall; an optional later refinement is a 15 to 30 minute publish delay so a
pre-live recall button can exist.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a hard import cycle
    from engine.render.base import Artifact


logger = logging.getLogger(__name__)


# GHL Social Planner endpoint shape. The API base and version header are stable;
# the concrete location id and account ids are per sub-account (see PLACEHOLDER).
GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"

# The channels the Social Planner API handles (D11). Anything else in a routing
# matrix (Property24, own website, WhatsApp, email, JamesEdition) is posted by a
# different mechanism and is ignored here.
GHL_SOCIAL_CHANNELS: tuple[str, ...] = (
    "facebook",
    "instagram",
    "linkedin",
    "tiktok",
    "x",
)

# The section 12 step 8 reality, surfaced verbatim to any caller/UI so the delete
# limitation is never a surprise.
DELETE_CAVEAT = (
    "GHL delete only clears the Social Planner queue. It does not remove posts "
    "already live on the connected pages, and Instagram and TikTok have no delete "
    "API. Changed live posts are handled by regenerate-and-repost (a REDUCED "
    "update); removing a live IG or TikTok post is a manual step."
)


@dataclass
class PlannerResult:
    """Outcome of a Social Planner post attempt.

    ``mode`` is ``"posted"`` when the API accepted the post, ``"ready_to_post_pack"``
    when no token was configured (the scaffold path), or ``"error"`` when a
    configured call failed and fell back to the pack. ``request`` is the exact
    request shape that would be or was sent (never contains the token). ``checklist``
    is the manual steps for a human when the pack path is taken.
    """

    dp: str
    mode: str
    posted: bool
    channels: List[str] = field(default_factory=list)
    request: Optional[Dict[str, Any]] = None
    response: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    artifacts: List[str] = field(default_factory=list)
    checklist: List[str] = field(default_factory=list)
    delete_caveat: str = DELETE_CAVEAT


# --- helpers -------------------------------------------------------------


def _artifact_attr(artifact: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` off an Artifact dataclass or a plain dict, safely."""
    if isinstance(artifact, dict):
        return artifact.get(name, default)
    return getattr(artifact, name, default)


def _social_channels(channels: Optional[Dict[str, bool]]) -> List[str]:
    """Return the enabled channels the Social Planner handles, in a stable order."""
    if not channels:
        return []
    return [c for c in GHL_SOCIAL_CHANNELS if channels.get(c)]


def _resolve_account_map(account_map: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Channel -> GHL social-account id map from arg or ``GHL_ACCOUNT_MAP`` (JSON)."""
    if account_map:
        return dict(account_map)
    raw = os.getenv("GHL_ACCOUNT_MAP")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            logger.warning("GHL_ACCOUNT_MAP is not valid JSON; ignoring it")
    return {}


def _account_ids(channels: List[str], account_map: Dict[str, str]) -> List[str]:
    """Resolve each enabled channel to its social-account id.

    Where a channel has no configured id (the common case with no creds), a
    stable placeholder id is used so the request shape stays well-formed and the
    gap is visible rather than silently dropped.
    """
    ids: List[str] = []
    for channel in channels:
        ids.append(account_map.get(channel) or f"PLACEHOLDER_{channel}_account_id")
    return ids


def _caption_for(artifacts: List[Any], dp: str) -> str:
    """Best-effort caption for the post, from the facebook_post copy if present.

    Reads the copy artifact off disk when it exists; falls back to a safe generic
    line keyed to the DP. Never raises: a missing or unreadable file just yields
    the fallback.
    """
    preferred = ("facebook_post", "whatsapp_blast", "email_blast")
    by_fmt = {_artifact_attr(a, "fmt"): a for a in artifacts}
    for fmt in preferred:
        artifact = by_fmt.get(fmt)
        if artifact is None:
            continue
        path = _artifact_attr(artifact, "path")
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read().strip()
            if text:
                return text
        except OSError:
            continue
    return f"Dynamic Auctioneers - DP{dp}. Contact us for the full property pack."


def _media_entries(artifacts: List[Any]) -> tuple[List[Dict[str, str]], List[str]]:
    """Build the Social Planner media list plus notes for non-postable artifacts.

    GHL needs publicly reachable image/video URLs. Local artifact paths are used
    as the ``url`` here with a hosting PLACEHOLDER; HTML/SVG artifacts (the demo
    ad, banners, boards) cannot be posted as-is and are noted for rasterising.
    """
    media: List[Dict[str, str]] = []
    notes: List[str] = []
    for artifact in artifacts:
        mime = (_artifact_attr(artifact, "mime") or "") or ""
        path = _artifact_attr(artifact, "path")
        fmt = _artifact_attr(artifact, "fmt")
        if not path:
            continue
        if mime.startswith("image/"):
            media.append({"url": path, "type": "image"})
        elif mime.startswith("video/"):
            media.append({"url": path, "type": "video"})
        else:
            notes.append(
                f"{fmt or path} ({mime or 'unknown type'}) needs rasterising to "
                "an image before it can be posted"
            )
    return media, notes


def build_planner_checklist(
    dp: str,
    channels: List[str],
    artifacts: List[Any],
    reason: str,
) -> List[str]:
    """Manual steps for a human when the automated post cannot run."""
    steps = [
        f"Reason automated posting did not run: {reason}",
        f"Open the GHL Social Planner in the Dynamic Solutions sub-account for DP{dp}.",
    ]
    if channels:
        steps.append("Post to: " + ", ".join(channels) + ".")
    else:
        steps.append("No social channels are enabled for this property.")
    for artifact in artifacts:
        fmt = _artifact_attr(artifact, "fmt")
        path = _artifact_attr(artifact, "path")
        if path:
            steps.append(f"Attach artifact [{fmt}]: {path}")
    steps.append("Use the generated copy as the caption; keep the pricing framing.")
    steps.append("If this is a change to a live post: " + DELETE_CAVEAT)
    return steps


# --- request builder (offline-testable shape) ----------------------------


def build_planner_request(
    dp: str,
    artifacts: List[Any],
    channels: Optional[Dict[str, bool]],
    *,
    location_id: Optional[str] = None,
    account_map: Optional[Dict[str, str]] = None,
    schedule_date: Optional[str] = None,
    status: str = "published",
) -> Dict[str, Any]:
    """Build the GHL Social Planner create-post request shape, without any token.

    Returns a dict of ``httpx``-ready kwargs: ``method``, ``url``, ``headers`` and
    ``json`` (the request body), plus ``meta`` for callers (resolved channels and
    any media notes). The token is deliberately absent here; it is attached only
    at call time in ``post_to_planner`` so this function stays pure and testable
    offline. The endpoint follows GHL's social-media-posting API (see PLACEHOLDER
    for the unverified live shape).
    """
    location = location_id or os.getenv("GHL_LOCATION_ID") or "PLACEHOLDER_location_id"
    social = _social_channels(channels)
    resolved_map = _resolve_account_map(account_map)
    account_ids = _account_ids(social, resolved_map)
    media, media_notes = _media_entries(artifacts)

    body: Dict[str, Any] = {
        "accountIds": account_ids,
        "summary": _caption_for(artifacts, dp),
        "media": media,
        "type": "post",
        "status": status,
    }
    if schedule_date:
        body["scheduleDate"] = schedule_date

    headers = {
        # Authorization is injected at call time; shown here as a placeholder so
        # the shape is complete for the offline test.
        "Authorization": "Bearer <token>",
        "Version": GHL_API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    return {
        "method": "POST",
        "url": f"{GHL_API_BASE}/social-media-posting/{location}/posts",
        "headers": headers,
        "json": body,
        "meta": {
            "dp": dp,
            "channels": social,
            "account_ids": account_ids,
            "media_notes": media_notes,
        },
    }


# --- entry point ---------------------------------------------------------


def post_to_planner(
    dp: str,
    artifacts: List[Any],
    channels: Optional[Dict[str, bool]],
    token: Optional[str] = None,
    *,
    location_id: Optional[str] = None,
    account_map: Optional[Dict[str, str]] = None,
    schedule_date: Optional[str] = None,
    client: Any = None,
    timeout: float = 30.0,
) -> PlannerResult:
    """Post a property's artifacts to the GHL Social Planner, or park a pack (D11).

    Fires the Social Planner call via ``httpx`` when a token is available (arg or
    ``GHL_API_TOKEN``); otherwise, and on any failure, returns a
    ``PlannerResult`` in ``ready_to_post_pack`` mode: the built request shape, the
    artifact paths and a manual checklist, with the intent logged. This function
    never raises and never hangs (calls carry an explicit timeout).

    Args:
        dp: property DP number.
        artifacts: rendered ``Artifact`` objects (or dicts) to attach/reference.
        channels: routing matrix (``engine.distribute.routing.channel_matrix``);
            only the Social Planner channels in it are used.
        token: GHL Private Integration token; falls back to ``GHL_API_TOKEN``.
        location_id: GHL sub-account (location) id; falls back to ``GHL_LOCATION_ID``.
        account_map: channel -> social-account id; falls back to ``GHL_ACCOUNT_MAP``.
        schedule_date: optional ISO schedule time; omit to post immediately.
        client: an optional injected HTTP client (for testing the posting path).
        timeout: per-request timeout in seconds.
    """
    token = token or os.getenv("GHL_API_TOKEN")
    request = build_planner_request(
        dp,
        artifacts,
        channels,
        location_id=location_id,
        account_map=account_map,
        schedule_date=schedule_date,
    )
    social: List[str] = request["meta"]["channels"]
    artifact_paths = [
        p for p in (_artifact_attr(a, "path") for a in artifacts) if p
    ]

    def _pack(mode: str, reason: str, response: Optional[Dict[str, Any]] = None) -> PlannerResult:
        logger.info(
            "GHL Social Planner not sent for DP%s (%s): %s; parking a ready-to-post pack",
            dp,
            mode,
            reason,
        )
        return PlannerResult(
            dp=dp,
            mode=mode,
            posted=False,
            channels=social,
            request=request,
            response=response,
            reason=reason,
            artifacts=artifact_paths,
            checklist=build_planner_checklist(dp, social, artifacts, reason),
        )

    # Scaffold path: no token, so never call out. This is the path this
    # credential-free environment runs.
    if not token:
        return _pack(
            "ready_to_post_pack",
            "no GHL Private Integration token configured "
            "(set GHL_API_TOKEN or pass token=...)",
        )

    if not social:
        return _pack(
            "ready_to_post_pack",
            "no Social Planner channels enabled in the routing matrix",
        )

    # Token present: attempt the real call, but still degrade gracefully. The
    # token is only attached here, never stored on the returned request.
    try:
        import httpx

        headers = dict(request["headers"])
        headers["Authorization"] = f"Bearer {token}"

        owns_client = client is None
        http = client or httpx.Client(timeout=timeout)
        try:
            resp = http.request(
                request["method"],
                request["url"],
                headers=headers,
                json=request["json"],
                timeout=timeout,
            )
        finally:
            if owns_client:
                http.close()

        if resp.status_code >= 400:
            return _pack(
                "error",
                f"GHL returned HTTP {resp.status_code}: {resp.text[:200]}",
            )

        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text[:500]}

        logger.info("Posted DP%s to GHL Social Planner: %s", dp, social)
        return PlannerResult(
            dp=dp,
            mode="posted",
            posted=True,
            channels=social,
            request=request,
            response=payload,
            reason=None,
            artifacts=artifact_paths,
            checklist=[],
        )
    except Exception as exc:  # noqa: BLE001 - degrade on any client/network error
        return _pack("error", f"GHL post failed: {type(exc).__name__}: {exc}")
