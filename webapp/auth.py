"""Authentication and role gating (M8, Phase 4).

Email + bcrypt-password accounts with two functional roles:

- ``marketing`` -- runs jobs (upload the pair, drive extraction/render)
- ``approver``  -- signs the three human gates

Sessions ride on a signed cookie via Starlette's ``SessionMiddleware`` (wired in
``main.py``); this module only reads and writes ``request.session["user"]`` (the
email). The password hash never enters the session.

A first-run ``seed_admin`` creates a single bootstrap account,
``admin@dynamicauctioneers.co.za``, and prints a random temporary password to the
server console. The bootstrap account is given the role ``admin``: to keep the two
functional roles intact while letting one account drive an end-to-end run before
any real users exist, ``require_role`` treats ``admin`` as satisfying every role.
Real accounts are created as ``marketing`` or ``approver``.

Design rules baked in here:
- No PII is stored or logged. The only personal datum is the login email.
- ``require_role`` raises a 303 redirect to ``/login`` for an unauthenticated
  request and a 403 for a wrong-role one, per the wiring contract.
"""

from __future__ import annotations

import os
import secrets
from typing import Any, Callable, Dict, Optional

import bcrypt
from fastapi import HTTPException, Request, status

from webapp import models

ADMIN_EMAIL = "admin@dynamicauctioneers.co.za"
ADMIN_ROLE = "admin"
ROLES = ("marketing", "approver")


# --- password hashing -----------------------------------------------------

def hash_password(password: str) -> str:
    """Return a bcrypt hash (utf-8 str) for ``password``."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, pw_hash: str) -> bool:
    """Return whether ``password`` matches ``pw_hash``. Never raises."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), pw_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# A fixed bcrypt hash of a random string, computed once at import. When a login
# names an account that does not exist, we still run a checkpw against this hash
# so the request takes the same ~time as a wrong password on a real account. That
# removes the timing side-channel an attacker would otherwise use to tell which
# emails are registered (user enumeration).
_DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt()).decode("utf-8")


# --- db path resolution ---------------------------------------------------

def db_path_for(request: Request) -> str:
    """Resolve the db path for a request: app.state override, else env/default.

    ``main.py`` sets ``app.state.db_path`` at startup; falling back to the env
    default keeps auth usable in tests that skip that wiring.
    """
    override = getattr(getattr(request, "app", None), "state", None)
    candidate = getattr(override, "db_path", None) if override is not None else None
    return models.resolve_db_path(candidate)


# --- session helpers ------------------------------------------------------

def current_user(request: Request) -> Optional[Dict[str, Any]]:
    """Return the logged-in user as a dict (``email``, ``role``), or ``None``.

    Reads the email from the session cookie and re-loads the user so a role
    change or a deleted account takes effect on the next request. The password
    hash is dropped from the returned dict.
    """
    try:
        email = request.session.get("user")
    except (AssertionError, AttributeError):
        # SessionMiddleware not installed (e.g. a bare test client).
        return None
    if not email:
        return None
    user = models.get_user(db_path_for(request), email)
    if user is None:
        return None
    user.pop("pw_hash", None)
    return user


def login_user(request: Request, email: str, password: str) -> bool:
    """Verify credentials and, on success, set the session. Returns success.

    Runs a bcrypt check even when the account does not exist (against a dummy
    hash) so the response time does not reveal whether an email is registered.
    """
    user = models.get_user(db_path_for(request), email)
    if user is None:
        verify_password(password, _DUMMY_HASH)  # equalise timing; result discarded
        return False
    if not verify_password(password, user["pw_hash"]):
        return False
    request.session["user"] = user["email"]
    return True


def logout(request: Request) -> None:
    """Clear the session for the current request."""
    try:
        request.session.pop("user", None)
    except (AssertionError, AttributeError):
        pass


# --- role gating ----------------------------------------------------------

def _has_role(user: Optional[Dict[str, Any]], roles: tuple) -> bool:
    if user is None:
        return False
    role = user.get("role")
    if role == ADMIN_ROLE:
        return True  # bootstrap account satisfies every role
    return role in roles


def require_role(*roles: str) -> Callable[[Request], Dict[str, Any]]:
    """FastAPI dependency: require a logged-in user with one of ``roles``.

    Raises a 303 redirect to ``/login`` when not authenticated, and a 403 when
    the user's role is not permitted. Returns the user dict on success so a route
    can annotate ``user: dict = Depends(require_role("approver"))``.
    """

    def dependency(request: Request) -> Dict[str, Any]:
        user = current_user(request)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                detail="Login required.",
                headers={"Location": "/login"},
            )
        if not _has_role(user, roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action needs one of: {', '.join(roles)}.",
            )
        return user

    return dependency


def require_login(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: require any logged-in user (either role)."""
    user = current_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Login required.",
            headers={"Location": "/login"},
        )
    return user


# --- first-run seeding ----------------------------------------------------

def seed_admin(db_path: Optional[str] = None) -> Optional[str]:
    """Create the bootstrap admin on first run and print a temp password.

    Returns the generated temporary password (also printed to stdout) when a new
    admin is created, or ``None`` when users already exist (so re-running is a
    no-op). The db schema is ensured first, so this is safe to call standalone.
    """
    resolved = models.init_db(db_path)
    if models.count_users(resolved) > 0:
        return None

    temp_password = secrets.token_urlsafe(12)
    models.create_user(
        resolved,
        email=ADMIN_EMAIL,
        pw_hash=hash_password(temp_password),
        role=ADMIN_ROLE,
    )
    # Printed to the server console only; never persisted in the clear.
    print(
        "\n"
        "  Dynamic Auctioneers platform - first run\n"
        f"  Admin account : {ADMIN_EMAIL}\n"
        f"  Temp password : {temp_password}\n"
        "  Change this password after logging in.\n",
        flush=True,
    )
    return temp_password
