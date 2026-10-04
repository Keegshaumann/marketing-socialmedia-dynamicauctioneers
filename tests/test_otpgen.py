"""OTP generation (M10, D114, D115, D123, D124): the templates, the fill, the checks. Offline.

The committed template is the only Dynamic file read; everything else is made
up, so no client's details appear here. (``test_otp.py`` is the other direction:
reading the terms out of an OTP someone uploads, D68.)
"""

from __future__ import annotations

import io
import re
from decimal import Decimal
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from engine.otpgen import checks, docx_build
from engine.otpgen.model import Otp, conditions, seller_line, tokens
from engine.otpgen.store import OtpStore
from engine.proposal.model import Erf
from engine.proposal.store import ProposalStore


def _otp(**overrides) -> Otp:
    base = dict(
        dp="9201",
        seller_capacity="liquidation",
        seller_name="Testco (Pty) Ltd",
        seller_id="2014/203299/07",
        masters_ref="G123/2026",
        erven=[Erf(legal_description='ERF 99, TOWN "TESTVILLE EXT 1", GAUTENG',
                   known_as="1 TEST STREET, TESTVILLE EXT 1, GAUTENG", title_deed="T12345/2001", extent="2018 m2")],
        commission_pct=Decimal("7.5"),
    )
    base.update(overrides)
    return Otp(**base)


def _paragraphs(path: Path):
    doc = Document(str(path))
    return ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in doc.element.body.iter(qn("w:p"))]


def _text(path: Path) -> str:
    return "\n".join(_paragraphs(path))


# --- the template ----------------------------------------------------------

TEMPLATES = pytest.mark.parametrize("template", [docx_build.TEMPLATE, docx_build.TEMPLATE_BRP], ids=["insolvency", "brp"])


@TEMPLATES
def test_template_slots(template):
    found = set(re.findall(r"\{\{[a-z_]+\}\}", _text(template)))
    expected = {
        "{{seller_heading}}", "{{seller_line}}", "{{erf_legal}}", "{{erf_known_as}}", "{{erf_title_deed}}",
        "{{erf_extent}}", "{{masters_ref}}", "{{deposit_pct}}", "{{deposit_words}}", "{{guarantee_days}}",
        "{{guarantee_words}}", "{{interest_pct}}", "{{interest_words}}", "{{confirmation_days}}",
        "{{confirmation_words}}", "{{commission_pct}}", "{{commission_words}}", "{{commission_vat}}",
        "{{commission_payer}}", "{{special_condition}}",
    }
    if template == docx_build.TEMPLATE_BRP:
        expected.discard("{{masters_ref}}")  # business rescue has no Master's reference (D124)
    assert found == expected


@TEMPLATES
def test_template_has_no_master_client_and_the_errors_are_corrected(template):
    text = _text(template)
    for leftover in ("JUST LETTING", "VORNA", "MONTAGU", "MYSTICAL", "KEMPTON", "PROHIBITATION", "STARUS",
                     "VARATION", "FOURTY", "Act 53 of 1979", "Act Number 28", "surely for", "cause of business",
                     "CONVEYENCER", "Gouws Avenue,Raslouw", "Roukoop", "the date the date", "Rights Act 1998",
                     "COMMISION", "BUSINESS PRACTITIONER"):
        assert leftover not in text, leftover
    assert "Legal Practice Act, 2014 (Act 28 of 2014)" in text
    assert "Restitution of Land Rights Act 22 of 1994" in text
    assert "PROHIBITION" in text and "MARITAL STATUS OF PURCHASER" in text and "VARIATION" in text
    # The October 2026 masters added four definitions and the index entry for clause 22 (D123).
    assert "Date of occupation, the date upon which possession" in text
    assert "Confirmation period, the period of days within which the Seller must accept or reject" in text
    assert [p.strip() for p in _paragraphs(template)].count("RESTITUTION OF LAND RIGHTS") == 2


@TEMPLATES
def test_template_headers_carry_the_properties_letterhead_only(template):
    wp = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
    a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    embed = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
    section = Document(str(template)).sections[0]
    for header in (section.header, section.first_page_header):
        wide = [d for d in header.part.element.iter(qn("w:drawing"))
                if int(d[0].find(wp + "extent").get("cx")) >= 150 * 36000]
        assert len(wide) == 1
        blob = header.part.related_parts[next(wide[0].iter(a + "blip")).get(embed)].blob
        with Image.open(io.BytesIO(blob)) as im:
            assert im.size == (794, 194)  # Letter Head 2026.png, the properties letterhead


# --- what gets printed ----------------------------------------------------------------

def test_seller_line_and_words_follow_the_master_style():
    assert seller_line(_otp()) == "TESTCO (PTY) LTD, REGISTRATION NUMBER 2014/203299/07"
    assert seller_line(_otp(seller_id="7601017486085")) == "TESTCO (PTY) LTD, ID NUMBER 7601017486085"
    values = tokens(_otp(deposit_pct=Decimal("7.5")))
    assert values["deposit_words"] == "Seven and a Half Percent"
    assert values["guarantee_words"] == "FORTY FIVE" and values["confirmation_words"] == "THIRTY"
    assert values["commission_pct"] == "7,5" and values["commission_words"] == "SEVEN AND A HALF"
    for text in values.values():
        assert "–" not in text and "—" not in text


def test_body_corporate_is_written_once_and_extra_conditions_follow():
    otp = _otp(body_corporate="Roswind Body Corporate", extra_conditions=["The pool is sold as is.", " "])
    assert conditions(otp) == [
        "The sale is subject to the rules and regulations of the ROSWIND Body Corporate.",
        "The pool is sold as is.",
    ]


def test_build_fills_every_part(tmp_path):
    otp = _otp(
        seller_capacity="insolvent",
        erven=[
            Erf(legal_description='ERF 99, TOWN "TESTVILLE EXT 1", GAUTENG', known_as="1 TEST STREET, TESTVILLE, GAUTENG",
                title_deed="T12345/2001", extent="2018 m2"),
            Erf(legal_description='ERF 100, TOWN "TESTVILLE EXT 1", GAUTENG', known_as="3 TEST STREET, TESTVILLE, GAUTENG",
                title_deed="T12346/2001", extent=""),
        ],
        guarantee_days=30, confirmation_days=14, deposit_pct=Decimal("20"), commission_pct=Decimal("6"),
        body_corporate="Sabie Mansions", extra_conditions=["Occupation on registration."],
    )
    out = docx_build.build(otp, tmp_path / "9201 - OTP.docx")
    paras = _paragraphs(out)
    text = "\n".join(paras)

    assert "{{" not in text
    assert "THE JOINT TRUSTEES OF INSOLVENT ESTATE:" in paras
    assert "TESTCO (PTY) LTD, REGISTRATION NUMBER 2014/203299/07" in paras
    assert text.count("In favor of: TESTCO (PTY) LTD, REGISTRATION NUMBER 2014/203299/07") == 2
    assert "AND" in paras and "T12346/2001" in text and "3 TEST STREET, TESTVILLE, GAUTENG" in paras
    assert text.count("MEASURING") == 1  # the second erf has no extent
    assert len(re.findall(r"MASTER REF: +G123/2026", text)) == 2  # padded to line up, as the master is
    assert "A cash deposit of 20% (Twenty Percent) of the PURCHASE PRICE" in text
    assert "within 30 (THIRTY) days from the DATE OF ACCEPTANCE" in text and "said 30 (THIRTY) calendar days" in text
    assert "within a period of 14 (FOURTEEN) days (the CONFIRMATION PERIOD)" in text
    assert "calculated at 12% (TWELVE PERCENT) per annum" in text
    assert ("The commission calculated at 6% (SIX PERCENT) of the purchase price plus VAT, will be due and "
            "payable by the SELLER to the AUCTIONEER.") in paras
    assert any(p.startswith("Upon confirmation by the SELLER of this agreement") for p in paras)
    assert "The sale is subject to the rules and regulations of the SABIE MANSIONS Body Corporate." in paras
    assert "Occupation on registration." in paras
    # Special conditions follow the master's own last condition and come before clause 22 (D123).
    arrears = next(i for i, p in enumerate(paras) if p.startswith("The SELLER and the PURCHASER, including the AUCTIONEER"))
    land = [p.strip() for p in paras].index("RESTITUTION OF LAND RIGHTS", arrears)
    assert arrears < paras.index("The sale is subject to the rules and regulations of the SABIE MANSIONS Body Corporate.") \
        < paras.index("Occupation on registration.") < land
    assert "Insolvency Act 24 of 1936" in text and "consent of the Master of the High Court" in text
    block = paras[paras.index("THE JOINT TRUSTEES OF INSOLVENT ESTATE:"):]
    block = block[: next(i for i, p in enumerate(block) if p.strip().startswith("Subject to the following conditions"))]
    # The master has at most two blank lines in a row here; a line left out takes its gap with it.
    assert not any(a == b == c == "" for a, b, c in zip(block, block[1:], block[2:]))


def test_business_rescue_uses_its_own_master(tmp_path):
    otp = _otp(seller_capacity="brp", seller_name="Testco (Pty) Ltd (in business rescue)",
               extra_conditions=["Occupation on registration."])
    assert docx_build.template_for(otp) == docx_build.TEMPLATE_BRP
    assert docx_build.template_for(otp.model_copy(update={"seller_capacity": "deceased"})) == docx_build.TEMPLATE
    paras = _paragraphs(docx_build.build(otp, tmp_path / "9201 - OTP.docx"))
    text = "\n".join(paras)
    assert "{{" not in text
    assert "THE BUSINESS RESCUE PRACTITIONERS OF:" in paras
    assert "TESTCO (PTY) LTD (IN BUSINESS RESCUE), REGISTRATION NUMBER 2014/203299/07" in paras
    assert "In favor of: TESTCO (PTY) LTD (IN BUSINESS RESCUE), REGISTRATION NUMBER 2014/203299/07" in text
    assert "References to Business Rescue Practitioners (Seller)." in text
    assert "Acceptance by the BUSINESS RESCUE PRACTITIONER/SELLER, within a period of 30 (THIRTY) days" in text
    assert "FROM the estate of:" in text and "insolvent estate" not in text
    for absent in ("Insolvency Act", "Master of the High Court", "Provisional Liquidator", "TRUSTEE/SELLER", "MASTER REF"):
        assert absent not in text, absent
    assert "Occupation on registration." in paras
    assert "ERF 99" in text and "T12345/2001" in text and "MEASURING:" in text


def test_without_special_conditions_the_slot_and_its_gap_go(tmp_path):
    paras = _paragraphs(docx_build.build(_otp(masters_ref=""), tmp_path / "p.docx"))
    assert not any(p.startswith("The sale is subject to the rules and regulations") for p in paras)
    stripped = [p.strip() for p in paras]
    land = stripped.index("RESTITUTION OF LAND RIGHTS", stripped.index("AGREEMENT AND CONDITIONS OF SALE"))
    assert paras[land - 1] == "" and paras[land - 2] != ""
    assert "MASTER REF" not in "\n".join(paras)


def test_a_missing_value_refuses_to_build(tmp_path, monkeypatch):
    original = docx_build.tokens
    monkeypatch.setattr(docx_build, "tokens", lambda o: {k: v for k, v in original(o).items() if k != "deposit_pct"})
    with pytest.raises(docx_build.TemplateError):
        docx_build.build(_otp(), tmp_path / "p.docx")


# --- checks --------------------------------------------------------------------------

def test_a_complete_otp_has_nothing_blocking():
    assert checks.blocking(checks.run(_otp())) == []


def test_missing_commission_and_seller_block():
    fields = {i.field for i in checks.blocking(checks.run(_otp(commission_pct=None, seller_name="", seller_capacity="")))}
    assert {"commission_pct", "seller_name", "seller_capacity"} <= fields


def test_a_masters_ref_on_a_business_rescue_otp_warns_that_it_will_not_print():
    assert any(i.field == "masters_ref" and i.level == "warn" for i in checks.run(_otp(seller_capacity="brp")))
    assert not any(i.field == "masters_ref" for i in checks.run(_otp(seller_capacity="brp", masters_ref="")))
    assert not any(i.field == "masters_ref" for i in checks.run(_otp()))


def test_days_the_words_cannot_be_written_for_block():
    fields = {i.field for i in checks.blocking(checks.run(_otp(guarantee_days=0, confirmation_days=365)))}
    assert {"guarantee_days", "confirmation_days"} <= fields


def test_a_sectional_title_without_a_body_corporate_warns():
    otp = _otp(erven=[Erf(legal_description="SECTION 17 OF PLAN 122/1985, KNOWN AS TESTPARK", known_as="UNIT 17 TESTPARK",
                          title_deed="ST65783/2017", extent="63 m2")])
    assert any(i.field == "body_corporate" for i in checks.run(otp))
    assert not any(i.field == "body_corporate" for i in checks.run(otp.model_copy(update={"body_corporate": "Testpark"})))


# --- store -----------------------------------------------------------------------------

def test_otps_and_proposals_keep_separate_records(tmp_path):
    db = tmp_path / "engine.db"
    with OtpStore(db) as store:
        store.save(_otp(), user="properties@dynamicauctioneers.co.za")
        assert store.get("9201") == _otp()
        assert store.list()[0]["dp"] == "9201"
    with ProposalStore(db) as proposals:
        assert proposals.get("9201") is None and proposals.list() == []
