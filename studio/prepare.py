"""Getting a property's set ready: the current words and photos, and Claude's read.

Shared by the screens (a person clicks "Read the documents") and the studio's own
job (``studio.auto``), so both fill a property the same way and both leave what a
person typed or placed alone.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from studio import catalog, facts as F, photopick, render, state
from studio.sharepoint import StoreError

log = logging.getLogger("studio.prepare")

# Not a person: the studio's own job signs its reads like this.
STUDIO = "studio"
# "_mode" sits in the edited set when a person chose for sale / auction themselves.
MODE_CHOSEN = "_mode"


def auto_mode(prop: catalog.Property) -> str:
    """Auction only once there is an auction date coming up; for sale otherwise."""
    d = prop.auction_date
    return "auction" if d and d >= date.today() else "forsale"


def current(db: str, prop: catalog.Property, items: list[catalog.Item]) -> dict:
    """The saved row, created with the sheet facts the first time a property opens.
    Sheet facts that changed since (a new auction date, a lower price) flow in
    unless a person edited that field."""
    row = state.load(db, prop.ref)
    if row is None:
        state.save(db, prop.ref, facts=F.initial(prop, items), mode=F.mode_for(prop))
        row = state.load(db, prop.ref)
    else:
        sheets = F.from_sheets(prop, items, row["mode"])
        merged = F.merge(row["facts"], sheets, row["edited"])
        if merged != row["facts"]:
            state.save(db, prop.ref, facts=merged)
            row["facts"] = merged
    if row["reading"] == "running" and stale(row["updated_at"]):
        state.save(db, prop.ref, reading="failed", message="The read was interrupted; start it again.")
        row = state.load(db, prop.ref)
    row["mode"] = row["mode"] or F.mode_for(prop)
    return row


def follow_the_sheets(db: str, prop: catalog.Property, row: dict) -> dict:
    """The studio's own sets switch to auction when an auction date is set (and back
    when it has passed), unless a person chose the mode."""
    want = auto_mode(prop)
    if MODE_CHOSEN in row["edited"] or row["mode"] == want:
        return row
    facts = dict(row["facts"])
    if "badge" not in row["edited"] and facts.get("sale_kind"):
        facts["badge"] = f"{facts['sale_kind']} {'AUCTION' if want == 'auction' else 'SALE'}!"
    sheets = F.from_sheets(prop, catalog.items(prop), want)
    facts = F.merge(facts, sheets, row["edited"])
    state.save(db, prop.ref, mode=want, facts=facts)
    return {**row, "mode": want, "facts": facts}


def stale(iso: str) -> bool:
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(iso) > timedelta(minutes=10)
    except ValueError:
        return True


def picks(row: dict, items: list[catalog.Item]) -> dict:
    return render.photo_picks(row["photos"], catalog.photos(items))


# --- Claude reads the folder -----------------------------------------------------

def choose_photos(db: str, prop: catalog.Property, items: list, row: dict, mode: str) -> bool:
    """Claude's photo choice, stored beside hers as photos["claude"]. True when it chose."""
    try:
        f = row["facts"] or {}
        about = ", ".join(x for x in (f.get("subline"), f.get("short_title"), f.get("headline")) if x)
        chosen = photopick.choose(prop, catalog.photos(items), auction=(mode == "auction"), about=about)
    except Exception:
        log.exception("photo choice failed for %s; the studio's own picks stay", prop.ref)
        return False
    if not chosen:
        return False
    fresh = state.load(db, prop.ref)               # she may have placed a photo meanwhile
    state.save(db, prop.ref, photos={**((fresh and fresh["photos"]) or {}), "claude": chosen})
    state.set_status(db, prop.ref, read_photos_sig=catalog.signatures(items)[0])
    return True


def placed_by_a_person(row: dict) -> bool:
    """She put a photo in a slot or changed the pack's photos herself."""
    p = row.get("photos") or {}
    return any(p.get(k) for k in state.SLOTS) or "gallery" in p


def read(db: str, ref: str, by: str) -> None:
    """Fill every field from the folder's documents and choose the photos.
    ``by`` is the person who asked, or ``STUDIO`` for the studio's own job."""
    try:
        prop = catalog.get(ref)
        items = catalog.items(prop)
        row = state.load(db, ref)
        if row is None or not row["facts"]:   # never opened: start from the sheets
            state.save(db, ref, facts=F.initial(prop, items),
                       mode=auto_mode(prop) if by == STUDIO else F.mode_for(prop))
            row = state.load(db, ref)
        mode = row["mode"] or F.mode_for(prop)
        sheets = F.from_sheets(prop, items, mode)
        # The documents and the photos are two separate questions to Claude, asked
        # side by side; a failed photo choice never fails the read.
        with ThreadPoolExecutor(1) as ex:
            docs = ex.submit(F.read_documents, prop, items, sheets)
            chosen = choose_photos(db, prop, items, row, mode)
            found, warnings = docs.result()
        row = state.load(db, ref)
        merged = F.merge(row["facts"], found, row["edited"])
        merged = F.merge(merged, sheets, row["edited"])   # the sheets win on dates and prices
        note = f"Read {datetime.now().strftime('%d %b %H:%M')}" + (", photos chosen" if chosen else "")
        if by == STUDIO:
            note += " by the studio"
        # The studio's own read leaves updated_by alone: "You worked on" is for people.
        state.save(db, ref, facts=merged, reading="done", warnings=warnings, message=note,
                   by="" if by == STUDIO else by)
        ph, dc = catalog.signatures(items)
        state.set_status(db, ref, read_docs_sig=dc, read_photos_sig=ph)
    except Exception as exc:  # shown to her; the details go to the log
        log.exception("studio read failed for %s", ref)
        msg = str(exc) if isinstance(exc, StoreError) else "Reading the documents failed. Try again, or type the details in."
        state.save(db, ref, reading="failed", message=msg)
