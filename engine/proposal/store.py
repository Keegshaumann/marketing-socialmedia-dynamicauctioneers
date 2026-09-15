"""SQLite persistence for proposals and OTPs, one row per DP (M9, D103, D114).

Each document type has its own table on the shared engine database, apart from
``records``: a proposal or OTP exists before (and sometimes without) a marketing
record, and it holds the seller's name and number, which the record keeps behind
``public_view``. The JSON blob is the source of truth; the few columns beside it
are for listing. ``OtpStore`` (``engine.otpgen.store``) is this class pointed at
its own table and model.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from engine.proposal.model import Proposal


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ProposalStore:
    TABLE = "proposals"
    MODEL: Type[BaseModel] = Proposal

    def __init__(self, db_path: "str | Path") -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        # TABLE is a class constant, never user input, so formatting it in is safe.
        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE} (
                dp            TEXT PRIMARY KEY,
                seller_name   TEXT,
                auction_date  TEXT,
                proposal_json TEXT NOT NULL,
                created_at    TEXT,
                updated_at    TEXT,
                updated_by    TEXT
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ProposalStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, dp: str):
        row = self.conn.execute(f"SELECT proposal_json FROM {self.TABLE} WHERE dp = ?", (dp,)).fetchone()
        return self.MODEL.model_validate_json(row["proposal_json"]) if row else None

    def save(self, doc, user: str = "") -> None:
        now = _now()
        auction_date = getattr(doc, "auction_date", None)
        self.conn.execute(
            f"""
            INSERT INTO {self.TABLE} (dp, seller_name, auction_date, proposal_json, created_at, updated_at, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dp) DO UPDATE SET
                seller_name = excluded.seller_name,
                auction_date = excluded.auction_date,
                proposal_json = excluded.proposal_json,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by
            """,
            (
                doc.dp,
                doc.seller_name,
                auction_date.isoformat() if auction_date else None,
                doc.model_dump_json(),
                now,
                now,
                user,
            ),
        )
        self.conn.commit()

    def delete(self, dp: str) -> bool:
        cur = self.conn.execute(f"DELETE FROM {self.TABLE} WHERE dp = ?", (dp,))
        self.conn.commit()
        return cur.rowcount == 1

    def list(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            f"SELECT dp, seller_name, updated_at, updated_by, proposal_json FROM {self.TABLE} ORDER BY updated_at DESC"
        ).fetchall()
        out = []
        for row in rows:
            doc = self.MODEL.model_validate_json(row["proposal_json"])
            out.append({
                "dp": row["dp"],
                "seller_name": row["seller_name"] or "",
                "auction_date": getattr(doc, "auction_date", None),
                "updated_at": row["updated_at"],
                "updated_by": row["updated_by"] or "",
                "generated_at": getattr(doc, "generated_at", ""),
                "has_pdf": bool(getattr(doc, "pdf_file", "")),
            })
        return out
