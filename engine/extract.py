"""Claude extraction of a source PDF pair into a ``PropertyRecord`` (M2).

This is the model-facing half of ingestion. It sends the Lightstone EVM and the
Dynamic Property Report to Claude and asks for the record's source-derived
sections back (SPEC.md 4.2), one section per call. Each call exposes a single
non-strict tool whose ``input_schema`` is that section's Pydantic schema; the
model's tool call carries the fields, and ``_extract_section`` validates them
with the same model (``model_validate``). Structured validation is preserved
(hard rule 3: validated data, never free text) without the grammar-constrained
``messages.parse`` path.

Why sectioned, and why tool use not ``messages.parse``: two API limits, found
live on the DP3060 golden run. (1) The full ``PropertyRecord`` schema compiles
to a grammar the API rejects (HTTP 400, "compiled grammar is too large"), so we
split into sections. (2) Even a single nested section schema is rejected by the
strict grammar path (HTTP 400, "Schema is too complex") because every field is
optional (unions) under ``extra="forbid"``. Non-strict tool use is not grammar-
constrained, so it has no such ceiling; we get validity back by validating the
tool input ourselves. Prompt caching still keeps cost near a single pass: every
section call shares the identical prefix (system brief + both source blocks,
cache breakpoint on the second block), so the documents are paid for once and
read from cache for the remaining calls.

Design rules baked in here:
- The extraction brief is one stable, cacheable system text block. Nothing
  property-specific goes in it, so the prefix caches across every ingest run
  (``cache_control`` ephemeral).
- Adaptive thinking is set explicitly (it is off by default on Opus 4.8).
  ``tool_choice`` therefore stays ``auto`` (forced tool choice is not allowed
  with extended thinking); the single tool plus a direct instruction makes the
  call reliable, and ``_extract_section`` forces the tool on a retry with
  thinking dropped if the model ever answers without calling it.
- The two document blocks are placed BEFORE the text block in the user turn;
  only the short per-section text block varies between the calls, so the cached
  prefix covers the system brief and both documents.
- Only source-derived sections are extracted. ``dp``/``parent_dp``/``status``/
  ``record_created``/``compliance`` and the source file paths are stamped by
  code; ``marketing`` (photo picks, copy) and ``verification`` belong to later
  stages and are never asked of the extraction model.
- ``build_request`` returns exactly the kwargs handed to ``messages.create``,
  so a test can assert the request shape offline with no API key and no network
  call.
- The documents go in as native base64 PDF blocks by default (full fidelity).
  ``EXTRACT_PDF_MODE=text`` instead sends the locally-extracted text layer,
  which is ~5x smaller (DP3060: ~5.7k vs ~26k tokens) so extraction fits under
  a low per-minute rate-limit tier; the trade is any fact that lives only in a
  page image. ``EXTRACT_PACE_SECONDS`` waits between the per-section calls so a
  capped tier is not exceeded. Both are opt-in; native + no pacing is default.
"""

from __future__ import annotations

import base64
import os
import time
from datetime import date
from pathlib import Path
from typing import Optional, Tuple, Type

import anthropic
import fitz  # PyMuPDF, already a dependency (engine/photos.py)
from dotenv import load_dotenv
from pydantic import ValidationError

from engine import MODEL
from engine.schema import (
    Compliance,
    FinancialsInternal,
    Identity,
    LightstoneSource,
    Physical,
    PropertyRecord,
    PropertyReportSource,
    SaleProcess,
    Sources,
    Valuation,
    _Base,
)

load_dotenv()


# --- constants -----------------------------------------------------------

MAX_TOKENS = 16000

# The single extraction tool's name. Each section call exposes one tool whose
# input_schema IS the section's Pydantic schema; the model's tool call carries
# the extracted fields, which we then validate with the same model.
TOOL_NAME = "record_section"

# How the two source PDFs are sent to Claude:
# - "native" (default, per SPEC tech conventions): base64 PDF document blocks,
#   which preserve page layout, tables and chart images at full fidelity.
# - "text": text extracted locally with PyMuPDF and sent as text blocks. Far
#   cheaper in tokens (DP3060: ~5.7k vs ~26k for both docs together), which lets
#   extraction run under a low per-minute rate-limit tier, at the cost of any
#   fact that lives only in a page image and not the text layer.
# Set with EXTRACT_PDF_MODE. Native stays the default so nothing changes for a
# high-tier account; text mode is opt-in.
DEFAULT_PDF_MODE = "native"

# Optional seconds to wait between the per-section calls, to respect a
# tokens-per-minute rate limit (each DP3060 section call is ~6.4k input tokens;
# a 10k/min tier only fits one such call per minute). 0 disables pacing. Set
# with EXTRACT_PACE_SECONDS.
DEFAULT_PACE_SECONDS = 0.0

# Dynamic's public viewing line. Safe to render; the occupant's own cell is
# POPIA-internal and must never land here.
DYNAMIC_CONTACT_PUBLIC = "086 155 2288 / properties.admin@dynamicauctioneers.co.za"

# The extraction brief. Stable and self-contained so the prefix caches across
# runs. It encodes the rules only; the field docs travel with the Pydantic model
# the SDK sends, and no property-specific fact is hardcoded here.
SYSTEM_PROMPT = (
    "You are the extraction engine for Dynamic Auctioneers, a South African "
    "property auction and sale house. You read two source documents about a "
    "single property and return one structured section of its property record "
    "per request.\n"
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


# --- sections -------------------------------------------------------------

# The source-derived sections, extracted one small structured-output call each
# (the full record's grammar is too large to compile in one call, see module
# docstring). Order matches the record layout; each entry is
# (record field name, section model, one-line extraction focus).
SECTIONS: Tuple[Tuple[str, Type[_Base], str], ...] = (
    (
        "sources",
        Sources,
        "the report metadata: the Lightstone report id, report date and who "
        "purchased it, and who prepared the Property Report and its figures-"
        "as-at date. Leave both file fields null; the system sets them.",
    ),
    (
        "identity",
        Identity,
        "the property's identity per the Lightstone deeds data: title type "
        "(sectional or freehold), scheme and unit or erf, legal description, "
        "street address, suburb, municipality (plus a note if the documents "
        "qualify it), province, GPS as [lat, lon], title deed number, and "
        "Dynamic's mandate/master reference if stated.",
    ),
    (
        "physical",
        Physical,
        "the physical reality per the inspection: unit and mother-erf sizes, "
        "zoning, bedrooms, bathrooms, separate toilet, garages (record "
        "garages_conflict when the sources disagree), the flatlet, and the "
        "main-unit and complex feature lists.",
    ),
    (
        "valuation",
        Valuation,
        "the valuation data per the Lightstone EVM: the EVM range as "
        "[low, high], suburb bands, municipal valuation and its year, "
        "estimated monthly rates, the comparables average sales price, and "
        "the same-scheme sale if one is reported.",
    ),
    (
        "financials_internal",
        FinancialsInternal,
        "the POPIA-internal financials: the owner's name and ID number, the "
        "last sale, the bond, outstanding rates/taxes/water and levies with "
        "their as-at date, and any note.",
    ),
    (
        "sale_process",
        SaleProcess,
        "the sale process: the method (offers_invited or auction), the terms "
        "lines as stated, and the viewing arrangements. contact_public must "
        "be Dynamic's own line; a private individual's cell goes only in "
        "contact_internal_only.",
    ),
)

_SECTION_INDEX = {name: (model, focus) for name, model, focus in SECTIONS}


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


def _pdf_text(path: str | Path) -> str:
    """Extract the text layer of ``path`` with PyMuPDF, one marked page block.

    Only the text layer is read; images (photos, chart bitmaps) are not. Facts
    that appear solely inside a page image are therefore not carried by text
    mode. Page markers keep the model's sense of document structure.
    """
    doc = fitz.open(path)
    try:
        parts = [f"[page {i + 1}]\n{page.get_text('text')}" for i, page in enumerate(doc)]
    finally:
        doc.close()
    return "\n".join(parts)


def _source_block(path: str | Path, mode: str, label: str) -> dict:
    """Return the content block for a source document in the given ``mode``.

    ``native`` -> a base64 PDF document block; ``text`` -> a labelled text block
    of the locally-extracted text layer. The label names which source it is so
    the merge rule still applies when the block is plain text.
    """
    if mode == "text":
        return {
            "type": "text",
            "text": f"=== {label} (text extracted from the source PDF) ===\n"
            + _pdf_text(path),
        }
    return _pdf_block(path)


def _section_tool(section: str, model: Type[_Base]) -> dict:
    """A single non-strict tool whose ``input_schema`` is the section's schema.

    Non-strict (no ``strict: True``): the model fills the schema and we validate
    the tool input with the same Pydantic model afterwards. This deliberately
    avoids the grammar-constrained ``messages.parse`` path, whose compiled
    grammar rejects these all-optional, ``extra="forbid"``, nested schemas as
    "too complex" (seen live on the physical/valuation/financials sections).
    """
    return {
        "name": TOOL_NAME,
        "description": (
            f"Return the `{section}` section of the property record as its "
            "structured fields. Populate only what the source documents "
            "support; leave every unsupported field null."
        ),
        "input_schema": model.model_json_schema(),
    }


def build_request(
    lightstone_pdf: str | Path,
    property_report_pdf: str | Path,
    dp: str,
    section: str,
    mode: Optional[str] = None,
) -> dict:
    """Build the kwargs dict passed to ``client.messages.create`` for a section.

    Factored out so a test can assert the request shape offline: the model id,
    adaptive thinking, the single section tool plus ``tool_choice``, the
    ``cache_control`` breakpoints, and the two source blocks preceding the text
    block. No API key or network access is needed to call it.

    ``mode`` is ``native`` (base64 PDF blocks, default) or ``text`` (locally
    extracted text blocks); it defaults to ``EXTRACT_PDF_MODE`` then
    ``DEFAULT_PDF_MODE``. Every section shares the identical prefix (system
    brief + both source blocks); only this trailing text block differs, so the
    second source block carries the cache breakpoint and the documents are read
    from cache for all but the first section call of a property.
    """
    mode = mode or os.environ.get("EXTRACT_PDF_MODE", DEFAULT_PDF_MODE)
    model, focus = _SECTION_INDEX[section]
    text_block = {
        "type": "text",
        "text": (
            "For DP " + str(dp) + ", extract ONLY the `" + section + "` section "
            "of the property record: " + focus + " The two documents above are "
            "the Lightstone EVM report (first) and the Dynamic Property Report "
            "(second). Merge them per the system rules and leave any fact the "
            "documents do not contain as null."
        ),
    }
    lightstone_block = _source_block(lightstone_pdf, mode, "LIGHTSTONE EVM REPORT")
    report_block = _source_block(property_report_pdf, mode, "DYNAMIC PROPERTY REPORT")
    # The cached prefix ends after the source blocks: system brief + both
    # documents are identical for every section call of a property, past the
    # 4096-token minimum cacheable prefix on claude-opus-4-8 in either mode
    # (~25.9k native, ~5.7k text for DP3060). The first call writes the cache;
    # the remaining sections read it at a tenth of the input price.
    report_block["cache_control"] = {"type": "ephemeral"}
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": [
            {
                # A breakpoint on the stable brief as well, so the brief alone
                # still caches across different properties in one session.
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    lightstone_block,
                    report_block,
                    text_block,
                ],
            }
        ],
        # tool_choice stays "auto" because it must be auto (not forced) while
        # extended thinking is on; a single tool plus the directive above makes
        # the call reliable. extract_record forces the tool on its retry (with
        # thinking dropped) if the model ever answers without calling it.
        "tools": [_section_tool(section, model)],
        "tool_choice": {"type": "auto"},
    }


# --- extraction ----------------------------------------------------------

def _extract_section(client, request: dict, section: str, model: Type[_Base]):
    """Run one section call and return the validated section model.

    Primary attempt: thinking on, ``tool_choice`` auto (as built). If the model
    answers without calling the tool, or its tool input fails validation, retry
    once with thinking dropped and the tool forced, which guarantees a call.
    Both the missing-tool and validation cases raise a clear error if the retry
    also fails, so a bad section never lands silently as null.
    """
    last_exc: Optional[Exception] = None
    for attempt in (0, 1):
        req = dict(request)
        if attempt == 1:
            # Forced tool_choice is only allowed with thinking off; dropping it
            # is an acceptable trade on the rare retry to guarantee the call.
            req.pop("thinking", None)
            req["tool_choice"] = {"type": "tool", "name": TOOL_NAME}
        response = client.messages.create(**req)
        block = next((b for b in response.content if b.type == "tool_use"), None)
        if block is None:
            last_exc = RuntimeError(
                f"model returned no {section} tool call (stop_reason="
                f"{response.stop_reason})"
            )
            continue
        try:
            return model.model_validate(block.input)
        except ValidationError as exc:
            last_exc = exc
    raise RuntimeError(f"extraction of the {section} section failed: {last_exc}")


def extract_record(
    lightstone_pdf: str | Path,
    property_report_pdf: str | Path,
    dp: str,
    parent_dp: Optional[str] = None,
    client=None,
    mode: Optional[str] = None,
    pace_seconds: Optional[float] = None,
) -> PropertyRecord:
    """Extract a validated ``PropertyRecord`` from the source PDF pair.

    Runs one structured-output call per source-derived section (see
    ``SECTIONS``) and assembles the record in code. ``dp``/``parent_dp``,
    ``status``, ``record_created``, the source file paths and the compliance
    marker are stamped here, never asked of the model. Raises a clear
    ``RuntimeError`` when the API key is missing rather than surfacing a raw
    SDK auth error.

    ``mode`` (``native``/``text``) and ``pace_seconds`` (a wait between the
    per-section calls, to stay under a tokens-per-minute rate limit) default to
    the ``EXTRACT_PDF_MODE`` / ``EXTRACT_PACE_SECONDS`` environment variables.
    """
    mode = mode or os.environ.get("EXTRACT_PDF_MODE", DEFAULT_PDF_MODE)
    if pace_seconds is None:
        pace_seconds = float(os.environ.get("EXTRACT_PACE_SECONDS", "") or DEFAULT_PACE_SECONDS)

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

    parts: dict = {}
    for idx, (section, model, _focus) in enumerate(SECTIONS):
        # Pace between calls (not before the first) so each minute stays under
        # the tokens-per-minute limit on a low tier.
        if pace_seconds and idx:
            time.sleep(pace_seconds)
        request = build_request(lightstone_pdf, property_report_pdf, dp, section, mode)
        try:
            parts[section] = _extract_section(client, request, section, model)
        except anthropic.AuthenticationError as exc:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set or invalid. Add a valid key to your "
                ".env or environment before running Claude extraction."
            ) from exc

    # The real source paths always come from the caller; the model cannot know
    # them and was told to leave the file fields null.
    sources = parts.get("sources") or Sources()
    if sources.lightstone_evm is None:
        sources.lightstone_evm = LightstoneSource()
    sources.lightstone_evm.file = str(lightstone_pdf)
    if sources.property_report is None:
        sources.property_report = PropertyReportSource()
    sources.property_report.file = str(property_report_pdf)
    parts["sources"] = sources

    return PropertyRecord(
        dp=dp,
        parent_dp=parent_dp,
        status="extracted",
        record_created=date.today().isoformat(),
        # Structural, not asserted by the model: owner PII lives only in
        # financials_internal (schema) and public_view() strips it (SPEC 4.4).
        compliance=Compliance(owner_pii_redacted=True),
        **parts,
    )
