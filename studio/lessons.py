"""What the marketing team teaches the studio (25 Nov 2026).

Under each output on a property's page, Nikki can say what is wrong ("you missed the
borehole", "the extent is wrong, it's in the title deed", "never use a photo of a broken
gate as the background"). Claude turns her words into one general rule for every future
property and says what it is about:

- ``words``  what the studio reads and writes: added to the read's instructions, to the
             feature choice and to the changes she asks for;
- ``photos`` which photos it chooses and where: added to the photo choice;
- ``layout`` how the files look: the studio can't change its own design, so these are
             kept on the lessons page for Keegan to build in.

She can turn a lesson off on that page. Asking for it to be fixed on this property too
also sends her words to ``studio.ask``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

log = logging.getLogger("studio.lessons")

SCOPES = {"words": "What it reads and writes", "photos": "Photos", "layout": "Layout and design"}
MOST = 60          # lessons of one kind in the instructions, newest first

SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_lessons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT NOT NULL DEFAULT '',      -- where she noticed it
    kind        TEXT NOT NULL DEFAULT '',      -- which output (render.FORMATS key)
    said        TEXT NOT NULL,                 -- her words
    rule        TEXT NOT NULL DEFAULT '',      -- the general rule the studio keeps
    scope       TEXT NOT NULL DEFAULT 'words', -- words | photos | layout
    reply       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'learning',  -- learning | done
    active      INTEGER NOT NULL DEFAULT 1,
    taught_by   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT ''
);
"""


def _conn(db: Optional[str]):
    from webapp import models

    conn = models._connect(db)
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add(db: str, ref: str, kind: str, said: str, by: str) -> int:
    with _conn(db) as conn:
        cur = conn.execute("INSERT INTO studio_lessons (ref, kind, said, taught_by, created_at) VALUES (?, ?, ?, ?, ?)",
                           (ref, kind, said.strip()[:1500], by, _now()))
        return int(cur.lastrowid)


def get(db: str, lesson_id: int) -> Optional[dict]:
    with _conn(db) as conn:
        r = conn.execute("SELECT * FROM studio_lessons WHERE id = ?", (lesson_id,)).fetchone()
    return dict(r) if r else None


def all_lessons(db: str) -> list[dict]:
    with _conn(db) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM studio_lessons ORDER BY id DESC")]


def set_active(db: str, lesson_id: int, on: bool) -> None:
    with _conn(db) as conn:
        conn.execute("UPDATE studio_lessons SET active = ? WHERE id = ?", (1 if on else 0, lesson_id))


def save_rule(db: str, lesson_id: int, rule: str, scope: str) -> None:
    with _conn(db) as conn:
        conn.execute("UPDATE studio_lessons SET rule = ?, scope = ?, status = 'done' WHERE id = ?",
                     (rule.strip()[:1500], scope if scope in SCOPES else "words", lesson_id))


def rules(db: Optional[str], scope: str) -> list[str]:
    try:
        with _conn(db) as conn:
            rows = conn.execute("SELECT rule, said FROM studio_lessons WHERE active = 1 AND scope = ? "
                                "ORDER BY id DESC LIMIT ?", (scope, MOST)).fetchall()
    except Exception:  # the instructions still work without the lessons
        log.exception("could not read the studio's lessons")
        return []
    return [r["rule"] or r["said"] for r in rows][::-1]


def block(scope: str, db: Optional[str] = None) -> str:
    """The lessons of one kind, as a part of Claude's instructions ("" when there are none)."""
    if db is None:
        from webapp import models

        try:
            db = models.resolve_db_path()
        except Exception:
            return ""
    got = rules(db, scope)
    if not got:
        return ""
    return ("\n\nLESSONS FROM THE MARKETING TEAM. They checked earlier sets and told you what was wrong; "
            "these are their rules and they win over anything above:\n" + "\n".join(f"- {r}" for r in got))


# --- turning her words into a rule ---------------------------------------------------

class Lesson(BaseModel):
    scope: str = Field(description='"words" (what is read from the documents or written on the ads and pack), '
                                   '"photos" (which photos are chosen, and where they go), or "layout" (how the '
                                   'files look: sizes, spacing, cropping, design)')
    rule: str = Field(description="Her point as one general rule for every future property, in the imperative")
    reply: str = Field(description="One short sentence to her saying what you will do from now on")


RULES = """You are the marketing studio of Dynamic Auctioneers, a South African auction house. It makes each \
property's adverts, banners, boards and information pack from the documents and photos in the property's \
folder. A marketer checked one of them and told you what was wrong. Turn her point into ONE general rule that \
makes every future property's files right, not only this one: say what to do (or never do) and when, in the \
imperative, in a sentence or two, the way an experienced marketer would brief a junior. Keep a specific example \
from her words when it helps ("e.g. a borehole and its yield"). Never put a person's name, an ID or account \
number, or this property's internal file name in the rule. Don't use dashes as punctuation."""


def learn(db: str, lesson_id: int) -> None:
    """Claude's rule for a lesson (the screens run this in the background). When Claude
    can't be asked, her own words stand as the rule."""
    from studio import catalog, facts as F, render

    lesson = get(db, lesson_id)
    if lesson is None:
        return
    try:
        about = ""
        prop = catalog.get(lesson["ref"]) if lesson["ref"] else None
        if prop is not None:
            from studio import state

            row = state.load(db, prop.ref) or {}
            facts = {k: v for k, v in (row.get("facts") or {}).items() if v not in ("", [], None)}
            about = (f"The property ({prop.ref}; internal name, never print it: {prop.name}) as the studio has "
                     f"it now:\n{json.dumps(facts, ensure_ascii=False)[:12000]}\n\n")
        fmt = render.FORMATS.get(lesson["kind"])
        what = fmt.label if fmt else "the set"
        got = F.ask(RULES, [{"type": "text", "text": f"{about}She was looking at: {what}.\nWhat she said: {lesson['said']}"}],
                    Lesson, max_tokens=2000, effort="low",
                    refusal="Claude declined; her words stand as the lesson.")
        save_rule(db, lesson_id, got.rule, got.scope.strip().lower())
        with _conn(db) as conn:
            conn.execute("UPDATE studio_lessons SET reply = ? WHERE id = ?", (got.reply.strip(), lesson_id))
    except Exception:
        log.exception("lesson %s: Claude couldn't word it; her words stand", lesson_id)
        save_rule(db, lesson_id, lesson["said"], "words")
        with _conn(db) as conn:
            conn.execute("UPDATE studio_lessons SET reply = ? WHERE id = ?",
                         ("Kept in your own words. The studio follows it from the next property on.", lesson_id))


def as_rows(lessons: list[dict]) -> dict[str, list[dict]]:
    """The lessons page: by kind, newest first."""
    out: dict[str, list[dict]] = {k: [] for k in SCOPES}
    for l in lessons:
        out.setdefault(l["scope"] if l["scope"] in SCOPES else "words", []).append(l)
    return out


def summary(lesson: dict[str, Any]) -> str:
    return lesson["rule"] or lesson["said"]
