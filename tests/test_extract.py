"""Extraction request-shape tests (M2), fully offline.

``build_request`` is factored out precisely so the request handed to
``client.messages.parse`` can be asserted without an API key or a network call.
These tests read the real DP3060 PDFs to confirm the base64 document blocks are
well formed; they never invoke the model. Skipped when the sample PDFs are
absent.
"""

from __future__ import annotations

import base64

from engine import MODEL
from engine.extract import build_request
from engine.schema import PropertyRecord


def test_build_request_top_level_shape(lightstone_3060, property_report_3060):
    req = build_request(lightstone_3060, property_report_3060, "3060")

    assert req["model"] == MODEL == "claude-opus-4-8"
    assert req["max_tokens"] == 16000
    assert req["thinking"] == {"type": "adaptive"}
    assert req["output_format"] is PropertyRecord
    # messages.parse injects output_config from output_format; passing our own
    # would collide, so it must be absent.
    assert "output_config" not in req


def test_build_request_system_block_is_cacheable(lightstone_3060, property_report_3060):
    req = build_request(lightstone_3060, property_report_3060, "3060")

    system = req["system"]
    assert isinstance(system, list) and len(system) == 1
    block = system[0]
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert block["text"].strip()  # non-empty stable brief


def test_build_request_document_blocks_precede_text(
    lightstone_3060, property_report_3060
):
    req = build_request(lightstone_3060, property_report_3060, "3060")

    messages = req["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert len(content) == 3

    doc_a, doc_b, text = content
    for block in (doc_a, doc_b):
        assert block["type"] == "document"
        source = block["source"]
        assert source["type"] == "base64"
        assert source["media_type"] == "application/pdf"
        # The data really is base64 of a PDF (starts with the %PDF magic) and
        # carries no newlines.
        assert "\n" not in source["data"]
        decoded = base64.b64decode(source["data"])
        assert decoded[:4] == b"%PDF"

    # The volatile text block comes last and names the DP.
    assert text["type"] == "text"
    assert "3060" in text["text"]


def test_build_request_orders_lightstone_first(lightstone_3060, property_report_3060):
    """The Lightstone PDF is the first document block, the Property Report the second."""
    req = build_request(lightstone_3060, property_report_3060, "3060")
    doc_a, doc_b, _ = req["messages"][0]["content"]

    lightstone_bytes = lightstone_3060.read_bytes()
    report_bytes = property_report_3060.read_bytes()
    assert base64.b64decode(doc_a["source"]["data"]) == lightstone_bytes
    assert base64.b64decode(doc_b["source"]["data"]) == report_bytes
