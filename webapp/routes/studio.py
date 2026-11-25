"""The Ad Studio screens (D127): pick a property, check the words and photos,
download the ads and the info pack.

- ``GET  /studio``                       the properties that need marketing work
- ``GET  /studio/{ref}``                 one property: facts, photos, live previews
- ``POST /studio/{ref}/save``            autosave of the form
- ``POST /studio/{ref}/mode``            for sale <-> auction
- ``POST /studio/{ref}/read``            Claude reads the folder's documents (background)
- ``GET  /studio/{ref}/read-status``     polled while that runs
- ``POST /studio/{ref}/pick``            put a photo in a slot / the pack's gallery
- ``GET  /studio/{ref}/photo/{id}/{size}``  a photo from the folder
- ``GET  /studio/{ref}/preview/{kind}``  the live HTML of one output
- ``GET  /studio/{ref}/file/{kind}``     the finished PNG / PDF
- ``GET  /studio/{ref}/download``        every file for the property in one zip
- ``POST /studio/{ref}/publish``         save the set into SharePoint now (D129)
- ``GET  /studio/{ref}/publish-status``  polled while that runs

The studio also works by itself (``studio.auto``, every 15 minutes): it reads the
properties waiting on an ad and files each set in SharePoint, and files it again
when the price, the auction date or someone's words change.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from pathlib import Path

from studio import auto, catalog, facts as F, form as FORM, prepare, publish, render, state
from studio.sharepoint import store
from studio.writer import writer
from webapp import auth, models

log = logging.getLogger("webapp.studio")
router = APIRouter()
_can = auth.require_role(*auth.OPERATIONS)


def _db(request: Request) -> str:
    return auth.db_path_for(request)


def _tpl(request: Request):
    return request.app.state.templates


def _prop(ref: str) -> catalog.Property:
    if not catalog.available():
        raise HTTPException(503, "The property list (director mirror) can't be read right now.")
    prop = catalog.get(ref)
    if prop is None:
        raise HTTPException(404, f"No open property {ref}")
    return prop


def _load(request: Request, prop: catalog.Property, items: list[catalog.Item]) -> dict:
    return prepare.current(_db(request), prop, items)


_stale = prepare.stale

SLOT_LABELS = {"hero": "Background", "aerial": "Auction background", "photo1": "Photo 1", "photo2": "Photo 2",
               "photo3": "Photo 3"}


# --- the list -----------------------------------------------------------------

@router.get("/studio", response_class=HTMLResponse)
def studio_home(request: Request, user=Depends(_can)):
    ok = catalog.available()
    props = catalog.open_properties() if ok else []
    kits = catalog.kits_for(props) if props else {}
    worked = state.all_rows(_db(request))
    today = date.today()

    sets = publish.all_sets(_db(request))

    def row(p):
        return {"p": p, "kit": kits.get(p.folder, {}), "worked": worked.get(p.ref), "set": sets.get(p.ref)}

    upcoming = sorted((p for p in props if p.auction_date and p.auction_date >= today), key=lambda p: p.auction_date)
    need = {p.ref: auto.needs_ad(p, kits.get(p.folder, {}), today) for p in props}
    waiting = [p for p in props if need[p.ref] == "waiting"]
    upcoming_rest = [p for p in upcoming if p not in waiting]
    no_ad = [p for p in props if need[p.ref] == "no_ad"]
    # "You worked on" is what people opened or changed, not what the studio did by itself.
    people = {ref: r for ref, r in worked.items() if r["updated_by"]}
    recent = sorted((p for p in props if p.ref in people), key=lambda p: people[p.ref]["updated_at"], reverse=True)[:8]
    return _tpl(request).TemplateResponse(request, "studio_home.html", {
        "user": user, "available": ok,
        "waiting": [row(p) for p in sorted(waiting, key=lambda p: p.auction_date or date.max)],
        "upcoming": [row(p) for p in upcoming_rest],
        "no_ad": [row(p) for p in no_ad],
        "recent": [row(p) for p in recent],
        "everyone": [row(p) for p in props],
        "kit_labels": [(k, label) for k, label, _ in catalog.KIT] + [("infopack", "Info pack")],
    })


# --- one property ---------------------------------------------------------------

def _photo_src(ref: str):
    # The pack's photo pages show many photos small: tiles are plenty for a preview.
    return lambda item_id, size: f"/studio/{quote(ref)}/photo/{item_id}/{'tile' if size == 'gallery' else size}"


@router.get("/studio/{ref}", response_class=HTMLResponse)
def studio_edit(ref: str, request: Request, user=Depends(_can)):
    prop = _prop(ref)
    items = catalog.items(prop)
    row = _load(request, prop, items)
    photos = catalog.photos(items)
    picks = render.photo_picks(row["photos"], photos)
    docs = catalog.documents(items)
    return _tpl(request).TemplateResponse(request, "studio_edit.html", {
        "user": user, "p": prop, "row": row, "v": FORM.to_form(row["facts"]), "icons": F.ICONS,
        "photos": photos, "picks": picks, "kit": catalog.kit(items),
        "formats": render.formats_for(row["mode"]),
        "docs": [(k, i) for k in ("report", "lightstone", "valuation", "otp", "infopack", "proposal") for i in docs[k][:2]],
        "names": {f.kind: F.name_for(prop, f.kind, row["mode"]) for f in render.formats_for(row["mode"])},
        "slot_labels": SLOT_LABELS,
        **_sync_ctx(request, prop),
    })


def _sync_ctx(request: Request, prop: catalog.Property) -> dict:
    db = _db(request)
    return {"set": publish.get_set(db, prop.ref), "set_files": publish.files(db, prop.ref),
            "can_save": writer() is not None}


@router.post("/studio/{ref}/save", response_class=HTMLResponse)
async def studio_save(ref: str, request: Request, user=Depends(_can)):
    prop = _prop(ref)
    db = _db(request)
    row = state.load(db, prop.ref)
    if row is None:
        raise HTTPException(409, "Open the property first.")
    submitted = FORM.from_form(dict(await request.form()))
    changed = FORM.changed_keys(row["facts"], submitted)
    if changed:
        facts = {**row["facts"], **{k: submitted[k] for k in changed}}
        state.save(db, prop.ref, facts=facts, edited=row["edited"] | changed, by=user["email"])
    stamp = datetime.now().strftime("%H:%M")
    return HTMLResponse(f'<span class="muted">Saved {stamp}</span>',
                        headers={"HX-Trigger": "studio-refresh"} if changed else {})


@router.post("/studio/{ref}/mode")
async def studio_mode(ref: str, request: Request, user=Depends(_can)):
    prop = _prop(ref)
    mode = (await request.form()).get("mode")
    if mode in ("forsale", "auction"):
        db = _db(request)
        row = state.load(db, prop.ref)
        facts = dict(row["facts"]) if row else {}
        # The badge follows the mode unless she typed her own.
        if row and "badge" not in row["edited"] and facts.get("sale_kind"):
            facts["badge"] = f"{facts['sale_kind']} {'AUCTION' if mode == 'auction' else 'SALE'}!"
        # Her choice sticks: the studio's own job no longer switches it with the sheets.
        edited = (row["edited"] if row else set()) | {prepare.MODE_CHOSEN}
        state.save(db, prop.ref, mode=mode, facts=facts, edited=edited, by=user["email"])
    return RedirectResponse(f"/studio/{quote(prop.ref)}", status_code=303)


# --- reading the documents ------------------------------------------------------

def _in_background(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


_read_job = prepare.read


@router.post("/studio/{ref}/read", response_class=HTMLResponse)
def studio_read(ref: str, request: Request, user=Depends(_can)):
    prop = _prop(ref)
    db = _db(request)
    row = state.load(db, prop.ref)
    if row and row["reading"] == "running" and not _stale(row["updated_at"]):
        return studio_read_status(ref, request, user)
    state.save(db, prop.ref, reading="running", message="")
    _in_background(_read_job, db, prop.ref, user["email"])
    return studio_read_status(ref, request, user)


@router.get("/studio/{ref}/read-status", response_class=HTMLResponse)
def studio_read_status(ref: str, request: Request, user=Depends(_can)):
    prop = _prop(ref)
    row = state.load(_db(request), prop.ref) or {}
    headers = {}
    if row.get("reading") == "done" and request.headers.get("HX-Request") and request.query_params.get("poll"):
        headers["HX-Refresh"] = "true"   # the form needs the new words
    return _tpl(request).TemplateResponse(request, "partials/_studio_read.html",
                                          {"p": prop, "row": row, "user": user}, headers=headers)


# --- photos -------------------------------------------------------------------

@router.post("/studio/{ref}/pick", response_class=HTMLResponse)
async def studio_pick(ref: str, request: Request, user=Depends(_can)):
    prop = _prop(ref)
    form = await request.form()
    slot, item_id = form.get("slot", ""), form.get("item", "")
    items = catalog.items(prop)
    photos = catalog.photos(items)
    if item_id not in {p.item_id for p in photos}:
        raise HTTPException(400, "Not a photo in this property's folder")
    db = _db(request)
    row = state.load(db, prop.ref)
    # Only what she places herself is stored as hers; the other slots keep
    # following Claude's choice (photos["claude"]) and the studio's rules.
    saved = dict(row["photos"]) if row and row["photos"] else {}
    if slot in state.SLOTS:
        saved[slot] = item_id
    elif slot == "gallery":
        g = render.photo_picks(saved, photos).get("gallery", [])
        saved["gallery"] = [x for x in g if x != item_id] if item_id in g else g + [item_id]
    else:
        raise HTTPException(400, "Unknown slot")
    state.save(db, prop.ref, photos=saved, by=user["email"])
    picks = render.photo_picks(saved, photos)
    return _tpl(request).TemplateResponse(request, "partials/_studio_photos.html", {
        "p": prop, "photos": photos, "picks": picks, "keep_open": True,
        "slot_labels": SLOT_LABELS,
    }, headers={"HX-Trigger": "studio-refresh"})


@router.get("/studio/{ref}/photo/{item_id}/{size}")
def studio_photo(ref: str, item_id: str, size: str, request: Request, user=Depends(_can)):
    if size not in ("tile", "view", "print"):
        raise HTTPException(404)
    prop = _prop(ref)
    item = catalog.find_item(prop, item_id)
    if item is None or not item.is_image:
        raise HTTPException(404)
    try:
        data = store().image(item, size)
    except Exception:
        log.exception("photo %s", item.path)
        raise HTTPException(502, "Couldn't fetch that photo from SharePoint")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# --- previews and files ---------------------------------------------------------

def _ctx(request: Request, prop, items, photo_src):
    row = _load(request, prop, items)
    picks = render.photo_picks(row["photos"], catalog.photos(items))
    return row, picks, render.context(prop, row["facts"], picks, row["mode"], photo_src)


@router.get("/studio/{ref}/preview/{kind}", response_class=HTMLResponse)
def studio_preview(ref: str, kind: str, request: Request, user=Depends(_can)):
    if kind not in render.FORMATS:
        raise HTTPException(404)
    prop = _prop(ref)
    items = catalog.items(prop)
    _, _, ctx = _ctx(request, prop, items, _photo_src(prop.ref))
    return HTMLResponse(render.html(kind, ctx))


def _hand_to_proposals(db: str, prop: catalog.Property, files: dict[str, bytes]) -> None:
    """Leave the latest ad where the proposal screen looks for "the platform's
    advert" (DP<n>/artifacts/demo_ad.png), so the properties team can drop it into
    the auction proposal's "Draft Proposed Advert" page with one click."""
    ad = next((data for name, data in files.items() if name.endswith(" AD.png")), None)
    if ad is None:
        return
    try:
        folder = Path(models.get_setting(db, "output_root") or ".") / f"DP{prop.number}" / "artifacts"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "demo_ad.png").write_bytes(ad)
    except OSError:
        log.exception("could not leave the ad for the proposal screen (%s)", prop.ref)


def _safe_name(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9 ._()&-]', "_", name)


@router.get("/studio/{ref}/file/{kind}")
def studio_file(ref: str, kind: str, request: Request, user=Depends(_can)):
    if kind not in render.FORMATS:
        raise HTTPException(404)
    prop = _prop(ref)
    items = catalog.items(prop)
    row = _load(request, prop, items)
    picks = render.photo_picks(row["photos"], catalog.photos(items))
    try:
        files = render.build(prop, row["facts"], picks, row["mode"], {i.item_id: i for i in items}, kinds=[kind])
    except Exception:
        log.exception("render %s %s", prop.ref, kind)
        raise HTTPException(500, "Making that file failed")
    _hand_to_proposals(_db(request), prop, files)
    name, data = next(iter(files.items()))
    media = "application/pdf" if name.endswith(".pdf") else "image/png"
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{_safe_name(name)}"'})


@router.get("/studio/{ref}/download")
def studio_download(ref: str, request: Request, user=Depends(_can)):
    prop = _prop(ref)
    items = catalog.items(prop)
    row = _load(request, prop, items)
    try:
        files = publish.build(prop, row, items)
    except Exception:
        log.exception("render all %s", prop.ref)
        raise HTTPException(500, "Making the files failed")
    _hand_to_proposals(_db(request), prop, files)
    word = "AUCTION" if row["mode"] == "auction" else "FOR SALE"
    return Response(render.zip_files(files), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="DP{prop.number} {word} SET.zip"'})


# --- saving into SharePoint (D129) ------------------------------------------------

def _publish_job(db: str, ref: str) -> None:
    try:
        publish.publish(db, catalog.get(ref), writer(), force=True)
    except Exception:
        log.exception("saving %s to SharePoint", ref)
        publish.set_status(db, ref, status="failed", message="Saving to SharePoint failed; try again in a minute.")


@router.post("/studio/{ref}/publish", response_class=HTMLResponse)
def studio_publish(ref: str, request: Request, user=Depends(_can)):
    prop = _prop(ref)
    db = _db(request)
    s = publish.get_set(db, prop.ref)
    if not (s and s["status"] == "saving" and not _stale(s["updated_at"])):
        publish.set_status(db, prop.ref, status="saving", message="")
        _in_background(_publish_job, db, prop.ref)
    return studio_publish_status(ref, request, user)


@router.get("/studio/{ref}/publish-status", response_class=HTMLResponse)
def studio_publish_status(ref: str, request: Request, user=Depends(_can)):
    prop = _prop(ref)
    return _tpl(request).TemplateResponse(request, "partials/_studio_sync.html",
                                          {"p": prop, "user": user, **_sync_ctx(request, prop)})
