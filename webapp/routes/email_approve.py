"""Approve-by-email endpoints (M8, Phase 4).

Gate 2 can be actioned straight from an approver's inbox, with no login. The
internal approval email (rendered on the gate-2 screen, see ``gates.py``) carries
two one-click links, each backed by a signed, single-use, expiring token
(``webapp.tokens``). This module verifies those tokens and performs the gate-2
action on the approver's behalf.

Flow:
- ``GET /email/gate2?token=...`` validates the signature and expiry *without*
  consuming the token (``tokens.peek``) and renders a confirmation screen. A
  tampered or expired link is rejected here with a clear message.
- ``POST /email/gate2`` consumes the token (``tokens.verify``); a second attempt
  with the same link is rejected as reused. On success it dispatches to the same
  gate-2 action helpers the logged-in buttons use, so the email path and the UI
  path cannot diverge.

Security notes:
- No session is required or created: the token *is* the authorisation, scoped to
  one DP and one action, single-use, and time-limited.
- Tokens travel in URLs, so only public, non-PII data (DP number, gate, action,
  approver email) is ever placed in a payload.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.templating import Jinja2Templates

from webapp import models, tokens
from webapp.routes.gates import action_gate2_approve, action_gate2_changes

router = APIRouter(prefix="/email", tags=["email-approve"])

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_ACTION_LABEL = {"approve": "Approve these adverts", "changes": "Request changes"}


def _db(request: Request) -> str:
    override = getattr(getattr(request, "app", None), "state", None)
    candidate = getattr(override, "db_path", None) if override is not None else None
    return models.resolve_db_path(candidate)


def _reject(request: Request, title: str, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "email_action.html",
        {"phase": "rejected", "title": title, "message": message},
        status_code=400,
    )


@router.get("/gate2", response_class=HTMLResponse)
def gate2_confirm(token: str, request: Request):
    """Validate (without consuming) the link and render a confirmation screen."""
    db_path = _db(request)
    try:
        payload = tokens.peek(token, db_path)
    except tokens.TokenExpired:
        return _reject(request, "Link expired", "This approval link has expired. Ask for a fresh one from the platform.")
    except tokens.TokenError:
        return _reject(request, "Invalid link", "This approval link is not valid. It may have been altered in transit.")

    action = payload.get("action", "approve")
    return templates.TemplateResponse(
        request,
        "email_action.html",
        {
            "phase": "confirm",
            "token": token,
            "dp": payload.get("dp"),
            "action": action,
            "action_label": _ACTION_LABEL.get(action, action),
        },
    )


@router.post("/gate2", response_class=HTMLResponse)
def gate2_action(request: Request, token: str = Form(...), note: str = Form("")):
    """Consume the single-use token and perform the gate-2 action, no login."""
    db_path = _db(request)
    try:
        payload = tokens.verify(token, db_path)
    except tokens.TokenReused:
        return _reject(request, "Link already used", "This approval link has already been actioned. Each link works once.")
    except tokens.TokenExpired:
        return _reject(request, "Link expired", "This approval link has expired. Ask for a fresh one from the platform.")
    except tokens.TokenError:
        return _reject(request, "Invalid link", "This approval link is not valid.")

    dp = payload.get("dp")
    approver = payload.get("approver") or "email-approver"
    action = payload.get("action", "approve")

    if not dp:
        return _reject(request, "Nothing to action", "The link did not name a property.")

    if action == "changes":
        state = action_gate2_changes(db_path, dp, approver, note.strip() or "Changes requested via email.")
        title = "Changes requested"
        message = f"Your change request for DP{dp} is logged and the adverts were regenerated for another review."
    else:
        state = action_gate2_approve(db_path, dp, approver)
        title = "Adverts approved"
        message = f"Thank you. DP{dp} is approved and has moved on to client approval."

    return templates.TemplateResponse(
        request,
        "email_action.html",
        {"phase": "done", "title": title, "message": message, "dp": dp, "state": state},
    )
