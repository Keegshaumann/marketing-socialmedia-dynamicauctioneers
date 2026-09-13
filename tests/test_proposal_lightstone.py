"""Lightstone deeds page and prefill for proposals (M9, D103). Offline, key-free.

The PDFs are drawn by the test with the EVM report's section headings, and the
model call goes to a fake client that records what it was sent.
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image

from engine import MODEL
from engine.proposal import lightstone
from engine.proposal.lightstone import LightstoneFacts
from engine.proposal.model import Erf, Proposal


def _evm(path: Path) -> Path:
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((40, 60), "EVM Report  AGENT NAME")
    p1.insert_text((40, 200), "Property Details")
    p1.insert_text((40, 240), "Erf 2188")
    p1.insert_text((40, 630), "EVM Valuation Details")
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((40, 24), "Owner Details")
    p2.insert_text((40, 60), "TESTCO PTY LTD   201420329907   T12345/2001")
    p2.insert_text((40, 100), "Transfer History")
    doc.save(str(path))
    return path


def test_deeds_page_crops_the_details_and_owner_strip(tmp_path):
    out = lightstone.deeds_image(_evm(tmp_path / "evm.pdf"), tmp_path / "deeds.png", zoom=1.0)
    with Image.open(out) as im:
        width, height = im.size
    assert width == 595
    # page 1 from just above "Property Details" to above the valuation (~430pt),
    # plus the owner strip (~80pt) and the gap; far shorter than two whole pages.
    assert 480 < height < 560


def test_a_report_without_the_evm_headings_gets_its_first_page(tmp_path):
    doc = fitz.open()
    doc.new_page(width=612, height=792).insert_text((40, 60), "DEED SEARCH")
    doc.save(str(tmp_path / "search.pdf"))
    out = lightstone.deeds_image(tmp_path / "search.pdf", tmp_path / "deeds.png", zoom=1.0)
    with Image.open(out) as im:
        assert im.size == (612, 792)


def test_an_unreadable_file_says_so(tmp_path):
    (tmp_path / "junk.pdf").write_bytes(b"not a pdf")
    with pytest.raises(lightstone.LightstoneUnreadable):
        lightstone.deeds_image(tmp_path / "junk.pdf", tmp_path / "deeds.png")


class _FakeClient:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.sent = None
        self._response = SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason)
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.sent = kwargs
        return self._response


def _facts(**overrides) -> LightstoneFacts:
    base = dict(
        owner_name="testco (pty)  ltd", owner_id="201420329907", title_type="freehold",
        legal_description='erf 99, town "testville ext 1", gauteng',
        street_address="1 test street, testville, gauteng", title_deed="t12345/2001", extent="2018 m2",
    )
    base.update(overrides)
    return LightstoneFacts(**base)


def test_the_model_gets_the_pdf_and_a_schema_and_the_answer_is_tidied(tmp_path):
    pdf = _evm(tmp_path / "evm.pdf")
    client = _FakeClient(_facts())
    facts = lightstone.read_facts(pdf, client=client)

    sent = client.sent
    assert sent["model"] == MODEL
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_format"] is LightstoneFacts
    document = sent["messages"][0]["content"][0]
    assert document["type"] == "document"
    assert base64.standard_b64decode(document["source"]["data"]) == pdf.read_bytes()

    assert facts.owner_name == "TESTCO (PTY) LTD"
    assert facts.owner_id == "2014/203299/07"
    assert facts.title_deed == "T12345/2001"


def test_a_refusal_is_an_error_not_an_empty_proposal(tmp_path):
    with pytest.raises(lightstone.LightstoneUnreadable):
        lightstone.read_facts(_evm(tmp_path / "evm.pdf"), client=_FakeClient(None, stop_reason="refusal"))


def test_facts_fill_only_blank_fields_and_add_a_second_erf():
    proposal = Proposal(dp="9001", seller_name="TYPED BY HAND")
    one = lightstone._tidy(_facts())
    two = lightstone._tidy(_facts(title_deed="T12346/2001", street_address="3 test street, testville, gauteng",
                                  legal_description='erf 100, town "testville ext 1", gauteng'))
    note = lightstone.apply_facts(proposal, [one, two])

    assert proposal.seller_name == "TYPED BY HAND"          # a person's typing wins
    assert proposal.seller_id == "2014/203299/07"
    assert [e.title_deed for e in proposal.erven] == ["T12345/2001", "T12346/2001"]
    assert "Check each against the title deed" in note


def test_the_same_title_deed_is_not_added_twice():
    proposal = Proposal(dp="9001", erven=[Erf(title_deed="T12345/2001", known_as="TYPED")])
    lightstone.apply_facts(proposal, [lightstone._tidy(_facts())])
    assert len(proposal.erven) == 1
    assert proposal.erven[0].known_as == "TYPED"
    assert proposal.erven[0].legal_description.startswith("ERF 99")
