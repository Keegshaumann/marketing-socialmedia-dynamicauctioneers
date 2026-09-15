"""OTP generation (M10, D114, D115): the template, the fill, the checks. Offline.

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

def test_template_slots():
    found = set(re.findall(r"\{\{[a-z_]+\}\}", _text(docx_build.TEMPLATE)))
    assert found == {
        "{{seller_heading}}", "{{seller_line}}", "{{erf_legal}}", "{{erf_known_as}}", "{{erf_title_deed}}",
        "{{erf_extent}}", "{{masters_ref}}", "{{deposit_pct}}", "{{deposit_words}}", "{{guarantee_days}}",
        "{{guarantee_words}}", "{{interest_pct}}", "{{interest_words}}", "{{confirmation_days}}",
        "{{confirmation_words}}", "{{commission_pct}}", "{{commission_words}}", "{{commission_vat}}",
        "{{commission_payer}}", "{{special_condition}}",
    }


def test_template_has_no_master_client_and_the_errors_are_corrected():
    text = _text(docx_build.TEMPLATE)
    for leftover in ("JUST LETTING", "VORNA", "MONTAGU", "PROHIBITATION", "STARUS", "VARATION", "FOURTY",
                     "Act 53 of 1979", "surely for", "cause of business", "CONVEYENCER", "Gouws Avenue,Raslouw"):
        assert leftover not in text, leftover
    assert "Legal Practice Act, 2014 (Act 28 of 2014)" in text
    assert "PROHIBITION" in text and "MARITAL STATUS OF PURCHASER" in text and "VARIATION" in text


def test_template_headers_carry_the_properties_letterhead_only():
    wp = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
    a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    embed = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
    section = Document(str(docx_build.TEMPLATE)).sections[0]
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
    assert text.count("MASTER REF: G123/2026") == 2
    assert "A cash deposit of 20% (Twenty Percent) of the PURCHASE PRICE" in text
    assert "within 30 (THIRTY) days from the DATE OF ACCEPTANCE" in text and "said 30 (THIRTY) calendar days" in text
    assert "within a period of 14 (FOURTEEN) days (the CONFIRMATION PERIOD)" in text
    assert "calculated at 12% (TWELVE PERCENT) per annum" in text
    assert ("The commission calculated at 6% (SIX PERCENT) of the purchase price plus VAT, will be due and "
            "payable by the SELLER to the AUCTIONEER.") in paras
    assert any(p.startswith("Upon confirmation by the SELLER of this agreement") for p in paras)
    assert "The sale is subject to the rules and regulations of the SABIE MANSIONS Body Corporate." in paras
    assert "Occupation on registration." in paras
    block = paras[paras.index("THE JOINT TRUSTEES OF INSOLVENT ESTATE:"):]
    block = block[: next(i for i, p in enumerate(block) if p.strip().startswith("Subject to the following conditions"))]
    # The master has at most two blank lines in a row here; a line left out takes its gap with it.
    assert not any(a == b == c == "" for a, b, c in zip(block, block[1:], block[2:]))


def test_without_special_conditions_the_slot_and_its_gap_go(tmp_path):
    paras = _paragraphs(docx_build.build(_otp(masters_ref=""), tmp_path / "p.docx"))
    assert not any(p.startswith("The sale is subject to the rules and regulations") for p in paras)
    land = [p.strip() for p in paras].index("RESTITUTION OF LAND RIGHTS")
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
