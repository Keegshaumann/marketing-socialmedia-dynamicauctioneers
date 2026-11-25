"""Changes asked for in her own words.

Nikki types what she wants different ("take SECOND 3-BED HOUSE out of the main house
and add the double garage", "use the pool photo as the background", "shorten the
caption") and Claude makes the change: it sees the set's current details and her
request, and answers with only the fields that change. A request about photos goes
on to ``photopick.rechoose``, which looks at the photos themselves.

What changes counts as hers (it joins the edited fields and placed photos), so a later
read never undoes it, and the studio files the set again as it does after any edit.
Every request is kept with what it replaced, so the latest one can be undone.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from studio import catalog, facts as F, form as FORM, lessons, photopick, prepare, state
from studio.sharepoint import StoreError

log = logging.getLogger("studio.ask")

SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_asks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT NOT NULL,
    text        TEXT NOT NULL,
    asked_by    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'running',   -- running | done | failed | undone
    reply       TEXT NOT NULL DEFAULT '',
    changed     TEXT NOT NULL DEFAULT '[]',        -- what changed, as she knows the fields
    before      TEXT NOT NULL DEFAULT '{}',        -- {"facts": {...}, "edited": [...], "photos": {...}}
    created_at  TEXT NOT NULL DEFAULT '',
    done_at     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS studio_asks_ref ON studio_asks (ref, id);
"""

PHOTO_LABELS = {"hero": "Background (for sale)", "aerial": "Background (auction)", "photo1": "Photo 1",
                "photo2": "Photo 2", "photo3": "Photo 3", "gallery": "Info pack photos"}
TOO_LONG = 1500       # characters in a request


def _conn(db: Optional[str]):
    from webapp import models

    conn = models._connect(db)
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r) -> dict[str, Any]:
    return {**dict(r), "changed": json.loads(r["changed"]), "before": json.loads(r["before"])}


def get(db: str, ask_id: int) -> Optional[dict]:
    with _conn(db) as conn:
        r = conn.execute("SELECT * FROM studio_asks WHERE id = ?", (ask_id,)).fetchone()
    return _row(r) if r else None


def history(db: str, ref: str, n: int = 6) -> list[dict]:
    """The newest first. A request still "running" after ten minutes was interrupted."""
    with _conn(db) as conn:
        rows = [_row(r) for r in conn.execute(
            "SELECT * FROM studio_asks WHERE ref = ? ORDER BY id DESC LIMIT ?", (ref, n))]
    for a in rows:
        if a["status"] == "running" and prepare.stale(a["created_at"]):
            _finish(db, a["id"], status="failed", reply="This request was interrupted; ask again.")
            a.update(status="failed", reply="This request was interrupted; ask again.")
    return rows


def latest(db: str, ref: str) -> Optional[dict]:
    got = history(db, ref, 1)
    return got[0] if got else None


def start(db: str, ref: str, text: str, by: str) -> int:
    with _conn(db) as conn:
        cur = conn.execute("INSERT INTO studio_asks (ref, text, asked_by, created_at) VALUES (?, ?, ?, ?)",
                           (ref, text.strip()[:TOO_LONG], by, _now()))
        return int(cur.lastrowid)


def _finish(db: str, ask_id: int, **cols: Any) -> None:
    for k in ("changed", "before"):
        if k in cols:
            cols[k] = json.dumps(cols[k])
    cols["done_at"] = _now()
    with _conn(db) as conn:
        conn.execute(f"UPDATE studio_asks SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                     (*cols.values(), ask_id))


# --- what Claude is asked ------------------------------------------------------------

class Change(BaseModel):
    field: str = Field(description="The field's key, exactly as listed")
    value: str = Field(description="The field's whole new value. A text field: the text itself. A list field: "
                                   "JSON in exactly the shape its current value has")


class Answer(BaseModel):
    changes: list[Change] = Field(description="Only the fields her request changes; empty when no words change")
    photos: str = Field(description='When her request is about the photos, what she wants, in a few words '
                                    '(e.g. "the pool photo as the background"); otherwise ""')
    reply: str = Field(description="One or two plain sentences to her: what you changed, or why you changed nothing")


RULES = """Now a marketer at the auction house asks for a change to this property's set in her own words. You get \
the set's current details by field (each field's key, then what it is called on her screen), and her request. \
Make exactly the change she asks for and nothing else:
- Return only the fields her request changes, each with its whole new value in the shape its current value has. \
In a list, keep the items she didn't mention as they are, in their order.
- The house style and the privacy rules above still apply. If she asks for something they forbid (a person's \
name, an ID number), don't do it, and say why in the reply.
- Feature lists stay even so the ads stay symmetrical: 4 or 6 for the main dwelling (features), 2 or 4 per flat \
or cottage (extra_units), 1 or 2 for the whole property (shared_features). When her change would leave an odd \
number, add the next best real feature the details support, or say in the reply what she should add.
- Write only what the details or her request support; never invent a fact. When the same thing is said in \
several places (the ad's features and the pack's improvements, the caption and the listing), change only what \
she asked about unless she says "everywhere".
- A feature's small second line rides in its text after " | " (e.g. "3 BEDROOMS | MAIN EN-SUITE").
- Photos are chosen separately: when her request is about photos, say what she wants in "photos" and change no \
words for it.
- For sale or auction is switched with the buttons at the top of her screen, not here: say so if she asks.
- The reply is to her, short and plain, in the first person ("I took out ..."). Don't use dashes as punctuation."""


def _content(prop: catalog.Property, facts: dict, mode: str, text: str) -> list[dict]:
    guide = "\n".join(f"- {k}: {label}" for k, label in FORM.LABELS.items())
    current = {k: facts.get(k, "" if k in FORM.STRINGS else []) for k in FORM.LABELS}
    sale = "an AUCTION set" if mode == "auction" else "a FOR SALE set"
    return [{"type": "text", "text": f"Property {prop.ref}, {sale}. Internal file name (never print it): {prop.name}.\n\n"
                                     f"The fields:\n{guide}\n\nFeature icons: one of {', '.join(F.ICONS)}.\n\n"
                                     f"The current details:\n{json.dumps(current, ensure_ascii=False, indent=1)}"},
            {"type": "text", "text": f"Her request: {text}"}]


# --- checking what comes back ------------------------------------------------------------

def _feature(x: Any) -> dict:
    if isinstance(x, str):                       # "icon | text", or just the text
        icon, _, text = x.partition("|")
        x = {"icon": icon, "text": text} if icon.strip().lower() in F.ICONS else {"icon": "home", "text": x}
    if not isinstance(x, dict):
        raise ValueError("not a feature")
    icon = str(x.get("icon") or "home").strip().lower()
    text = str(x.get("text") or "").strip()
    if x.get("note"):
        text = f"{text} | {str(x['note']).strip()}"
    return {"icon": icon if icon in F.ICONS else "home", "text": text}


def clean(field: str, raw: str) -> Any:
    """Her field's new value in the shape the studio keeps it, or ValueError."""
    if field in FORM.STRINGS:
        raw = (raw or "").strip()
        if raw.startswith('"') and raw.endswith('"'):
            try:
                raw = str(json.loads(raw))
            except ValueError:
                pass
        return raw.strip()
    value = json.loads(raw or "[]")
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    if field in FORM.LINES or field in FORM.PARAGRAPHS:
        return [str(x).strip() for x in value if str(x).strip()]
    if field in FORM.ROWS:
        return [{"label": str(x.get("label", "")).strip(), "text": str(x.get("text", "")).strip()}
                for x in value if isinstance(x, dict)]
    if field == "portions":
        return [{k: str(x.get(k, "")).strip() for k in FORM.PORTION_KEYS} for x in value if isinstance(x, dict)]
    if field in FORM.FEATURE_SLOTS:
        return [f for f in (_feature(x) for x in value) if f["text"]]
    if field in FORM.GROUPS:
        groups = []
        for g in value:
            if not isinstance(g, dict):
                raise ValueError(f"{field} must be a list of groups")
            items = [f for f in (_feature(x) for x in g.get("items") or []) if f["text"]]
            if items:
                groups.append({"title": str(g.get("title", "")).strip().rstrip(":"), "items": items})
        return groups
    raise ValueError(f"unknown field {field}")


def _short(field: str) -> str:
    return FORM.LABELS[field].split(" (")[0]


# --- doing it ---------------------------------------------------------------------------

def run(db: str, ask_id: int) -> None:
    """Make the change asked for (the screens run this in the background)."""
    a = get(db, ask_id)
    if a is None:
        return
    try:
        _run(db, a)
    except Exception as exc:  # shown to her; the details go to the log
        log.exception("studio request %s for %s failed", ask_id, a["ref"])
        if isinstance(exc, StoreError):
            msg = str(exc)
        elif prepare.out_of_credit(exc):
            msg = prepare.NO_CREDIT
        else:
            msg = "Making the change failed. Try again, or change it in the fields below."
        _finish(db, ask_id, status="failed", reply=msg)


def _run(db: str, a: dict) -> None:
    ref = a["ref"]
    prop = catalog.get(ref)
    if prop is None:
        raise StoreError(f"{ref} isn't an open property any more.")
    items = catalog.items(prop)
    row = state.load(db, ref) or prepare.current(db, prop, items)
    mode = row["mode"] or F.mode_for(prop)
    answer = F.ask(F.SYSTEM + lessons.block("words", db) + "\n\n" + RULES, _content(prop, row["facts"], mode, a["text"]), Answer,
                   max_tokens=12000, refusal="Claude declined that request; change it in the fields below.")

    changes: dict[str, Any] = {}
    notes: list[str] = []
    for c in answer.changes:
        if c.field not in FORM.LABELS:
            continue
        try:
            changes[c.field] = clean(c.field, c.value)
        except ValueError:
            log.warning("request %s: unusable value for %s: %r", a["id"], c.field, c.value[:200])
            notes.append(f"I couldn't change the {_short(c.field).lower()}; change it below.")

    moved: dict[str, Any] = {}
    if answer.photos.strip():
        try:
            moved = photopick.rechoose(prop, catalog.photos(items), auction=(mode == "auction"),
                                       wish=answer.photos, current=prepare.picks(row, items))
        except Exception:
            log.exception("request %s: changing the photos failed", a["id"])
            notes.append("Changing the photos failed; place them by hand under Photos.")
        else:
            if not moved:
                notes.append("No photo in the folder matched, so the photos stay as they were.")

    fresh = state.load(db, ref)                     # she may have typed meanwhile
    facts = dict(fresh["facts"])
    touched = set(changes)
    if "extra_units" in touched:
        touched |= {"main_title", "shared_features"}
    balanced = F.balance(F.tidy({**facts, **changes}))
    for key in ("features", "shared_features"):
        if key in changes and len(balanced.get(key) or []) < len(changes[key]):
            left = [x["text"] for x in changes[key][len(balanced.get(key) or []):]]
            notes.append(f"The ad takes an even number of features, so I left out {', '.join(left)}.")
    final = {**facts, **{k: balanced.get(k) for k in touched if k in balanced}}
    changed = [k for k in FORM.LABELS if k in touched and final.get(k) != facts.get(k)]   # in screen order

    photos = dict(fresh["photos"] or {})
    before_photos = {slot: photos.get(slot) for slot in moved}
    if "gallery" in moved and "gallery" not in photos:
        before_photos["gallery"] = None
    before = {"facts": {k: facts.get(k) for k in changed}, "edited": sorted(fresh["edited"] & set(changed)),
              "photos": before_photos}
    if changed or moved:
        state.save(db, ref, facts=final if changed else None, edited=(fresh["edited"] | set(changed)) if changed else None,
                   photos={**photos, **moved} if moved else None,
                   warnings=F.privacy_warnings(prop, final) if changed else None, by=a["asked_by"])
    what = [_short(k) for k in changed] + [PHOTO_LABELS[s] for s in moved]
    reply = " ".join(x for x in [answer.reply.strip()] + notes if x)
    _finish(db, a["id"], status="done", reply=reply or "Nothing needed changing.", changed=what, before=before)


def undo(db: str, ask_id: int, by: str) -> bool:
    """Put back what the latest request changed. False when it isn't the latest, or
    changed nothing."""
    a = get(db, ask_id)
    if a is None or a["status"] != "done":
        return False
    last = latest(db, a["ref"])
    if not last or last["id"] != a["id"] or not a["changed"]:
        return False
    before = a["before"]
    row = state.load(db, a["ref"])
    facts, edited, photos = dict(row["facts"]), set(row["edited"]), dict(row["photos"] or {})
    for k, v in (before.get("facts") or {}).items():
        if v is None:
            facts.pop(k, None)
        else:
            facts[k] = v
        if k in (before.get("edited") or []):
            edited.add(k)
        else:
            edited.discard(k)
    for slot, v in (before.get("photos") or {}).items():
        if v is None:
            photos.pop(slot, None)
        else:
            photos[slot] = v
    prop = catalog.get(a["ref"])
    state.save(db, a["ref"], facts=facts, edited=edited, photos=photos if before.get("photos") else None,
               warnings=F.privacy_warnings(prop, facts) if prop and before.get("facts") else None, by=by)
    _finish(db, ask_id, status="undone")
    return True
