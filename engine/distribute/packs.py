"""Manual-channel packs, per-channel status log and price-drop bursts (M6, 5A).

Distribution has two arms (SPEC M6). API channels (GoHighLevel Social Planner,
WhatsApp via Peach, Property24 via the Prop Data feed) push automatically once
credentials exist. Every other channel gets a **ready-to-post pack**: the
rendered artifacts copied into one folder with a ``checklist.md`` a human works
down. This module builds that pack, records what actually went out per DP per
channel per version (the "Proof of Marketing" trail, SPEC M6 acceptance), and
turns a price *drop* into a REDUCED re-engagement event.

Design rules baked in here:
- The pack is built from already-rendered artifacts (``engine.render``), which
  are produced from ``public_view()`` only, so no pack can leak owner PII.
- ``channel_status`` lives in the same SQLite database as ``records`` and
  ``state_events`` (engine.store), so the marketing trail and the lifecycle
  trail share one file. The log is append-only: one row per post attempt, so the
  history of every version posted to every channel is preserved.
- ``price_drop_burst`` fires only on a genuine decrease. A price *rise* or an
  unchanged/unpriced record returns ``None`` (nothing to re-engage on), so a
  caller can queue the burst unconditionally and get an event only when it is
  warranted.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional


# Channels that have no confirmed automated path in v1 and so always fall back
# to the manual pack (SPEC M6 "Mechanisms", open questions #5/#7). A caller may
# override this set; it is the sensible default while feed/portal access is
# unconfirmed.
DEFAULT_MANUAL_CHANNELS: List[str] = [
    "property24",       # until the Prop Data feed is confirmed (api-support@propdata.net)
    "jamesedition",     # >= R10m only; listing method unknown, manual v1
    "own_website",      # platform unknown (open question #5)
    "auction_boards",   # printing and erecting is physical
    "meta_paid_boost",  # paid campaign stays a human boost in v1
]

# How each rendered format is used on a manual channel, for the checklist.
_FORMAT_USE = {
    "portal_listing": "Property24 / JamesEdition listing copy",
    "facebook_post": "Facebook post copy",
    "whatsapp_blast": "WhatsApp broadcast text",
    "email_blast": "Email campaign subject and body",
    "demo_ad": "Branded advert image (attach to every channel)",
    "info_pack": "Buyer info pack for the auction page",
    "webapp_icon": "Upcoming-auction website tile",
    "saia_banner": "SAIA alert banner",
    "alert_mailer": "Alert mailer HTML and audience list",
    "auction_board": "Print-ready auction board",
}


# --- small helpers --------------------------------------------------------

def _now() -> str:
    """UTC timestamp in ISO 8601, seconds precision (matches engine.store)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_db_path(db_path: Optional[str | Path]) -> str:
    """Resolve the database path: arg, else ``ENGINE_DB``, else ``./engine.db``.

    Mirrors ``engine.store`` so the ``channel_status`` table lands in the same
    SQLite file as ``records`` and ``state_events``.
    """
    if db_path is not None:
        return str(db_path)
    return os.environ.get("ENGINE_DB", "engine.db")


def _art_field(artifact, name: str):
    """Read a field off an ``Artifact`` dataclass or a manifest dict."""
    if isinstance(artifact, dict):
        return artifact.get(name)
    return getattr(artifact, name, None)


def _as_record_dict(record) -> dict:
    """Normalise a ``PropertyRecord`` or a plain dict to a dict.

    Only non-PII fields (``dp``, ``marketing``) are read downstream, so using the
    full ``model_dump`` here is safe; ``public_view`` is unnecessary.
    """
    if hasattr(record, "model_dump"):
        return record.model_dump(mode="json")
    return record or {}


def _parse_price(display) -> Optional[float]:
    """Extract a numeric rand figure from a price string, or ``None``.

    ``"R2 500 000"`` and ``"2500000"`` become ``2500000.0``; a non-numeric phrase
    such as ``"Offers invited"`` returns ``None`` (no figure to compare).
    """
    if display is None:
        return None
    if isinstance(display, (int, float)) and not isinstance(display, bool):
        return float(display)
    digits = re.sub(r"[^\d]", "", str(display))
    return float(digits) if digits else None


# --- ready-to-post pack ---------------------------------------------------

def build_manual_pack(
    dp: str,
    artifacts: Iterable,
    output_root: str = ".",
    channels: Optional[List[str]] = None,
) -> str:
    """Build a ready-to-post folder plus ``checklist.md`` for the manual channels.

    Copies every rendered artifact into
    ``<output_root>/DP<dp>/packs/v<version>/`` and writes a ``checklist.md`` that
    lists the manual channels to work down and maps each artifact file to its
    use. ``version`` is the highest version among the artifacts (default 1), so a
    re-render lands in its own pack folder and the previous version is kept.

    ``artifacts`` may be ``Artifact`` dataclasses (engine.render) or the plain
    dicts from ``artifacts/manifest.json``. An artifact whose file is missing is
    still listed, flagged ``(file missing)``, rather than crashing the pack.

    Returns the pack folder path.
    """
    arts = list(artifacts)
    versions = [(_art_field(a, "version") or 1) for a in arts]
    version = max(versions) if versions else 1

    pack_dir = Path(output_root) / f"DP{dp}" / "packs" / f"v{version}"
    pack_dir.mkdir(parents=True, exist_ok=True)

    manual_channels = channels if channels is not None else DEFAULT_MANUAL_CHANNELS

    rows: List[str] = []
    for art in arts:
        fmt = _art_field(art, "fmt") or "artifact"
        raw_path = _art_field(art, "path")
        use = _FORMAT_USE.get(fmt, "Attach where relevant")
        if raw_path and Path(raw_path).exists():
            dest = pack_dir / Path(raw_path).name
            shutil.copy2(raw_path, dest)
            filename = dest.name
        else:
            filename = f"{Path(raw_path).name if raw_path else fmt} (file missing)"
        rows.append(f"| {fmt} | {filename} | {use} |")

    checklist = _render_checklist(dp, version, manual_channels, rows)
    (pack_dir / "checklist.md").write_text(checklist, encoding="utf-8")
    return str(pack_dir)


def _render_checklist(
    dp: str,
    version: int,
    manual_channels: List[str],
    artifact_rows: List[str],
) -> str:
    """Render the ``checklist.md`` markdown for a manual pack."""
    channel_lines = "\n".join(f"- [ ] {ch}" for ch in manual_channels)
    if artifact_rows:
        table = (
            "| Format | File | Use |\n"
            "|---|---|---|\n" + "\n".join(artifact_rows)
        )
    else:
        table = "_No artifacts rendered for this version._"

    return (
        f"# DP{dp} ready-to-post pack (version {version})\n\n"
        f"Generated: {_now()}\n\n"
        "This pack covers the channels without a confirmed automated path. The "
        "API channels (GoHighLevel social, WhatsApp via Peach, Property24 via "
        "the Prop Data feed) post automatically once their credentials are "
        "configured, so they are not on this list.\n\n"
        "## Manual channels to post\n\n"
        f"{channel_lines}\n\n"
        "## Artifacts in this pack\n\n"
        f"{table}\n\n"
        "## After posting\n\n"
        "Log each channel back into the platform (Proof of Marketing) so the "
        "posted/not-posted status per channel per version stays accurate.\n"
    )


# --- per-channel status log (Proof of Marketing) --------------------------

def _connect(db_path: Optional[str | Path]) -> sqlite3.Connection:
    conn = sqlite3.connect(_resolve_db_path(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_status (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            dp      TEXT NOT NULL,
            channel TEXT NOT NULL,
            version INTEGER NOT NULL,
            status  TEXT NOT NULL,
            at      TEXT NOT NULL,
            note    TEXT
        )
        """
    )
    conn.commit()
    return conn


def log_posted(
    db_path: Optional[str | Path],
    dp: str,
    channel: str,
    version: int,
    status: str,
    note: Optional[str] = None,
) -> dict:
    """Append a post attempt to ``channel_status`` (append-only, one row each).

    ``status`` is a free string; the workflow uses ``"posted"``, ``"pending"``,
    ``"failed"``, ``"skipped"`` and ``"manual"``. Every outbound artifact logged
    per DP per channel per version is the "Proof of Marketing" trail (SPEC M6).
    Returns the inserted row as a dict.
    """
    conn = _connect(db_path)
    try:
        at = _now()
        cur = conn.execute(
            """
            INSERT INTO channel_status (dp, channel, version, status, at, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (dp, channel, int(version), status, at, note),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "dp": dp,
            "channel": channel,
            "version": int(version),
            "status": status,
            "at": at,
            "note": note,
        }
    finally:
        conn.close()


def list_status(db_path: Optional[str | Path], dp: str) -> List[dict]:
    """Return every ``channel_status`` row for ``dp`` (Proof of Marketing view).

    Ordered by channel, then version, then time, so the full posting history of
    each channel across every version reads top to bottom.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, dp, channel, version, status, at, note
              FROM channel_status
             WHERE dp = ?
             ORDER BY channel, version, at, id
            """,
            (dp,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# --- price-drop re-engagement burst ---------------------------------------

def price_drop_burst(record_before, record_after) -> Optional[dict]:
    """Return a REDUCED re-engagement event when the price dropped, else ``None``.

    A price *decrease* is a marketing event, not silent maintenance (SPEC M6): it
    queues a re-engagement blast on WhatsApp and Facebook labelled REDUCED. A
    price rise, an unchanged price, or a record with no comparable figure on
    either side (for example ``"Offers invited"``) returns ``None``.

    ``record_before`` / ``record_after`` may be ``PropertyRecord`` instances or
    plain dicts. The figure is read from ``marketing.price_display``.
    """
    before = _as_record_dict(record_before)
    after = _as_record_dict(record_after)

    old_display = (before.get("marketing") or {}).get("price_display")
    new_display = (after.get("marketing") or {}).get("price_display")

    old_price = _parse_price(old_display)
    new_price = _parse_price(new_display)
    if old_price is None or new_price is None:
        return None
    if new_price >= old_price:
        return None

    drop_amount = old_price - new_price
    drop_pct = round(drop_amount / old_price * 100, 1) if old_price else None

    return {
        "event": "price_drop_burst",
        "dp": after.get("dp") or before.get("dp"),
        "label": "REDUCED",
        "old_price": old_price,
        "new_price": new_price,
        "old_display": old_display,
        "new_display": new_display,
        "drop_amount": drop_amount,
        "drop_pct": drop_pct,
        "channels": ["whatsapp_broadcast", "facebook"],
        "created": _now(),
    }
