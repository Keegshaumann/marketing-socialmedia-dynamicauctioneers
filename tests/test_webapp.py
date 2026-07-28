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
MARKETING_EMAIL = "marketing@dynamicauctioneers.co.za"
MARKETING_PW = "marketing-pass-123"


# --- one-time platform setup ---------------------------------------------
# Seed the bootstrap admin (captures its printed temp password) plus an
# approver-role and a marketing-role account. init_db is idempotent; the app
# already ran it at import.

DB_PATH = models.init_db()
ADMIN_PW = auth.seed_admin(DB_PATH)  # temp password on first run, else None
models.set_setting(DB_PATH, "output_root", str(_TMP))  # contain all writes

for _email, _pw, _role in (
    (APPROVER_EMAIL, APPROVER_PW, "approver"),
    (MARKETING_EMAIL, MARKETING_PW, "marketing"),
):
    if models.get_user(DB_PATH, _email) is None:
        models.create_user(
            DB_PATH, email=_email, pw_hash=auth.hash_password(_pw), role=_role
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
    _golden_clone(dp)  # carries the garages PHYSICAL_CONFLICT block flag
    client = _client()
    _login_admin(client)

    # Refused: the physical conflict is untouched (no source pick submitted).
    refused = client.post(f"/gates/{dp}/verify", data={}, follow_redirects=False)
    assert refused.status_code == 400
    assert _state(dp) == "extracted"

    # Accepted: picking a source for the garage conflict resolves it, signs the
    # gate off and drafts the record onto gate 2.
    ok = client.post(
        f"/gates/{dp}/verify",
        data={
            "conflict_source__garages": "property_report",
            "conflict_reason__garages": "Agent confirmed no garages; guest parking only.",
        },
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


def test_gate1_override_to_non_default_source_swaps_the_value():
    # The garage conflict defaults to the Property Report (none -> 0 garages).
    # Overriding to Lightstone must write Lightstone's value (3) onto the record.
    _needs_golden()
    dp = "9007"
    _golden_clone(dp)
    client = _client()
    _login_admin(client)

    ok = client.post(
        f"/gates/{dp}/verify",
        data={
            "conflict_source__garages": "lightstone",
            "conflict_reason__garages": "Seller confirmed the 3 garages on the deeds.",
        },
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert _state(dp) == "drafted"

    store = RecordStore(DB_PATH)
    try:
        record = store.get(dp)
    finally:
        store.close()
    assert record.physical.garages == 3  # Lightstone's value now stands
    assert record.physical.conflicts[0].resolved_source == "lightstone"


def test_board_delete_removes_the_record():
    _needs_golden()
    dp = "9008"
    _golden_clone(dp)
    client = _client()
    _login_admin(client)

    assert _state(dp) == "extracted"
    resp = client.post(f"/board/{dp}/delete", follow_redirects=False)
    assert resp.status_code == 200  # returns the refreshed ledger body

    store = RecordStore(DB_PATH)
    try:
        assert store.get(dp) is None  # gone
    finally:
        store.close()


def test_gate2_auto_generate_headline_returns_filled_input():
    _needs_golden()
    dp = "9010"
    _golden_clone(dp)
    client = _client()
    _login_admin(client)

    resp = client.post(f"/gates/{dp}/ads/headline")
    assert resp.status_code == 200
    assert 'name="headline"' in resp.text  # the swap-in input
    assert "Pelham North" in resp.text  # a headline was generated from the facts


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


def test_hermetic_env_strips_ghl_creds_despite_webapp_dotenv():
    # webapp.main calls load_dotenv() at import; the autouse hermetic fixture MUST
    # still guarantee no live GHL/Anthropic credentials are visible during a test,
    # so a distribution test can never fire a live post to DA's real pages.
    import os

    import webapp.main  # noqa: F401 - ensures the module + its load_dotenv ran

    for var in (
        "GHL_API_TOKEN", "GHL_USER_ID", "GHL_LOCATION_ID",
        "GHL_ACCOUNT_MAP", "GHL_POST_STATUS", "ANTHROPIC_API_KEY",
    ):
        assert os.getenv(var) in (None, ""), f"{var} leaked into a test"


def test_post_screen_shows_when_to_post_selector():
    dp = "7330"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)
    body = client.get(f"/post/{dp}").text
    assert 'name="post_mode"' in body
    assert 'value="schedule"' in body
    assert 'type="datetime-local"' in body


def test_post_screen_shows_draft_lock_banner(monkeypatch):
    monkeypatch.setenv("GHL_POST_STATUS", "draft")
    dp = "7331"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)
    body = client.get(f"/post/{dp}").text
    assert "Draft-only safeguard is on" in body


def test_distribute_schedule_without_time_is_blocked():
    dp = "7332"
    _seed_golden_live(dp)  # 'live' is a postable state
    client = _client()
    _login_admin(client)
    r = client.post(f"/post/{dp}/distribute", data={"post_mode": "schedule"})
    assert r.status_code == 409
    assert "date and time" in r.text.lower()


def test_posting_choice_stamps_sast_timezone():
    from webapp.routes.post import _posting_choice

    status, when = _posting_choice({"post_mode": "schedule", "schedule_at": "2026-07-24T17:00"})
    assert status == "scheduled"
    assert when == "2026-07-24T17:00:00+02:00"  # SAST, not read as UTC by GHL
    assert _posting_choice({"post_mode": "now"}) == ("published", None)
    assert _posting_choice({"post_mode": "draft"}) == ("draft", None)
    assert _posting_choice({}) == ("draft", None)  # default is draft


def test_distribute_without_token_does_not_claim_a_live_post():
    # No token (hermetic env + empty Settings) -> the post did not run; the
    # summary must say so, never "posted live".
    dp = "7333"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)
    body = client.post(f"/post/{dp}/distribute", data={"post_mode": "now"}).text
    assert "did not run" in body.lower()
    assert "posted live" not in body.lower()


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


# --- gate-2 photo upload / management ------------------------------------

# A minimal valid 1x1 PNG for upload tests.
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _png_of(width: int, height: int) -> bytes:
    """A real PNG of a given pixel size, for the low-res-warning tests."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 180, 120)).save(buf, format="PNG")
    return buf.getvalue()


def _seed_live_no_photos(dp: str) -> None:
    """Golden clone at ``live`` with its photos cleared, so uploads start clean."""
    _needs_golden()
    record = PropertyRecord.model_validate_json(GOLDEN_RECORD.read_text(encoding="utf-8"))
    record.dp = dp
    if record.marketing is None:
        record.marketing = Marketing()
    record.marketing.hero_photo = None
    record.marketing.gallery = []
    store = RecordStore(DB_PATH)
    try:
        store.upsert(record, state="live")
    finally:
        store.close()


def test_photo_upload_hero_delete_and_serve():
    dp = "7301"
    _seed_live_no_photos(dp)
    client = _client()
    _login_admin(client)

    resp = client.post(
        f"/gates/{dp}/ads/photos/upload",
        files=[
            ("files", ("front.png", _PNG_1X1, "image/png")),
            ("files", ("back.png", _PNG_1X1, "image/png")),
        ],
    )
    assert resp.status_code == 200, resp.text
    assert _state(dp) == "updated"  # editing photos reopens the update cycle
    pv = _public_view(dp)
    assert pv["marketing"]["hero_photo"] == "photos/front.png"  # first upload is the lead
    assert "photos/back.png" in (pv["marketing"]["gallery"] or [])

    # the serve route returns the file (this is what makes advert-preview images resolve)
    served = client.get(f"/gates/{dp}/ads/photos/front.png")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content[:8] == b"\x89PNG\r\n\x1a\n"

    # set the other photo as the lead
    client.post(f"/gates/{dp}/ads/photos/hero", data={"name": "back.png"})
    assert _public_view(dp)["marketing"]["hero_photo"] == "photos/back.png"

    # delete one
    client.post(f"/gates/{dp}/ads/photos/delete", data={"name": "front.png"})
    pv = _public_view(dp)
    remaining = [pv["marketing"].get("hero_photo")] + (pv["marketing"].get("gallery") or [])
    assert "photos/front.png" not in remaining


def test_photo_upload_rejects_non_image_and_does_not_reopen():
    dp = "7302"
    _seed_live_no_photos(dp)
    client = _client()
    _login_admin(client)
    resp = client.post(
        f"/gates/{dp}/ads/photos/upload",
        files=[("files", ("notes.txt", b"not an image", "text/plain"))],
    )
    assert resp.status_code == 200
    assert "No photos added" in resp.text
    assert not (_public_view(dp)["marketing"].get("gallery") or [])
    assert _state(dp) == "live"  # a rejected upload is not an edit; the listing stays live


def test_photo_serve_missing_returns_404():
    dp = "7303"
    _seed_live_no_photos(dp)
    client = _client()
    _login_admin(client)
    assert client.get(f"/gates/{dp}/ads/photos/nope.png").status_code == 404


def test_photo_delete_hero_promotes_next_to_lead():
    dp = "7304"
    _seed_live_no_photos(dp)
    client = _client()
    _login_admin(client)
    client.post(f"/gates/{dp}/ads/photos/upload", files=[
        ("files", ("front.png", _PNG_1X1, "image/png")),
        ("files", ("back.png", _PNG_1X1, "image/png")),
    ])
    assert _public_view(dp)["marketing"]["hero_photo"] == "photos/front.png"
    client.post(f"/gates/{dp}/ads/photos/delete", data={"name": "front.png"})
    assert _public_view(dp)["marketing"]["hero_photo"] == "photos/back.png"  # next promoted


def test_photo_noops_do_not_reopen_live_listing():
    dp = "7308"
    _seed_live_no_photos(dp)  # live, no photos
    client = _client()
    _login_admin(client)
    # deleting a non-existent photo is a no-op and must not knock live -> updated
    r = client.post(f"/gates/{dp}/ads/photos/delete", data={"name": "ghost.png"})
    assert r.status_code == 200 and "No change" in r.text
    assert _state(dp) == "live"
    # setting a non-existent (or already-lead) photo as lead is likewise a no-op
    r2 = client.post(f"/gates/{dp}/ads/photos/hero", data={"name": "ghost.png"})
    assert r2.status_code == 200 and "No change" in r2.text
    assert _state(dp) == "live"


def test_photo_upload_on_drafted_renders_without_reopening():
    dp = "7305"
    _needs_golden()
    record = PropertyRecord.model_validate_json(GOLDEN_RECORD.read_text(encoding="utf-8"))
    record.dp = dp
    record.marketing = record.marketing or Marketing()
    record.marketing.hero_photo = None
    record.marketing.gallery = []
    store = RecordStore(DB_PATH)
    try:
        store.upsert(record, state="drafted")
    finally:
        store.close()
    client = _client()
    _login_admin(client)
    r = client.post(f"/gates/{dp}/ads/photos/upload", files=[("files", ("a.png", _PNG_1X1, "image/png"))])
    assert r.status_code == 200
    assert _public_view(dp)["marketing"]["hero_photo"] == "photos/a.png"
    assert _state(dp) == "drafted"  # not live, so no reopen — just re-rendered


def test_photo_upload_oversized_rejected(monkeypatch):
    dp = "7306"
    _seed_live_no_photos(dp)
    client = _client()
    _login_admin(client)
    from webapp.routes import gates as gates_mod
    monkeypatch.setattr(gates_mod, "_MAX_PHOTO_BYTES", 4)  # the 1x1 PNG exceeds this
    r = client.post(f"/gates/{dp}/ads/photos/upload", files=[("files", ("big.png", _PNG_1X1, "image/png"))])
    assert r.status_code == 200
    assert "No photos added" in r.text
    assert not (_public_view(dp)["marketing"].get("gallery") or [])
    assert _state(dp) == "live"


def test_photo_upload_dedups_duplicate_filename():
    dp = "7307"
    _seed_live_no_photos(dp)
    client = _client()
    _login_admin(client)
    client.post(f"/gates/{dp}/ads/photos/upload", files=[
        ("files", ("front.png", _PNG_1X1, "image/png")),
        ("files", ("front.png", _PNG_1X1, "image/png")),
    ])
    pv = _public_view(dp)
    allp = [pv["marketing"]["hero_photo"]] + (pv["marketing"]["gallery"] or [])
    assert "photos/front.png" in allp and "photos/front_1.png" in allp
    assert client.get(f"/gates/{dp}/ads/photos/front.png").status_code == 200
    assert client.get(f"/gates/{dp}/ads/photos/front_1.png").status_code == 200


def test_photo_upload_response_swaps_gallery_and_photos_oob():
    dp = "7310"
    _seed_live_no_photos(dp)
    client = _client()
    _login_admin(client)
    body = client.post(f"/gates/{dp}/ads/photos/upload", files=[("files", ("front.png", _PNG_1X1, "image/png"))]).text
    assert 'id="gallery"' in body
    assert 'id="photos"' in body
    assert 'hx-swap-oob="true"' in body
    assert f"/gates/{dp}/ads/photos/front.png" in body   # thumbnail url
    assert "badge--ok" in body                            # lead badge


def test_advert_preview_img_points_at_serve_route():
    dp = "7311"
    _seed_live_no_photos(dp)
    client = _client()
    _login_admin(client)
    client.post(f"/gates/{dp}/ads/photos/upload", files=[("files", ("front.png", _PNG_1X1, "image/png"))])
    html = client.get(f"/gates/{dp}/ads/artifact/demo_ad").text
    # resolves to /gates/{dp}/ads/photos/front.png in the sandboxed preview iframe
    assert 'src="../photos/front.png"' in html


def test_photo_low_res_warns_but_still_saves():
    dp = "7320"
    _seed_live_no_photos(dp)
    client = _client()
    _login_admin(client)
    # A small photo (like the PDF thumbnails) is flagged but not blocked.
    body = client.post(
        f"/gates/{dp}/ads/photos/upload",
        files=[("files", ("tiny.png", _png_of(276, 207), "image/png"))],
    ).text
    assert "Low-res" in body        # warned
    assert "276x207" in body        # shows the actual size
    # non-blocking: it was still saved and used as the lead
    assert _public_view(dp)["marketing"]["hero_photo"] == "photos/tiny.png"


def test_photo_full_res_not_flagged():
    dp = "7321"
    _seed_live_no_photos(dp)
    client = _client()
    _login_admin(client)
    body = client.post(
        f"/gates/{dp}/ads/photos/upload",
        files=[("files", ("big.png", _png_of(1200, 1200), "image/png"))],
    ).text
    assert "Low-res" not in body    # 1200px shorter side clears the 1080 bar


def test_image_dimensions_none_for_unreadable(tmp_path):
    from webapp.routes.gates import _image_dimensions

    good = tmp_path / "good.png"
    good.write_bytes(_png_of(1200, 800))
    assert _image_dimensions(good) == (1200, 800)

    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not really a png")
    assert _image_dimensions(bad) is None            # corrupt -> no false warning
    assert _image_dimensions(tmp_path / "missing.png") is None  # absent -> no crash


# --- smoke boot (real lifespan: worker start/stop) -----------------------

def test_app_boots_via_testclient():
    """Boot the app through the real lifespan and confirm the login page loads."""
    with TestClient(app) as client:
        resp = client.get("/login")
        assert resp.status_code == 200


def _enable_canva_picker(monkeypatch, sets_json: str) -> None:
    """Make the design picker (D33) live: named sets + a usable Canva backend.

    The picker only shows when the active renderer routes through Canva and the
    backend reports available, so the tests fake availability and the render
    itself (no credentials, no network) the same way test_mixed does.
    """
    import pathlib

    from engine.render import canva_backend as cb
    from engine.render.base import Artifact as RenderArtifact

    monkeypatch.setenv("CANVA_TEMPLATE_MAP", sets_json)
    monkeypatch.setenv("ENGINE_RENDERER", "mixed")
    monkeypatch.setattr(cb.CanvaBackend, "available", lambda self: (True, "ok"))

    def _fake_render(self, request):
        out = pathlib.Path(request.output_root) / f"DP{request.dp}" / "artifacts"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{request.fmt}.pdf"
        path.write_bytes(b"%PDF-1.4 canva")
        return RenderArtifact(
            dp=request.dp, fmt=request.fmt, backend="canva", path=str(path),
            mime="application/pdf",
        )

    monkeypatch.setattr(cb.CanvaBackend, "render", _fake_render)


_TWO_SETS = '{"Classic gold": {"demo_ad": "TPL_A"}, "Modern dark": {"demo_ad": "TPL_B"}}'


def test_gate2_template_picker_lists_sets_and_saves_the_pick(monkeypatch):
    """The design picker (D33) lists the sets, saves a pick, and can clear it."""
    _enable_canva_picker(monkeypatch, _TWO_SETS)
    dp = "7104"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)

    page = client.get(f"/gates/{dp}/ads")
    assert page.status_code == 200, page.text
    assert 'name="template_set"' in page.text
    assert "Follow the default (Classic gold)" in page.text
    assert "Modern dark" in page.text

    # An untouched form (the select submits the current blank pick) is not an
    # edit: no pick is pinned, no re-render, no audit row.
    resp = client.post(f"/gates/{dp}/ads/copy", data={"template_set": ""})
    assert resp.status_code == 200, resp.text
    assert "No changes" in resp.text
    assert _public_view(dp)["marketing"].get("template_set") is None

    # A real pick saves.
    resp = client.post(f"/gates/{dp}/ads/copy", data={"template_set": "Modern dark"})
    assert resp.status_code == 200, resp.text
    assert _public_view(dp)["marketing"]["template_set"] == "Modern dark"

    # Re-submitting the same pick is not an edit.
    resp = client.post(f"/gates/{dp}/ads/copy", data={"template_set": "Modern dark"})
    assert "No changes" in resp.text

    # A crafted set name the picker does not offer is ignored.
    client.post(f"/gates/{dp}/ads/copy", data={"template_set": "Evil set"})
    assert _public_view(dp)["marketing"]["template_set"] == "Modern dark"

    # Blank ("Follow the default") clears the pick back to None.
    resp = client.post(f"/gates/{dp}/ads/copy", data={"template_set": ""})
    assert resp.status_code == 200, resp.text
    assert _public_view(dp)["marketing"].get("template_set") is None


def test_gate2_template_picker_hidden_with_a_single_set(monkeypatch):
    """One configured set means no choice to make: the picker stays hidden."""
    _enable_canva_picker(monkeypatch, '{"demo_ad": "TPL_A"}')
    dp = "7105"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)
    page = client.get(f"/gates/{dp}/ads")
    assert page.status_code == 200, page.text
    assert 'name="template_set"' not in page.text


def test_gate2_template_picker_hidden_when_renderer_is_html(monkeypatch):
    """Two sets configured but the renderer never touches Canva: a pick would
    be a silent no-op, so the picker must not show and a crafted POST must not
    land on the record."""
    monkeypatch.setenv("CANVA_TEMPLATE_MAP", _TWO_SETS)
    monkeypatch.delenv("ENGINE_RENDERER", raising=False)  # default: html
    dp = "7106"
    _seed_golden_live(dp)
    client = _client()
    _login_admin(client)

    page = client.get(f"/gates/{dp}/ads")
    assert page.status_code == 200, page.text
    assert 'name="template_set"' not in page.text

    client.post(f"/gates/{dp}/ads/copy", data={"template_set": "Modern dark"})
    assert _public_view(dp)["marketing"].get("template_set") is None


# --- RBAC (D34): marketing runs everything operational incl. gates;
#     Settings (connection strings) is admin-only --------------------------

def _login_marketing(client):
    resp = _login(client, MARKETING_EMAIL, MARKETING_PW)
    assert resp.status_code == 303, resp.text


def test_marketing_can_reach_the_gates():
    """The marketing role can now open the gate screens (verify/approve)."""
    dp = "7201"
    _seed_golden_live(dp)
    client = _client()
    _login_marketing(client)
    assert client.get(f"/gates/{dp}/ads").status_code == 200


def test_marketing_cannot_reach_settings():
    """Connection strings are off-limits to operational staff (view + save)."""
    client = _client()
    _login_marketing(client)
    assert client.get("/settings").status_code == 403
    assert client.post("/settings/credentials", data={"ghl_token": "x"}).status_code == 403
    assert client.post("/settings/channels", data={}).status_code == 403
    assert client.post("/settings/approvers", data={"approver_emails": "a@b.co"}).status_code == 403


def test_admin_can_reach_settings():
    client = _client()
    _login_admin(client)
    assert client.get("/settings").status_code == 200


def test_settings_link_hidden_for_marketing_shown_for_admin():
    m = _client(); _login_marketing(m)
    assert 'href="/settings"' not in m.get("/board").text
    a = _client(); _login_admin(a)
    assert 'href="/settings"' in a.get("/board").text
