"""Signed, single-use, expiring tokens for approve-by-email links (M8, Phase 4).

Gate emails to ``admin@dynamicauctioneers.co.za`` carry one-click
approve / request-changes links so an approver can action gate 2 without logging
in (SPEC M8). Each link embeds a token produced by ``sign`` and checked by
``verify``. A token is:

- **Signed** -- HMAC via ``itsdangerous.URLSafeSerializer`` over the shared app
  secret (``webapp.models.get_or_create_secret``), so tampering is detected.
- **Expiring** -- an ``exp`` epoch is embedded and checked, so a stale link is
  rejected.
- **Single-use** -- a random ``jti`` nonce is embedded and claimed atomically in
  the ``used_tokens`` table on ``verify``, so a link cannot be actioned twice.

``verify(token)`` consumes the nonce (that is the whole point of a one-click
action). ``peek(token)`` validates signature + expiry *without* consuming, for a
GET that renders a confirmation screen before the actioning POST.

No new dependency: ``itsdangerous`` is already installed for session cookies.
Only ``public_view``-safe data (DP number, gate, action) should ever be placed
in a payload -- tokens travel in email URLs.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from itsdangerous import BadSignature, URLSafeSerializer
from itsdangerous.exc import BadData

from webapp import models

_SALT = "da-approve-token-v1"


# --- errors ---------------------------------------------------------------

class TokenError(Exception):
    """Base class for any token failure (invalid, expired, reused)."""


class TokenInvalid(TokenError):
    """The token is malformed or its signature does not verify (tampered)."""


class TokenExpired(TokenError):
    """The token's embedded expiry has passed."""


class TokenReused(TokenError):
    """The token's single-use nonce has already been spent."""


# --- signer ---------------------------------------------------------------

def _serializer(db_path: Optional[str] = None) -> URLSafeSerializer:
    secret = models.get_or_create_secret(db_path)
    return URLSafeSerializer(secret, salt=_SALT)


def _new_jti() -> str:
    import secrets as _secrets

    return _secrets.token_urlsafe(9)


# --- public API -----------------------------------------------------------

def sign(payload: Dict[str, Any], ttl_seconds: int, db_path: Optional[str] = None) -> str:
    """Return a signed token carrying ``payload``, valid for ``ttl_seconds``.

    A random single-use ``jti`` and an absolute ``exp`` epoch are added to the
    payload before signing; ``verify`` strips both back out. ``payload`` must be
    JSON-serialisable and must not contain the reserved keys ``jti`` / ``exp``.
    """
    if "jti" in payload or "exp" in payload:
        raise ValueError("payload must not contain reserved keys 'jti'/'exp'.")
    body = dict(payload)
    body["jti"] = _new_jti()
    body["exp"] = int(time.time()) + int(ttl_seconds)
    return _serializer(db_path).dumps(body)


def _load(token: str, db_path: Optional[str]) -> Dict[str, Any]:
    try:
        data = _serializer(db_path).loads(token)
    except (BadSignature, BadData, ValueError, TypeError) as exc:
        raise TokenInvalid("Token is invalid or has been tampered with.") from exc
    if not isinstance(data, dict) or "jti" not in data or "exp" not in data:
        raise TokenInvalid("Token payload is malformed.")
    if int(time.time()) > int(data["exp"]):
        raise TokenExpired("Token has expired.")
    return data


def peek(token: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Validate signature + expiry WITHOUT consuming the single-use nonce.

    Use this for the GET that renders a confirmation screen. Raises
    ``TokenInvalid`` / ``TokenExpired``; does not raise ``TokenReused``.
    Returns the payload without the ``jti`` / ``exp`` internals.
    """
    data = _load(token, db_path)
    data.pop("jti", None)
    data.pop("exp", None)
    return data


def verify(token: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Validate and CONSUME a single-use token, returning its payload.

    Raises ``TokenInvalid`` (tampered/malformed), ``TokenExpired`` (past its
    ttl), or ``TokenReused`` (nonce already spent). On success the nonce is
    claimed so a second ``verify`` of the same token raises ``TokenReused``.
    The returned payload has the ``jti`` / ``exp`` internals removed.
    """
    data = _load(token, db_path)
    jti = data["jti"]
    if not models.mark_token_used(db_path, jti):
        raise TokenReused("Token has already been used.")
    data.pop("jti", None)
    data.pop("exp", None)
    return data
