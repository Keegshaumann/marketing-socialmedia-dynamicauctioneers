"""Platform UI tests (M8, Phase 4) -- FastAPI TestClient, key-free.

Everything here runs offline: no ``ANTHROPIC_API_KEY``, no network. The worker's
model steps are never exercised (we do not run the background worker for the
functional tests, so job processing is deterministic); the one exception is the
smoke-boot test, which runs the real lifespan (worker start/stop) and only asserts
the login page loads.

Isolation: the environment is pinned to a throwaway SQLite file and a fixed app
secret *before* ``webapp.main`` is imported, so the module-level ``app`` opens the
test database. Each functional test uses a distinct DP number, because the store's
``upsert`` sets the lifecycle state only on first insert -- reusing a DP would
carry a state across tests.

Coverage:
- seed the bootstrap admin and a second (approver) account;
- login flow: a bad password is rejected, a good one sets the session;
- the board renders for a logged-in user;
- an intake upload creates a background job;
- gate order is enforced: gate 2 cannot be actioned before gate 1 sign-off;
- gate 1 sign-off is refused while a block flag is unresolved, then records and
  transitions once the block flag is overridden with a written reason;
- a tokenised gate-2 approve link actions the gate with NO session;
- an expired token and a reused token are both rejected;
- poison-marker PII test: a record seeded with poison owner name / ID / occupant
  cell renders its gate-2 artifacts with none of those markers reaching the
  gate-2 view, the board, or the rendered artifact files on disk.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# --- isolate the environment BEFORE importing the app --------------------
# webapp.main builds ``app`` at import time (init_db + secret read), so the DB
# path and secret must be pinned first.
_TMP = Path(tempfile.mkdtemp(prefix="da-webapp-test-"))
os.environ["ENGINE_DB"] = str(_TMP / "engine.db")
os.environ["APP_SECRET"] = "test-secret-fixed-da-m8"
os.environ.pop("ANTHROPIC_API_KEY", None)  # guarantee the key-free paths

import pytest
from fastapi.testclient import TestClient

from webapp import auth, models, tokens
from webapp.main import app
from engine.schema import (
    FinancialsInternal,
    Identity,
    Marketing,
    Owner,
    Physical,
    PropertyRecord,
    SaleProcess,
    Viewing,
)
from engine.store import RecordStore

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_RECORD = REPO_ROOT / "DP3060" / "record.json"

# Poison markers: distinctive strings placed only in the POPIA internal layer.
# None may surface in any public artifact, screen, or the board.
POISON_OWNER = "ZZOWNERPOISON_DoNotPublish"
POISON_ID = "ZZID_9999999999"
POISON_CELL = "ZZCELL_0820001111"
POISON_MARKERS = (POISON_OWNER, POISON_ID, POISON_CELL)

APPROVER_EMAIL = "approver@dynamicauctioneers.co.za"
APPROVER_PW = "approver-pass-123"


# --- one-time platform setup ---------------------------------------------
# Seed the bootstrap admin (captures its printed temp password) and a second,
# approver-role account. init_db is idempotent; the app already ran it at import.

DB_PATH = models.init_db()
ADMIN_PW = auth.seed_admin(DB_PATH)  # temp password on first run, else None
models.set_setting(DB_PATH, "output_root", str(_TMP))  # contain all writes

if models.get_user(DB_PATH, APPROVER_EMAIL) is None:
    models.create_user(
        DB_PATH,
        email=APPROVER_EMAIL,
        pw_hash=auth.hash_password(APPROVER_PW),
        role="approver",
    )


# --- helpers --------------------------------------------------------------

def _client() -> TestClient:
    """A fresh client (isolated cookie jar) against the shared app."""
    return TestClient(app)


def _login(client: TestClient, email: str, password: str):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )


def _login_admin(client: TestClient) -> None:
    assert ADMIN_PW is not None, "admin temp password was not seeded"
    resp = _login(client, auth.ADMIN_EMAIL, ADMIN_PW)
    assert resp.status_code == 303, resp.text


def _seed_minimal(dp: str, state: str) -> None:
    """A renderer-agnostic record: enough for the board columns and the state."""
    record = PropertyRecord(
        dp=dp,
        identity=Identity(suburb="Testville", title_type="sectional"),
        marketing=Marketing(headline="A tidy unit", price_display="R1 000 000"),
    )
    store = RecordStore(DB_PATH)
    try:
        store.upsert(record, state=state)
    finally:
        store.close()


def _golden_clone(dp: str, poison: bool = False) -> None:
    """Seed a clone of the golden DP3060 record under a fresh DP number.

    The golden record carries the garage block flag (gate 1) and renders cleanly
    through the html backend, which the minimal record does not guarantee.
    """
    record = PropertyRecord.model_validate_json(
        GOLDEN_RECORD.read_text(encoding="utf-8")
    )
    record.dp = dp
    if poison:
        if record.financials_internal is None:
            record.financials_internal = FinancialsInternal()
        record.financials_internal.owner = Owner(name=POISON_OWNER, id_number=POISON_ID)
        if record.sale_process is None:
            record.sale_process = SaleProcess()
        if record.sale_process.viewing is None:
            record.sale_process.viewing = Viewing()
        record.sale_process.viewing.contact_internal_only = POISON_CELL
    store = RecordStore(DB_PATH)
    try:
        store.upsert(record, state="extracted")
    finally:
        store.close()


def _state(dp: str):
    store = RecordStore(DB_PATH)
    try:
        return store.get_state(dp)
    finally:
        store.close()


def _needs_golden():
    if not GOLDEN_RECORD.exists():
        pytest.skip(f"golden record not present: {GOLDEN_RECORD}")


# --- login flow -----------------------------------------------------------

def test_admin_seeded_and_second_user_exists():
    assert ADMIN_PW, "seed_admin should return a temp password on first run"
    assert models.get_user(DB_PATH, auth.ADMIN_EMAIL) is not None
    approver = models.get_user(DB_PATH, APPROVER_EMAIL)
    assert approver is not None and approver["role"] == "approver"


def test_bad_password_rejected():
    client = _client()
    resp = _login(client, APPROVER_EMAIL, "wrong-password")
    assert resp.status_code == 401
    # No session was set: the board still bounces to login.
    board = client.get("/board", follow_redirects=False)
    assert board.status_code == 303
    assert board.headers["location"] == "/login"


def test_good_password_sets_session():
    client = _client()
    resp = _login(client, APPROVER_EMAIL, APPROVER_PW)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/board"
    # The session cookie now authenticates the board.
    board = client.get("/board")
    assert board.status_code == 200


# --- board renders --------------------------------------------------------

def test_board_renders_for_logged_in_user():
    client = _client()
    _login_admin(client)
    resp = client.get("/board")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# --- intake upload creates a job -----------------------------------------

def test_upload_creates_a_job():
    client = _client()
    _login_admin(client)

    # Fake PDF bytes: classification falls back to the filename hint (the content
    # is unreadable), so name each file so it lands in the right slot for DP 3099.
    files = [
        ("files", ("3099 - EVM valuation report.pdf", b"%PDF-fake-lightstone", "application/pdf")),
        ("files", ("3099 - property report.pdf", b"%PDF-fake-report", "application/pdf")),
    ]
    resp = client.post("/intake/upload", files=files)
    assert resp.status_code == 200, resp.text

    jobs_for_dp = models.list_jobs(DB_PATH, dp="3099")
    assert jobs_for_dp, "upload should have enqueued a job for DP 3099"
    assert any(j["kind"] == "extract" for j in jobs_for_dp)


# --- gate order: gate 2 cannot be actioned before gate 1 -----------------

def test_gate2_approve_before_gate1_does_not_advance():
    dp = "9002"
    _seed_minimal(dp, state="extracted")
    client = _client()
    _login_admin(client)

    # Approving gate 2 on a record that has not passed gate 1 must not advance it:
    # extracted -> approved is not a legal move, so the state is unchanged.
    resp = client.post(f"/gates/{dp}/ads/approve", follow_redirects=False)
    assert resp.status_code in (204, 303)
    assert _state(dp) == "extracted", "gate 2 must not advance a pre-gate-1 record"


# --- gate 1: sign-off is refused, then records and transitions -----------

def test_gate1_signoff_refused_then_records_and_transitions():
    _needs_golden()
    dp = "9001"
    _golden_clone(dp)  # carries the GARAGE_CONFLICT block flag
    client = _client()
    _login_admin(client)

    # Refused: the block flag has no written override.
    refused = client.post(f"/gates/{dp}/verify", data={}, follow_redirects=False)
    assert refused.status_code == 400
    assert _state(dp) == "extracted"

    # Accepted: overriding the block flag with a reason signs the gate off and
    # the record is drafted onto gate 2.
    ok = client.post(
        f"/gates/{dp}/verify",
        data={"override__GARAGE_CONFLICT": "Agent confirmed no garages; guest parking only."},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert ok.headers["location"] == f"/gates/{dp}/ads"
    assert _state(dp) == "drafted"

    # The sign-off is recorded on the record itself.
    store = RecordStore(DB_PATH)
    try:
        record = store.get(dp)
    finally:
        store.close()
    assert record.verification is not None
    assert record.verification.human_signoff


# --- tokenised gate-2 approve without a session --------------------------

def test_tokenised_gate2_approve_without_session():
    dp = "9003"
    _seed_minimal(dp, state="drafted")  # drafted -> approved is legal

    token = tokens.sign(
        {"dp": dp, "gate": "2", "action": "approve", "approver": APPROVER_EMAIL},
        3600,
        DB_PATH,
    )

    client = _client()  # deliberately NOT logged in

    # The confirmation GET validates without consuming the token.
    confirm = client.get(f"/email/gate2", params={"token": token})
    assert confirm.status_code == 200

    # The action POST consumes the token and approves gate 2 with no session.
    resp = client.post("/email/gate2", data={"token": token})
    assert resp.status_code == 200, resp.text
    assert _state(dp) == "approved", "the email token must action gate 2 without login"


def test_reused_token_is_rejected():
    dp = "9004"
    _seed_minimal(dp, state="drafted")
    token = tokens.sign(
        {"dp": dp, "gate": "2", "action": "approve", "approver": APPROVER_EMAIL},
        3600,
        DB_PATH,
    )
    client = _client()

    first = client.post("/email/gate2", data={"token": token})
    assert first.status_code == 200
    assert _state(dp) == "approved"

    second = client.post("/email/gate2", data={"token": token})
    assert second.status_code == 400
    assert "already" in second.text.lower()


def test_expired_token_is_rejected():
    dp = "9005"
    # A negative ttl puts the expiry in the past. No record is needed: the token
    # is rejected before any gate action runs.
    token = tokens.sign(
        {"dp": dp, "gate": "2", "action": "approve", "approver": APPROVER_EMAIL},
        -10,
        DB_PATH,
    )
    client = _client()

    confirm = client.get("/email/gate2", params={"token": token})
    assert confirm.status_code == 400
    assert "expired" in confirm.text.lower()

    action = client.post("/email/gate2", data={"token": token})
    assert action.status_code == 400


# --- poison-marker PII test (gate-2 view + board + on-disk artifacts) -----

def test_poison_pii_absent_from_gate2_view_and_board():
    _needs_golden()
    dp = "9006"
    _golden_clone(dp, poison=True)
    client = _client()
    _login_admin(client)

    # Gate 2 renders the artifact gallery + the internal approval email, all from
    # public_view(). No poison marker may appear in the response.
    ads = client.get(f"/gates/{dp}/ads")
    assert ads.status_code == 200, ads.text
    for marker in POISON_MARKERS:
        assert marker not in ads.text, f"{marker} leaked into the gate-2 view"

    # The board must not surface any PII either (it reads only indexed columns).
    board = client.get("/board")
    assert board.status_code == 200
    for marker in POISON_MARKERS:
        assert marker not in board.text, f"{marker} leaked into the board"

    # The rendered artifact files on disk are also PII-free.
    art_dir = _TMP / f"DP{dp}" / "artifacts"
    rendered = [p for p in art_dir.iterdir() if p.is_file()] if art_dir.exists() else []
    assert rendered, "gate 2 should have rendered artifacts"
    for path in rendered:
        blob = path.read_bytes()
        for marker in POISON_MARKERS:
            assert marker.encode("utf-8") not in blob, f"{marker} leaked into {path.name}"


# --- small edits on a live listing: edit -> approve -> repost -------------

def _seed_golden_live(dp: str) -> None:
    """Seed a golden clone directly at ``live`` (renders cleanly through html)."""
    _needs_golden()
    record = PropertyRecord.model_validate_json(GOLDEN_RECORD.read_text(encoding="utf-8"))
    record.dp = dp
    store = RecordStore(DB_PATH)
    try:
        store.upsert(record, state="live")
    finally:
        store.close()


def _public_view(dp: str) -> dict:
    store = RecordStore(DB_PATH)
    try:
        return store.get(dp).public_view()
    finally:
        store.close()


def test_board_gate_links_use_the_valid_shape():
    dp = "7104"
    _seed_minimal(dp, "verified")
    client = _client()
    _login_admin(client)
    board = client.get("/board")
    assert board.status_code == 200
    assert f"/gates/{dp}/ads" in board.text      # fixed href shape
    assert f"/gates/ad/{dp}" not in board.text   # old broken shape is gone


def test_live_edit_reopens_and_applies_override():
    dp = "7101"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)

    # The editor shows the update-cycle delete caveat for a live listing.
    page = client.get(f"/gates/{dp}/ads")
    assert page.status_code == 200, page.text
    assert "Editing a live listing" in page.text

    # Save a small edit: address (a human_overrides fact) + price.
    resp = client.post(
        f"/gates/{dp}/ads/copy",
        data={"street_address": "17 Kingfisher Lane", "price_display": "900000"},
    )
    assert resp.status_code == 200, resp.text

    # Editing a live listing reopened it into the update cycle.
    assert _state(dp) == "updated"
    pv = _public_view(dp)
    assert pv["identity"]["street_address"] == "17 Kingfisher Lane"
    assert pv["marketing"]["price_display"] == "R900 000"  # typed number formatted


def test_repost_requires_internal_approval_then_delete_ack():
    dp = "7102"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)

    client.post(f"/gates/{dp}/ads/copy", data={"suburb": "Riverbend"})
    assert _state(dp) == "updated"

    # Blocked before an internal approval is recorded (even with the ack ticked).
    blocked = client.post(f"/post/{dp}/distribute", data={"deleted_old_posts": "1"})
    assert blocked.status_code == 409
    assert "internal approval" in blocked.text.lower()

    # Approve the adverts (gate 2): an update cycle routes to the repost screen.
    approve = client.post(f"/gates/{dp}/ads/approve", follow_redirects=False)
    assert approve.status_code in (204, 303)
    target = approve.headers.get("HX-Redirect") or approve.headers.get("location")
    assert target == f"/post/{dp}"

    # Still blocked until the operator confirms the old posts were deleted.
    no_ack = client.post(f"/post/{dp}/distribute")
    assert no_ack.status_code == 409
    assert "deleted" in no_ack.text.lower()

    # With the confirmation, the repost proceeds and the record goes live.
    ok = client.post(f"/post/{dp}/distribute", data={"deleted_old_posts": "1"})
    assert ok.status_code == 200, ok.text
    assert _state(dp) == "live"


def test_editor_never_exposes_or_writes_popia_fields():
    dp = "7103"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)

    page = client.get(f"/gates/{dp}/ads")
    assert "financials_internal" not in page.text
    assert "id_number" not in page.text

    # A crafted POST of a POPIA field name is ignored (not a known editable
    # field): no override is written and public_view stays PII-free.
    client.post(
        f"/gates/{dp}/ads/copy",
        data={"financials_internal.owner.name": POISON_OWNER, "suburb": "Riverbend"},
    )
    pv = _public_view(dp)
    assert "financials_internal" not in pv
    store = RecordStore(DB_PATH)
    try:
        overrides = store.get(dp).human_overrides or {}
    finally:
        store.close()
    assert all(not k.startswith("financials_internal") for k in overrides)


# --- small-edits: guards, routing, coverage ------------------------------

def test_empty_save_is_a_noop_on_live():
    dp = "7201"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)
    resp = client.post(f"/gates/{dp}/ads/copy", data={})  # nothing filled in
    assert resp.status_code == 200
    assert _state(dp) == "live"  # an empty save must not reopen the listing


def test_first_time_gate2_approve_routes_to_gate3_plain_and_htmx():
    _seed_minimal("7202", "drafted")
    _seed_minimal("7203", "drafted")
    client = _client()
    _login_admin(client)

    plain = client.post("/gates/7202/ads/approve", follow_redirects=False)
    assert plain.status_code == 303
    assert plain.headers["location"] == "/gates/7202/client"
    assert _state("7202") == "approved"

    hx = client.post("/gates/7203/ads/approve", headers={"HX-Request": "true"}, follow_redirects=False)
    assert hx.status_code == 204
    assert hx.headers["HX-Redirect"] == "/gates/7203/client"
    assert _state("7203") == "approved"


def test_distribute_blocked_when_not_postable():
    dp = "7204"
    _seed_minimal(dp, "drafted")
    client = _client()
    _login_admin(client)
    resp = client.post(f"/post/{dp}/distribute")
    assert resp.status_code == 409
    assert "client approval" in resp.text.lower()
    assert _state(dp) == "drafted"


def test_board_reflects_suburb_and_price_override():
    dp = "7205"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)
    client.post(f"/gates/{dp}/ads/copy", data={"suburb": "Riverbend", "price_display": "900000"})
    board = client.get("/board")
    assert board.status_code == 200
    assert "Riverbend" in board.text
    assert "R900 000" in board.text


def test_terms_split_and_method_allowlist():
    dp = "7206"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)
    client.post(f"/gates/{dp}/ads/copy", data={"terms": "Auction 20 Aug 2026\nDeposit 10%", "method": "auction"})
    pv = _public_view(dp)
    assert pv["sale_process"]["terms"] == ["Auction 20 Aug 2026", "Deposit 10%"]
    assert pv["sale_process"]["method"] == "auction"
    # A junk method value is not a valid option, so nothing is written.
    client.post(f"/gates/{dp}/ads/copy", data={"method": "junk"})
    store = RecordStore(DB_PATH)
    try:
        overrides = store.get(dp).human_overrides or {}
    finally:
        store.close()
    assert overrides.get("sale_process.method") == "auction"


def test_blank_fields_do_not_overwrite():
    dp = "7207"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)
    pv0 = _public_view(dp)
    orig_headline = pv0["marketing"]["headline"]
    orig_suburb = pv0["identity"]["suburb"]
    client.post(
        f"/gates/{dp}/ads/copy",
        data={"price_display": "900000", "headline": "", "suburb": "", "street_address": "", "terms": ""},
    )
    pv1 = _public_view(dp)
    assert pv1["marketing"]["price_display"] == "R900 000"
    assert pv1["marketing"]["headline"] == orig_headline
    assert pv1["identity"]["suburb"] == orig_suburb


def test_open_in_canva_link_only_when_edit_url_present():
    import json as _json
    dp = "7208"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)

    # Default html backend -> no design link on the gallery.
    page = client.get(f"/gates/{dp}/ads")
    assert "Open in Canva" not in page.text

    # Inject a Canva edit_url into the (now-rendered) manifest and re-fetch.
    man_path = _TMP / f"DP{dp}" / "artifacts" / "manifest.json"
    man = _json.loads(man_path.read_text(encoding="utf-8"))
    for art in man:
        if art["fmt"] == "demo_ad":
            art["design_id"] = "DAF1"
            art["edit_url"] = "https://www.canva.com/design/DAF1/edit"
    man_path.write_text(_json.dumps(man), encoding="utf-8")

    page2 = client.get(f"/gates/{dp}/ads")
    assert "Open in Canva" in page2.text
    assert "canva.com/design/DAF1/edit" in page2.text


def test_edit_render_failure_is_graceful(monkeypatch):
    dp = "7209"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)

    from webapp.routes import gates as gates_mod

    def _boom(*a, **k):
        raise RuntimeError("Canva quota exceeded")

    monkeypatch.setattr(gates_mod, "_save_edits", _boom)
    resp = client.post(f"/gates/{dp}/ads/copy", data={"suburb": "Riverbend"})
    assert resp.status_code == 200  # graceful, not a 500
    assert "Re-render failed" in resp.text


# --- smoke boot (real lifespan: worker start/stop) -----------------------

def test_app_boots_via_testclient():
    """Boot the app through the real lifespan and confirm the login page loads."""
    with TestClient(app) as client:
        resp = client.get("/login")
        assert resp.status_code == 200
