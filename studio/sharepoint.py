"""Reads a property's photos and documents: Microsoft Graph on the server, the
synced OneDrive folder in development.

Graph uses the director platform's app registration ("Dynamic Director View",
``Sites.Selected`` with read on the Marketing and Company Share sites), set as
``STUDIO_GRAPH_*`` in .env, so the studio can read but never change anything on
SharePoint.

Everything fetched is cached on disk under ``STUDIO_CACHE`` keyed by the item's id
and modified time, so a photo is downloaded once however often it is previewed.
"""

from __future__ import annotations

import hashlib
import io
import os
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from studio.catalog import Item

GRAPH = "https://graph.microsoft.com/v1.0"

# The longest edge kept for each use: grid tiles, previews and print. 2400 px
# covers the 1080 px ads and the billboard's photo panels; an A4 info pack
# photo half a page wide needs about 1200.
SIZES = {"tile": 360, "view": 1400, "print": 2400}


class StoreError(RuntimeError):
    pass


def cache_dir() -> Path:
    p = Path(os.environ.get("STUDIO_CACHE", Path(__file__).resolve().parent.parent / ".studio-cache"))
    p.mkdir(parents=True, exist_ok=True)
    return p


# The photos the studio uses are colour graded (``_grade``). Bump when the grading
# changes, so every cached copy is graded again.
GRADE = "g1"


def _key(item: Item, what: str) -> Path:
    h = hashlib.sha1(f"{item.drive_id}|{item.item_id}|{item.modified}|{what}".encode()).hexdigest()[:24]
    jpeg = what.split("-")[0] in SIZES or what.startswith("thumb")
    return cache_dir() / h[:2] / f"{h}{'.jpg' if jpeg else item.ext}"


class Store:
    name = "store"

    def _original(self, item: Item) -> bytes:
        raise NotImplementedError

    def _graph_render(self, item: Item, px: int) -> Optional[bytes]:
        return None

    # -- public -------------------------------------------------------------

    def content(self, item: Item) -> bytes:
        path = _key(item, "original")
        if path.exists():
            return path.read_bytes()
        data = self._original(item)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    def image(self, item: Item, size: str = "view") -> bytes:
        """A JPEG of the photo, longest edge at most ``SIZES[size]``, turned upright and
        colour graded (``_grade``: light and colour only, nothing in the picture changes)."""
        px = SIZES[size]
        path = _key(item, f"{size}-{GRADE}")
        if path.exists():
            return path.read_bytes()
        data = None
        if item.ext in (".heic", ".heif"):
            data = self._graph_render(item, px)   # Pillow cannot read HEIC; SharePoint can
            if data is not None:
                data = _shrink(data, px, grade=True)
        if data is None:
            data = _shrink(self.content(item), px, grade=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    def thumb(self, item: Item, px: int = 480) -> Optional[bytes]:
        """A JPEG preview of any file the team saved (an image, a PDF board or pack,
        a reel), or None when there is none to be had."""
        path = _key(item, f"thumb{px}")
        if path.exists():
            return path.read_bytes()
        data = self._graph_render(item, px)
        if data is None and item.ext in (".png", ".jpg", ".jpeg", ".webp"):
            data = _shrink(self.content(item), px)
        if data is None and item.ext == ".pdf":
            data = _pdf_first_page(self.content(item), px)
        if data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return data


def _shrink(raw: bytes, px: int, grade: bool = False) -> bytes:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(raw)) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
            im = bg
        im.thumbnail((px, px))
        im = im.convert("RGB")
        if grade:
            im = _grade(im)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88, optimize=True)
        return out.getvalue()


def _grade(im):
    """Colour grading for property photos, nothing more: the photo's own levels
    stretched (a grey, hazy shot gets its blacks and whites back), vibrancy added
    where it is flat (a photo that already has colour gets almost none), a dark
    photo lifted a little and a flat one given a touch of contrast. Nothing is
    added to or taken out of the picture."""
    from PIL import ImageEnhance, ImageOps, ImageStat

    hsv = im.convert("HSV")
    sat = ImageStat.Stat(hsv.getchannel("S")).mean[0] / 255
    val = ImageStat.Stat(hsv.getchannel("V")).mean[0] / 255
    spread = ImageStat.Stat(im.convert("L")).stddev[0] / 255
    im = ImageOps.autocontrast(im, cutoff=(0.4, 0.4), preserve_tone=True)
    im = ImageEnhance.Color(im).enhance(1.06 + max(0.0, min(0.30, (0.40 - sat) * 1.1)))
    if val < 0.45:
        im = ImageEnhance.Brightness(im).enhance(1 + min(0.16, (0.45 - val) * 0.8))
    if spread < 0.20:
        im = ImageEnhance.Contrast(im).enhance(1.06)
    return im


def _pdf_first_page(raw: bytes, px: int) -> Optional[bytes]:
    try:
        import fitz

        with fitz.open(stream=raw, filetype="pdf") as doc:
            page = doc[0]
            zoom = px / max(page.rect.width, page.rect.height)
            return _shrink(page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png"), px)
    except Exception:
        return None


class LocalStore(Store):
    """The OneDrive folder synced to this Mac (development and tests)."""

    name = "local"

    def __init__(self, root: str):
        self.root = Path(root)

    def _file(self, item: Item) -> Path:
        return self.root / item.library / item.path

    def _original(self, item: Item) -> bytes:
        f = self._file(item)
        if not f.exists():
            raise StoreError(f"Not in the synced folder: {item.path}")
        return f.read_bytes()


class GraphStore(Store):
    name = "graph"

    def __init__(self, tenant: str, client_id: str, secret: str):
        self.tenant, self.client_id, self.secret = tenant, client_id, secret
        self.http = httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=True)
        self._token, self._until = "", 0.0
        self._lock = threading.Lock()

    def _auth(self) -> str:
        with self._lock:
            if self._token and time.time() < self._until:
                return self._token
            r = self.http.post(
                f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token",
                data={"grant_type": "client_credentials", "client_id": self.client_id,
                      "client_secret": self.secret, "scope": "https://graph.microsoft.com/.default"})
            if r.status_code != 200:
                raise StoreError(f"Microsoft sign-in failed ({r.status_code})")
            body = r.json()
            self._token = body["access_token"]
            self._until = time.time() + int(body.get("expires_in", 3600)) - 120
            return self._token

    def _get(self, url: str) -> httpx.Response:
        r = None
        for attempt in range(5):
            try:
                r = self.http.get(url, headers={"Authorization": f"Bearer {self._auth()}"})
            except httpx.TransportError:
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(30, int(r.headers.get("Retry-After", 2 ** attempt) or 1)))
                continue
            if r.status_code == 401 and attempt == 0:
                self._token = ""
                continue
            return r
        if r is None:
            raise StoreError("Could not reach Microsoft")
        return r

    def _original(self, item: Item) -> bytes:
        r = self._get(f"{GRAPH}/drives/{item.drive_id}/items/{item.item_id}/content")
        if r.status_code != 200:
            raise StoreError(f"SharePoint answered {r.status_code} for {item.name}")
        return r.content

    def _graph_render(self, item: Item, px: int) -> Optional[bytes]:
        r = self._get(f"{GRAPH}/drives/{item.drive_id}/items/{item.item_id}/thumbnails/0/c{px}x{px}/content")
        return r.content if r.status_code == 200 and r.content else None


_store: Optional[Store] = None


def store() -> Store:
    """Graph when the app registration is configured, else the synced folder."""
    global _store
    if _store is None:
        source = os.environ.get("STUDIO_SOURCE", "").lower()
        # Its own names, not MS_GRAPH_*: setting those would switch on the dormant
        # proposal upload to SharePoint (engine/proposal/sharepoint.py, D105).
        tenant, cid, secret = (os.environ.get(k, "") for k in
                               ("STUDIO_GRAPH_TENANT_ID", "STUDIO_GRAPH_CLIENT_ID", "STUDIO_GRAPH_CLIENT_SECRET"))
        if source != "local" and tenant and cid and secret:
            _store = GraphStore(tenant, cid, secret)
        else:
            root = os.environ.get("STUDIO_LOCAL_ROOT",
                                  str(Path.home() / "Library/CloudStorage/OneDrive-SharedLibraries-MasterTrainingSolutions"))
            _store = LocalStore(root)
    return _store
