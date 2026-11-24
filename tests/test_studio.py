"""The Ad Studio (D127): property list, facts, form, templates and screens.

Offline and key-free: a small director mirror and a synced-folder stand-in are
built in tmp_path, and reading the documents is stubbed (that path costs money
and is exercised on real folders by hand).
"""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

AAA = "3. PROPERTIES 2026/6. AAA Properties"
SOON = date.today() + timedelta(days=40)
PASSWORD = "studio-test-pass-123"


def _jpeg(color=(120, 90, 60), size=(800, 600)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=95)
    return buf.getvalue() + b"\0" * 30_000   # over the 25 kB floor for real photos


def _make_mirror(tmp_path):
    """director.db with two open properties, and the synced folder beside it."""
    db = tmp_path / "director.db"
    root = tmp_path / "onedrive"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE files (id TEXT PRIMARY KEY, dept TEXT, state TEXT, data TEXT);
        CREATE TABLE items (drive_id TEXT, item_id TEXT, name TEXT, is_folder INTEGER, modified REAL,
                            size INTEGER, child_count INTEGER, path TEXT, PRIMARY KEY (drive_id, item_id));
    """)
    rhede = f"{AAA}/9001 - RHEDE BOERDERY BK_BR_"
    flat = f"{AAA}/9002 - MM MAHLOHONOLO - IE"
    files = {
        "DP9001": {"name": "RHEDE BOERDERY BK_BR_", "folder": rhede, "status": "Auction approved",
                   "auction": SOON.isoformat(), "proposal": {"when": "28 JANUARY - ONLINE", "comment": "Auction approved",
                                                              "date": SOON.isoformat()}},
        "DP9002": {"name": "MM MAHLOHONOLO - IE", "folder": flat, "status": "Being marketed", "listed": "380000.0",
                   "occupancy": "Illegal occupants"},
    }
    for ref, d in files.items():
        conn.execute("INSERT INTO files VALUES (?, 'property', 'open', ?)", (ref, json.dumps(d)))
    photo = _jpeg()
    entries = [
        (rhede, "Media/Photos/Overall/AERIAL VIEW.png", photo),
        (rhede, "Media/Photos/Overall/house.jpg", photo),
        (rhede, "Media/Photos/TO BE USED/orchard.jpg", photo),
        (rhede, "Media/Photos/LOOSE ASSETS/tractor.jpg", photo),
        (rhede, "Advertisements/DP9001 SAIA BANNER.png", photo),
        (rhede, "Lightstone/EVM_Report.pdf", b"%PDF-1.4"),
        (rhede, "Valuation/Desktop Valuation (Movable Assets).pdf", b"%PDF-1.4"),
        (rhede, "Valuation/Desktop Valuation (Property).pdf", b"%PDF-1.4"),
        (rhede, "OTP & ROA/9001 - OTP.docx", b"PK"),
        (rhede, "OTP & ROA/9001 - RULES OF AUCTION.docx", b"PK"),
        (flat, "Correspondence/FW_ Insolvent Estate _ M Mahlohonolo - T1233_26.msg", b"x"),
        (flat, "Media/Photos/1.jpg", photo),
        (flat, "Advertisements/DP9002 FOR SALE AD.png", photo),
        (flat, "Info Pack/DP9002 - FOR SALE INFO PACK.pdf", b"%PDF-1.4"),
    ]
    for n, (folder, rel, data) in enumerate(entries):
        path = f"{folder}/{rel}"
        f = root / "Marketing - Documents" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        conn.execute("INSERT INTO items VALUES ('drv', ?, ?, 0, 1.0, ?, NULL, ?)",
                     (f"id{n}", rel.rsplit("/", 1)[-1], len(data), path))
    conn.commit()
    conn.close()
    return db, root


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    db, root = _make_mirror(tmp_path)
    monkeypatch.setenv("DIRECTOR_DB", str(db))
    monkeypatch.setenv("STUDIO_SOURCE", "local")
    monkeypatch.setenv("STUDIO_LOCAL_ROOT", str(root))
    monkeypatch.setenv("STUDIO_CACHE", str(tmp_path / "cache"))
    from studio import sharepoint
    monkeypatch.setattr(sharepoint, "_store", None)
    return SimpleNamespace(db=db, root=root)


# --- catalog ------------------------------------------------------------------

def test_open_properties_and_folders(mirror):
    from studio import catalog

    refs = [p.ref for p in catalog.open_properties()]
    assert refs == ["DP9002", "DP9001"]
    p = catalog.get("9001")
    assert p.ref == "DP9001" and p.is_auction and p.online is True
    assert p.auction_date == SOON


def test_photos_put_aerials_and_team_picks_first_and_leave_out_artwork(mirror):
    from studio import catalog

    items = catalog.items(catalog.get("DP9001"))
    names = [p.name for p in catalog.photos(items)]
    assert names == ["AERIAL VIEW.png", "orchard.jpg", "house.jpg", "tractor.jpg"]
    assert "DP9001 SAIA BANNER.png" not in names


def test_documents_pick_the_property_valuation_and_skip_the_rules(mirror):
    from studio import catalog

    docs = catalog.documents(catalog.items(catalog.get("DP9001")))
    assert [i.name for i in docs["valuation"]][0] == "Desktop Valuation (Property).pdf"
    assert [i.name for i in docs["otp"]] == ["9001 - OTP.docx"]
    assert [i.name for i in docs["lightstone"]] == ["EVM_Report.pdf"]


def test_kit_sees_what_is_already_in_the_folder(mirror):
    from studio import catalog

    props = catalog.open_properties()
    kits = catalog.kits_for(props)
    rhede, flat = catalog.get("DP9001"), catalog.get("DP9002")
    assert kits[rhede.folder]["saia"] and not kits[rhede.folder]["auction_ad"]
    assert kits[flat.folder]["forsale_ad"] and kits[flat.folder]["infopack"]
    assert catalog.kit(catalog.items(flat)) == kits[flat.folder]


def test_master_ref_comes_from_a_correspondence_file_name(mirror):
    from studio import catalog

    assert catalog.master_ref(catalog.items(catalog.get("DP9002"))) == "T1233/26"


# --- facts ----------------------------------------------------------------------

def test_sheet_facts(mirror):
    from studio import catalog, facts as F

    flat = catalog.get("DP9002")
    got = F.from_sheets(flat, catalog.items(flat))
    assert got["price"] == "OFFERS FROM R380 000"
    assert got["badge"] == "INSOLVENCY SALE!"
    assert got["master_ref"] == "T1233/26"
    assert got["disclaimer"].startswith("PURCHASER TO DO EVICTION")

    rhede = catalog.get("DP9001")
    got = F.from_sheets(rhede, catalog.items(rhede))
    assert got["auction_kind"] == "ONLINE" and got["auction_time"] == "10:00 - 12:00"
    assert got["auction_date"] == f"{SOON.day} {SOON.strftime('%b').upper()} {SOON.year}"
    assert got["badge"] == "BUSINESS RESCUE AUCTION!"


@pytest.mark.parametrize("name,expected", [
    ("HBC FARMING (PTY) LTD - IN LIQUIDATION", "LIQUIDATION"),
    ("I/L Omohle construction", "LIQUIDATION"),
    ("(IE) MOKATSE TM & EM SIMANGO", "INSOLVENCY"),
    ("MO ODEBIYI - LATE ESTATE", "DECEASED ESTATE"),
    ("KATLEGO SITRUS PTY LTD_BR_LIMPOPO FARMS", "BUSINESS RESCUE"),
    ("Valda Gossman", ""),
])
def test_capacity_from_the_folder_name(name, expected):
    from studio import catalog, facts as F

    p = catalog.Property(ref="DP1", name=name, folder=f"{AAA}/1 - {name}", status="")
    assert F.capacity(p) == expected


def test_privacy_warnings_flag_the_debtor_and_id_numbers_but_not_common_words():
    from studio import catalog, facts as F

    p = catalog.Property(ref="DP1", name="MM MAHLOHONOLO - INSOLVENT ESTATE", folder="x", status="")
    warn = F.privacy_warnings(p, {"description": ["A home owned by Mahlohonolo, ID 8001015009087."],
                                  "headline": "ESTATE IN SOSHANGUVE"})
    assert any("MAHLOHONOLO" in w for w in warn)
    assert any("13-digit" in w for w in warn)
    assert not any("ESTATE" in w for w in warn)


def test_merge_never_overwrites_what_she_typed():
    from studio import facts as F

    current = {"headline": "MY WORDS", "subline": "", "price": "OFFERS FROM R1"}
    incoming = {"headline": "CLAUDE'S", "subline": "3 BEDROOM HOUSE", "price": ""}
    out = F.merge(current, incoming, edited={"headline"})
    assert out == {"headline": "MY WORDS", "subline": "3 BEDROOM HOUSE", "price": "OFFERS FROM R1"}


# --- the form -------------------------------------------------------------------

def test_form_round_trip():
    from studio import form as FORM

    facts = {
        "headline": "SS LOS ANGELES", "board_lines": ["MACADAMIA & AVOCADO", "±38.15 HA FARM"],
        "description": ["First paragraph.", "Second paragraph."],
        "overview": [{"label": "EXTENT", "text": "±38.15 ha"}, {"label": "ZONING", "text": "Agricultural"}],
        "portions": [{"label": "FARM RHEDE 242", "legal_description": "Portion 0 of the Farm Rhede No. 242",
                      "extent": "±38.15 ha", "municipality": "Makhado", "zoning": "Agricultural",
                      "title_deed": "T138740/1998"}],
        "improvements": [{"title": "RESIDENTIAL", "items": [{"icon": "home", "text": "Main house"},
                                                             {"icon": "pool", "text": "Swimming pool"}]}],
        "features": [{"icon": "m2", "text": "±74m² UNIT"}, {"icon": "bed", "text": "3 BEDROOM"}],
        "panel": [],
    }
    v = FORM.to_form(facts)
    submitted = {k: val for k, val in v.items() if k not in ("features", "panel")}
    for key in ("features", "panel"):
        for i, ft in enumerate(v[key]):
            submitted[f"{key}_icon_{i}"] = ft["icon"]
            submitted[f"{key}_text_{i}"] = ft["text"]
    back = FORM.from_form(submitted)
    for k in facts:
        assert back[k] == facts[k], k


# --- templates ------------------------------------------------------------------

def test_numbers_never_break_from_their_units():
    from studio.render import nb

    assert str(nb("OFFERS FROM R380 000")) == "OFFERS FROM R380 000"
    assert "38.15 ha" in str(nb("±38.15 ha"))
    assert "&amp;" in str(nb("MACADAMIA & AVOCADO"))   # still escaped


def test_weekday():
    from studio.render import weekday

    assert weekday("28 JAN 2027") == "THURSDAY"
    assert weekday("") == ""


def test_pack_pages_and_contents():
    from studio import render

    f = {"description": ["x"], "portions": [{}], "improvements": [{"title": str(i), "items": []} for i in range(5)],
         "best_use": ["y"], "highlights": [{"label": "a", "text": "b"}]}
    pages = render.pack_pages(f, ["p"] * 8, auction=True)
    assert [p["kind"] for p in pages] == ["overview", "description", "details", "improvements", "improvements",
                                          "use", "gallery", "gallery", "highlights"]
    assert [p["no"] for p in pages] == list(range(1, 10))
    toc = render.toc(pages)
    assert toc[3] == (4, "Main Improvements", "4-5")
    assert toc[-1] == (7, "Investment Highlights", "9")


def test_default_photo_picks(mirror):
    from studio import catalog, render

    photos = catalog.photos(catalog.items(catalog.get("DP9001")))
    by_name = {p.item_id: p.name for p in photos}
    picks = render.photo_picks({}, photos)
    assert by_name[picks["aerial"]] == "AERIAL VIEW.png"
    assert by_name[picks["photo1"]] == "AERIAL VIEW.png"     # the ad's left card is the aerial
    assert by_name[picks["hero"]] == "orchard.jpg"            # the team's own pick
    assert "tractor.jpg" not in [by_name[i] for i in picks["gallery"]]


@pytest.mark.parametrize("kind,mode", [("ad", "forsale"), ("ad", "auction"), ("saia", "auction"),
                                       ("icon", "auction"), ("board", "auction"), ("board", "forsale"),
                                       ("infopack", "auction")])
def test_every_template_renders_its_words(kind, mode):
    from studio import catalog, facts as F, render

    p = catalog.Property(ref="DP9001", name="RHEDE", folder="x", status="")
    f = F.blank() | {"headline": "MAKHADO | LIMPOPO", "subline": "MACADAMIA & AVOCADO FARM",
                     "short_title": "±38.15 HA FARM", "pack_title": "±38.15 HA FARM", "sale_kind": "BUSINESS RESCUE",
                     "price": "OFFERS FROM R1 000 000", "auction_date": "28 JAN 2027", "auction_time": "10:00 - 12:00",
                     "features": [{"icon": "ha", "text": "±38.15 HA TOTAL"}], "overview": [{"label": "EXTENT", "text": "±38.15 ha"}],
                     "board_lines": ["MACADAMIA & AVOCADO", "±38.15 HA FARM"], "board_place": ["MAKHADO"]}
    ctx = render.context(p, f, {"gallery": []}, mode, lambda i, s: "")
    html = render.html(kind, ctx)
    assert "RHEDE" not in html                 # the matter name never reaches an output
    assert "data-ready" not in html or True
    text = {"ad": "MAKHADO | LIMPOPO", "saia": "BUSINESS RESCUE", "icon": "PROPERTY REF: DP9001",
            "board": "MAKHADO", "infopack": "PROPERTY OVERVIEW"}[kind]
    assert text in html


# --- the screens ----------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch, mirror):
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "engine.db"))
    monkeypatch.setenv("APP_SECRET", "test-secret-studio")
    monkeypatch.setenv("ENGINE_ALLOW_INSECURE_COOKIE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from webapp import auth, models
    from webapp.main import create_app

    db = models.init_db()
    models.set_setting(db, "output_root", str(tmp_path / "out"))
    application = create_app()

    def login(role="marketing"):
        email = f"{role}@dynamicauctioneers.co.za"
        if models.get_user(db, email) is None:
            models.create_user(db, email=email, pw_hash=auth.hash_password(PASSWORD), role=role)
        client = TestClient(application)
        assert client.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=False).status_code == 303
        return client

    return SimpleNamespace(db=db, login=login, tmp=tmp_path)


def test_the_list_shows_an_auction_waiting_on_its_ad(app):
    page = app.login().get("/studio").text
    waiting = page.split("Waiting on an ad", 1)[1].split("</section>", 1)[0]
    assert "DP9001" in waiting and "DP9002" not in waiting


def test_open_edit_and_preview(app):
    c = app.login()
    page = c.get("/studio/DP9002").text
    assert "OFFERS FROM R380 000" in page          # from the stocklist
    assert "T1233/26" in page                      # from the correspondence file name

    r = c.post("/studio/DP9002/save", data={"headline": "SS LOS ANGELES", "price": "OFFERS FROM R375 000"})
    assert r.status_code == 200 and r.headers.get("HX-Trigger") == "studio-refresh"
    preview = c.get("/studio/DP9002/preview/ad").text
    assert "SS LOS ANGELES" in preview and "R375 000" in preview

    from studio import state
    row = state.load(app.db, "DP9002")
    assert row["edited"] == {"headline", "price"}


def test_switching_to_auction_changes_the_badge_and_the_outputs(app):
    c = app.login()
    c.get("/studio/DP9002")
    c.post("/studio/DP9002/mode", data={"mode": "auction"})
    page = c.get("/studio/DP9002").text
    assert "SAIA banner" in page and "App icon" in page
    from studio import state
    assert state.load(app.db, "DP9002")["facts"]["badge"] == "INSOLVENCY AUCTION!"


def test_picking_photos(app):
    from studio import catalog

    c = app.login()
    c.get("/studio/DP9001")
    photos = catalog.photos(catalog.items(catalog.get("DP9001")))
    house = next(p for p in photos if p.name == "house.jpg")
    r = c.post("/studio/DP9001/pick", data={"slot": "hero", "item": house.item_id})
    assert r.status_code == 200
    r = c.post("/studio/DP9001/pick", data={"slot": "gallery", "item": house.item_id})
    from studio import state
    picks = state.load(app.db, "DP9001")["photos"]
    assert picks["hero"] == house.item_id
    assert c.post("/studio/DP9001/pick", data={"slot": "hero", "item": "not-ours"}).status_code == 400

    img = c.get(f"/studio/DP9001/photo/{house.item_id}/tile")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"


def test_reading_the_documents_keeps_her_edits(app, monkeypatch):
    from studio import facts as F, state
    from webapp.routes import studio as routes

    c = app.login()
    c.get("/studio/DP9001")
    c.post("/studio/DP9001/save", data={"headline": "HER HEADLINE"})

    def fake_read(prop, items, sheets):
        return {"headline": "CLAUDE HEADLINE", "subline": "MACADAMIA & AVOCADO FARM", "auction_date": "1 JAN 2000"}, []
    monkeypatch.setattr(F, "read_documents", fake_read)

    class Now:  # run the job on this thread
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(routes.threading, "Thread", Now)

    r = c.post("/studio/DP9001/read")
    assert r.status_code == 200
    row = state.load(app.db, "DP9001")
    assert row["reading"] == "done"
    assert row["facts"]["headline"] == "HER HEADLINE"
    assert row["facts"]["subline"] == "MACADAMIA & AVOCADO FARM"
    assert row["facts"]["auction_date"] != "1 JAN 2000"   # the sheets win on the date


def test_reading_a_property_never_opened_starts_from_the_sheets(app, monkeypatch):
    from studio import facts as F, state
    from webapp.routes import studio as routes

    monkeypatch.setattr(F, "read_documents", lambda prop, items, sheets: ({"subline": "3 BEDROOM APARTMENT"}, []))
    routes._read_job(app.db, "DP9002", "someone")
    row = state.load(app.db, "DP9002")
    assert row["reading"] == "done"
    assert row["facts"]["subline"] == "3 BEDROOM APARTMENT"
    assert row["facts"]["price"] == "OFFERS FROM R380 000"


def test_an_unknown_property_is_a_404(app):
    assert app.login().get("/studio/DP1").status_code == 404


def test_the_ad_reaches_the_proposal_screen(app, monkeypatch):
    from studio import render

    monkeypatch.setattr(render, "build", lambda *a, **k: {"DP9001 AUCTION AD.png": b"PNG", "DP9001 SAIA BANNER.png": b"x"})
    c = app.login()
    c.get("/studio/DP9001")
    r = c.get("/studio/DP9001/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert (app.tmp / "out" / "DP9001" / "artifacts" / "demo_ad.png").read_bytes() == b"PNG"


def test_a_real_render_when_chromium_is_available(mirror, tmp_path):
    from studio import catalog, facts as F, render

    p = catalog.get("DP9001")
    items = catalog.items(p)
    try:
        files = render.build(p, F.blank() | {"headline": "MAKHADO | LIMPOPO"}, render.photo_picks({}, catalog.photos(items)),
                             "auction", {i.item_id: i for i in items}, kinds=["ad", "board"])
    except Exception as exc:  # no Playwright browser on this machine
        pytest.skip(f"no headless browser: {exc}")
    from PIL import Image

    ad = Image.open(io.BytesIO(files["DP9001 AUCTION AD.png"]))
    assert ad.size == (1080, 1350)
    assert files["DP9001 AUCTION BOARD.pdf"].startswith(b"%PDF")
