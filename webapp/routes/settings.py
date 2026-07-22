"""Settings screen (M8 screen 8, Phase 4).

One place to hold the operational configuration the platform needs but cannot
ask a person for on every run: the GoHighLevel Social Planner token and
sub-account, the Canva Connect credentials, which channels a property is routed
to, and the approver email list that the gate emails go to.

Everything persists through ``webapp.models`` settings helpers (a plain key/value
table on the shared SQLite file), so the worker (``webapp.jobs``) and the
distribution package read the same values a human typed here.

Access:
- Viewing the page needs any logged-in user (``require_login``).
- Saving a section needs the ``marketing`` role (``require_role('marketing')``);
  the bootstrap ``admin`` satisfies it. Approver accounts sign gates, they do not
  edit credentials.

No PII: the only personal data here is the approver *login* emails, which are
operational addresses, not owner or occupant contact details.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from webapp import auth, models

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# --- configuration surface -----------------------------------------------

# Channels a property can be routed to. ``default_on`` mirrors the SPEC 5.6
# routing baseline (every property → P24, own site, FB, email) so a
# fresh install already routes sensibly before anyone touches this screen.
CHANNELS = [
    ("property24", "Property24", True),
    ("own_website", "Own website", True),
    ("facebook", "Facebook", True),
    ("instagram", "Instagram", True),
    ("linkedin", "LinkedIn", False),
    ("email_list", "Email list", True),
    ("jamesedition", "JamesEdition (from R10m)", False),
]

# Credential fields that must never be echoed back into the page. A blank submit
# leaves the stored value untouched (so re-saving a section does not wipe a
# token the user cannot see).
_SECRET_KEYS = {"ghl_token", "canva_client_secret", "canva_refresh_token"}

# Non-secret config fields; a blank submit clears them.
_PLAIN_KEYS = {
    "ghl_location_id",
    "ghl_account_map",
    "canva_client_id",
    "canva_template_map",
    "output_root",
}


# --- helpers --------------------------------------------------------------

def _ctx(request: Request, **extra: Any) -> Dict[str, Any]:
    """Base template context: request + current user + extras."""
    ctx: Dict[str, Any] = {"request": request, "user": auth.current_user(request)}
    ctx.update(extra)
    return ctx


def channel_enabled(db_path: str, key: str, default: bool) -> bool:
    """Whether channel ``key`` is enabled (stored ``on``/``off``, else default)."""
    value = models.get_setting(db_path, f"channel_{key}")
    if value is None:
        return default
    return value == "on"


def _channel_view(db_path: str) -> list[dict]:
    return [
        {"key": key, "label": label, "enabled": channel_enabled(db_path, key, default)}
        for key, label, default in CHANNELS
    ]


def _normalise_emails(raw: str) -> str:
    """Split a free-text approver list into a clean, deduped, comma-joined string."""
    parts = re.split(r"[\s,;]+", raw or "")
    seen: list[str] = []
    for part in parts:
        email = part.strip().lower()
        if email and email not in seen:
            seen.append(email)
    return ", ".join(seen)


# --- routes ---------------------------------------------------------------

@router.get("/settings")
def settings_page(
    request: Request,
    user: dict = Depends(auth.require_login),
):
    """Render the settings screen with the current stored values."""
    db_path = auth.db_path_for(request)
    settings = models.all_settings(db_path)

    return templates.TemplateResponse(
        request,
        "settings.html",
        _ctx(
            request,
            settings=settings,
            channels=_channel_view(db_path),
            approver_emails=settings.get("approver_emails", ""),
            # booleans so the template can show "configured" without the value
            ghl_token_set=bool(settings.get("ghl_token")),
            canva_secret_set=bool(settings.get("canva_client_secret")),
            canva_refresh_set=bool(settings.get("canva_refresh_token")),
        ),
    )


@router.post("/settings/credentials")
async def save_credentials(
    request: Request,
    user: dict = Depends(auth.require_role("marketing")),
):
    """Persist the GHL + Canva credentials and the output root."""
    db_path = auth.db_path_for(request)
    form = await request.form()

    for key in _SECRET_KEYS:
        value = (form.get(key) or "").strip()
        if value:  # blank leaves the existing secret untouched
            models.set_setting(db_path, key, value)

    for key in _PLAIN_KEYS:
        if key in form:
            models.set_setting(db_path, key, (form.get(key) or "").strip())

    return templates.TemplateResponse(
        request,
        "_settings_saved.html",
        _ctx(
            request,
            title="Credentials saved",
            text="The distribution and Canva credentials have been stored.",
        ),
    )


@router.post("/settings/channels")
async def save_channels(
    request: Request,
    user: dict = Depends(auth.require_role("marketing")),
):
    """Persist the per-channel routing toggles (checkbox present means on)."""
    db_path = auth.db_path_for(request)
    form = await request.form()

    enabled = []
    for key, label, _default in CHANNELS:
        on = form.get(f"channel_{key}") is not None
        models.set_setting(db_path, f"channel_{key}", "on" if on else "off")
        if on:
            enabled.append(label)

    text = (
        "Routing to: " + ", ".join(enabled) + "."
        if enabled
        else "No channels are enabled; nothing will be routed."
    )
    return templates.TemplateResponse(
        request,
        "_settings_saved.html",
        _ctx(request, title="Channels saved", text=text),
    )


@router.post("/settings/approvers")
async def save_approvers(
    request: Request,
    user: dict = Depends(auth.require_role("marketing")),
):
    """Persist the approver email list used by the gate emails."""
    db_path = auth.db_path_for(request)
    form = await request.form()

    cleaned = _normalise_emails(form.get("approver_emails") or "")
    models.set_setting(db_path, "approver_emails", cleaned)

    count = len([e for e in cleaned.split(",") if e.strip()])
    text = (
        f"{count} approver address{'es' if count != 1 else ''} on file."
        if count
        else "No approver addresses on file yet."
    )
    return templates.TemplateResponse(
        request,
        "_settings_saved.html",
        _ctx(request, title="Approvers saved", text=text),
    )
