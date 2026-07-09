"""Inspect Canva brand templates so you can wire CANVA_TEMPLATE_MAP safely (D14).

Two modes, both read-only (they never create or edit anything in your tenant):

    python3 scripts/canva_templates.py
        List every brand template you can see: brand_template_id + title.
        Find your duplicated test template by name and copy its id.

    python3 scripts/canva_templates.py <brand_template_id>
        Print that template's autofill DATA FIELDS (name + type). These are the
        field names your CANVA_TEMPLATE_MAP target must expose; the backend fills
        headline / price / body / address / suburb / dp and photo1..N. If your
        test template uses different names, either rename them in Canva or tell
        me and I will point engine/render/canva_backend.py._autofill_data at them.

Auth reuses engine/render/canva_backend.py (CANVA_CLIENT_ID / CANVA_CLIENT_SECRET
/ CANVA_REFRESH_TOKEN from .env). Nothing is written to your Canva account.

Note: brand-template + autofill endpoints are Canva Enterprise features (D12).
On a Teams plan these calls may return 403/404; the error is surfaced verbatim.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

# Allow running as `python3 scripts/canva_templates.py` from the repo root by
# putting the repo root (this file's parent's parent) on the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_API = "https://api.canva.com/rest/v1"
_BACKEND_TEXT_FIELDS = {
    "headline", "price", "body", "address", "suburb", "dp",
    "property_ref", "master_ref", "beds", "baths", "garages", "size", "features",
}


def _get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        _API + path,
        method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _list_templates(token: str) -> None:
    seen = 0
    continuation = None
    while True:
        path = "/brand-templates?limit=100"
        if continuation:
            path += "&continuation=" + urllib.parse.quote(continuation)
        payload = _get(path, token)
        for item in payload.get("items", []) or []:
            seen += 1
            print(f"  {item.get('id')}   {item.get('title', '(untitled)')}")
        continuation = payload.get("continuation")
        if not continuation:
            break
    if seen == 0:
        print("  (no brand templates visible - Enterprise-only, or none created yet)")


def _print_dataset(token: str, template_id: str) -> None:
    payload = _get(f"/brand-templates/{template_id}/dataset", token)
    dataset = payload.get("dataset") or {}
    if not dataset:
        print("  (this template defines no autofill data fields)")
        return
    print(f"  data fields on {template_id}:")
    for name, spec in dataset.items():
        print(f"    {name:20s} {spec.get('type', '?')}")
    have = set(dataset)
    photos = sorted(n for n in have if n.startswith("photo"))
    print()
    print(f"  backend text fields matched: {sorted(_BACKEND_TEXT_FIELDS & have) or 'none'}")
    print(f"  backend text fields NOT in template: {sorted(_BACKEND_TEXT_FIELDS - have) or 'none'}")
    print(f"  image fields (photo1..N) present: {photos or 'none'}")


def main() -> int:
    if load_dotenv:
        load_dotenv()

    required = ("CANVA_CLIENT_ID", "CANVA_CLIENT_SECRET", "CANVA_REFRESH_TOKEN")
    if not all(os.getenv(v) for v in required):
        print(
            "Set CANVA_CLIENT_ID, CANVA_CLIENT_SECRET and CANVA_REFRESH_TOKEN in .env "
            "first (run scripts/canva_authorize.py for the refresh token).",
            file=sys.stderr,
        )
        return 1

    try:
        from engine.render.canva_backend import CanvaBackend
    except Exception as exc:  # pragma: no cover
        print(f"Could not import the Canva backend: {exc}", file=sys.stderr)
        return 1

    try:
        token = CanvaBackend()._access_token()  # refresh -> access, rotation persisted
    except Exception as exc:
        print(f"Token exchange failed: {exc}", file=sys.stderr)
        return 1

    try:
        if len(sys.argv) > 1:
            _print_dataset(token, sys.argv[1])
        else:
            print("Brand templates (id   title):")
            _list_templates(token)
    except urllib.error.HTTPError as exc:
        print(f"Canva API error ({exc.code}): {exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
