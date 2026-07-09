"""WhatsApp broadcast composer and CRM-tag audience builder (M6, 5A).

WhatsApp is one of the automated M6 channels: a broadcast keyed to CRM tags with
the "reply <DP>" hook (SPEC M6). The live send goes through Peach Software
Solutions (Cognexa's existing white-label supplier) or BotSailor, neither of
which is wired up in this environment:

    # PLACEHOLDER(D-M6.2): needs Peach/BotSailor line (API base + line id +
    token). Until then ``send_broadcast`` degrades to a ready-to-send payload
    (message + audience) and reports the missing line instead of calling out.

Design rules baked in here:
- The message is built from ``public_view`` fields only (headline, suburb, price
  line), so a broadcast can never carry owner or occupant PII (SPEC 4.4).
- The audience is derived from CRM tags. The contacts table is owned by
  ``engine.crm`` (M7, Phase 7); this module reads it defensively and returns an
  empty audience if it is not present yet, so distribution never crashes on a
  seed database.
- Nothing here performs network I/O. ``send_broadcast`` returns a plain dict
  describing what would go out, gated behind the presence of a configured line.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional


# --- helpers --------------------------------------------------------------

def _resolve_db_path(db_path: Optional[str | Path]) -> str:
    """Resolve the database path: arg, else ``ENGINE_DB``, else ``./engine.db``."""
    if db_path is not None:
        return str(db_path)
    return os.environ.get("ENGINE_DB", "engine.db")


def _public(record) -> dict:
    """Return the public projection of a record (or a dict passed straight).

    A ``PropertyRecord`` is reduced through ``public_view()`` so no PII is read;
    a plain dict is used as given (only non-PII fields are touched below).
    """
    if hasattr(record, "public_view"):
        return record.public_view()
    return record or {}


def _tag_set(value) -> set:
    """Normalise a contact's tags (list or comma string) to a lowercased set."""
    if value is None:
        return set()
    if isinstance(value, str):
        parts = value.split(",")
    else:
        try:
            parts = list(value)
        except TypeError:
            return set()
    return {str(p).strip().lower() for p in parts if str(p).strip()}


def _contact_tags(contact) -> set:
    """Read the ``tags`` field off a contact dict or object."""
    if isinstance(contact, dict):
        return _tag_set(contact.get("tags"))
    return _tag_set(getattr(contact, "tags", None))


# --- broadcast message ----------------------------------------------------

def build_broadcast(record, reduced: bool = False) -> str:
    """Compose the WhatsApp broadcast text for a listing, with the reply hook.

    Uses only public fields. When ``reduced`` is set the message leads with a
    REDUCED line, which is what a price-drop burst sends (SPEC M6). SA English,
    no em dashes, no emojis.
    """
    public = _public(record)
    dp = public.get("dp") or ""
    marketing = public.get("marketing") or {}
    identity = public.get("identity") or {}
    sale = public.get("sale_process") or {}

    headline = marketing.get("headline") or "New listing"
    suburb = identity.get("suburb")
    price_display = marketing.get("price_display")
    method = sale.get("method")

    lines: List[str] = ["Dynamic Auctioneers"]
    if reduced:
        lines.append("REDUCED")
    lines.append(headline)
    if suburb:
        lines.append(suburb)
    if price_display:
        lines.append(price_display)
    elif method == "auction":
        lines.append("On auction")
    elif method == "offers_invited":
        lines.append("Offers invited")
    if dp:
        lines.append(f"Reply {dp} for the full property pack and viewing times.")
    return "\n".join(lines)


# --- audience from CRM tags -----------------------------------------------

def audience_from_tags(
    contacts: Iterable,
    tags: Iterable[str],
    match: str = "any",
) -> List:
    """Filter ``contacts`` to those matching ``tags`` (pure, no database).

    ``match="any"`` (default) keeps a contact sharing at least one tag;
    ``match="all"`` requires every wanted tag. Each contact may be a dict or an
    object exposing a ``tags`` field (list or comma-separated string). Contacts
    are returned unchanged, preserving whatever the caller passed in.
    """
    wanted = _tag_set(tags)
    if not wanted:
        return []

    out: List = []
    for contact in contacts:
        have = _contact_tags(contact)
        if match == "all":
            hit = wanted.issubset(have)
        else:
            hit = bool(wanted & have)
        if hit:
            out.append(contact)
    return out


def crm_audience(
    db_path: Optional[str | Path],
    tags: Iterable[str],
    match: str = "any",
) -> List[dict]:
    """Build the audience from the CRM ``contacts`` table, filtered by ``tags``.

    The ``contacts`` table is produced by ``engine.crm`` (M7). This reads it
    defensively: if the table does not exist yet (seed database, CRM not built),
    the audience is empty rather than an error. Rows are returned as dicts and
    filtered through ``audience_from_tags``.
    """
    conn = sqlite3.connect(_resolve_db_path(db_path))
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute("SELECT * FROM contacts").fetchall()
        except sqlite3.OperationalError:
            return []  # contacts table not created yet (engine.crm, M7)
    finally:
        conn.close()
    contacts = [dict(row) for row in rows]
    return audience_from_tags(contacts, tags, match=match)


# --- line configuration + send scaffold -----------------------------------

def _line_config() -> "tuple[bool, str]":
    """Return ``(configured, reason)`` for the WhatsApp line.

    Looks for a Peach or BotSailor credential in the environment. This
    environment carries none, so the send path degrades gracefully.
    """
    if os.getenv("PEACH_API_KEY") or os.getenv("PEACH_LINE"):
        return True, "peach line configured"
    if os.getenv("BOTSAILOR_API_KEY"):
        return True, "botsailor line configured"
    return (
        False,
        "no WhatsApp line configured (set PEACH_API_KEY/PEACH_LINE or "
        "BOTSAILOR_API_KEY) - PLACEHOLDER(D-M6.2)",
    )


def build_broadcast_payload(message: str, audience: List, line: Optional[str] = None) -> dict:
    """Shape the provider-agnostic broadcast request (offline-testable).

    Recipients are reduced to phone identifiers where available; a contact with
    no phone/whatsapp field is dropped from the send list. No network call.
    """
    recipients: List[str] = []
    for contact in audience:
        if isinstance(contact, dict):
            phone = contact.get("whatsapp") or contact.get("phone")
        else:
            phone = getattr(contact, "whatsapp", None) or getattr(contact, "phone", None)
        if phone:
            recipients.append(str(phone))
    return {
        "line": line,
        "message": message,
        "recipients": recipients,
        "recipient_count": len(recipients),
    }


def send_broadcast(
    record,
    db_path: Optional[str | Path] = None,
    tags: Optional[Iterable[str]] = None,
    reduced: bool = False,
    line: Optional[str] = None,
) -> dict:
    """Send (or, without a line, stage) a WhatsApp broadcast for a listing.

    Builds the message and the CRM-tag audience, then either hands off to the
    provider or, when no Peach/BotSailor line is configured, returns a
    ready-to-send payload with the reason. Never performs network I/O in this
    build and never raises on a missing line.

    # PLACEHOLDER(D-M6.2): the live Peach/BotSailor call is not wired up; wire it
    into the ``configured`` branch once the line credentials exist.
    """
    message = build_broadcast(record, reduced=reduced)
    audience = crm_audience(db_path, tags) if (db_path and tags) else []
    payload = build_broadcast_payload(message, audience, line=line)

    configured, reason = _line_config()
    if not configured:
        return {
            "sent": False,
            "reason": reason,
            "message": message,
            "audience_size": len(audience),
            "payload": payload,
        }

    # PLACEHOLDER(D-M6.2): a line is configured but no live client is wired yet.
    return {
        "sent": False,
        "reason": "WhatsApp line configured but live send not implemented (D-M6.2)",
        "message": message,
        "audience_size": len(audience),
        "payload": payload,
    }
