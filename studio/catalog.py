"""Every open property, its folder and what is in it, read from the director mirror.

The director platform (``/opt/dynamic-director``) walks the property folders on
SharePoint every hour and keeps the listing in SQLite, along with what the team's
sheets say about each file (auction date, listed price, occupancy...). The studio
reads a copy of its ``files`` and ``items`` tables, refreshed every ten minutes
(``scripts/copy_director_db.py``), opened read-only, so the two apps cannot
disturb each other.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

COMPANY = "Company Share - Documents"
MARKETING = "Marketing - Documents"

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
# Files in the folders that are the team's finished artwork, not photos.
_ARTWORK = re.compile(r"\b(AD|ADS|REEL|BANNER|ICON|QR|BOARD|BORD|BILLBOARD|LOGO|DISCOVERY|RESPONSIVE|"
                      r"SOLD|POSTER|FLYER|MAILER)\b|\bAD\s*\d|\d+\s*X\s*\d+", re.I)


def director_db() -> str:
    # On the server this is the studio's own copy (scripts/copy_director_db.py):
    # the director's live WAL database cannot be read reliably from another account.
    return os.environ.get("DIRECTOR_DB", "/var/lib/da-studio/director.db")


def _connect() -> sqlite3.Connection:
    path = director_db()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def available() -> bool:
    try:
        with _connect() as conn:
            conn.execute("SELECT 1 FROM files LIMIT 1")
        return True
    except sqlite3.Error:
        return False


@dataclass
class Item:
    drive_id: str
    item_id: str
    name: str
    path: str            # library-relative, as SharePoint has it
    rel: str             # relative to the property folder
    is_folder: bool
    size: int
    modified: float

    @property
    def library(self) -> str:
        return COMPANY if self.path.startswith("1. LOOSE ASSETS") else MARKETING

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1].lower()

    @property
    def is_image(self) -> bool:
        return not self.is_folder and self.ext in IMAGE_EXT

    @property
    def top(self) -> str:
        """The first folder under the property folder, upper-cased ('MEDIA')."""
        return self.rel.split("/", 1)[0].upper() if "/" in self.rel else ""


@dataclass
class Property:
    ref: str                       # DP3107, DP2865.5
    name: str                      # the matter name (internal: never printed on an ad)
    folder: str
    status: str
    auction: Optional[str] = None  # ISO date
    auction_when: str = ""         # the team's words: "28 JANUARY - ONLINE"
    proposal_note: str = ""
    listed: Optional[float] = None
    occupancy: str = ""
    client: str = ""
    last: str = ""
    data: dict = field(default_factory=dict)

    @property
    def number(self) -> str:
        return self.ref[2:] if self.ref.upper().startswith("DP") else self.ref

    @property
    def auction_date(self) -> Optional[date]:
        try:
            return date.fromisoformat(self.auction) if self.auction else None
        except ValueError:
            return None

    @property
    def is_auction(self) -> bool:
        d = self.auction_date
        return bool(d and d >= date.today()) or "AUCTION" in self.status.upper()

    @property
    def online(self) -> Optional[bool]:
        w = self.auction_when.upper()
        if "ONLINE" in w:
            return True
        if "SITE" in w or "ON-SITE" in w or "ONSITE" in w:
            return False
        return None

    @property
    def waiting_on_ad(self) -> bool:
        return "AWAIT AD" in self.proposal_note.upper()


def _property(row: sqlite3.Row) -> Optional[Property]:
    d = json.loads(row["data"])
    if not d.get("folder"):
        return None
    prop = d.get("proposal") or {}
    listed = d.get("listed")
    try:
        listed = float(listed) if listed not in (None, "") else None
    except (TypeError, ValueError):
        listed = None
    return Property(
        ref=row["id"], name=d.get("name") or "", folder=d["folder"], status=d.get("status") or "",
        auction=d.get("auction") or prop.get("date"), auction_when=prop.get("when") or "",
        proposal_note=prop.get("comment") or "", listed=listed, occupancy=d.get("occupancy") or "",
        client=d.get("client") or "", last=d.get("last") or "", data=d,
    )


def open_properties() -> list[Property]:
    with _connect() as conn:
        rows = conn.execute("SELECT id, data FROM files WHERE dept = 'property' AND state = 'open'").fetchall()
    out = [p for p in (_property(r) for r in rows) if p]
    return sorted(out, key=_ref_key, reverse=True)


def get(ref: str) -> Optional[Property]:
    ref = normalise_ref(ref)
    with _connect() as conn:
        row = conn.execute("SELECT id, data FROM files WHERE id = ?", (ref,)).fetchone()
    return _property(row) if row else None


def normalise_ref(ref: str) -> str:
    ref = ref.strip().upper().replace(" ", "")
    return ref if ref.startswith(("DP", "L")) else "DP" + ref


def _ref_key(p: Property) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", p.ref)] or [0]
    return tuple(nums)


def items(prop: Property) -> list[Item]:
    """Everything under the property folder, files and folders."""
    lo, hi = prop.folder + "/", prop.folder + "0"
    with _connect() as conn:
        rows = conn.execute(
            "SELECT drive_id, item_id, name, path, is_folder, size, modified FROM items "
            "WHERE path >= ? AND path < ? ORDER BY path", (lo, hi)).fetchall()
    out = []
    for r in rows:
        out.append(Item(r["drive_id"], r["item_id"], r["name"], r["path"], r["path"][len(lo):],
                        bool(r["is_folder"]), r["size"] or 0, r["modified"] or 0.0))
    return out


def find_item(prop: Property, item_id: str) -> Optional[Item]:
    return next((i for i in items(prop) if i.item_id == item_id), None)


# --- what is in the folder ---------------------------------------------------

def photos(all_items: list[Item]) -> list[Item]:
    """The property's photos, the folders the team keeps them in first.

    Media/Photos is where the inspection photos go; Advertisements/IMAGES and
    AV IMAGES hold the aerials with the red boundary drawn on. Finished artwork
    (ads, banners, QR codes, Google display sizes) is left out.
    """
    return sorted(_photo_items(all_items), key=photo_rank)


def photo_rank(i: Item) -> tuple:
    """Aerials, then the team's own picks ("TO BE USED"), then the general
    photos, with valuer and loose-asset photos last."""
    rel = i.rel.upper()
    if re.search(r"AERIA?E?L", rel) or "AV IMAGE" in rel or "/AV " in rel:
        group = 0
    elif "IMAGES" in rel and rel.startswith("ADVERTISEMENT"):
        group = 1
    elif re.search(r"TO BE USED|FOR WEBSITE|FOR MARKETING|USE FOR", rel):
        group = 2
    elif is_asset_photo(i):
        group = 6
    elif re.search(r"VALUER|VALUATOR|VAL PHOTO", rel):
        group = 5
    elif rel.startswith(("MEDIA", "PHOTOS", "5.FOTOS")):
        group = 3
    else:
        group = 4
    return (group, i.rel.lower())


def is_asset_photo(i: Item) -> bool:
    """Photos of the movables kept beside the property's (vehicles, equipment)."""
    return bool(re.search(r"LOOSE ASSET|MOVE?ABLE|VEHICLE|EQUIPMENT|INVENTORY|IMPLEMENT", i.rel.upper()))


def _photo_items(all_items: list[Item]) -> list[Item]:
    out = []
    for i in all_items:
        if not i.is_image or i.size < 25_000:
            continue
        top = i.top
        if top in ("PROOF OF MARKETING", "INSTRUCTION", "CORRESPONDENCE", "INVOICES", "LEADS", "OFFERS",
                   "RATES AND TAXES", "VAT", "QUOTES", "OTP & ROA", "BODY CORPORATE DETAILS"):
            continue
        if top.startswith("ADVERTISEMENT") and _ARTWORK.search(os.path.splitext(i.name)[0]) and "IMAGES" not in i.rel.upper():
            continue
        out.append(i)
    return out


def documents(all_items: list[Item]) -> dict[str, list[Item]]:
    """The documents worth reading for the facts, newest first per kind."""
    kinds: dict[str, list[Item]] = {"lightstone": [], "report": [], "otp": [], "valuation": [],
                                    "infopack": [], "mailer": [], "proposal": []}
    for i in all_items:
        if i.is_folder:
            continue
        top, ext, name = i.top, i.ext, i.name.upper()
        if top.startswith("LIGHTSTONE") and ext == ".pdf":
            kinds["lightstone"].append(i)
        elif top.startswith("PROPERTY REPORT") and ext in (".docx", ".pdf"):
            kinds["report"].append(i)
        elif top.startswith(("OTP", "OFFER TO PURCHASE")) and ext == ".docx" and "RULES" not in name:
            kinds["otp"].append(i)
        elif top.startswith("VALUATION") and ext == ".pdf":
            kinds["valuation"].append(i)
        elif top.startswith("INFO PACK") and ext == ".pdf":
            kinds["infopack"].append(i)
        elif "MAILER" in top.replace(" ", "") and ext == ".msg":
            kinds["mailer"].append(i)
        elif top.startswith("AUCTION PREP") and ext == ".docx" and "PROPOSAL" in name:
            kinds["proposal"].append(i)
    for k in kinds:
        # A Word property report beats its PDF twin (cleaner text); then newest first.
        kinds[k].sort(key=lambda i: (i.ext != ".docx", -i.modified))
    # The property's valuation, not the one for the movables kept beside it.
    kinds["valuation"].sort(key=lambda i: (bool(re.search(r"MOVE?ABLE", i.name.upper())),
                                           "PROPERT" not in i.name.upper(), -i.modified))
    return kinds


KIT = (
    ("forsale_ad", "For sale ad", re.compile(r"FOR\s*SALE\s*AD", re.I)),
    ("auction_ad", "Auction ad", re.compile(r"AUCTION\s*AD", re.I)),
    ("reel", "Reel", re.compile(r"REEL", re.I)),
    ("saia", "SAIA banner", re.compile(r"SAIA", re.I)),
    ("icon", "App icon", re.compile(r"ICON", re.I)),
    ("board", "Board", re.compile(r"BOARD|BORD", re.I)),
)


def kit(all_items: list[Item]) -> dict[str, bool]:
    """Which of the usual outputs are already saved in the folder."""
    done = {k: False for k, _, _ in KIT}
    done["infopack"] = False
    for i in all_items:
        if i.is_folder:
            continue
        if i.top.startswith("ADVERTISEMENT"):
            for key, _, rx in KIT:
                if rx.search(i.name):
                    done[key] = True
        elif i.top.startswith("INFO PACK") and i.ext == ".pdf":
            done["infopack"] = True
    return done


def kits_for(props: list[Property]) -> dict[str, dict[str, bool]]:
    """``kit`` for many properties in one query (the home list)."""
    roots = sorted({p.folder.rsplit("/", 1)[0] for p in props})
    found: dict[str, list[Item]] = {p.folder: [] for p in props}
    with _connect() as conn:
        for root in roots:
            rows = conn.execute(
                "SELECT drive_id, item_id, name, path, size, modified FROM items WHERE is_folder = 0 "
                "AND path >= ? AND path < ? AND (upper(path) LIKE '%/ADVERT%' OR upper(path) LIKE '%/INFO PACK/%')",
                (root + "/", root + "0")).fetchall()
            for r in rows:
                parts = r["path"].split("/")
                folder = "/".join(parts[:root.count("/") + 2])
                if folder in found:
                    found[folder].append(Item(r["drive_id"], r["item_id"], r["name"], r["path"],
                                              r["path"][len(folder) + 1:], False, r["size"] or 0, r["modified"] or 0))
    return {folder: kit(its) for folder, its in found.items()}


_MASTER = re.compile(r"\b([A-Z]{1,2})\s?(\d{2,5})\s?[_/ ]\s?(\d{2}|20\d{2})\b")


def master_ref(all_items: list[Item]) -> str:
    """The Master of the High Court reference (T1233/26), when a file name carries it."""
    for i in all_items:
        if i.top in ("CORRESPONDENCE", "INSTRUCTION", "INSTRUCTIONS"):
            m = _MASTER.search(i.name.upper())
            if m and m.group(1) in ("T", "G", "C", "D", "N", "P", "K", "B", "M", "J", "EL", "ES"):
                return f"{m.group(1)}{m.group(2)}/{m.group(3)}"
    return ""


# The documents a read is made from. The info pack and proposal are left out: the
# studio's own info pack lands in Info Pack and must not count as news.
READ_KINDS = ("report", "lightstone", "valuation", "otp")


def signatures(all_items: list[Item]) -> tuple[str, str]:
    """(photos, documents): change when a photo or a document is added, replaced or removed."""
    import hashlib

    docs = documents(all_items)
    ph = sorted(f"{i.item_id}:{i.modified}" for i in photos(all_items))
    dc = sorted(f"{i.item_id}:{i.modified}" for k in READ_KINDS for i in docs[k])
    return (hashlib.sha1("|".join(ph).encode()).hexdigest()[:16],
            hashlib.sha1("|".join(dc).encode()).hexdigest()[:16])


def newest(all_items: list[Item]) -> float:
    """When the newest photo or document was saved (0 when there are none)."""
    docs = documents(all_items)
    stamps = [i.modified for i in photos(all_items)] + [i.modified for k in READ_KINDS for i in docs[k]]
    return max(stamps, default=0.0)


def modified_label(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d %b %Y") if ts else ""
