"""Proposals screen (M9, D103): the routes the properties team uses. Offline, key-free.

Each test builds a fresh app on its own database. ENGINE_DB is read per request
(no lifespan runs under a bare TestClient), so pointing it at a temp file with
monkeypatch keeps these tests apart from test_webapp.py's shared app.
"""

from __future__ import annotations

import html
import io
from datetime import date, timedelta
from types import SimpleNamespace
from urllib.parse import unquote

import fitz
import pytest
from docx import Document
from fastapi.testclient import TestClient
from PIL import Image

from engine.proposal.lightstone import LightstoneFacts
from engine.proposal.store import ProposalStore
from engine.schema import FinancialsInternal, Identity, Owner, PropertyRecord, SaleProcess
from engine.store import RecordStore

EMAIL = "properties@dynamicauctioneers.co.za"
PASSWORD = "proposal-pass-123"
HX = {"HX-Request": "true"}


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "engine.db"))
    monkeypatch.setenv("APP_SECRET", "test-secret-proposals")
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
    return SimpleNamespace(client=client, db=db, out=out, tmp=tmp_path, monkeypatch=monkeypatch)


def _start(env, dp="9101"):
    resp = env.client.post("/proposals/new", data={"dp": dp}, follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"]


def _get(env, dp):
    with ProposalStore(env.db) as store:
        return store.get(dp)


def _form(**overrides):
    form = {
        "_full_form": "1",
        "seller_capacity": "liquidation",
        "seller_name": "TESTCO (PTY) LTD",
        "seller_id": "2014/203299/07",
        "masters_ref": "",
        "erf_count": "1",
        "erf-1-legal": 'ERF 99, TOWN "TESTVILLE EXT 1", GAUTENG',
        "erf-1-known_as": "1 TEST STREET, TESTVILLE, GAUTENG",
        "erf-1-title_deed": "T12345/2001",
        "erf-1-extent": "",
        "auction_date": (date.today() + timedelta(days=40)).isoformat(),
        "time_from": "10:00",
        "time_to": "12:00",
        "channel": "online",
        "venue_address": "",
        "administrator": "Andrea Oberholzer",
        "deadline": (date.today() + timedelta(days=10)).isoformat(),
        "line_count": "2",
        "line-1-media": "SAIA featured ad",
        "line-1-description": "FEATURED WEB LISTING",
        "line-1-placement": "IMMEDIATELY",
        "line-1-cost": "2600",
        "line-2-media": "WhatsApp",
        "line-2-description": "DATABASE NOTIFICATIONS",
        "line-2-placement": "IMMEDIATELY",
        "line-2-cost": "FREE",
        "deposit_pct": "10",
        "confirmation_days": "30",
        "commission_pct": "7,5",
        "commission_payer": "",
        "commission_vat": "",
    }
    form.update(overrides)
    return form


def _otp_bytes() -> bytes:
    doc = Document()
    for line in (
        "INDEX TO DEED OF SALE",
        "THE JOINT LIQUIDATORS OF: TESTCO (PTY) LTD REGISTRATION NUMBER 2014/203299/07",
        "HELD BY TITLE DEED: T12345/2001",
        "A cash deposit of 10% (Ten Percent) of the PURCHASE PRICE is payable on SIGNATURE DATE.",
        "The commission calculated at 7,5% (SEVEN AND HALF PERCENT) of the purchase price plus VAT, will be payable by the SELLER.",
        "within a period of 30 (THIRTY) days (the CONFIRMATION PERIOD).",
    ):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _png_bytes(size=(1080, 1350)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (25, 22, 19)).save(buf, "PNG")
    return buf.getvalue()


def _evm_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((40, 200), "Property Details")
    page.insert_text((40, 630), "EVM Valuation Details")
    doc.new_page(width=595, height=842).insert_text((40, 24), "Owner Details")
    return doc.tobytes()


def _upload(env, dp, kind, name, data, form=None):
    return env.client.post(
        f"/proposals/{dp}/upload/{kind}", files={"files": (name, data)}, data=form or {}, headers=HX
    )


# --- access and starting -------------------------------------------------------------

def test_the_screen_needs_a_login(app_env):
    anonymous = TestClient(app_env.client.app)
    resp = anonymous.get("/proposals", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/login"


def test_start_open_and_list(app_env):
    assert _start(app_env, "DP9101") == "/proposals/9101"
    page = app_env.client.get("/proposals/9101")
    assert page.status_code == 200
    text = html.unescape(page.text)
    assert "Auction proposal 9101" in text
    assert "Upload this property's OTP" in text  # the checks show from the start
    listing = app_env.client.get("/proposals")
    assert "/proposals/9101" in listing.text and "Draft" in listing.text


def test_a_bad_dp_number_is_refused(app_env):
    resp = app_env.client.post("/proposals/new", data={"dp": "Moremi"}, follow_redirects=False)
    assert resp.headers["location"] == "/proposals?error=dp"


def test_a_dp_on_the_board_starts_from_its_record(app_env):
    store = RecordStore(app_env.db)
    try:
        store.upsert(PropertyRecord(
            dp="9102",
            identity=Identity(legal_description='Erf 99, Town "Testville"', street_address="1 Test Street", title_deed_no="T12345/2001"),
            financials_internal=FinancialsInternal(owner=Owner(name="Testco (Pty) Ltd", id_number="2014/203299/07")),
            sale_process=SaleProcess(auction_channel="Online"),
        ), state="extracted")
    finally:
        store.close()
    _start(app_env, "9102")
    proposal = _get(app_env, "9102")
    assert proposal.seller_name == "TESTCO (PTY) LTD"
    assert proposal.erven[0].title_deed == "T12345/2001"
    assert proposal.channel == "online"
    assert "marketing record" in proposal.prefill_note


# --- saving ------------------------------------------------------------------------------

def test_save_keeps_the_fields_and_runs_the_checks(app_env):
    _start(app_env, "9103")
    resp = app_env.client.post("/proposals/9103/save", data=_form(), headers=HX)
    assert resp.status_code == 200
    assert 'id="checks"' in resp.text and "Saved" in resp.text
    assert "R 2 990.00" in resp.text  # R 2 600 plus VAT, in the totals swap
    proposal = _get(app_env, "9103")
    assert proposal.seller_name == "TESTCO (PTY) LTD"
    assert proposal.budget[1].cost == "FREE"


def test_adding_a_property_reloads_at_the_property_panel(app_env):
    _start(app_env, "9104")
    resp = app_env.client.post("/proposals/9104/save", data=_form(action="add_erf"), headers=HX)
    assert resp.headers["HX-Redirect"].endswith("#property")
    assert len(_get(app_env, "9104").erven) == 2


def test_values_that_cannot_be_read_are_named(app_env):
    _start(app_env, "9105")
    resp = app_env.client.post("/proposals/9105/save", data=_form(time_from="25:99", deposit_pct="ten"), headers=HX)
    assert "not a time" in resp.text and "not a number" in resp.text
    proposal = _get(app_env, "9105")
    assert proposal.time_from == "" and proposal.terms.deposit_pct is None


# --- uploads -------------------------------------------------------------------------------

def test_the_otp_upload_fills_blank_terms(app_env):
    _start(app_env, "9106")
    resp = _upload(app_env, "9106", "otp", "9106 - OTP.docx", _otp_bytes())
    assert resp.headers["HX-Redirect"].startswith("/proposals/9106")
    proposal = _get(app_env, "9106")
    assert proposal.otp_file == "otp/9106 - OTP.docx"
    assert str(proposal.terms.deposit_pct) == "10" and proposal.terms.confirmation_days == 30
    assert proposal.otp_notes == []


def test_pictures_are_stored_and_served_and_wrong_types_skipped(app_env):
    _start(app_env, "9107")
    _upload(app_env, "9107", "advert", "advert.png", _png_bytes())
    _upload(app_env, "9107", "deeds", "deeds.png", _png_bytes((900, 1200)))
    skipped = _upload(app_env, "9107", "otp", "notes.txt", b"hello")
    assert "skipped" in skipped.headers["HX-Redirect"]
    proposal = _get(app_env, "9107")
    assert proposal.advert_image and proposal.deeds_images and not proposal.otp_file
    picture = app_env.client.get("/proposals/9107/file/advert/0")
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/png"


def test_a_stored_path_outside_the_proposal_folder_is_not_served(app_env):
    _start(app_env, "9108")
    with ProposalStore(app_env.db) as store:
        proposal = store.get("9108")
        proposal.deeds_images = ["../../engine.db"]
        store.save(proposal)
    assert app_env.client.get("/proposals/9108/file/deeds/0").status_code == 404


def test_a_lightstone_upload_without_a_key_still_draws_the_deeds_page(app_env):
    _start(app_env, "9109")
    resp = _upload(app_env, "9109", "lightstone", "EVM_Report.pdf", _evm_bytes())
    assert "Anthropic key" in unquote(resp.headers["HX-Redirect"])
    proposal = _get(app_env, "9109")
    assert proposal.lightstone_files == ["lightstone/EVM_Report.pdf"]
    assert proposal.deeds_images == ["deeds/EVM_Report.png"]
    assert proposal.prefill_job == 0


def test_the_prefill_job_fills_blank_fields_from_the_report(app_env):
    from engine.proposal import lightstone
    from webapp import jobs

    app_env.monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    app_env.monkeypatch.setattr(lightstone, "read_facts", lambda path, client=None: LightstoneFacts(
        owner_name="TESTCO (PTY) LTD", owner_id="2014/203299/07", title_type="freehold",
        legal_description='ERF 99, TOWN "TESTVILLE EXT 1", GAUTENG', street_address="1 TEST STREET, TESTVILLE, GAUTENG",
        title_deed="T12345/2001", extent="2018 m2",
    ))
    _start(app_env, "9110")
    _upload(app_env, "9110", "lightstone", "EVM_Report.pdf", _evm_bytes())
    assert _get(app_env, "9110").prefill_job

    jobs.drain(app_env.db)
    proposal = _get(app_env, "9110")
    assert proposal.seller_name == "TESTCO (PTY) LTD"
    assert proposal.erven[0].title_deed == "T12345/2001"
    assert proposal.lightstone_read == ["lightstone/EVM_Report.pdf"]
    status = app_env.client.get("/proposals/9110/prefill-status", headers=HX)
    assert status.headers.get("HX-Refresh") == "true"


# --- generating ----------------------------------------------------------------------------

def test_generate_is_refused_until_the_checks_pass_then_builds_the_word_file(app_env):
    _start(app_env, "9111")
    refused = app_env.client.post("/proposals/9111/generate", data=_form(), headers=HX)
    assert "Not generated" in refused.text
    assert not _get(app_env, "9111").docx_file

    _upload(app_env, "9111", "otp", "otp.docx", _otp_bytes(), form=_form())
    _upload(app_env, "9111", "advert", "advert.png", _png_bytes(), form=_form())
    _upload(app_env, "9111", "deeds", "deeds.png", _png_bytes((900, 1200)), form=_form())
    built = app_env.client.post("/proposals/9111/generate", data=_form(), headers=HX)
    assert "Proposal generated" in built.text, built.text

    proposal = _get(app_env, "9111")
    assert proposal.docx_file == "out/9111 - Auction proposal.docx"
    assert "save it as a PDF" in proposal.pdf_note  # Word only is the normal path (D105)
    download = app_env.client.get("/proposals/9111/download/docx")
    assert download.status_code == 200
    # Starlette percent-encodes the name (filename*=utf-8''...); browsers decode it.
    assert "9111 - Auction proposal.docx" in unquote(download.headers["content-disposition"])
    assert app_env.client.get("/proposals/9111/download/pdf").status_code == 404


def test_the_publish_job_waits_for_sharepoint_credentials(app_env):
    from webapp import jobs, models

    _start(app_env, "9112")
    job_id = jobs.enqueue(app_env.db, "proposal_publish", "9112", payload={"output_root": str(app_env.out)})
    jobs.drain(app_env.db)
    assert models.get_job(app_env.db, job_id)["state"].startswith("skipped")
