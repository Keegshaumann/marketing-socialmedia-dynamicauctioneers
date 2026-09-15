"""OTP screen (M10, D114): the routes, and that it stays apart from the proposals. Offline, key-free.

Built like ``test_proposal_webapp.py``: a fresh app on its own database per test.
"""

from __future__ import annotations

import html
from types import SimpleNamespace
from urllib.parse import unquote

import fitz
import pytest
from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

from engine.otpgen.store import OtpStore
from engine.proposal.lightstone import LightstoneFacts
from engine.proposal.store import ProposalStore
from engine.schema import FinancialsInternal, Identity, Owner, PropertyRecord
from engine.store import RecordStore

EMAIL = "properties@dynamicauctioneers.co.za"
PASSWORD = "otp-pass-123"
HX = {"HX-Request": "true"}


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "engine.db"))
    monkeypatch.setenv("APP_SECRET", "test-secret-otps")
    monkeypatch.setenv("ENGINE_ALLOW_INSECURE_COOKIE", "1")
    for key in ("ANTHROPIC_API_KEY", "MS_GRAPH_TENANT_ID", "MS_GRAPH_CLIENT_ID", "MS_GRAPH_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)

    from webapp import auth, models
    from webapp.main import create_app

    db = models.init_db()
    out = tmp_path / "out"
    out.mkdir()
    models.set_setting(db, "output_root", str(out))
    models.create_user(db, email=EMAIL, pw_hash=auth.hash_password(PASSWORD), role="properties")
    client = TestClient(create_app())
    resp = client.post("/login", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return SimpleNamespace(client=client, db=db, out=out, monkeypatch=monkeypatch)


def _start(env, dp="9201", base="/otps"):
    resp = env.client.post(f"{base}/new", data={"dp": dp}, follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"]


def _get(env, dp):
    with OtpStore(env.db) as store:
        return store.get(dp)


def _form(**overrides):
    form = {
        "_full_form": "1",
        "seller_capacity": "liquidation",
        "seller_name": "TESTCO (PTY) LTD",
        "seller_id": "2014/203299/07",
        "masters_ref": "G123/2026",
        "erf_count": "1",
        "erf-1-legal": 'ERF 99, TOWN "TESTVILLE EXT 1", GAUTENG',
        "erf-1-known_as": "1 TEST STREET, TESTVILLE, GAUTENG",
        "erf-1-title_deed": "T12345/2001",
        "erf-1-extent": "2018 m2",
        "deposit_pct": "10",
        "guarantee_days": "45",
        "interest_pct": "12",
        "confirmation_days": "30",
        "commission_pct": "7,5",
        "commission_payer": "seller",
        "commission_vat": "yes",
        "body_corporate": "",
        "extra_conditions": "",
    }
    form.update(overrides)
    return form


def _pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page(width=595, height=842).insert_text((40, 200), "Property Details")
    return doc.tobytes()


def _upload(env, dp, name, data, form=None):
    return env.client.post(f"/otps/{dp}/upload/lightstone", files={"files": (name, data)}, data=form or {}, headers=HX)


# --- access and starting -------------------------------------------------------------

def test_the_screen_needs_a_properties_login(app_env):
    from webapp import auth, models

    anonymous = TestClient(app_env.client.app)
    resp = anonymous.get("/otps", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/login"

    models.create_user(app_env.db, email="marketing@dynamicauctioneers.co.za", pw_hash=auth.hash_password(PASSWORD), role="marketing")
    marketing = TestClient(app_env.client.app)
    marketing.post("/login", data={"email": "marketing@dynamicauctioneers.co.za", "password": PASSWORD}, follow_redirects=False)
    assert marketing.get("/otps", follow_redirects=False).status_code == 403


def test_start_open_and_list(app_env):
    assert _start(app_env, "DP9201") == "/otps/9201"
    page = app_env.client.get("/otps/9201")
    assert page.status_code == 200
    text = html.unescape(page.text)
    assert "Offer to purchase 9201" in text
    assert "The commission % is missing." in text  # the checks show from the start
    listing = app_env.client.get("/otps")
    assert "/otps/9201" in listing.text and "Draft" in listing.text
    assert app_env.client.post("/otps/new", data={"dp": "Moremi"}, follow_redirects=False).headers["location"] == "/otps?error=dp"


def test_the_switcher_and_nav_reach_both_generators(app_env):
    listing = html.unescape(app_env.client.get("/proposals").text)
    assert "Property documents" in listing and 'href="/otps"' in listing and 'href="/reports"' in listing

    _start(app_env, "9202", base="/proposals")
    proposal_page = app_env.client.get("/proposals/9202").text
    assert 'action="/otps/new"' in proposal_page and 'value="9202"' in proposal_page

    _start(app_env, "9202")
    otp_page = app_env.client.get("/otps/9202").text
    assert 'action="/proposals/new"' in otp_page and 'aria-current="page">OTP 9202' in otp_page


def test_the_otp_starts_from_the_proposal_but_stays_a_separate_record(app_env):
    _start(app_env, "9203", base="/proposals")
    with ProposalStore(app_env.db) as store:
        proposal = store.get("9203")
        proposal.seller_capacity = "insolvent"
        proposal.seller_name = "TESTCO (PTY) LTD"
        proposal.erven[0].title_deed = "T12345/2001"
        store.save(proposal)
    assert _get(app_env, "9203") is None

    _start(app_env, "9203")
    otp = _get(app_env, "9203")
    assert otp.seller_capacity == "insolvent" and otp.seller_name == "TESTCO (PTY) LTD"
    assert otp.erven[0].title_deed == "T12345/2001"
    assert "auction proposal" in otp.prefill_note

    app_env.client.post("/otps/9203/save", data=_form(seller_name="OTHERCO (PTY) LTD"), headers=HX)
    with ProposalStore(app_env.db) as store:
        assert store.get("9203").seller_name == "TESTCO (PTY) LTD"
    assert "/otps/9203" not in app_env.client.get("/proposals").text


def test_a_dp_on_the_board_starts_from_its_record(app_env):
    store = RecordStore(app_env.db)
    try:
        store.upsert(PropertyRecord(
            dp="9204",
            identity=Identity(legal_description='Erf 99, Town "Testville"', street_address="1 Test Street", title_deed_no="T12345/2001"),
            financials_internal=FinancialsInternal(owner=Owner(name="Testco (Pty) Ltd", id_number="2014/203299/07")),
        ), state="extracted")
    finally:
        store.close()
    _start(app_env, "9204")
    otp = _get(app_env, "9204")
    assert otp.seller_name == "TESTCO (PTY) LTD" and otp.erven[0].title_deed == "T12345/2001"
    assert "marketing record" in otp.prefill_note


# --- saving ------------------------------------------------------------------------------

def test_save_keeps_the_fields_and_names_what_it_refused(app_env):
    _start(app_env, "9205")
    resp = app_env.client.post(
        "/otps/9205/save",
        data=_form(guarantee_days="forty", body_corporate="Testpark", extra_conditions="Sold as is.\n\nOccupation on registration."),
        headers=HX,
    )
    assert resp.status_code == 200 and 'id="checks"' in resp.text
    assert "not a number of days" in resp.text
    otp = _get(app_env, "9205")
    assert otp.guarantee_days is None and str(otp.commission_pct) == "7.5"
    assert otp.body_corporate == "Testpark"
    assert otp.extra_conditions == ["Sold as is.", "Occupation on registration."]


def test_adding_a_property_reloads_at_a_new_url(app_env):
    _start(app_env, "9206")
    url = app_env.client.post("/otps/9206/save", data=_form(action="add_erf"), headers=HX).headers["HX-Redirect"]
    path, _, rest = url.partition("?")
    assert path == "/otps/9206" and rest.startswith("at=") and url.endswith("#property")
    assert len(_get(app_env, "9206").erven) == 2


# --- the Lightstone report ------------------------------------------------------------------

def test_a_lightstone_upload_without_a_key_is_kept_and_says_so(app_env):
    _start(app_env, "9207")
    resp = _upload(app_env, "9207", "EVM_Report.pdf", _pdf_bytes())
    assert "Anthropic key" in unquote(resp.headers["HX-Redirect"])
    skipped = _upload(app_env, "9207", "notes.txt", b"hello")
    assert "skipped" in unquote(skipped.headers["HX-Redirect"])
    otp = _get(app_env, "9207")
    assert otp.lightstone_files == ["lightstone/EVM_Report.pdf"] and otp.prefill_job == 0
    assert (app_env.out / "DP9207" / "otp" / "lightstone" / "EVM_Report.pdf").is_file()


def test_the_prefill_job_fills_the_otp(app_env):
    from engine.proposal import lightstone
    from webapp import jobs

    app_env.monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    app_env.monkeypatch.setattr(lightstone, "read_facts", lambda path, client=None: LightstoneFacts(
        owner_name="TESTCO (PTY) LTD", owner_id="2014/203299/07", title_type="freehold",
        legal_description='ERF 99, TOWN "TESTVILLE EXT 1", GAUTENG', street_address="1 TEST STREET, TESTVILLE, GAUTENG",
        title_deed="T12345/2001", extent="2018 m2",
    ))
    _start(app_env, "9208")
    _upload(app_env, "9208", "EVM_Report.pdf", _pdf_bytes())
    assert _get(app_env, "9208").prefill_job

    jobs.drain(app_env.db)
    otp = _get(app_env, "9208")
    assert otp.seller_name == "TESTCO (PTY) LTD" and otp.erven[0].extent == "2018 m2"
    assert otp.lightstone_read == ["lightstone/EVM_Report.pdf"]
    assert app_env.client.get("/otps/9208/prefill-status", headers=HX).headers.get("HX-Refresh") == "true"


# --- generating ----------------------------------------------------------------------------

def test_generate_is_refused_until_the_checks_pass_then_builds_the_word_file(app_env):
    _start(app_env, "9209")
    refused = app_env.client.post("/otps/9209/generate", data=_form(commission_pct=""), headers=HX)
    assert "Not generated" in refused.text
    assert not _get(app_env, "9209").docx_file

    built = app_env.client.post("/otps/9209/generate", data=_form(body_corporate="Testpark"), headers=HX)
    assert "OTP generated" in built.text, built.text
    otp = _get(app_env, "9209")
    assert otp.docx_file == "out/9209 - OTP.docx" and otp.generated_by == EMAIL

    download = app_env.client.get("/otps/9209/download")
    assert download.status_code == 200
    assert "9209 - OTP.docx" in unquote(download.headers["content-disposition"])
    doc = Document(str(app_env.out / "DP9209" / "otp" / otp.docx_file))
    paras = ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in doc.element.body.iter(qn("w:p"))]
    assert "THE JOINT LIQUIDATORS OF:" in paras
    assert "TESTCO (PTY) LTD, REGISTRATION NUMBER 2014/203299/07" in paras
    assert "The sale is subject to the rules and regulations of the TESTPARK Body Corporate." in paras
    assert not any("{{" in p for p in paras)


def test_download_before_generating_is_a_404(app_env):
    _start(app_env, "9210")
    assert app_env.client.get("/otps/9210/download").status_code == 404
