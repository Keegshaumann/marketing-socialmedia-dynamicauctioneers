#!/usr/bin/env python3
"""One-time Canva Connect re-authorisation (PKCE) — mints a fresh refresh token.

Canva rotates the OAuth refresh token on every use and invalidates the old one,
so if the stored token is ever lost or double-spent the whole grant dies with
``invalid_grant: refresh token used twice``. This tool re-authorises the app and
prints a fresh refresh token to paste into ``CANVA_REFRESH_TOKEN`` (and clear the
rotated-token state file so the seed is used).

Run it on the machine with the browser (the redirect goes to 127.0.0.1). It needs
the app credentials in the environment — fetch them from the server's .env, e.g.:

    export CANVA_CLIENT_ID=$(ssh root@SERVER 'grep -m1 ^CANVA_CLIENT_ID= /opt/da-marketing/.env | cut -d= -f2-')
    export CANVA_CLIENT_SECRET=$(ssh root@SERVER 'grep -m1 ^CANVA_CLIENT_SECRET= /opt/da-marketing/.env | cut -d= -f2-')
    export CANVA_REDIRECT_URI=$(ssh root@SERVER 'grep -m1 ^CANVA_REDIRECT_URI= /opt/da-marketing/.env | cut -d= -f2-')
    python3 scripts/canva_reauth.py

It prints an authorise URL: open it, sign into the Canva account that owns the
brand templates, click Allow. The new refresh token is written to
``$REAUTH_OUT`` (default /tmp/canva_new_refresh_token.txt, mode 600) — never
echoed to stdout. Install it into the server's ``CANVA_REFRESH_TOKEN`` and remove
any ``~/.dynamic-auctioneers/canva-state.json`` so the fresh seed is used.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import parse_qs, urlparse

API_BASE = "https://api.canva.com/rest/v1"
TOKEN_URL = f"{API_BASE}/oauth/token"
AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
# Scopes the autofill render flow uses (a subset of the Connect app's config).
SCOPES = (
    "asset:read asset:write "
    "brandtemplate:content:read brandtemplate:meta:read "
    "design:content:read design:content:write design:meta:read"
)
OUT_FILE = os.environ.get("REAUTH_OUT", "/tmp/canva_new_refresh_token.txt")

client_id = os.environ["CANVA_CLIENT_ID"]
client_secret = os.environ["CANVA_CLIENT_SECRET"]
redirect_uri = os.environ.get("CANVA_REDIRECT_URI", "http://127.0.0.1:8080/callback")

verifier = secrets.token_urlsafe(72)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
state = secrets.token_urlsafe(16)
auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
    "code_challenge": challenge,
    "code_challenge_method": "S256",
    "scope": SCOPES,
    "response_type": "code",
    "client_id": client_id,
    "redirect_uri": redirect_uri,
    "state": state,
})

parsed = urlparse(redirect_uri)
host, port, cb_path = parsed.hostname, parsed.port or 80, parsed.path or "/"
result: dict = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path != cb_path:
            self.send_response(404); self.end_headers(); return
        q = parse_qs(u.query)
        result.update(
            code=(q.get("code") or [None])[0],
            state=(q.get("state") or [None])[0],
            error=(q.get("error") or [None])[0],
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Canva authorisation received.</h2><p>You can close this tab and return to the terminal.</p>")
        threading.Thread(target=self.server.shutdown, daemon=True).start()


print("AUTH_URL_BEGIN")
print(auth_url)
print("AUTH_URL_END")
print(f"Listening on {host}:{port}{cb_path} for the Canva redirect ...", flush=True)

http.server.HTTPServer((host, port), Handler).serve_forever()

if result.get("error"):
    print("REAUTH_ERROR: " + str(result["error"])); sys.exit(1)
if result.get("state") != state:
    print("REAUTH_ERROR: state mismatch (possible CSRF); aborting."); sys.exit(1)
code = result.get("code")
if not code:
    print("REAUTH_ERROR: no authorization code received."); sys.exit(1)

body = urllib.parse.urlencode({
    "grant_type": "authorization_code",
    "code": code,
    "code_verifier": verifier,
    "redirect_uri": redirect_uri,
}).encode()
basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
    "Authorization": f"Basic {basic}",
    "Content-Type": "application/x-www-form-urlencoded",
})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode())
except urllib.error.HTTPError as e:
    print("REAUTH_ERROR: token exchange HTTP " + str(e.code) + " " + e.read().decode()[:400]); sys.exit(1)

refresh = payload.get("refresh_token")
if not refresh:
    print("REAUTH_ERROR: no refresh_token in response"); sys.exit(1)

fd = os.open(OUT_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(refresh)
print(f"REAUTH_OK: new refresh token written to {OUT_FILE} (len {len(refresh)}); scopes: {payload.get('scope', '?')}")
