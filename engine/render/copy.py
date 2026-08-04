"""Channel-aware marketing copy generation (M5, 3A).

Every rendered artifact needs words: a headline, a price line, channel-appropriate
body text, and email subject lines. This module produces that copy in one of two
ways:

- **Model path** (when an ``ANTHROPIC_API_KEY`` or a client is available): Claude
  writes channel-aware copy in South African English, framed as "offers invited"
  or "auction" per ``sale_process.method``, validated through structured outputs.
- **Deterministic template path** (key-free, the default offline): copy is built
  from the record's own public fields so rendering still works without any model
  call. This is the path the offline tests exercise.

Design rules baked in here:
- Copy is derived ONLY from ``record.public_view()`` (SPEC 4.4): the POPIA internal
  layer (owner PII, occupant cell, financials) is never in scope, so no artifact
  copy can leak it.
- South African English throughout. No em dashes or en dashes, no emojis, no
  AI-tells (SPEC M5).
- The sale is framed exactly as the record states it. ``sale_process.method`` of
  ``"auction"`` yields auction language; anything else (default ``offers_invited``)
  yields "offers invited" language. Copy never reframes the sale.
- ``build_copy_request`` returns exactly the kwargs handed to ``messages.parse``,
  so a test can assert the request shape offline with no key and no network.
- The model path overlays its output on the template dict, so a usable, complete
  copy dict is returned even if the model omits a field.
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from engine import MODEL
from engine.schema import PropertyRecord

load_dotenv()


# --- constants -----------------------------------------------------------

MAX_TOKENS = 4000

# Dynamic's public enquiry line. Safe to render; used as a fallback when the
# record does not carry a public viewing contact.
DYNAMIC_CONTACT_PUBLIC = (
    "Dynamic Auctioneers | 086 155 2288 | properties@dynamicauctioneers.co.za "
    "| properties.admin@dynamicauctioneers.co.za"
)


# --- copy schema (structured output) -------------------------------------

class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FormatCopy(_Base):
    """Copy for a single text channel (portal, Facebook)."""

    headline: Optional[str] = None
    body: Optional[str] = None


class EmailCopy(_Base):
    """Email copy carries two subject lines for A/B testing plus a body."""

    subject_a: Optional[str] = None
    subject_b: Optional[str] = None
    body: Optional[str] = None


class CopyBundle(_Base):
    """The full channel-aware copy set the model returns.

    ``headline`` and ``price_display`` are required so every artifact has at
    least a title and a price line; the per-channel blocks are optional and the
    template path fills any the model leaves out.
    """

    headline: str
    price_display: str
    summary: Optional[str] = None
    terms: Optional[List[str]] = None
    portal_listing: Optional[FormatCopy] = None
    facebook_post: Optional[FormatCopy] = None
    email_blast: Optional[EmailCopy] = None


# --- small text helpers --------------------------------------------------

def _num(value) -> str:
    """Render a number without a trailing ``.0`` (185.0 -> "185")."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return str(f)


def _join_list(items: List[str]) -> str:
    """Join a list in SA English prose: "a, b and c" (no Oxford comma)."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _framing(method: Optional[str]) -> dict:
    """Return the offers-vs-auction framing for ``sale_process.method``.

    The sale is described exactly as the record states it. Auction framing is
    used only when the method is explicitly ``"auction"``; every other value
    (including the default ``offers_invited`` and a missing method) uses the
    offers framing.
    """
    if method == "auction":
        return {
            "price_label": "On auction",
            "sentence": "This property is going on auction.",
            "cta": "Register to bid",
        }
    return {
        "price_label": "Offers invited",
        "sentence": "Offers are invited on this property.",
        "cta": "Submit your offer",
    }


def _build_headline(beds, title_type, flatlet: dict, suburb) -> str:
    """Construct a headline from physical facts when the record has none."""
    kind = "Home"
    if title_type == "sectional":
        kind = "Sectional Title Home"
    elif title_type == "freehold":
        kind = "Freehold Home"

    head = f"{beds} Bedroom {kind}" if beds else kind
    if flatlet.get("present"):
        head += " with Separate Flatlet"
    if suburb:
        head += f" in {suburb}"
    return head


def _features_clause(beds, baths, size, title_type, flatlet: dict, suburb) -> str:
    """A single prose sentence describing the property from public facts."""
    lead_bits = []
    if size:
        lead_bits.append(f"{_num(size)} m2")
    if title_type:
        lead_bits.append(f"{title_type} title")

    sentence = "This"
    if lead_bits:
        sentence += " " + " ".join(lead_bits)
    sentence += " home"
    if suburb:
        sentence += f" in {suburb}"

    detail = []
    if beds:
        detail.append(f"{beds} bedroom" + ("s" if beds != 1 else ""))
    if baths:
        detail.append(f"{baths} bathroom" + ("s" if baths != 1 else ""))
    if flatlet.get("present"):
        detail.append("a separate flatlet")
    if detail:
        sentence += " offers " + _join_list(detail)

    sentence += "."
    return sentence


# --- deterministic template path -----------------------------------------

def _template_copy(record: PropertyRecord) -> dict:
    """Build a complete, usable copy dict from the record's public fields.

    Runs with no API key and no network. Every string is South African English
    with no em dashes, so a renderer handed this dict can produce every artifact
    offline.
    """
    pub = record.public_view()
    identity = pub.get("identity") or {}
    physical = pub.get("physical") or {}
    marketing = pub.get("marketing") or {}
    sale = pub.get("sale_process") or {}

    method = sale.get("method") or "offers_invited"
    frame = _framing(method)

    suburb = identity.get("suburb")
    title_type = identity.get("title_type")
    beds = physical.get("bedrooms")
    baths = physical.get("bathrooms_main_unit")
    size = physical.get("unit_size_m2")
    flatlet = physical.get("flatlet") or {}
    features_main = physical.get("features_main") or []

    headline = marketing.get("headline") or _build_headline(
        beds, title_type, flatlet, suburb
    )
    price_display = marketing.get("price_display") or frame["price_label"]
    terms = sale.get("terms") or []
    contact = (sale.get("viewing") or {}).get("contact_public") or DYNAMIC_CONTACT_PUBLIC
    dp = record.dp

    features_clause = _features_clause(beds, baths, size, title_type, flatlet, suburb)
    summary = f"{headline.rstrip('.')}. {features_clause} {frame['sentence']}"

    # Portal listing (Property24): formal, complete, with features and terms.
    portal_parts = [summary]
    if features_main:
        portal_parts.append("Features: " + "; ".join(features_main) + ".")
    if terms:
        portal_parts.append("Sale terms: " + "; ".join(terms) + ".")
    portal_parts.append(f"Enquiries: {contact}.")
    portal_body = " ".join(portal_parts)

    # Facebook: punchy, one call to action.
    fb_body = (
        f"{headline.rstrip('.')}. {frame['sentence']} {frame['cta']} today. "
        f"Enquiries: {contact}."
    )

    # Email: two subject lines for A/B, plus a fuller body.
    subject_a = f"{headline.rstrip('.')} | {price_display}"
    if suburb:
        subject_b = f"New listing in {suburb}: {price_display}"
    else:
        subject_b = f"New listing: {price_display}"
    email_parts = [summary]
    if features_main:
        email_parts.append("Features: " + "; ".join(features_main) + ".")
    email_parts.append(f"{frame['cta']} or arrange a viewing. Enquiries: {contact}.")
    email_body = " ".join(email_parts)

    return {
        "headline": headline,
        "price_display": price_display,
        "summary": summary,
        "terms": list(terms),
        "contact_public": contact,
        "method": method,
        "framing": "auction" if method == "auction" else "offers",
        "portal_listing": {"headline": headline, "body": portal_body},
        "facebook_post": {"headline": headline, "body": fb_body},
        "email_blast": {
            "subject_a": subject_a,
            "subject_b": subject_b,
            "body": email_body,
        },
    }


# --- model request construction ------------------------------------------

SYSTEM_PROMPT = (
    "You are the marketing copywriter for Dynamic Auctioneers, a South African "
    "property auction and sale house. You are given the public, POPIA-safe view "
    "of one property record and you return channel-aware marketing copy.\n"
    "\n"
    "You receive only public fields. Owner names, ID numbers, bond and arrears "
    "figures and the occupant's private cell number are not in your input and "
    "must never appear in the copy. Enquiries always route to Dynamic's own "
    "public line, never to a private individual.\n"
    "\n"
    "Framing (this is authoritative): follow sale_process.method. If the method "
    "is \"auction\", frame the sale as an auction and invite bidders to register. "
    "For any other method, including \"offers_invited\", frame it as offers "
    "invited and invite buyers to submit an offer. Never reframe the sale as "
    "something the record does not state, and never invent a price.\n"
    "\n"
    "Language: South African English throughout. Do not use em dashes or en "
    "dashes, and do not use emojis or obvious AI phrasing.\n"
    "\n"
    "Channels: write a headline and a price display line, a short summary, the "
    "sale terms, and per-channel copy: a formal portal listing, a punchy "
    "Facebook post, and an email with two subject lines (A and B) for "
    "testing plus a body. Every fact must trace to a field in the record; leave "
    "anything the record does not contain out rather than guessing."
)


def build_copy_request(record: PropertyRecord) -> dict:
    """Build the kwargs dict passed to ``client.messages.parse``.

    Factored out so a test can assert the request shape offline: the model id,
    adaptive thinking, ``output_format`` being ``CopyBundle``, the
    ``cache_control`` on the stable system block, and the public-record payload
    in the user turn. No API key or network access is needed to call it, and it
    reads only ``record.public_view()`` so PII is never sent to the model.
    """
    public_record = record.public_view()
    text_block = {
        "type": "text",
        "text": (
            "Write the marketing copy for DP " + str(record.dp) + ". The public "
            "property record follows as JSON. Frame the sale per "
            "sale_process.method, keep every fact traceable to a field, and use "
            "South African English with no em dashes.\n\n"
            + json.dumps(public_record, ensure_ascii=False, indent=2)
        ),
    }
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": [
            {
                # The copywriting brief is the stable prefix, so it carries the
                # cache breakpoint. Note: on claude-opus-4-8 the minimum
                # cacheable prefix is 4096 tokens; this brief is shorter, so the
                # marker is structurally correct but inert for now (the per-
                # property record after it varies every call). It keeps the
                # breakpoint in the right place for when the brief grows.
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [text_block],
            }
        ],
        "output_format": CopyBundle,
    }


# --- public API ----------------------------------------------------------

def _merge_bundle(base: dict, bundle: CopyBundle) -> dict:
    """Overlay the model's copy onto the template dict, field by field.

    The template dict guarantees a complete, usable result; the model's
    non-null fields replace their template counterparts. Nested channel blocks
    are merged key by key so a partial block from the model does not wipe the
    template's other keys.
    """
    produced = bundle.model_dump(exclude_none=True)
    for key, value in produced.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key].update(value)
        else:
            base[key] = value
    return base


def generate_copy(record: PropertyRecord, client=None) -> dict:
    """Return channel-aware marketing copy for ``record``.

    With no ``client`` and no ``ANTHROPIC_API_KEY``, returns the deterministic
    template copy so rendering works offline. When a key or client is present,
    asks Claude for channel-aware copy and overlays it on the template; any
    credential or API failure degrades gracefully back to the template rather
    than crashing (SPEC global rule: never crash, never hang).
    """
    template = _template_copy(record)

    if client is None and not os.getenv("ANTHROPIC_API_KEY"):
        return template

    try:
        client = client or anthropic.Anthropic()
    except anthropic.AnthropicError:
        # No credential resolved at construction time; use the template.
        return template

    request = build_copy_request(record)
    try:
        response = client.messages.parse(**request)
        bundle = response.parsed_output
    except Exception:
        # Copy generation is best-effort: any failure at request time (auth,
        # connection, API error, schema validation, refusal) degrades to the
        # deterministic template, which always renders. The template is the
        # guaranteed floor, so this resilience boundary swallows broadly.
        return template

    if bundle is None:
        return template

    return _scrub_forbidden(_merge_bundle(template, bundle))


# A municipal valuation must never appear in client-facing copy (owner
# directive: the money line is always the offers framing). The figure is now
# stripped from ``public_view`` so the model cannot see it, but this scrub is
# the second line of defence - it also cleans a bundle cached before that fix.
_FORBIDDEN_PHRASE = re.compile(
    r"\s*[.|;-]?\s*municipal\s+valuation[^.|]*[.|]?", re.IGNORECASE
)


def _scrub_forbidden(bundle: dict) -> dict:
    """Remove any municipal-valuation mention from every string in the bundle."""
    for key, value in list(bundle.items()):
        if isinstance(value, str) and "municipal" in value.lower():
            cleaned = _FORBIDDEN_PHRASE.sub("", value).strip(" .|;-")
            bundle[key] = cleaned or None
    return bundle


# --- single-headline generation (gate-2 "auto-generate") -----------------

class HeadlineSuggestion(_Base):
    """One marketing headline. A trivial one-field schema, so messages.parse's
    grammar compiles cleanly (unlike the big extraction schemas)."""

    headline: str


HEADLINE_SYSTEM_PROMPT = (
    "You are the marketing copywriter for Dynamic Auctioneers, a South African "
    "property auction and sale house. Given the public, POPIA-safe view of one "
    "property, write ONE short marketing headline of about four to nine words.\n"
    "\n"
    "Only public fields are given. Never put an owner name, a contact number or a "
    "price in the headline. Frame in keeping with sale_process.method, though the "
    "headline itself need not name the sale method. Use South African English, no "
    "em dashes or en dashes, no emojis and no obvious AI phrasing. Lead with what "
    "a buyer cares about: property type, bedrooms, a standout feature (a separate "
    "flatlet, estate security, a pool) and the suburb. Every word must trace to a "
    "field in the record; do not invent a feature. Return the headline only."
)


def build_headline_request(record: PropertyRecord) -> dict:
    """Kwargs for ``messages.parse`` that ask Claude for one headline (offline-
    constructible; reads only ``public_view`` so no PII is ever sent)."""
    public_record = record.public_view()
    text_block = {
        "type": "text",
        "text": (
            "Write one marketing headline for this property. The public record "
            "follows as JSON. South African English, no em dashes, every word "
            "traceable to a field.\n\n"
            + json.dumps(public_record, ensure_ascii=False, indent=2)
        ),
    }
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": [
            {"type": "text", "text": HEADLINE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": [text_block]}],
        "output_format": HeadlineSuggestion,
    }


def generate_headline(record: PropertyRecord, client=None) -> str:
    """Return one marketing headline for ``record``.

    Key-gated like ``generate_copy``: with no client and no ``ANTHROPIC_API_KEY``
    it returns the deterministic ``_build_headline``; with a key it asks Claude
    and falls back to the deterministic headline on any failure (never crashes,
    always returns a usable string).
    """
    public = record.public_view()
    physical = public.get("physical") or {}
    identity = public.get("identity") or {}
    flatlet = physical.get("flatlet") or {}
    fallback = _build_headline(
        physical.get("bedrooms"), identity.get("title_type"), flatlet, identity.get("suburb")
    )

    if client is None and not os.getenv("ANTHROPIC_API_KEY"):
        return fallback
    try:
        client = client or anthropic.Anthropic()
    except anthropic.AnthropicError:
        return fallback

    try:
        response = client.messages.parse(**build_headline_request(record))
        suggestion = response.parsed_output
    except Exception:
        return fallback
    if suggestion is None or not (suggestion.headline or "").strip():
        return fallback
    return suggestion.headline.strip()
