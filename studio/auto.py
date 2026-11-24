"""The studio working by itself (D129). Run every 15 minutes by da-studio-auto.timer:

    python -m studio.auto [--reads N] [--dry-run]

1. **Prepares** each open property that needs an ad (the list's "Waiting on an
   ad" and "Being marketed, no ad" groups) once its folder is ready, meaning at
   least one Lightstone, property report, valuation or OTP and four photos:
   Claude reads the documents and chooses the photos, exactly as the "Read the
   documents" button does. At most ``--reads`` per run (each costs about R10 of
   Claude), soonest auction first; a failed read is retried the next day.
2. **Files** every prepared set in the property's SharePoint folder, and files it
   again whenever it changes: a lower listed price or a new auction date on the
   sheets, or someone's edit in the studio (left 10 minutes to settle first).
   See ``studio.publish`` for what is never touched.

Switch it off with the setting ``studio_auto = off`` (scripts or the database);
the screens keep working either way.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from studio import catalog, prepare, publish, state
from studio.writer import Writer, writer

log = logging.getLogger("studio.auto")

MIN_PHOTOS = 4
SETTLE = timedelta(minutes=10)
RETRY_READ = timedelta(hours=20)


def needs_ad(p: catalog.Property, kit: dict, today: date) -> str:
    """'waiting' | 'no_ad' | '' : the same groups as the studio's list."""
    upcoming = bool(p.auction_date and p.auction_date >= today)
    if p.waiting_on_ad or (upcoming and not kit.get("auction_ad")):
        return "waiting"
    if p.status == "Being marketed" and not upcoming and not (kit.get("forsale_ad") or kit.get("auction_ad")):
        return "no_ad"
    return ""


def folder_ready(items: list[catalog.Item]) -> bool:
    docs = catalog.documents(items)
    has_docs = any(docs[k] for k in ("report", "lightstone", "valuation", "otp"))
    return has_docs and len(catalog.photos(items)) >= MIN_PHOTOS


def _ago(iso: str) -> timedelta:
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return timedelta.max


def _priority(p: catalog.Property, group: str) -> tuple:
    d = p.auction_date if p.auction_date and p.auction_date >= date.today() else None
    return (d is None, d or date.max, group != "waiting", tuple(-n for n in catalog._ref_key(p)))


def run(db: str, w: Optional[Writer] = None, *, reads: int = 4, dry_run: bool = False) -> list[str]:
    from webapp import models

    out: list[str] = []

    def say(line: str) -> None:            # logged as it happens: a run can take minutes
        out.append(line)
        log.info(line)

    if (models.get_setting(db, "studio_auto", "on") or "on").lower() == "off":
        say("switched off (setting studio_auto)")
        return out
    if not catalog.available():
        say("the director mirror can't be read; nothing done")
        return out
    today = date.today()
    props = catalog.open_properties()
    kits = catalog.kits_for(props)
    sets = publish.all_sets(db)
    rows = state.all_rows(db)

    # 1. prepare what needs an ad
    wanted = {}
    for p in props:
        group = needs_ad(p, kits.get(p.folder, {}), today)
        if group:
            wanted[p.ref] = (p, group)
    queue = []
    for ref, (p, group) in wanted.items():
        r = rows.get(ref)
        if r and r["reading"] in ("done", "running"):
            continue
        tried = (sets.get(ref) or {}).get("read_at", "")
        if tried and _ago(tried) < RETRY_READ:
            continue
        if not folder_ready(catalog.items(p)):
            continue
        queue.append((p, group))
    queue.sort(key=lambda pg: _priority(*pg))
    for p, group in queue[:reads]:
        if dry_run:
            say(f"read {p.ref} ({group})")
            continue
        publish.set_status(db, p.ref, read_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        prepare.read(db, p.ref, prepare.STUDIO)
        row = state.load(db, p.ref)
        say(f"read {p.ref} ({group}): {row['reading']}" + (f" ({row['message']})" if row["reading"] == "failed" else ""))
    if len(queue) > reads:
        say(f"{len(queue) - reads} more to read in later runs")

    # 2. file what is prepared, and what changed since
    sets = publish.all_sets(db)
    rows = state.all_rows(db)
    open_refs = {p.ref: p for p in props}
    for ref in sorted(set(wanted) | set(sets)):
        p = open_refs.get(ref)
        r = rows.get(ref)
        if p is None or r is None:
            continue
        if ref not in sets and r["reading"] != "done":
            continue                                   # not prepared yet
        if r["reading"] == "running" or (sets.get(ref) or {}).get("status") == "saving" and _ago(sets[ref]["updated_at"]) < SETTLE:
            continue
        if r["updated_by"] and _ago(r["updated_at"]) < SETTLE:
            say(f"{ref}: someone is editing; next run")
            continue
        if dry_run:
            say(f"file {ref}")
            continue
        before = (sets.get(ref) or {}).get("message")
        s = publish.publish(db, p, w)
        if s and (s["message"] != before or s["status"] != (sets.get(ref) or {}).get("status")):
            say(f"{ref}: {s['status']} - {s['message']}")
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reads", type=int, default=int(os.environ.get("STUDIO_AUTO_READS", "4")))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    except ImportError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from webapp import models
    db = models.resolve_db_path()
    run(db, writer(), reads=args.reads, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
