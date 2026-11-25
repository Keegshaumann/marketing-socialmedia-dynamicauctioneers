"""Filing a property's set in its SharePoint folder, and keeping it current (D129).

The rules, in the order they apply:

- **The team's own files always win.** A kind of file the team already has in the
  folder (their own auction ad, board, info pack...) is not added beside it, a file
  of the same name is never replaced, and once someone changes or replaces one of
  the studio's files in SharePoint the studio stops touching it. A file someone
  deleted is not added back unless a person presses "Save to SharePoint".
- **Nothing is filed that needs a person's eye first**: a set whose words mention
  a name from the matter (the privacy check) waits until someone has looked and
  pressed save, and a set missing its gold line, its background photo or (for an
  auction) its date waits until that is filled in.
- **It keeps up with the sheets and with edits.** Each set has a fingerprint of
  everything that goes into it (the words, the photos and when each photo last
  changed, for sale or auction). When the listed price drops, the auction date
  moves or someone edits a word in the studio, the fingerprint changes and the
  files that came out different are replaced in place, same file, new version.

Files go where the team keeps them: the ad, banner, icon, board, QR codes and
posting copy in the property's ``Advertisements`` folder, the info pack in
``Info Pack`` (whatever spelling the folder already has; created if missing).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from studio import catalog, facts as F, prepare, render, state
from studio.writer import Writer, WriteError, sha

log = logging.getLogger("studio.publish")

# Bump when a layout changes so every filed set is made again with it.
RENDER_VERSION = "7"   # 25 Nov 2026: the pack fills its pages (cards, dividers, tables, filler photos), movables

SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_files (
    ref       TEXT NOT NULL,
    name      TEXT NOT NULL,
    drive_id  TEXT NOT NULL DEFAULT '',
    item_id   TEXT NOT NULL DEFAULT '',
    ctag      TEXT NOT NULL DEFAULT '',
    sha       TEXT NOT NULL DEFAULT '',
    web_url   TEXT NOT NULL DEFAULT '',
    status    TEXT NOT NULL DEFAULT 'ours',  -- ours | theirs (someone changed it) | removed (deleted)
    saved_at  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (ref, name)
);
"""


def _conn(db: Optional[str]):
    from webapp import models

    conn = models._connect(db)
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# The set's state lives with the property's other state (studio.state).
get_set, all_sets, set_status = state.get_set, state.all_sets, state.set_status


def files(db: str, ref: str) -> dict[str, dict]:
    with _conn(db) as conn:
        rows = conn.execute("SELECT * FROM studio_files WHERE ref = ? ORDER BY name", (ref,)).fetchall()
    return {r["name"]: dict(r) for r in rows}


def _record(db: str, ref: str, name: str, **cols: Any) -> None:
    with _conn(db) as conn:
        conn.execute("INSERT OR IGNORE INTO studio_files (ref, name) VALUES (?, ?)", (ref, name))
        conn.execute(f"UPDATE studio_files SET {', '.join(f'{k} = ?' for k in cols)} WHERE ref = ? AND name = ?",
                     (*cols.values(), ref, name))


# --- what goes into a set ---------------------------------------------------------

def posting_copy(facts: dict) -> str:
    return "\n\n".join(x for x in (facts.get("caption"), facts.get("listing")) if x)


def build(prop: catalog.Property, row: dict, items: list[catalog.Item],
          kinds: Optional[list[str]] = None) -> dict[str, bytes]:
    """Every file of the set (or of ``kinds``), named the team's way."""
    pk = prepare.picks(row, items)
    out = render.build(prop, row["facts"], pk, row["mode"], {i.item_id: i for i in items},
                       kinds=kinds, with_qr=row["mode"] == "auction")
    copy = posting_copy(row["facts"])
    if copy:
        out[f"DP{prop.number} POSTING COPY.txt"] = copy.encode("utf-8")
    return out


def fingerprint(row: dict, pk: dict, items: list[catalog.Item]) -> str:
    by_id = {i.item_id: i for i in items}
    used = [pk[s] for s in state.SLOTS if pk.get(s)] + list(pk.get("gallery", []))
    blob = json.dumps({"v": RENDER_VERSION, "mode": row["mode"], "facts": row["facts"], "picks": pk,
                       "changed": {i: by_id[i].modified for i in used if i in by_id}},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def missing(row: dict, pk: dict) -> list[str]:
    """What a set can't go out without."""
    f = row["facts"] or {}
    out = []
    if not (f.get("headline") or "").strip():
        out.append("the gold line")
    background, extras = render.ad_photo_ids(pk, row["mode"] == "auction")
    if not background:
        out.append("a background photo")
    elif len(extras) < 2:
        out.append("more photos in the folder (the ad uses a background and 3 more)")
    if row["mode"] == "auction" and not (f.get("auction_date") or "").strip():
        out.append("the auction date")
    real = [x for x in f.get("features") or [] if (x.get("text") or "").strip()]
    if len(real) < 4:
        out.append("a fourth feature" if len(real) == 3 else f"{4 - len(real)} more features (4 or 6 in all)")
    for u in f.get("extra_units") or []:
        if len([x for x in u.get("items") or [] if (x.get("text") or "").strip()]) == 1:
            out.append(f"a second feature for the {(u.get('title') or 'flat').lower()} (2 or 4)")
    return out


# --- where each file goes ---------------------------------------------------------

_ADVERTS = re.compile(r"^ADVERTI?S?E?MENTS?$", re.I)
_PACK = re.compile(r"^INFO\s*-?\s*PACKS?$", re.I)


def folder_for(name: str, items: list[catalog.Item]) -> str:
    """The property's own folder for this file, as the team spells it."""
    pack = name.upper().endswith("INFO PACK.PDF")
    tops = sorted({i.rel for i in items if i.is_folder and "/" not in i.rel})
    want = _PACK if pack else _ADVERTS
    hit = next((t for t in tops if want.match(t.strip())), None)
    if hit is None and not pack:
        hit = next((t for t in tops if t.upper().startswith("ADVERT") and "BUDGET" not in t.upper()), None)
    return hit or ("Info Pack" if pack else "Advertisements")


def _kind(name: str) -> str:
    n = name.upper()
    if "BILLBOARD" in n:
        return "billboard"
    for key, label, rx in catalog.KIT:
        if rx.search(n):
            return key
    if n.endswith("INFO PACK.PDF"):
        return "infopack"
    if "QR CODE" in n:
        return "qr"
    return ""


def team_has(name: str, items: list[catalog.Item], ours: set[str]) -> bool:
    """The team already keeps its own file of this kind in the folder."""
    kind = _kind(name)
    if not kind:
        return False
    theirs = [i for i in items if not i.is_folder and i.item_id not in ours]
    if kind == "qr":
        return any(i.top.startswith("ADVERT") and "QR" in i.name.upper() for i in theirs)
    if kind == "billboard":
        return any(i.top.startswith("ADVERT") and "BILLBOARD" in i.name.upper() for i in theirs)
    return catalog.kit(theirs).get(kind, False)


TEAM_KINDS = {"auction_ad": "Auction ad", "forsale_ad": "For sale ad", "saia": "SAIA banner", "icon": "App icon",
              "board": "Board", "billboard": "Billboard", "infopack": "Info pack", "reel": "Reel", "qr": "QR code"}


def kind_of(name: str) -> str:
    """What a file in the ad folders is, from its name ("" when it is none of the outputs)."""
    n = name.upper()
    if "BILLBOARD" in n:
        return "billboard"
    k = _kind(name)
    if k:
        return k
    return "qr" if re.match(r"^(DP)?\s*\d{3,5}(\.\d+)?(\s*\(\d+\))?\.(PNG|JPE?G)$", n) else ""


def team_files(db: str, items: list[catalog.Item], ref: str) -> list[dict]:
    """The outputs the team made themselves (not the studio's), newest first."""
    ours = {f["item_id"] for f in files(db, ref).values() if f["item_id"]}
    out = []
    for i in items:
        if i.is_folder or i.item_id in ours or not i.top.startswith(("ADVERT", "INFO PACK")):
            continue
        kind = kind_of(i.name)
        if kind and (kind != "infopack" or i.ext == ".pdf"):
            out.append({"item": i, "kind": kind, "label": TEAM_KINDS[kind],
                        "date": datetime.fromtimestamp(i.modified).strftime("%d %b %Y") if i.modified else ""})
    return sorted(out, key=lambda f: -f["item"].modified)


def gaps(prop: catalog.Property, mode: str, items: list[catalog.Item], ours: set[str] = frozenset()) -> list[str]:
    """The set's main files (ad, banner, icon, board, pack) the folder has no file of
    its own for. Empty: the team already made the whole set, nothing to add."""
    return [F.name_for(prop, fmt.kind, mode) for fmt in render.formats_for(mode)
            if fmt.kind in CORE and not team_has(F.name_for(prop, fmt.kind, mode), items, set(ours))]


# The set proper. AD 2 and the billboard are extras: they go with any set the studio
# files, but a missing extra alone is never a reason to read a folder or file a set.
CORE = ("ad", "saia", "icon", "board", "infopack")


def _to_make(prop: catalog.Property, mode: str, items: list[catalog.Item], known: dict, ours: set,
             force: bool) -> list[str]:
    """The outputs worth making: not ones the team has their own of, nor ones the studio
    may not touch (changed in SharePoint, or deleted unless a person pressed save). The
    reel animates the studio's own ad, so it goes only where that ad does."""
    names = {fmt.kind: F.name_for(prop, fmt.kind, mode) for fmt in render.formats_for(mode)}

    def theirs(name: str) -> bool:
        f = known.get(name)
        return (not f or f["status"] != "ours") and team_has(name, items, ours)

    out = []
    for kind, name in names.items():
        f = known.get(name)
        if f and (f["status"] == "theirs" or (f["status"] == "removed" and not force)):
            continue
        if theirs(name) or (kind == "reel" and theirs(names["ad"])):
            continue
        out.append(kind)
    return out


# --- filing -----------------------------------------------------------------------

def publish(db: str, prop: catalog.Property, w: Optional[Writer], *, force: bool = False) -> dict:
    """Make and file the set when it changed. ``force``: a person pressed save, so
    the privacy warnings count as checked and deleted files are added back.
    Returns the set's status row."""
    items = catalog.items(prop)
    row = prepare.current(db, prop, items)
    row = prepare.follow_the_sheets(db, prop, row)
    pk = prepare.picks(row, items)
    fp = fingerprint(row, pk, items)
    s = get_set(db, prop.ref) or {"cleared": [], "fingerprint": "", "status": ""}
    cleared = list(s["cleared"])
    if force:
        cleared = sorted(set(cleared) | set(row["warnings"]))
        set_status(db, prop.ref, cleared=cleared)

    known = files(db, prop.ref)
    ours = {f["item_id"] for f in known.values() if f["item_id"]}
    if not gaps(prop, row["mode"], items, ours):
        set_status(db, prop.ref, status="complete", mode=row["mode"],
                   message="The folder already has the team's own files for this set, so there is nothing to add.")
        return get_set(db, prop.ref)
    need = missing(row, pk)
    if need:
        set_status(db, prop.ref, status="held", mode=row["mode"],
                   message="Not saved yet: fill in " + ", ".join(need) + ".")
        return get_set(db, prop.ref)
    unchecked = [x for x in row["warnings"] if x not in cleared]
    if unchecked:
        set_status(db, prop.ref, status="held", mode=row["mode"],
                   message="Not saved yet: " + " ".join(unchecked) + " Press save once it's right.")
        return get_set(db, prop.ref)
    if w is None:
        set_status(db, prop.ref, status="ready", mode=row["mode"],
                   message="Ready. The studio can't save to SharePoint yet, so download the set for now.")
        return get_set(db, prop.ref)
    if not force and s["status"] == "saved" and s["fingerprint"] == fp:
        return s
    drive = next((i.drive_id for i in items), "")
    if not drive:
        set_status(db, prop.ref, status="failed", message="The property folder looks empty in the mirror; try again later.")
        return get_set(db, prop.ref)

    set_status(db, prop.ref, status="saving", mode=row["mode"], message="")
    known = files(db, prop.ref)
    ours = {f["item_id"] for f in known.values() if f["item_id"]}
    try:
        made = build(prop, row, items, kinds=_to_make(prop, row["mode"], items, known, ours, force))
    except Exception:
        log.exception("render for SharePoint %s", prop.ref)
        set_status(db, prop.ref, status="failed", message="Making the files failed; the studio tries again shortly.")
        return get_set(db, prop.ref)

    done, skipped, problems = [], [], []
    for name, data in made.items():
        f = known.get(name)
        folder = folder_for(name, items)
        try:
            if f and f["status"] == "theirs":
                skipped.append(f"{name} (changed in SharePoint by someone, so left alone)")
                continue
            if f and f["status"] == "removed" and not force:
                continue
            if (not f or f["status"] != "ours") and team_has(name, items, ours):
                continue
            if f and f["status"] == "ours" and f["item_id"]:
                if f["sha"] == sha(data) and not force:
                    continue
                now = w.item(f["drive_id"] or drive, f["item_id"])
                if now is None:
                    _record(db, prop.ref, name, status="removed")
                    if not force:
                        continue
                elif now.get("cTag") != f["ctag"] and not w.made_by_us(now):
                    _record(db, prop.ref, name, status="theirs", web_url=now.get("webUrl", f["web_url"]))
                    skipped.append(f"{name} (changed in SharePoint by someone, so left alone)")
                    continue
                else:
                    it = w.replace(f["drive_id"] or drive, f["item_id"], name, data)
                    _record(db, prop.ref, name, ctag=it.get("cTag", ""), sha=sha(data), status="ours",
                            web_url=it.get("webUrl", ""), saved_at=_now())
                    done.append(name)
                    continue
            path = f"{prop.folder}/{folder}/{name}"
            try:
                it = w.add(drive, path, data)
            except WriteError as exc:
                if exc.kind != "conflict":
                    raise
                there = w.by_path(drive, path)
                if not (there and w.made_by_us(there)):   # a file the team saved under that name
                    skipped.append(f"{name} (the folder already has a file of that name)")
                    continue
                it = w.replace(drive, there["id"], name, data)
            _record(db, prop.ref, name, drive_id=drive, item_id=it["id"], ctag=it.get("cTag", ""), sha=sha(data),
                    status="ours", web_url=it.get("webUrl", ""), saved_at=_now())
            done.append(name)
        except WriteError as exc:
            log.warning("saving %s for %s: %s", name, prop.ref, exc)
            problems.append(str(exc))
            if exc.kind == "denied":
                break

    url = s.get("folder_url") or ""
    if done and not url:
        url = w.folder_url(drive, f"{prop.folder}/{folder_for(prop.ref + ' AD.png', items)}")
    if problems:
        set_status(db, prop.ref, status="failed", folder_url=url,
                   message=f"Saved {len(done)} file{'s' if len(done) != 1 else ''}; {problems[0]}")
    else:
        when = datetime.now().strftime("%d %b %H:%M")
        what = (f"saved {len(done)} file{'s' if len(done) != 1 else ''}" if done else "nothing new to save")
        note = f"{when}: {what}." + (" Left alone: " + "; ".join(skipped) + "." if skipped else "")
        set_status(db, prop.ref, status="saved", fingerprint=fp, folder_url=url, message=note,
                   **({"saved_at": _now()} if done else {}))
    return get_set(db, prop.ref)
