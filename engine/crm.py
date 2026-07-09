"""Buyer CRM seed (M7).

Every enquiry a listing draws in self-tags with a DP number, so the buyer CRM
builds itself as a by-product of distribution (SPEC M7, section 12). This module
is the seed of that CRM: it turns raw enquiries ("reply 3060", an email link
``?dp=3060``, a Facebook lead) into ``Contact`` rows tagged with the DP plus the
category, area and budget band derived from that DP's verified record. A new
verified listing can then query the matched buyers and produce a broadcast line.

Design rules baked in here:
- Pure code, no model, no external credentials. The whole module runs offline;
  the tests exercise these deterministic paths (build-contract Phase 7).
- Tags are *derived from the record*, never guessed: category comes from
  ``physical.zoning`` / identity, area from ``identity.suburb``, budget band
  from a price resolved out of ``valuation`` (or a numeric ``marketing``
  price_display). A DP whose record is not in the store leaves those tags
  ``None`` rather than inventing them.
- Contacts and enquiries live in the same SQLite database as the record store
  (``RecordStore``), in their own ``contacts`` / ``enquiries`` tables, so the
  CRM shares one file with the rest of the engine.
- SA English, no em dashes, no emojis (matches ``engine.schema`` style).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from engine.schema import PropertyRecord
from engine.store import RecordStore, _resolve_db_path


# --- contact model -------------------------------------------------------

class Contact(BaseModel):
    """One buyer, deduplicated by ``handle`` (phone / email / FB lead id).

    A contact carries the tags derived from the last DP it enquired about:
    ``category`` (residential | industrial), ``area`` (suburb) and
    ``budget_band``. ``matched_buyers`` matches a new listing against exactly
    these three tags.
    """

    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    handle: Optional[str] = None
    source: Optional[str] = None
    dp: Optional[str] = None
    category: Optional[str] = None
    area: Optional[str] = None
    budget_band: Optional[str] = None
    raw: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# --- helpers -------------------------------------------------------------

def _now() -> str:
    """UTC timestamp in ISO 8601, seconds precision (matches ``engine.store``)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: Optional[str | Path]) -> sqlite3.Connection:
    """Open the CRM database and ensure the ``contacts`` / ``enquiries`` tables.

    Shares the record store's database file (``ENGINE_DB`` by default) so the
    whole engine lives in one SQLite file.
    """
    conn = sqlite3.connect(_resolve_db_path(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS contacts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            handle      TEXT UNIQUE,
            source      TEXT,
            dp          TEXT,
            category    TEXT,
            area        TEXT,
            budget_band TEXT,
            raw         TEXT,
            created_at  TEXT,
            updated_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS enquiries (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER,
            source     TEXT,
            raw        TEXT,
            dp         TEXT,
            at         TEXT
        );
        """
    )
    conn.commit()
    return conn


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Require an explicit key:value / key=value separator so a structured Facebook
# lead id is matched but free text ("please lead me to the property") is not.
_FB_RE = re.compile(r"(?:fb|facebook|lead)[:=]\s*([\w-]+)", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+?27|0)\d{9}")
_DP_PATTERNS = [
    re.compile(r"reply\s*#?\s*(\d{3,5})", re.IGNORECASE),
    re.compile(r"[?&]dp=(\d{3,5})", re.IGNORECASE),
    re.compile(r'"dp"\s*:\s*"?(\d{3,5})', re.IGNORECASE),
    re.compile(r"\bDP[\s:-]?(\d{3,5})\b", re.IGNORECASE),
]


def parse_dp(raw: str, dp: Optional[str] = None) -> Optional[str]:
    """Resolve the DP number: an explicit ``dp`` argument wins, else parse it
    out of ``raw`` ("reply 3060", ``?dp=3060``, a Facebook lead payload)."""
    if dp is not None:
        return str(dp).strip()
    if not raw:
        return None
    for pattern in _DP_PATTERNS:
        m = pattern.search(raw)
        if m:
            return m.group(1)
    return None


def _normalise_phone(number: str) -> str:
    """Normalise a South African number to ``+27`` international form."""
    digits = re.sub(r"[^\d]", "", number)
    if digits.startswith("27"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return "+27" + digits


def parse_handle(source: str, raw: str) -> Optional[str]:
    """Extract a stable contact key from ``raw`` for deduplication.

    Prefers an email address, then an explicit Facebook lead id, then a South
    African phone number normalised to ``+27`` form. Returns ``None`` when no
    identifier is present, in which case the enquiry is stored anonymously.
    """
    if not raw:
        return None
    m = _EMAIL_RE.search(raw)
    if m:
        return m.group(0).lower()
    m = _FB_RE.search(raw)
    if m:
        return "fb:" + m.group(1)
    condensed = re.sub(r"[\s()-]", "", raw)
    m = _PHONE_RE.search(condensed)
    if m:
        return _normalise_phone(m.group(0))
    return None


# --- tag derivation (from the verified record) ---------------------------

_INDUSTRIAL_HINTS = ("industrial", "commercial", "warehouse", "factory", "retail")


def derive_category(record: PropertyRecord) -> str:
    """Residential vs industrial, from zoning / identity. Defaults residential."""
    parts: List[str] = []
    if record.physical is not None and record.physical.zoning:
        parts.append(record.physical.zoning)
    if record.identity is not None:
        for value in (record.identity.legal_description, record.identity.title_type):
            if value:
                parts.append(value)
    text = " ".join(parts).lower()
    if any(hint in text for hint in _INDUSTRIAL_HINTS):
        return "industrial"
    return "residential"


def derive_area(record: PropertyRecord) -> Optional[str]:
    """The listing's area: the suburb from the identity block."""
    if record.identity is not None:
        return record.identity.suburb
    return None


def _parse_price_display(price_display: Optional[str]) -> Optional[float]:
    """Pull a number out of a marketing price string, else ``None``.

    "R1 250 000" -> 1250000.0; "Offers invited" -> None.
    """
    if not price_display:
        return None
    digits = re.sub(r"[^\d]", "", price_display)
    if not digits:
        return None
    return float(digits)


def derive_price(record: PropertyRecord) -> Optional[float]:
    """Resolve a single indicative price for banding.

    Prefers an explicitly numeric ``marketing.price_display``; falls back
    through the valuation block (municipal valuation, EVM midpoint, comparables
    average, suburb mid band). Returns ``None`` when nothing numeric is known.
    """
    if record.marketing is not None:
        display = _parse_price_display(record.marketing.price_display)
        if display is not None:
            return display

    val = record.valuation
    if val is None:
        return None
    if val.municipal_valuation:
        return float(val.municipal_valuation)
    if val.evm_range and len(val.evm_range) == 2:
        return (float(val.evm_range[0]) + float(val.evm_range[1])) / 2
    if val.comparables_avg_sales_price:
        return float(val.comparables_avg_sales_price)
    if val.suburb_bands is not None and val.suburb_bands.mid:
        return float(val.suburb_bands.mid)
    return None


def budget_band(price: Optional[float]) -> Optional[str]:
    """Map a price to a coarse budget band label, or ``None`` when unknown."""
    if price is None:
        return None
    if price < 500_000:
        return "under_R500k"
    if price < 1_000_000:
        return "R500k_R1m"
    if price < 2_000_000:
        return "R1m_R2m"
    if price < 5_000_000:
        return "R2m_R5m"
    return "R5m_plus"


def derive_tags(record: PropertyRecord) -> tuple[str, Optional[str], Optional[str]]:
    """Return ``(category, area, budget_band)`` derived from a record."""
    return (
        derive_category(record),
        derive_area(record),
        budget_band(derive_price(record)),
    )


def _row_to_contact(row: sqlite3.Row) -> Contact:
    return Contact(
        id=row["id"],
        handle=row["handle"],
        source=row["source"],
        dp=row["dp"],
        category=row["category"],
        area=row["area"],
        budget_band=row["budget_band"],
        raw=row["raw"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# --- public API ----------------------------------------------------------

def record_enquiry(
    db_path: Optional[str | Path],
    source: str,
    raw: str,
    dp: Optional[str] = None,
) -> Contact:
    """Record one enquiry and create or update the buyer contact behind it.

    Parses the DP (argument, else ``raw``) and a dedup handle out of ``raw``,
    then, when that DP's record is in the store, tags the contact with the DP
    plus the category / area / budget band derived from that record. A contact
    matched by handle is updated in place (new tags win, blanks preserve the
    old); a handle-less enquiry is stored as a fresh anonymous contact. The
    enquiry itself is appended to the ``enquiries`` log. Returns the contact.
    """
    resolved_dp = parse_dp(raw, dp)
    handle = parse_handle(source, raw)

    category = area = band = None
    if resolved_dp is not None:
        store = RecordStore(db_path)
        try:
            record = store.get(resolved_dp)
        finally:
            store.close()
        if record is not None:
            category, area, band = derive_tags(record)

    conn = _connect(db_path)
    try:
        now = _now()
        existing = None
        if handle is not None:
            existing = conn.execute(
                "SELECT * FROM contacts WHERE handle = ?", (handle,)
            ).fetchone()

        if existing is None:
            cur = conn.execute(
                """
                INSERT INTO contacts (
                    handle, source, dp, category, area, budget_band, raw,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (handle, source, resolved_dp, category, area, band, raw, now, now),
            )
            contact_id = cur.lastrowid
        else:
            contact_id = existing["id"]
            conn.execute(
                """
                UPDATE contacts
                   SET source = ?, dp = ?, category = ?, area = ?,
                       budget_band = ?, raw = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    source,
                    resolved_dp or existing["dp"],
                    category or existing["category"],
                    area or existing["area"],
                    band or existing["budget_band"],
                    raw,
                    now,
                    contact_id,
                ),
            )

        conn.execute(
            """
            INSERT INTO enquiries (contact_id, source, raw, dp, at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (contact_id, source, raw, resolved_dp, now),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        return _row_to_contact(row)
    finally:
        conn.close()


def matched_buyers(
    db_path: Optional[str | Path],
    record: PropertyRecord,
) -> List[Contact]:
    """Return the buyers whose tags match a new listing.

    A buyer matches when their ``category``, ``area`` and ``budget_band`` all
    equal the values derived from ``record``. Tags the record derives as
    ``None`` (for example an unknown price) are treated as unconstrained on that
    axis, so a listing with a resolvable price only matches same-band buyers.
    """
    category, area, band = derive_tags(record)

    clauses = ["category = ?"]
    params: List[object] = [category]
    if area is not None:
        clauses.append("area = ?")
        params.append(area)
    if band is not None:
        clauses.append("budget_band = ?")
        params.append(band)

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT * FROM contacts WHERE {' AND '.join(clauses)} ORDER BY id",
            params,
        ).fetchall()
        return [_row_to_contact(row) for row in rows]
    finally:
        conn.close()


def broadcast_text(record: PropertyRecord, matched: List[Contact]) -> str:
    """The targeted-broadcast line for a new listing (SPEC M7).

    For example: "new residential property in Pelham North - 3 matched buyers".
    Uses a hyphen, never an em dash.
    """
    category = derive_category(record)
    area = derive_area(record) or "the area"
    n = len(matched)
    buyers = "buyer" if n == 1 else "buyers"
    return f"new {category} property in {area} - {n} matched {buyers}"
