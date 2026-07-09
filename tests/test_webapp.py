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


# --- smoke boot (real lifespan: worker start/stop) -----------------------

def test_app_boots_via_testclient():
    """Boot the app through the real lifespan and confirm the login page loads."""
    with TestClient(app) as client:
        resp = client.get("/login")
        assert resp.status_code == 200
