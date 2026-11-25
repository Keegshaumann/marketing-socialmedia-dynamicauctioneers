"""Claude looks at a property's photos and chooses the ones its set opens with.

The studio's own rules (an aerial by name, the team's TO BE USED folder, then folder
order) only work where the folders say what a photo is. Most inspection photos are
named by the camera or the inspection app (IMG_7167.jpeg, a UUID), and only about one
open property in eight has an aerial, so on the rest the auction ad's background was
simply the first photo by name: on DP2989, a close-up of a cracked wall.

So "Read the documents" also sends Claude small copies of the photos (at most
``MAX_PHOTOS``, the team's picks first) and asks for the five ad slots and the info
pack's photo pages. Its choice is stored beside hers (``photos["claude"]``) and never
replaces a photo she placed herself (``render.photo_picks``).

``rechoose`` is for when she asks for a change in her own words ("use the pool as the
background", ``studio.ask``): Claude sees the same photos and the current choice, and
changes only the slots her request is about.
"""

from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pydantic import BaseModel, Field

from studio import catalog
from studio.catalog import Item, Property
from studio.sharepoint import store

log = logging.getLogger("studio.photopick")

MAX_PHOTOS = 60
GALLERY = 18


class Picks(BaseModel):
    aerial: str = Field(description="Label of the auction advert's full-bleed background")
    hero: str = Field(description="Label of the for-sale advert's full-bleed background")
    photo1: str = Field(description="Label of the first supporting photo")
    photo2: str = Field(description="Label of the second supporting photo")
    photo3: str = Field(description="Label of the third supporting photo")
    gallery: list[str] = Field(description=f"Up to {GALLERY} labels for the information pack, in walk-through order")


SYSTEM = f"""You choose the photos for a South African auction house's property marketing: a 1080x1350 \
social media advert, a SAIA banner and an A4 information pack. The photos come from the property's \
folder: inspection photos, valuer photos, and sometimes Google Earth captures with the boundary drawn \
in red. Each photo is labelled P1, P2, ... and shown after its folder path.

Choose, by label:
- aerial: the AUCTION advert's full-bleed background. A true aerial (drone shot, or a Google Earth \
capture with the boundary) if there is one; otherwise the best wide exterior showing the whole property.
- hero: the FOR SALE advert's full-bleed background. The best exterior: the front of the house or \
building, the street view, or the farm's landscape. An aerial only when there is no good exterior.
- photo1, photo2 and photo3: the three supporting photos shown in a block beside the features on both \
adverts, all different from hero and aerial and from each other, and varied: what a buyer cares about \
most (the front or street view, the main living area, the kitchen, a pool, garden or view, a second \
dwelling, sheds, orchards). Pick the most attractive, bright and well-composed ones.
- gallery: up to {GALLERY} photos for the information pack's photo pages, in walk-through order: \
outside first, then living areas, kitchen, bedrooms, bathrooms, outbuildings, garden and extras. \
No near-duplicates.

Only satellite or drone imagery counts as an aerial: a street map or a screenshot of a map app (street \
names on grey or white roads) is never the aerial, the hero or a supporting photo.

Never choose for aerial, hero, photo1, photo2 or photo3: QR codes, logos, adverts or other artwork, \
close-ups of damage or defects (cracks, damp, broken \
fittings), clutter or rubbish, dark, blurred or badly tilted shots, people, vehicles with readable \
number plates, documents, screenshots, meters, or bare walls, floors and ceilings. Leave damage out of \
the gallery too unless the folder has nothing else. A folder named TO BE USED holds the team's own \
choices: prefer those when they are good photos.

Answer with labels only, exactly as given (e.g. "P7")."""


def candidates(photos: list[Item]) -> list[Item]:
    """At most MAX_PHOTOS: aerials and the team's picks first, then an even spread
    of everything else (a farm can have 500 inspection photos)."""
    usable = [p for p in photos if not catalog.is_asset_photo(p)] or list(photos)
    if len(usable) <= MAX_PHOTOS:
        return usable
    first = [p for p in usable if catalog.photo_rank(p)[0] <= 2]
    ids = {p.item_id for p in first}
    rest = [p for p in usable if p.item_id not in ids]
    keep = min(len(first), max(MAX_PHOTOS // 2, MAX_PHOTOS - len(rest)))
    first = first[:keep]
    room = MAX_PHOTOS - keep
    step = len(rest) / room if room else 0
    return first + ([rest[int(i * step)] for i in range(room)] if len(rest) > room else rest)


def _tile(item: Item) -> Optional[bytes]:
    try:
        return store().image(item, "tile")
    except Exception:   # one unreadable photo just stays out of the choice
        log.warning("photo %s could not be fetched for the choice", item.rel)
        return None


def _shown(prop: Property, pool: list[Item], intro: str) -> tuple[list[dict], dict[str, str]]:
    """The photos as Claude sees them, each after its label and folder path; and the
    labels as item ids."""
    with ThreadPoolExecutor(8) as ex:
        tiles = list(ex.map(_tile, pool))
    content: list[dict] = [{"type": "text", "text": intro}]
    labels: dict[str, str] = {}
    for n, (item, data) in enumerate(zip(pool, tiles), 1):
        if not data:
            continue
        label = f"P{n}"
        labels[label] = item.item_id
        content.append({"type": "text", "text": f"{label}: {item.rel}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(data).decode("ascii")}})
    return content, labels


def choose(prop: Property, photos: list[Item], *, auction: bool, about: str = "") -> dict:
    """Claude's picks as item ids: {"aerial", "hero", "photo1", "photo2", "photo3", "gallery"}.
    Slots it left empty or answered with an unknown label are left out."""
    from studio import facts as F

    pool = candidates(photos)
    if not pool:
        return {}
    sale = "It is going to AUCTION." if auction else "It is FOR SALE."
    content, labels = _shown(prop, pool, f"Property {prop.ref}. {sale}" + (f" What it is: {about}." if about else ""))
    if not labels:
        return {}
    content.append({"type": "text", "text": "Choose the photos."})

    answer = F.ask(SYSTEM, content, Picks, max_tokens=4000,
                   refusal="Claude declined to choose the photos; pick them by hand.")

    def item_id(label: str) -> Optional[str]:
        return labels.get((label or "").strip().upper())

    out: dict = {}
    for slot in ("aerial", "hero", "photo1", "photo2", "photo3"):
        got = item_id(getattr(answer, slot))
        if got:
            out[slot] = got
    gallery: list[str] = []
    for label in answer.gallery:
        got = item_id(label)
        if got and got not in gallery:
            gallery.append(got)
    if gallery:
        out["gallery"] = gallery[:GALLERY]
    return out


# --- her own request ------------------------------------------------------------------

SLOTS = ("aerial", "hero", "photo1", "photo2", "photo3")


class Rechosen(Picks):
    changed: list[str] = Field(description='The slots her request changes, any of "aerial", "hero", "photo1", '
                                           '"photo2", "photo3", "gallery"; empty when none of the photos fit it')


WISH = """This time the marketer has asked, in her own words, for a change to the photos. You are given \
the photos and the current choice for each slot. Change only what her request asks for, keep every other slot \
exactly as it is (same label), and list the slots you changed in "changed". On an AUCTION set the advert's \
background is the aerial slot; on a FOR SALE set it is the hero slot. "The small photos" or "the photos beside \
the features" are photo1, photo2 and photo3. "The pack" or "the info pack" is the gallery. Her request wins over \
the rules above (if she wants a photo you would not choose, use it), but never invent a label. When no photo \
fits what she asked for, change nothing."""


def rechoose(prop: Property, photos: list[Item], *, auction: bool, wish: str, current: dict) -> dict:
    """The slots her request changes, as item ids (only those, never the rest).
    ``current`` is the choice now (render.photo_picks)."""
    from studio import facts as F

    by_id = {p.item_id: p for p in photos}
    pool = candidates(photos)
    shown = {p.item_id for p in pool}
    for slot in SLOTS:                     # the current picks are always among what Claude sees
        cur = by_id.get(current.get(slot) or "")
        if cur and cur.item_id not in shown:
            pool.append(cur)
            shown.add(cur.item_id)
    if not pool:
        return {}
    sale = "It is an AUCTION set." if auction else "It is a FOR SALE set."
    content, labels = _shown(prop, pool, f"Property {prop.ref}. {sale}")
    if not labels:
        return {}
    label_of = {v: k for k, v in labels.items()}
    now = [f"{slot}: {label_of.get(current.get(slot) or '', 'none')}" for slot in SLOTS]
    now.append("gallery: " + (", ".join(label_of[g] for g in current.get("gallery") or [] if g in label_of) or "none"))
    content.append({"type": "text", "text": "The current choice:\n" + "\n".join(now)})
    content.append({"type": "text", "text": f"Her request: {wish}"})

    answer = F.ask(SYSTEM + "\n\n" + WISH, content, Rechosen, max_tokens=4000,
                   refusal="Claude declined to change the photos; place them by hand.")
    out: dict = {}
    for slot in {c.strip().lower() for c in answer.changed}:
        if slot == "gallery":
            gallery = []
            for label in answer.gallery:
                got = labels.get((label or "").strip().upper())
                if got and got not in gallery:
                    gallery.append(got)
            if gallery and gallery != list(current.get("gallery") or []):
                out["gallery"] = gallery[:GALLERY]
        elif slot in SLOTS:
            got = labels.get((getattr(answer, slot) or "").strip().upper())
            if got and got != current.get(slot):
                out[slot] = got
    return out
