"""Job board, login and root routing (M8, Phase 4, screen 1).

The board is the platform's home: a ruled ledger of every property record with
its lifecycle state, days in that state, the last human to act, and the next
action. The ledger body refreshes itself over HTMX so a job finishing in the
background (extraction, render, a gate transition) shows up without a reload.

This module also owns the small auth surface (``/login``, ``/logout``) and the
root redirect, because those sit outside any single screen's prefix.

POPIA: the board reads only non-PII columns (dp, state, suburb, title type,
price display) from the store's indexed columns. No owner or occupant detail is
loaded here, so the ledger cannot leak PII.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from webapp import auth, models
from webapp.ratelimit import account_key, client_ip, safe_log, throttle

router = APIRouter()

logger = logging.getLogger("webapp.auth")


# --- shared render helper -------------------------------------------------

def _view(request: Request, name: str, ctx: Optional[dict] = None, status_code: int = 200):
    templates = request.app.state.templates
    data: Dict[str, Any] = {"user": auth.current_user(request)}
    if ctx:
        data.update(ctx)
    return templates.TemplateResponse(request, name, data, status_code=status_code)


# --- next-action mapping (state -> what a human does next) ----------------
# href targets follow the platform's screen prefixes; a screen still being
# built simply 404s the link without affecting the board.

def _next_action(dp: str, state: str) -> Dict[str, Any]:
    table = {
        "intake":          ("Awaiting extraction", None, "muted"),
        "extracted":       ("Verify", f"/gates/{dp}/verify", "primary"),
        "flags_raised":    ("Review flags", f"/gates/{dp}/verify", "primary"),
        "verified":        ("Draft ad", f"/gates/{dp}/ads", "primary"),
        "drafted":         ("Review ad", f"/gates/{dp}/ads", "gold"),
        "approved":        ("Client approval", f"/gates/{dp}/client", "gold"),
        "client_approved": ("Build assets", f"/artifacts/{dp}", "primary"),
        "assets_built":    ("Post", f"/post/{dp}", "gold"),
        "live":            ("Edit & repost", f"/gates/{dp}/ads", "gold"),
        "updated":         ("Re-approve", f"/gates/{dp}/ads", "gold"),
        "sold":            ("Archive", None, "muted"),
        "withdrawn":       ("Archive", None, "muted"),
        "archived":        ("Complete", None, "muted"),
    }
    label, href, variant = table.get(state, ("Open", f"/artifacts/{dp}", "ghost"))
    return {"label": label, "href": href, "variant": variant}


# --- pipeline stage (state -> position on the 5-step track) ---------------
# The lifecycle has 13 states; the board shows them condensed onto five
# milestones so a row reads as a progress track (Intake -> Verify -> Draft ->
# Approve -> Live) rather than an opaque status word.

STAGES = ("Intake", "Verify", "Draft", "Approve", "Live")
_STAGE_INDEX = {
    "intake": 0, "extracted": 1, "flags_raised": 1, "verified": 2,
    "drafted": 2, "approved": 3, "client_approved": 3, "assets_built": 3,
    "live": 4, "updated": 4, "sold": 4, "withdrawn": 4, "archived": 4,
}


def _pipeline_stage(state: str) -> int:
    return _STAGE_INDEX.get(state, 0)


def _deck_stats(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """The command-deck KPIs: live listings, the action queue, open flags, total."""
    live = sum(1 for r in rows if r["state"] in ("live", "updated"))
    flags = sum(1 for r in rows if r["state"] == "flags_raised")
    queue = sum(
        1 for r in rows
        if r["next"]["href"] and r["state"] not in ("live", "updated")
    )
    return {"live": live, "queue": queue, "flags": flags, "total": len(rows)}


def _days_in_state(entered_at: Optional[str]) -> Dict[str, Any]:
    if not entered_at:
        return {"n": None, "label": "-"}
    try:
        entered = datetime.fromisoformat(entered_at)
    except ValueError:
        return {"n": None, "label": "-"}
    if entered.tzinfo is None:
        entered = entered.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - entered
    days = max(delta.days, 0)
    return {"n": days, "label": "today" if days == 0 else f"{days}d"}


def _owner_from_note(note: Optional[str]) -> Optional[str]:
    """Pull the actor out of a state-event note like 'signoff gate=1 user=x'."""
    if not note or "user=" not in note:
        return None
    tail = note.split("user=", 1)[1].strip()
    actor = tail.split()[0] if tail else ""
    actor = actor.rstrip(":,")
    return actor or None


def _load_rows(db_path: str) -> List[Dict[str, Any]]:
    """Return the ledger rows (one per record), newest activity first.

    Reads only the store's non-PII indexed columns plus the state-event trail
    for days-in-state and the last actor.
    """
    conn = sqlite3.connect(models.resolve_db_path(db_path))
    conn.row_factory = sqlite3.Row
    try:
        records = conn.execute(
            """
            SELECT dp, state, suburb, title_type, price_display, updated_at
              FROM records
             ORDER BY updated_at DESC, dp
            """
        ).fetchall()
        rows: List[Dict[str, Any]] = []
        for rec in records:
            dp = rec["dp"]
            state = rec["state"]
            entered = conn.execute(
                "SELECT MAX(at) AS at FROM state_events "
                "WHERE dp = ? AND to_state = ?",
                (dp, state),
            ).fetchone()
            last_actor = conn.execute(
                "SELECT note FROM state_events "
                "WHERE dp = ? AND note LIKE '%user=%' ORDER BY id DESC LIMIT 1",
                (dp,),
            ).fetchone()
            title_type = (rec["title_type"] or "").title() or None
            price = rec["price_display"]
            sub_bits = [b for b in (title_type, price) if b]
            rows.append(
                {
                    "dp": dp,
                    "state": state,
                    "property": rec["suburb"] or f"DP {dp}",
                    "sub": "  ".join(sub_bits),
                    "days": _days_in_state(entered["at"] if entered else None),
                    "owner": _owner_from_note(last_actor["note"] if last_actor else None)
                    or "Unassigned",
                    "next": _next_action(dp, state),
                    "stage": _pipeline_stage(state),
                }
            )
        return rows
    finally:
        conn.close()


# --- root + auth ----------------------------------------------------------

@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/board", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    # Already signed in: send them to the board.
    if auth.current_user(request) is not None:
        return RedirectResponse("/board", status_code=303)
    return _view(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    key = account_key(email)
    # Count the attempt and get the allow/deny decision atomically, BEFORE the
    # bcrypt check, so a concurrent burst cannot overrun the cap.
    wait = throttle.hit(key)
    if wait:
        minutes = max(1, (wait + 59) // 60)
        logger.warning("login locked for %s from %s", safe_log(key), client_ip(request))
        return _view(
            request,
            "login.html",
            {
                "error": (
                    f"Too many failed attempts. Try again in about {minutes} "
                    "minute" + ("s" if minutes != 1 else "") + "."
                ),
                "email": email,
            },
            status_code=429,
        )

    if auth.login_user(request, email, password):
        throttle.record_success(key)
        return RedirectResponse("/board", status_code=303)

    # The attempt was already counted by hit(). Log it (email + source IP,
    # both CR/LF-sanitised) for intrusion detection; never the password, and the
    # same generic message goes back whether or not the email exists.
    logger.warning("failed login for %s from %s", safe_log(key), client_ip(request))
    return _view(
        request,
        "login.html",
        {"error": "Those credentials were not recognised.", "email": email},
        status_code=401,
    )


@router.get("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    auth.logout(request)
    return RedirectResponse("/login", status_code=303)


# --- board ----------------------------------------------------------------

@router.get("/board", response_class=HTMLResponse)
def board(request: Request):
    user = auth.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    rows = _load_rows(auth.db_path_for(request))
    return _view(request, "board.html", {"rows": rows, "animate": True, "stats": _deck_stats(rows)})


@router.get("/board/rows", response_class=HTMLResponse)
def board_rows(request: Request):
    """The ledger body only: HTMX polls this to keep the board live."""
    if auth.current_user(request) is None:
        return RedirectResponse("/login", status_code=303)
    rows = _load_rows(auth.db_path_for(request))
    return _view(request, "_board_rows.html", {"rows": rows, "animate": False})


@router.post("/board/{dp}/delete", response_class=HTMLResponse)
def board_delete(
    dp: str,
    request: Request,
    user: dict = Depends(auth.require_role("approver", "marketing")),
):
    """Remove a property from the board (e.g. intaken in error). Irreversible.

    Deletes the record and its state history and any queued/finished jobs for the
    DP, then returns the refreshed ledger body for the HTMX swap. Uploaded source
    files on disk are left in place. Gated to the operational roles.
    """
    from engine.store import RecordStore

    db_path = auth.db_path_for(request)
    store = RecordStore(models.resolve_db_path(db_path))
    try:
        store.delete(dp)
    finally:
        store.close()
    models.delete_jobs_for_dp(db_path, dp)
    rows = _load_rows(db_path)
    return _view(request, "_board_rows.html", {"rows": rows, "animate": False})
