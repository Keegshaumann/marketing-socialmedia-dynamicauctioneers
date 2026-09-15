"""Property report screen (M11, D116): the routes, uploads, seeding and generation. Offline, key-free.

Built like ``test_proposal_webapp.py``: a fresh app on its own database per test.
Everyone and everything in here is made up.
"""

from __future__ import annotations

import html
import io
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import unquote

import fitz
import pytest
from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient
from PIL import Image

from engine.otpgen.model import Otp
from engine.otpgen.store import OtpStore
from engine.proposal.lightstone import LightstoneFacts
from engine.proposal.model import Erf
from engine.propertyreport.model import EMAILS
from engine.propertyreport.store import ReportStore
from engine.schema import FinancialsInternal, Identity, Owner, Physical, PropertyRecord, Valuation
from engine.store import RecordStore

EMAIL = "properties@dynamicauctioneers.co.za"
PASSWORD = "report-pass-123"
HX = {"HX-Request": "true"}


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "engine.db"))
    monkeypatch.setenv("APP_SECRET", "test-secret-reports")
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


def _start(env, dp="9501"):
    resp = env.client.post("/reports/new", data={"dp": dp}, follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"]


def _get(env, dp):
    with ReportStore(env.db) as store:
        return store.get(dp)


def _form(**overrides):
    form = {
        "_full_form": "1",
        "property_type": "house",
        "owner_name": "TESTCO (PTY) LTD",
        "owner_id": "2014/203299/07",
        "legal_description": 'ERF 99, TOWN "TESTVILLE EXT 1", GAUTENG',
        "known_as": "1 TEST STREET, TESTVILLE EXT 1, GAUTENG",
        "extent": "525 m2",
        "zoning": "RESIDENTIAL",
        "local_authority": "CITY OF TESTVILLE",
        "municipal_valuation": "R 850 000",
        "municipal_valuation_year": "2023",
        "title_deed": "T12345/2001",
        "gps": "-25.7,28.2",
        "description": "The house is a 3-bedroom home in Testville.",
        "inspection_date": "2026-09-10",
        "prepared_by": "Test Person",
        "prepared_email": EMAILS[1],
        "prepared_cell": "086 155 2288",
        "i_bedrooms": "3",
        "i_bathrooms": "2",
        "i_rooms": ["study", "kitchen"],
        "i_features": ["pool"],
        "i_security": "Remote access",
        "i_security_items": ["alarm"],
        "i_occupancy": "vacant",
        "rates_outstanding": "",
        "rates_as_at": "",
        "rates_note": "",
        "levies_outstanding": "",
        "levies_as_at": "",
        "managing_agent": "",
        "deposit_pct": "10",
        "commission_pct": "5",
        "guarantee_days": "30",
        "confirmation_days": "",
        "viewing_contact": "Dynamic Auctioneers: 086 155 2288",
        "conclusion": "We will market the property to attract potential offers.",
    }
    form.update(overrides)
    return form


def _jpeg(size, orientation=None) -> bytes:
    buf = io.BytesIO()
    image = Image.new("RGB", size, (120, 90, 60))
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        image.save(buf, "JPEG", exif=exif)
    else:
        image.save(buf, "JPEG")
    return buf.getvalue()


def _evm_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((40, 200), "Property Details")
    page.insert_text((40, 630), "EVM Valuation Details")
    doc.new_page(width=595, height=842).insert_text((40, 24), "Owner Details")
    return doc.tobytes()


def _upload(env, dp, kind, files, form=None):
    return env.client.post(f"/reports/{dp}/upload/{kind}", files=[("files", f) for f in files], data=form or {}, headers=HX)


# --- access and starting -------------------------------------------------------------

def test_the_screen_needs_a_properties_login(app_env):
    from webapp import auth, models

    anonymous = TestClient(app_env.client.app)
    resp = anonymous.get("/reports", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/login"

    models.create_user(app_env.db, email="marketing@dynamicauctioneers.co.za", pw_hash=auth.hash_password(PASSWORD), role="marketing")
    marketing = TestClient(app_env.client.app)
    marketing.post("/login", data={"email": "marketing@dynamicauctioneers.co.za", "password": PASSWORD}, follow_redirects=False)
    assert marketing.get("/reports", follow_redirects=False).status_code == 403


def test_start_open_list_and_switch(app_env):
    assert _start(app_env, "DP9501") == "/reports/9501"
    page = html.unescape(app_env.client.get("/reports/9501").text)
    assert "Property report 9501" in page
    assert "Choose the kind of property" in page  # the checks show from the start
    assert 'action="/proposals/new"' in page and 'action="/otps/new"' in page
    assert 'aria-current="page">Property report 9501' in page
    assert "Property documents" in page
    listing = app_env.client.get("/reports")
    assert "/reports/9501" in listing.text and "Draft" in listing.text
    assert app_env.client.post("/reports/new", data={"dp": "Moremi"}, follow_redirects=False).headers["location"] == "/reports?error=dp"


def test_a_report_starts_from_the_otp_and_the_record(app_env):
    with OtpStore(app_env.db) as store:
        store.save(Otp(
            dp="9502", seller_capacity="liquidation", seller_name="TESTCO (PTY) LTD", seller_id="2014/203299/07",
            erven=[Erf(legal_description='ERF 99, TOWN "TESTVILLE EXT 1", GAUTENG', known_as="1 TEST STREET",
                       title_deed="T12345/2001", extent="525 m2")],
            commission_pct=Decimal("7.5"), guarantee_days=45,
        ))
    records = RecordStore(app_env.db)
    try:
        records.upsert(PropertyRecord(
            dp="9502",
            identity=Identity(legal_description="Something else", municipality="City of Testville", gps=[-25.7, 28.2]),
            physical=Physical(zoning="Residential", bedrooms=3, garages=2),
            valuation=Valuation(municipal_valuation=850000.0, municipal_valuation_year=2023),
            financials_internal=FinancialsInternal(owner=Owner(name="Other Name", id_number="7601017486085")),
        ), state="extracted")
    finally:
        records.close()

    _start(app_env, "9502")
    report = _get(app_env, "9502")
    assert report.owner_name == "TESTCO (PTY) LTD"  # the OTP comes first
    assert report.legal_description.startswith("ERF 99")
    assert report.local_authority == "CITY OF TESTVILLE" and report.zoning == "RESIDENTIAL" and report.gps == "-25.7,28.2"
    assert report.municipal_valuation == Decimal("850000") and report.municipal_valuation_year == 2023
    assert report.inspection.bedrooms == 3 and report.inspection.garages == 2
    assert report.commission_pct == Decimal("7.5") and report.guarantee_days == 45
    assert "OTP" in report.prefill_note and "marketing record" in report.prefill_note


# --- saving ------------------------------------------------------------------------------

def test_save_keeps_the_checklist_and_names_bad_values(app_env):
    _start(app_env, "9503")
    resp = app_env.client.post("/reports/9503/save", data=_form(municipal_valuation="lots", i_bedrooms="three"), headers=HX)
    assert resp.status_code == 200 and 'id="checks"' in resp.text
    assert "not a rand amount" in resp.text and "not a number" in resp.text
    report = _get(app_env, "9503")
    assert report.municipal_valuation is None and report.inspection.bedrooms is None
    assert report.inspection.rooms == ["kitchen", "study"]  # the checklist's own order
    assert report.inspection.features == ["pool"] and report.inspection.security_items == ["alarm"]
    assert report.prepared_email == EMAILS[1] and report.inspection.occupancy == "vacant"


# --- files ---------------------------------------------------------------------------------

def test_photos_are_turned_upright_made_smaller_ordered_and_served(app_env):
    _start(app_env, "9504")
    _upload(app_env, "9504", "photos", [("a.jpg", _jpeg((400, 200), orientation=6), "image/jpeg"),
                                        ("b.jpg", _jpeg((2400, 1200)), "image/jpeg")])
    skipped = _upload(app_env, "9504", "photos", [("notes.txt", b"hello", "text/plain")])
    assert "skipped" in unquote(skipped.headers["HX-Redirect"])
    report = _get(app_env, "9504")
    assert report.photos == ["photos/a.jpg", "photos/b.jpg"]
    folder = app_env.out / "DP9504" / "report" / "photos"
    with Image.open(folder / "a.jpg") as a, Image.open(folder / "b.jpg") as b:
        assert a.size == (200, 400) and b.size == (1600, 800)
    picture = app_env.client.get("/reports/9504/file/photos/0")
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/jpeg"

    app_env.client.post("/reports/9504/earlier/1", headers=HX)
    assert _get(app_env, "9504").photos == ["photos/b.jpg", "photos/a.jpg"]
    app_env.client.post("/reports/9504/remove/photos/0", headers=HX)
    assert _get(app_env, "9504").photos == ["photos/a.jpg"]


def test_a_lightstone_upload_without_a_key_still_draws_the_cover(app_env):
    _start(app_env, "9505")
    resp = _upload(app_env, "9505", "lightstone", [("EVM_Report.pdf", _evm_bytes(), "application/pdf")])
    assert "Anthropic key" in unquote(resp.headers["HX-Redirect"])
    report = _get(app_env, "9505")
    assert report.lightstone_files == ["lightstone/EVM_Report.pdf"]
    assert report.cover_image == "cover/EVM_Report.png" and report.prefill_job == 0
    assert app_env.client.get("/reports/9505/file/cover/0").headers["content-type"] == "image/png"


def test_the_prefill_job_fills_the_municipal_facts(app_env):
    from engine.proposal import lightstone
    from webapp import jobs

    app_env.monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    app_env.monkeypatch.setattr(lightstone, "read_facts", lambda path, client=None: LightstoneFacts(
        owner_name="TESTCO (PTY) LTD", owner_id="2014/203299/07", title_type="freehold",
        legal_description='ERF 99, TOWN "TESTVILLE EXT 1", GAUTENG', street_address="1 TEST STREET, TESTVILLE, GAUTENG",
        title_deed="T12345/2001", extent="525 m2", local_authority="CITY OF TESTVILLE", usage="RESIDENTIAL",
        municipal_valuation="R 850 000", municipal_valuation_year="2023", coordinates="-25.7,28.2", last_sale_price="R900 000",
    ))
    _start(app_env, "9506")
    _upload(app_env, "9506", "lightstone", [("EVM_Report.pdf", _evm_bytes(), "application/pdf")])
    assert _get(app_env, "9506").prefill_job

    jobs.drain(app_env.db)
    report = _get(app_env, "9506")
    assert report.owner_name == "TESTCO (PTY) LTD" and report.local_authority == "CITY OF TESTVILLE"
    assert report.municipal_valuation == Decimal("850000") and report.lightstone_last_sale == Decimal("900000")
    assert report.lightstone_read == ["lightstone/EVM_Report.pdf"]
    assert app_env.client.get("/reports/9506/prefill-status", headers=HX).headers.get("HX-Refresh") == "true"


# --- generating ----------------------------------------------------------------------------

def test_generate_is_refused_until_the_checks_pass_then_builds_the_word_file(app_env):
    _start(app_env, "9507")
    refused = app_env.client.post("/reports/9507/generate", data=_form(property_type=""), headers=HX)
    assert "Not generated" in refused.text
    assert not _get(app_env, "9507").docx_file

    _upload(app_env, "9507", "photos", [("a.jpg", _jpeg((633, 476)), "image/jpeg")], form=_form())
    built = app_env.client.post("/reports/9507/generate", data=_form(), headers=HX)
    assert "Property report generated" in built.text, built.text
    report = _get(app_env, "9507")
    assert report.docx_file == "out/9507 - PROPERTY REPORT.docx" and report.generated_by == EMAIL

    download = app_env.client.get("/reports/9507/download")
    assert download.status_code == 200
    assert "9507 - PROPERTY REPORT.docx" in unquote(download.headers["content-disposition"])
    doc = Document(str(app_env.out / "DP9507" / "report" / report.docx_file))
    paras = ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in doc.element.body.iter(qn("w:p"))]
    assert "Residential Dwelling:" in paras and "3 Bedrooms" in paras and "The property is vacant." in paras
    assert "R 850 000.00 (2023 valuation roll)" in "\n".join(paras)
    assert len(list(doc.element.body.iter(qn("w:drawing")))) == 1


def test_download_before_generating_is_a_404(app_env):
    _start(app_env, "9508")
    assert app_env.client.get("/reports/9508/download").status_code == 404
