"""Claude extraction of a source PDF pair into a ``PropertyRecord`` (M2).

This is the model-facing half of ingestion. It sends the Lightstone EVM and the
Dynamic Property Report to Claude as base64 document blocks and asks for a single
validated ``PropertyRecord`` back (SPEC.md 4.2). The Anthropic SDK enforces the
schema through ``client.messages.parse(..., output_format=PropertyRecord)``.

Design rules baked in here:
- The extraction brief is one stable, cacheable system text block. Nothing
  property-specific goes in it, so the prefix caches across every ingest run
  (``cache_control`` ephemeral).
- Adaptive thinking is set explicitly (it is off by default on Opus 4.8); effort
  defaults to high. ``messages.parse`` injects ``output_config.format`` from
  ``output_format`` itself, so we never pass ``output_config`` alongside it.
- The two PDF document blocks are placed BEFORE the text block in the user turn,
  so the volatile per-request text (the DP number) sits at the end.
- ``build_request`` returns exactly the kwargs handed to ``messages.parse``, so a
  test can assert the request shape offline with no API key and no network call.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

from engine import MODEL
from engine.schema import PropertyRecord

load_dotenv()


# --- constants -----------------------------------------------------------

MAX_TOKENS = 16000

# Dynamic's public viewing line. Safe to render; the occupant's own cell is
# POPIA-internal and must never land here.
DYNAMIC_CONTACT_PUBLIC = "086 155 2288 / properties.admin@dynamicauctioneers.co.za"

# The extraction brief. Stable and self-contained so the prefix caches across
# runs. It encodes the rules only; the field docs travel with the Pydantic model
# the SDK sends, and no property-specific fact is hardcoded here.
SYSTEM_PROMPT = (
    "You are the extraction engine for Dynamic Auctioneers, a South African "
    "property auction and sale house. You read two source documents about a "
    "single property and return one structured property record.\n"
    "\n"
    "The two sources and how to merge them (this is the authoritative rule):\n"
    "- The Lightstone EVM report carries the deeds, market and valuation data. "
    "It wins for identity (title type, scheme, unit, erf, legal description, "
    "title deed number, GPS), for valuation (EVM range, suburb bands, municipal "
    "valuation, rates, comparable sales) and for the ownership and financial "
    "history.\n"
    "- The Dynamic Property Report is a physical inspection. It wins for physical "
    "reality: rooms, bedrooms, bathrooms, garages, the flatlet, condition and "
    "features actually seen on site, and for the sale process and viewing "
    "arrangements.\n"
    "- Where the two sources disagree on the same fact, do NOT silently pick one. "
    "Record both readings in the relevant conflict or note field (for example "
    "physical.garages_conflict, or a *_note field) and leave the primary field "
    "set to the source that owns that fact under the rule above.\n"
    "\n"
    "POPIA (South African data protection) is enforced by where you put things:\n"
    "- The owner's name and ID number go ONLY into financials_internal.owner. "
    "Bond, arrears, last sale and any other financial detail go into "
    "financials_internal as well.\n"
    "- The occupant's or owner's personal cell number goes ONLY into "
    "sale_process.viewing.contact_internal_only.\n"
    "- The public viewing contact is always Dynamic's own line. Put "
    "\"" + DYNAMIC_CONTACT_PUBLIC + "\" into sale_process.viewing.contact_public. "
    "Never place a private individual's number in contact_public.\n"
    "\n"
    "Truthfulness:\n"
    "- Every field the source documents do not contain must be left null. Never "
    "guess, infer or fill a value that is not supported by the documents. A "
    "missing fact is null, not an approximation.\n"
    "\n"
    "Framing and language:\n"
    "- Follow sale_process.method for how the sale is framed. If the method is an "
    "auction, describe it as an auction; if offers are invited, describe it as "
    "offers invited. Do not reframe the sale as something the documents do not "
    "state.\n"
    "- Use South African English throughout. Do not use em dashes or en dashes, "
    "and do not use emojis.\n"
)


# --- request construction ------------------------------------------------

def _pdf_block(path: str | Path) -> dict:
    """Return a base64 PDF document content block for ``path``.

    The base64 data carries no newlines (``standard_b64encode`` output decoded
    to ASCII), as the document-block format requires.
    """
    data = Path(path).read_bytes()
    encoded = base64.standard_b64encode(data).decode()
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": encoded,
        },
    }


def build_request(
    lightstone_pdf: str | Path,
    property_report_pdf: str | Path,
    dp: str,
) -> dict:
    """Build the kwargs dict passed to ``client.messages.parse``.

    Factored out so a test can assert the request shape offline: the model id,
    adaptive thinking, ``output_format`` being ``PropertyRecord``, the
    ``cache_control`` on the system block, and the two base64 PDF document blocks
    preceding the text block. No API key or network access is needed to call it.
    """
    text_block = {
        "type": "text",
        "text": (
            "Extract the property record for DP " + str(dp) + ". The two "
            "documents above are the Lightstone EVM report (first) and the "
            "Dynamic Property Report (second). Merge them per the rules, keep "
            "the owner's name, ID and private cell in the internal layer, and "
            "leave any fact the documents do not contain as null."
        ),
    }
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": [
            {
                # The extraction brief is the stable prefix, so it carries the
                # cache breakpoint. Note: on claude-opus-4-8 the minimum
                # cacheable prefix is 4096 tokens; this brief is shorter, so the
                # marker is structurally correct but inert for now (the per-
                # property PDFs after it vary every call and never cache). It
                # keeps the breakpoint in the right place for when the brief
                # grows or the model tier changes.
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    _pdf_block(lightstone_pdf),
                    _pdf_block(property_report_pdf),
                    text_block,
                ],
            }
        ],
        "output_format": PropertyRecord,
    }


# --- extraction ----------------------------------------------------------

def extract_record(
    lightstone_pdf: str | Path,
    property_report_pdf: str | Path,
    dp: str,
    parent_dp: Optional[str] = None,
    client=None,
) -> PropertyRecord:
    """Extract a validated ``PropertyRecord`` from the source PDF pair.

    Sends both PDFs to Claude and returns the parsed record. Fills ``dp`` /
    ``parent_dp`` from the caller if the model left them blank, and stamps the
    extraction status. Raises a clear ``RuntimeError`` when the API key is
    missing rather than surfacing a raw SDK auth error.
    """
    try:
        client = client or anthropic.Anthropic()
    except anthropic.AnthropicError as exc:  # pragma: no cover - env dependent
        # The SDK raises the base AnthropicError (not AuthenticationError, which
        # is a request-time 401 subclass) from the constructor when no credential
        # source resolves at all.
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to your .env or environment "
            "before running Claude extraction."
        ) from exc

    request = build_request(lightstone_pdf, property_report_pdf, dp)
    try:
        response = client.messages.parse(**request)
    except anthropic.AuthenticationError as exc:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set or invalid. Add a valid key to your .env "
            "or environment before running Claude extraction."
        ) from exc

    rec = response.parsed_output

    if not rec.dp:
        rec.dp = dp
    if rec.parent_dp is None:
        rec.parent_dp = parent_dp

    # Reflect that extraction has run. Keep a verification substatus if the model
    # already raised one; otherwise mark the record extracted.
    if rec.status not in ("flags_raised", "verified"):
        rec.status = "extracted"

    return rec
