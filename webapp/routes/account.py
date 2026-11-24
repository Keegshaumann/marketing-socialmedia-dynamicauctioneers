"""Sign in, sign out and the root redirect.

These lived in the old job board module until the marketing pipeline was
scrapped (D127); they are unchanged apart from moving here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from webapp import auth
from webapp.ratelimit import account_key, client_ip, safe_log, throttle

router = APIRouter()
logger = logging.getLogger("webapp.auth")


def _view(request: Request, name: str, ctx: Optional[dict] = None, status_code: int = 200):
    data: Dict[str, Any] = {"user": auth.current_user(request)}
    if ctx:
        data.update(ctx)
    return request.app.state.templates.TemplateResponse(request, name, data, status_code=status_code)


@router.get("/", include_in_schema=False)
def root(request: Request) -> RedirectResponse:
    return RedirectResponse(auth.home_for(auth.current_user(request)), status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    user = auth.current_user(request)
    if user is not None:
        return RedirectResponse(auth.home_for(user), status_code=303)
    return _view(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    key = account_key(email)
    # Count the attempt before the bcrypt check, so a burst cannot overrun the cap.
    wait = throttle.hit(key)
    if wait:
        minutes = max(1, (wait + 59) // 60)
        logger.warning("login locked for %s from %s", safe_log(key), client_ip(request))
        return _view(request, "login.html", {
            "error": f"Too many failed attempts. Try again in about {minutes} minute" + ("s" if minutes != 1 else "") + ".",
            "email": email}, status_code=429)

    if auth.login_user(request, email, password):
        throttle.record_success(key)
        return RedirectResponse(auth.home_for(auth.current_user(request)), status_code=303)

    # Same message whether or not the account exists; never log the password.
    logger.warning("failed login for %s from %s", safe_log(key), client_ip(request))
    return _view(request, "login.html", {"error": "Those credentials were not recognised.", "email": email},
                 status_code=401)


@router.get("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    auth.logout(request)
    return RedirectResponse("/login", status_code=303)
