"""One-time Canva Connect authorizer: get the initial refresh token (D14).

The Canva Developer Portal only builds the *authorization URL* (step 1). The
refresh token comes from the *token exchange* (step 3): Canva redirects the
consent back to a loopback URL with a short-lived ``code``, which you POST -
together with the PKCE ``code_verifier`` and your client secret - to Canva's
token endpoint, which returns an access token and a refresh token.

This script runs that whole flow so you do not have to do PKCE by hand:

  1. read CANVA_CLIENT_ID / CANVA_CLIENT_SECRET / CANVA_REDIRECT_URI from .env,
  2. generate a PKCE verifier + S256 challenge,
  3. open the Canva consent page in your browser,
  4. catch the redirect on the loopback address in CANVA_REDIRECT_URI,
  5. exchange the code (Basic auth, matching engine/render/canva_backend.py),
  6. print the refresh token to paste into .env as CANVA_REFRESH_TOKEN.

Run once:  python -m scripts.canva_authorize   (or: python scripts/canva_authorize.py)

Notes:
- The redirect URI here must be identical to the one registered in the portal.
- Canva's autofill/brand-template API is Enterprise-only (D12); this authorizer
  works on any plan, but the backend's autofill calls only succeed on Enterprise.
- Nothing secret is written to the repo. The refresh token is printed once for
  you to store in .env (gitignored).
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

import os

# Endpoints (authorize host differs from the REST/token host).
AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"

# The scopes the autofill flow needs (match the Developer Portal integration).
SCOPES = (
    "asset:read asset:write "
    "design:content:read design:content:write design:meta:read "
    "brandtemplate:meta:read brandtemplate:content:read"
)

DEFAULT_REDIRECT = "http://127.0.0.1:8080/callback"


def _pkce_pair() -> "tuple[str, str]":
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = secrets.token_urlsafe(64)  # 43-128 chars, URL-safe
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catch the single OAuth redirect and stash the query params."""

    result: dict = {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        _CallbackHandler.result = dict(urllib.parse.parse_qsl(parsed.query))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in _CallbackHandler.result
        msg = "Authorized. You can close this tab and return to the terminal." if ok \
            else "Authorization failed. Check the terminal."
        self.wfile.write(f"<html><body style='font-family:sans-serif'>{msg}</body></html>".encode())

    def log_message(self, *args) -> None:  # silence the default logging
        pass


def _wait_for_code(host: str, port: int) -> dict:
    server = http.server.HTTPServer((host, port), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request)  # one request only
    thread.start()
    thread.join(timeout=300)  # 5 minutes to consent
    server.server_close()
    return _CallbackHandler.result


def _exchange(code: str, verifier: str, redirect_uri: str, client_id: str, client_secret: str) -> dict:
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    if load_dotenv:
        load_dotenv()

    client_id = os.getenv("CANVA_CLIENT_ID")
    client_secret = os.getenv("CANVA_CLIENT_SECRET")
    redirect_uri = os.getenv("CANVA_REDIRECT_URI", DEFAULT_REDIRECT)
    if not client_id or not client_secret:
        print(
            "Set CANVA_CLIENT_ID and CANVA_CLIENT_SECRET in .env first "
            "(from the Canva Developer Portal), then re-run.",
            file=sys.stderr,
        )
        return 1

    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8080

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    authorize = AUTHORIZE_URL + "?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "s256",
            "state": state,
        }
    )

    print("Opening the Canva consent page in your browser...")
    print("If it does not open, paste this URL into your browser:\n")
    print("  " + authorize + "\n")
    print(f"Waiting for the redirect on {redirect_uri} (up to 5 minutes)...")
    try:
        webbrowser.open(authorize)
    except Exception:
        pass

    result = _wait_for_code(host, port)
    if result.get("state") != state:
        print("State mismatch or timeout; aborting for safety.", file=sys.stderr)
        return 1
    code = result.get("code")
    if not code:
        print(f"No authorization code returned. Response: {result}", file=sys.stderr)
        return 1

    try:
        tokens = _exchange(code, verifier, redirect_uri, client_id, client_secret)
    except urllib.error.HTTPError as exc:
        print(f"Token exchange failed ({exc.code}): {exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1

    refresh = tokens.get("refresh_token")
    if not refresh:
        print(f"No refresh token in the response: {tokens}", file=sys.stderr)
        return 1

    print("\nSuccess. Add this line to your .env (do NOT commit it):\n")
    print(f"  CANVA_REFRESH_TOKEN={refresh}\n")
    print("The Canva backend rotates this token automatically from now on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
