"""The studio working by itself (D129). Run every 15 minutes by da-studio-auto.timer:

    python -m studio.auto [--reads N] [--dry-run]

1. **Prepares** each open property that needs an ad (the list's "Waiting on an
   ad" and "Being marketed, no ad" groups), and each auction coming up whose ad the
   team made but which has no info pack yet (DP3107), once its folder is ready, meaning at
   least one Lightstone, property report, valuation or OTP and four photos, and
   still missing part of the set (an "await ad" note can outlive the ad):
   Claude reads the documents and chooses the photos, exactly as the "Read the
   documents" button does. At most ``--reads`` per run (each costs about R10 of
   Claude), soonest auction first; a failed read is retried the next day.
2. **Waits for the folder to settle.** Photos and documents arrive over days, so
   nothing is made or filed until nothing new has arrived for 24 hours (setting
   ``studio_quiet_hours``). When more arrive after a set was made, Claude chooses
   the photos again (unless someone placed photos themselves), or reads the
   documents again when a new one came in, and the set is filed again.
3. **Files** every prepared set in the property's SharePoint folder, and files it
   again whenever it changes: a lower listed price or a new auction date on the
   sheets, or someone's edit in the studio (left 10 minutes to settle first).
   See ``studio.publish`` for what is never touched.

A person can skip the wait and the queue with **Rush** (``rush``): the set is made
and filed straight away with what the folder holds, and the folder counts as
settled until something new arrives.

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
# Photos and documents often arrive over days (the valuer's first, the inspection
# photos later). The studio waits until nothing new has arrived for this long.
QUIET_HOURS = 24
RETRY_READ = timedelta(hours=20)


def needs_ad(p: catalog.Property, kit: dict, today: date) -> str:
    """'waiting' | 'no_ad' | 'no_pack' | '' : the list's groups, and 'no_pack' for a
    property being marketed or going to auction whose ad the team made but which has no
    info pack in the folder."""
    upcoming = bool(p.auction_date and p.auction_date >= today)
    if p.waiting_on_ad or (upcoming and not kit.get("auction_ad")):
        return "waiting"
    marketed = p.status == "Being marketed"
    if marketed and not upcoming and not (kit.get("forsale_ad") or kit.get("auction_ad")):
        return "no_ad"
    if (upcoming or marketed) and not kit.get("infopack"):
        return "no_pack"
    return ""


def reads_by_itself(db: str, p: catalog.Property, group: str, today: date) -> bool:
    """Whether the studio prepares this property without being asked. A missing info
    pack alone counts only for an auction coming up, unless the setting
    ``studio_marketed_packs`` is on (on 25 Nov 2026 that was 69 properties, about R700
    of reads); the rest can be rushed one by one."""
    from webapp import models

    if group != "no_pack" or (p.auction_date and p.auction_date >= today):
        return bool(group)
    return (models.get_setting(db, "studio_marketed_packs", "off") or "off").lower() == "on"


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


def quiet_for(db: str) -> timedelta:
    from webapp import models

    try:
        return timedelta(hours=float(models.get_setting(db, "studio_quiet_hours", "") or QUIET_HOURS))
    except ValueError:
        return timedelta(hours=QUIET_HOURS)


def observe(db: str, p: catalog.Property, items: list[catalog.Item], s: Optional[dict], quiet: timedelta) -> bool:
    """Note what the folder holds now; True once nothing has been added or replaced
    for ``quiet``. The first time a folder is seen, its newest file's date stands
    in for the last change, so a folder that has sat for weeks is settled at once."""
    ph, dc = catalog.signatures(items)
    s = s or {}
    if not s.get("changed_at"):
        stamp = catalog.newest(items)
        changed = (datetime.fromtimestamp(stamp, timezone.utc) if stamp else datetime.now(timezone.utc))
        changed = min(changed, datetime.now(timezone.utc)).isoformat(timespec="seconds")
    elif (ph, dc) != (s.get("photos_sig"), s.get("docs_sig")):
        changed = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        return _ago(s["changed_at"]) >= quiet or _rushed_with(s, ph, dc)
    cols = {"photos_sig": ph, "docs_sig": dc, "changed_at": changed}
    row = state.load(db, p.ref)
    if not s.get("read_docs_sig") and row and row["reading"] == "done":
        # read before the studio kept track: it was made from what is there now
        cols.update(read_photos_sig=ph, read_docs_sig=dc)
    state.set_status(db, p.ref, **cols)
    return _ago(changed) >= quiet or _rushed_with(s, ph, dc)


def _rushed_with(s: dict, ph: str, dc: str) -> bool:
    """Someone pressed Rush and nothing has arrived since: it counts as settled."""
    return bool(s.get("rushed_sig")) and s["rushed_sig"] == f"{ph}|{dc}"


def _waiting(db: str, ref: str, s: Optional[dict], quiet: timedelta) -> None:
    s = state.get_set(db, ref) or {}
    try:
        when = datetime.fromisoformat(s["changed_at"]).astimezone().strftime("%d %b %H:%M")
    except (KeyError, ValueError):
        when = "recently"
    hours = int(quiet.total_seconds() // 3600)
    msg = (f"Photos or documents were added to the folder {when}. The studio makes the set once nothing "
           f"new has arrived for {hours} hours. To make it now with what is there, press Rush.")
    if s.get("status") != "waiting" or s.get("message") != msg:
        state.set_status(db, ref, status="waiting", message=msg)


def wants_read(db: str, ref: str, items: list[catalog.Item]) -> bool:
    """Whether making the set now needs Claude to read the documents first: they were
    never read (or the last read failed), or a new document came in since."""
    row = state.load(db, ref)
    if not row or row["reading"] != "done":
        return True
    read_sig = (state.get_set(db, ref) or {}).get("read_docs_sig")
    return bool(read_sig) and read_sig != catalog.signatures(items)[1]


def rush(db: str, ref: str, by: str, w: Optional[Writer], *, read: Optional[bool] = None) -> Optional[dict]:
    """A person pressed Rush: make and file this property's set now, with what its
    folder holds, instead of waiting for the folder to settle or for its turn.

    The documents are read first when they were never read or a new one came in (and
    the photos chosen again when more arrived and nobody placed their own). It files
    the way the studio's own job does, never as a person's "save": a privacy warning
    still waits for someone to look. Later runs count the folder as settled until
    something new arrives. Returns the set's status row."""
    p = catalog.get(ref)
    if p is None:
        return None
    items = catalog.items(p)
    observe(db, p, items, state.get_set(db, ref), quiet_for(db))
    ph, dc = catalog.signatures(items)
    state.set_status(db, ref, rushed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     rushed_sig=f"{ph}|{dc}")
    if read is None:
        read = wants_read(db, ref, items)
    if read:
        prepare.read(db, ref, by)
    else:
        row = state.load(db, ref)
        s = state.get_set(db, ref) or {}
        if row and s.get("read_photos_sig") and s["read_photos_sig"] != ph and not prepare.placed_by_a_person(row):
            prepare.choose_photos(db, p, items, row, row["mode"] or prepare.auto_mode(p))
    row = state.load(db, ref)
    s = publish.publish(db, p, w)
    if row and row["reading"] == "failed" and s and s["status"] != "saved":
        state.set_status(db, ref, message=f"{row['message']} {s['message']}".strip())
        s = state.get_set(db, ref)
    return s


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
    quiet = quiet_for(db)
    props = catalog.open_properties()
    kits = catalog.kits_for(props)
    sets = state.all_sets(db)
    rows = state.all_rows(db)
    open_refs = {p.ref: p for p in props}
    wanted = {}
    for p in props:
        group = needs_ad(p, kits.get(p.folder, {}), today)
        if group and reads_by_itself(db, p, group, today):
            wanted[p.ref] = (p, group)
    # Files marketed together (DP2956.1 and .2) get one set, from the first of them.
    for ref in list(wanted):
        p = wanted[ref][0]
        sibs = catalog.siblings(p)
        if sibs and min(sibs + [ref], key=lambda r: catalog._ref_key(open_refs[r]) if r in open_refs else (0,)) != ref \
                and catalog.sold_together(p, catalog.items(p), sibs):
            del wanted[ref]

    # 0. what each folder holds now, and whether files are still arriving
    settled: dict[str, bool] = {}
    folder: dict[str, list[catalog.Item]] = {}
    for ref in sorted(set(wanted) | set(sets)):
        p = open_refs.get(ref)
        if p is None:
            continue
        folder[ref] = catalog.items(p)
        settled[ref] = observe(db, p, folder[ref], sets.get(ref), quiet)
    sets = state.all_sets(db)

    # 1. prepare what needs an ad, and catch up with folders that changed since
    jobs = []      # (priority, kind, property, why)
    for ref, (p, group) in wanted.items():
        r = rows.get(ref)
        if r and r["reading"] in ("done", "running"):
            continue
        tried = (sets.get(ref) or {}).get("read_at", "")
        if tried and _ago(tried) < RETRY_READ:
            continue
        items = folder[ref]
        if not folder_ready(items):
            continue
        if not publish.gaps(p, prepare.auto_mode(p), items):
            continue                                   # the team already made the whole set
        if not settled[ref]:
            if not dry_run:
                _waiting(db, ref, sets.get(ref), quiet)
            say(f"{ref}: files still arriving; waiting")
            continue
        jobs.append((_priority(p, group), "read", p, group))
    for ref, s in sets.items():
        r = rows.get(ref)
        p = open_refs.get(ref)
        if p is None or not r or r["reading"] != "done" or not settled.get(ref) or not s.get("read_docs_sig"):
            continue
        if s["docs_sig"] != s["read_docs_sig"]:
            if (s.get("read_at") or "") > (s.get("changed_at") or "") and _ago(s["read_at"]) < RETRY_READ:
                continue                               # tried since they came and it failed; the earlier read stands
            jobs.append((_priority(p, "waiting"), "read", p, "new documents"))
        elif s["photos_sig"] != s["read_photos_sig"]:
            if prepare.placed_by_a_person(state.load(db, ref)):
                state.set_status(db, ref, read_photos_sig=s["photos_sig"])   # her choice stands
            else:
                jobs.append((_priority(p, "waiting"), "photos", p, "new photos"))
    jobs.sort(key=lambda j: j[0])
    for _, kind, p, why in jobs[:reads]:
        if dry_run:
            say(f"{'read' if kind == 'read' else 'choose photos for'} {p.ref} ({why})")
            continue
        if kind == "read":
            state.set_status(db, p.ref, read_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            prepare.read(db, p.ref, prepare.STUDIO)
            row = state.load(db, p.ref)
            say(f"read {p.ref} ({why}): {row['reading']}" + (f" ({row['message']})" if row["reading"] == "failed" else ""))
        else:
            row = state.load(db, p.ref)
            items = catalog.items(p)
            chosen = prepare.choose_photos(db, p, items, row, row["mode"] or prepare.auto_mode(p))
            state.set_status(db, p.ref, read_photos_sig=catalog.signatures(items)[0])
            say(f"chose the photos again for {p.ref} ({why})" + ("" if chosen else ": kept the earlier ones"))
    if len(jobs) > reads:
        say(f"{len(jobs) - reads} more to read in later runs")

    # 2. file what is prepared, and what changed since
    sets = state.all_sets(db)
    rows = state.all_rows(db)
    for ref in sorted(set(wanted) | set(sets)):
        p = open_refs.get(ref)
        r = rows.get(ref)
        s = sets.get(ref) or {}
        if p is None or r is None:
            continue
        if r["reading"] != "done" and not publish.files(db, ref):
            continue                                   # not prepared yet
        if r["reading"] == "running" or s.get("status") == "saving" and _ago(s["updated_at"]) < SETTLE:
            continue
        if not settled.get(ref, True):
            if not dry_run and s.get("status") not in ("saved", "complete"):
                _waiting(db, ref, s, quiet)     # a filed set keeps saying so while it waits
            continue
        if r["updated_by"] and _ago(r["updated_at"]) < SETTLE:
            say(f"{ref}: someone is editing; next run")
            continue
        if dry_run:
            say(f"file {ref}")
            continue
        s = publish.publish(db, p, w)
        if s and (s["message"] != (sets.get(ref) or {}).get("message") or s["status"] != (sets.get(ref) or {}).get("status")):
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
