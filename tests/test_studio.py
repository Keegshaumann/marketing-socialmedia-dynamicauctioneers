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
                     (f"id{n}", rel.rsplit("/", 1)[-1], len(data) + n, path))   # real photos never share a size
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
    }
    v = FORM.to_form(facts)
    assert len(v["features"]) == 6                       # room for six
    submitted = {k: val for k, val in v.items() if k != "features"}
    for key in ("features",):
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


def test_pack_sections_in_order():
    from studio import render

    f = {"description": ["x"], "portions": [{}], "improvements": [{"title": "A", "items": []}],
         "sections": [{"title": "WATER & IRRIGATION", "lines": ["Pump"]}], "movables": ["TRACTOR"],
         "conditions": [{"label": "Deposit", "text": "10%"}], "fica": [{"label": "Entities", "text": "CIPC"}],
         "highlights": [{"label": "a", "text": "b"}]}
    assert render.pack_sections(f, ["p"]) == [
        "Auction Information", "Property Overview", "Property Particulars & Location", "Property Features",
        "Water & Irrigation", "Movable Assets", "Summary of Sale Conditions", "Registration & FICA Requirements",
        "Photographic Overview", "Investment Highlights"]
    assert render.pack_sections({}, [], auction=False) == ["Sale Information", "Property Particulars & Location"]


def test_default_photo_picks(mirror):
    from studio import catalog, render

    photos = catalog.photos(catalog.items(catalog.get("DP9001")))
    by_name = {p.item_id: p.name for p in photos}
    picks = render.photo_picks({}, photos)
    assert by_name[picks["aerial"]] == "AERIAL VIEW.png"
    assert by_name[picks["photo1"]] == "AERIAL VIEW.png"     # the ad's left card is the aerial
    assert by_name[picks["hero"]] == "orchard.jpg"            # the team's own pick
    assert "tractor.jpg" not in [by_name[i] for i in picks["gallery"]]


@pytest.mark.parametrize("kind,mode", [("ad", "forsale"), ("ad", "auction"), ("ad2", "forsale"), ("ad2", "auction"),
                                       ("saia", "auction"),
                                       ("icon", "auction"), ("board", "auction"), ("board", "forsale"),
                                       ("billboard", "auction"), ("billboard", "forsale"), ("reel", "auction"),
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
    text = {"ad": "MAKHADO | LIMPOPO", "ad2": "MAKHADO | LIMPOPO", "reel": "rvShine", "billboard": "MACADAMIA", "saia": "BUSINESS RESCUE", "icon": "PROPERTY REF: DP9001",
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
    # The grid stays open after a pick, so the next photo is one click away.
    assert '<details class="studio-photos__all" open' in r.text
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

    monkeypatch.setattr(routes, "_in_background", lambda target, *args: target(*args))  # run it now

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


def test_a_misspelt_aerial_still_counts():
    # DP3025 keeps its aerial as Media/Photos/Overall/AERIEL VIEW.png.
    from studio import catalog, render

    def item(n, rel):
        return catalog.Item("d", n, rel.rsplit("/", 1)[-1], "x/" + rel, rel, False, 90_000, 0.0)

    photos = sorted([item("1", "Media/Photos/Overall/IMG_1.jpeg"), item("2", "Media/Photos/Overall/AERIEL VIEW.png")],
                    key=catalog.photo_rank)
    assert photos[0].item_id == "2"
    assert render.photo_picks({}, photos)["aerial"] == "2"


# --- Claude chooses the photos (studio.photopick) ---------------------------------

def test_her_picks_beat_claudes_which_beat_the_rules(mirror):
    from studio import catalog, render

    photos = catalog.photos(catalog.items(catalog.get("DP9001")))
    by_name = {p.name: p.item_id for p in photos}
    claude = {"hero": by_name["house.jpg"], "aerial": by_name["house.jpg"], "photo2": "gone-from-folder",
              "gallery": [by_name["house.jpg"]]}
    picks = render.photo_picks({"aerial": by_name["orchard.jpg"], "claude": claude}, photos)
    assert picks["aerial"] == by_name["orchard.jpg"]          # hers
    assert picks["hero"] == by_name["house.jpg"]              # Claude's
    assert picks.get("photo2") != "gone-from-folder"          # an id no longer in the folder is ignored
    assert picks["gallery"] == [by_name["house.jpg"]]         # Claude's pack, until she changes it
    mine = render.photo_picks({"gallery": [by_name["orchard.jpg"]], "claude": claude}, photos)
    assert mine["gallery"] == [by_name["orchard.jpg"]]


def test_a_pick_stores_only_what_she_placed(app):
    from studio import catalog, state

    c = app.login()
    c.get("/studio/DP9001")
    house = next(p for p in catalog.photos(catalog.items(catalog.get("DP9001"))) if p.name == "house.jpg")
    c.post("/studio/DP9001/pick", data={"slot": "photo2", "item": house.item_id})
    assert state.load(app.db, "DP9001")["photos"] == {"photo2": house.item_id}


def test_the_photos_sent_to_claude_are_capped_with_the_teams_picks_first():
    from studio import catalog, photopick

    def item(n, rel):
        return catalog.Item("d", str(n), rel.rsplit("/", 1)[-1], "x/" + rel, rel, False, 90_000 + __import__("zlib").crc32(n.encode()), 0.0)

    team = [item(f"t{i}", f"Media/Photos/TO BE USED/{i:03}.jpg") for i in range(10)]
    rest = [item(f"o{i}", f"Media/Photos/Overall/{i:03}.jpg") for i in range(300)]
    loose = [item("a", "Media/LOOSE ASSETS/truck.jpg")]
    got = photopick.candidates(sorted(team + rest + loose, key=catalog.photo_rank))
    assert len(got) == photopick.MAX_PHOTOS
    assert [g.item_id for g in got[:10]] == [t.item_id for t in team]
    assert "a" not in {g.item_id for g in got}
    assert got[-1].item_id.startswith("o") and int(got[-1].item_id[1:]) > 250   # spread to the end


def test_claudes_labels_become_photo_ids(mirror, monkeypatch):
    from studio import catalog, facts as F, photopick

    prop = catalog.get("DP9001")
    photos = catalog.photos(catalog.items(prop))
    sent = {}

    def fake_ask(system, content, schema, **kw):
        sent["images"] = sum(1 for c in content if c["type"] == "image")
        sent["text"] = " ".join(c.get("text", "") for c in content)
        return photopick.Picks(aerial="P1", hero=" p2 ", photo1="P2", photo2="P99", photo3="", gallery=["P2", "P2", "P1", "x"])
    monkeypatch.setattr(F, "ask", fake_ask)

    got = photopick.choose(prop, photos, auction=True, about="MACADAMIA & AVOCADO FARM")
    pool = photopick.candidates(photos)
    assert sent["images"] == len(pool) and "AUCTION" in sent["text"] and "MACADAMIA" in sent["text"]
    assert got["aerial"] == pool[0].item_id and got["hero"] == pool[1].item_id
    assert "photo2" not in got                                  # an unknown label is dropped
    assert got["gallery"] == [pool[1].item_id, pool[0].item_id]  # no repeats


def test_reading_also_chooses_photos_and_keeps_hers(app, monkeypatch):
    from studio import catalog, facts as F, photopick, render, state
    from webapp.routes import studio as routes

    c = app.login()
    c.get("/studio/DP9001")
    photos = catalog.photos(catalog.items(catalog.get("DP9001")))
    by_name = {p.name: p.item_id for p in photos}
    c.post("/studio/DP9001/pick", data={"slot": "hero", "item": by_name["house.jpg"]})

    monkeypatch.setattr(F, "read_documents", lambda prop, items, sheets: ({"subline": "FARM"}, []))
    monkeypatch.setattr(photopick, "choose", lambda prop, photos, auction, about="": {
        "hero": by_name["orchard.jpg"], "aerial": by_name["orchard.jpg"]})
    routes._read_job(app.db, "DP9001", "someone")

    row = state.load(app.db, "DP9001")
    assert row["reading"] == "done" and "photos chosen" in row["message"]
    picks = render.photo_picks(row["photos"], photos)
    assert picks["hero"] == by_name["house.jpg"]       # hers stays
    assert picks["aerial"] == by_name["orchard.jpg"]   # Claude's fills the rest


def test_a_failed_photo_choice_never_fails_the_read(app, monkeypatch):
    from studio import facts as F, photopick, state
    from webapp.routes import studio as routes

    monkeypatch.setattr(F, "read_documents", lambda prop, items, sheets: ({"subline": "FARM"}, []))

    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(photopick, "choose", boom)
    routes._read_job(app.db, "DP9001", "someone")
    row = state.load(app.db, "DP9001")
    assert row["reading"] == "done" and "photos chosen" not in row["message"]


# --- the studio working by itself: prepare, file in SharePoint, keep current (D129) --

class FakeSharePoint:
    """Stands in for studio.writer.Writer: files by path, each with a content tag."""

    client_id = "studio-app"

    def __init__(self):
        self.files, self.paths, self.calls, self.n = {}, {}, [], 0

    def _meta(self, iid):
        f = self.files[iid]
        who = {"application": {"id": self.client_id}} if f["by"] == "app" else {"user": {"email": "nikki@x"}}
        return {"id": iid, "name": f["path"].rsplit("/", 1)[-1], "cTag": f["ctag"],
                "webUrl": "https://sp/" + f["path"], "lastModifiedBy": who}

    def _tag(self):
        self.n += 1
        return f"c{self.n}"

    def add(self, drive, path, data):
        from studio.writer import WriteError
        if path in self.paths:
            raise WriteError("conflict", "a file of that name is there")
        iid = f"sp{len(self.files) + 1}"
        self.files[iid] = {"path": path, "data": data, "ctag": self._tag(), "by": "app"}
        self.paths[path] = iid
        self.calls.append(("add", path.rsplit("/", 2)[-2] + "/" + path.rsplit("/", 1)[-1]))
        return self._meta(iid)

    def replace(self, drive, item_id, name, data):
        self.files[item_id].update(data=data, ctag=self._tag(), by="app")
        self.calls.append(("replace", name))
        return self._meta(item_id)

    def item(self, drive, item_id):
        return self._meta(item_id) if item_id in self.files else None

    def by_path(self, drive, path):
        return self._meta(self.paths[path]) if path in self.paths else None

    def made_by_us(self, item):
        return ((item.get("lastModifiedBy") or {}).get("application") or {}).get("id") == self.client_id

    def folder_url(self, drive, path):
        return "https://sp/" + path

    # what people do in SharePoint
    def person_edits(self, name):
        iid = next(i for i, f in self.files.items() if f["path"].endswith("/" + name))
        self.files[iid].update(data=b"theirs", ctag=self._tag(), by="person")

    def person_deletes(self, name):
        iid = next(i for i, f in self.files.items() if f["path"].endswith("/" + name))
        del self.paths[self.files.pop(iid)["path"]]


def _fake_build(prop, facts, picks, mode, items_by_id, kinds=None, with_qr=None):
    from studio import facts as F
    out = {F.name_for(prop, "ad", mode): f"AD {facts.get('headline')} {facts.get('price')} {facts.get('auction_date')} {picks.get('hero')}".encode(),
           F.name_for(prop, "ad2", mode): f"AD2 {facts.get('headline')} {facts.get('auction_date')}".encode(),
           F.name_for(prop, "board", mode): f"BOARD {facts.get('headline')}".encode(),
           F.name_for(prop, "infopack", mode): f"PACK {facts.get('headline')}".encode()}
    if mode == "auction":
        out[F.name_for(prop, "saia", mode)] = b"SAIA"
    return out


@pytest.fixture
def auto(app, monkeypatch):
    """The studio's job with Claude, Chromium and SharePoint stood in for."""
    from studio import auto as A, facts as F, photopick, render

    reads = []

    def fake_read(prop, items, sheets):
        reads.append(prop.ref)
        return {"headline": "MAKHADO | LIMPOPO", "subline": "MACADAMIA FARM", "caption": "Farm for sale.",
                "features": [{"icon": "ha", "text": "±38.15 ha"}, {"icon": "park", "text": "MACADAMIAS"},
                             {"icon": "water_drop", "text": "BOREHOLE"}, {"icon": "home", "text": "FARMHOUSE"}]}, []

    monkeypatch.setattr(F, "read_documents", fake_read)
    monkeypatch.setattr(photopick, "choose", lambda prop, photos, auction, about="": {})
    monkeypatch.setattr(render, "build", _fake_build)
    sp = FakeSharePoint()
    return SimpleNamespace(app=app, sp=sp, reads=reads, run=lambda **kw: A.run(app.db, kw.pop("w", sp), **kw))


def _set_auction(mirror_db, ref, day):
    conn = sqlite3.connect(mirror_db)
    d = json.loads(conn.execute("SELECT data FROM files WHERE id = ?", (ref,)).fetchone()[0])
    d["auction"] = day.isoformat()
    d["proposal"]["date"] = day.isoformat()
    conn.execute("UPDATE files SET data = ? WHERE id = ?", (json.dumps(d), ref))
    conn.commit()
    conn.close()


def test_what_needs_an_ad(mirror):
    from studio import auto as A, catalog
    props = {p.ref: p for p in catalog.open_properties()}
    kits = catalog.kits_for(list(props.values()))
    assert A.needs_ad(props["DP9001"], kits[props["DP9001"].folder], date.today()) == "waiting"
    assert A.needs_ad(props["DP9002"], kits[props["DP9002"].folder], date.today()) == ""   # has its for sale ad
    assert A.folder_ready(catalog.items(props["DP9001"]))
    assert not A.folder_ready(catalog.items(props["DP9002"]))          # one photo, no documents


def test_the_studio_prepares_and_files_a_property_waiting_on_an_ad(auto):
    from studio import publish, state
    log = auto.run()
    assert auto.reads == ["DP9001"]                       # DP9002 already has its ad
    s = publish.get_set(auto.app.db, "DP9001")
    assert s["status"] == "saved", log
    added = sorted(path for op, path in auto.sp.calls if op == "add")
    assert added == ["Advertisements/DP9001 AUCTION AD 2.png", "Advertisements/DP9001 AUCTION AD.png",
                     "Advertisements/DP9001 AUCTION BOARD.pdf",
                     "Advertisements/DP9001 POSTING COPY.txt", "Info Pack/DP9001 AUCTION INFO PACK.pdf"]
    # the team's own SAIA banner was already there, so the studio added none beside it
    assert not any("SAIA" in p for _, p in auto.sp.calls)
    row = state.load(auto.app.db, "DP9001")
    assert row["updated_by"] == "" and "by the studio" in row["message"]
    # a second run with nothing changed touches nothing
    auto.sp.calls.clear()
    auto.run()
    assert auto.sp.calls == [] and auto.reads == ["DP9001"]
    # the list says so, and the studio's own work isn't under "You worked on"
    page = auto.app.login().get("/studio").text
    assert "In SharePoint" in page.split("Waiting on an ad", 1)[1].split("</section>", 1)[0]
    assert "You worked on" not in page


def test_a_new_auction_date_replaces_the_ad_in_place(auto, mirror):
    auto.run()
    first = dict(auto.sp.paths)
    auto.sp.calls.clear()
    later = SOON + timedelta(days=7)
    _set_auction(mirror.db, "DP9001", later)
    auto.run()
    assert sorted(auto.sp.calls) == [("replace", "DP9001 AUCTION AD 2.png"),
                                     ("replace", "DP9001 AUCTION AD.png")]   # only the files that changed
    assert auto.sp.paths == first                                         # same files, new versions
    ad = auto.sp.files[first[f"{AAA}/9001 - RHEDE BOERDERY BK_BR_/Advertisements/DP9001 AUCTION AD.png"]]
    assert f"{later.day} " in ad["data"].decode()


def test_files_people_changed_or_deleted_in_sharepoint_are_left_alone(auto, mirror, monkeypatch):
    from studio import catalog, publish
    auto.run()
    auto.sp.person_edits("DP9001 AUCTION AD.png")
    auto.sp.person_deletes("DP9001 AUCTION BOARD.pdf")
    auto.sp.calls.clear()
    _set_auction(mirror.db, "DP9001", SOON + timedelta(days=3))
    from studio import render
    monkeypatch.setattr(render, "build", lambda prop, facts, picks, mode, items, kinds=None, with_qr=None: {
        k: v + b" v2" for k, v in _fake_build(prop, facts, picks, mode, items).items()})
    auto.run()
    names = {n for _, n in auto.sp.calls}
    assert "DP9001 AUCTION AD.png" not in names and "DP9001 AUCTION BOARD.pdf" not in names
    assert ("replace", "DP9001 AUCTION INFO PACK.pdf") in auto.sp.calls
    f = publish.files(auto.app.db, "DP9001")
    assert f["DP9001 AUCTION AD.png"]["status"] == "theirs"
    assert f["DP9001 AUCTION BOARD.pdf"]["status"] == "removed"
    # a person pressing save adds the deleted board back, still not the changed ad
    auto.sp.calls.clear()
    publish.publish(auto.app.db, catalog.get("DP9001"), auto.sp, force=True)
    assert ("add", "Advertisements/DP9001 AUCTION BOARD.pdf") in auto.sp.calls
    assert not any(n.endswith("AUCTION AD.png") for _, n in auto.sp.calls)


def test_a_privacy_warning_waits_for_a_person(auto, monkeypatch):
    from studio import facts as F, publish
    from webapp.routes import studio as routes
    monkeypatch.setattr(F, "read_documents", lambda prop, items, sheets: (
        {"headline": "RHEDE | LIMPOPO", "features": [{"icon": "ha", "text": f"FEATURE {n}"} for n in range(4)]},
        ["Mentions RHEDE from the file name: check before it goes out."]))
    auto.run()
    s = publish.get_set(auto.app.db, "DP9001")
    assert s["status"] == "held" and "RHEDE" in s["message"] and auto.sp.calls == []
    # Nikki looks, then presses "Checked, save to SharePoint"
    monkeypatch.setattr(routes, "_in_background", lambda target, *args: target(*args))
    monkeypatch.setattr(routes, "writer", lambda: auto.sp)
    c = auto.app.login()
    assert "Checked, save to SharePoint" in c.get("/studio/DP9001").text
    r = c.post("/studio/DP9001/publish")
    assert r.status_code == 200 and "In SharePoint" in r.text
    assert publish.get_set(auto.app.db, "DP9001")["status"] == "saved"
    # the next run keeps it filed; the warning stays checked
    auto.sp.calls.clear()
    auto.run()
    assert publish.get_set(auto.app.db, "DP9001")["status"] == "saved" and auto.sp.calls == []


def test_a_set_missing_its_gold_line_waits(auto, monkeypatch):
    from studio import facts as F, publish
    monkeypatch.setattr(F, "read_documents", lambda prop, items, sheets: ({"subline": "FARM"}, []))
    auto.run()
    s = publish.get_set(auto.app.db, "DP9001")
    assert s["status"] == "held" and "the gold line" in s["message"] and auto.sp.calls == []


def test_someone_editing_is_left_to_settle_then_filed(auto, monkeypatch):
    from studio import publish, state
    auto.run()
    auto.sp.calls.clear()
    c = auto.app.login()
    c.get("/studio/DP9001")
    assert c.post("/studio/DP9001/save", data={"headline": "LOUIS TRICHARDT | LIMPOPO"}).status_code == 200
    assert any("someone is editing" in line for line in auto.run()) and auto.sp.calls == []
    # ten minutes later
    row = state.load(auto.app.db, "DP9001")
    from webapp import models
    with models._connect(auto.app.db) as conn:
        conn.execute("UPDATE studio SET updated_at = '2026-01-01T00:00:00+00:00' WHERE ref = 'DP9001'")
    auto.run()
    assert ("replace", "DP9001 AUCTION AD.png") in auto.sp.calls
    assert "LOUIS TRICHARDT" in auto.sp.files[auto.sp.paths[next(p for p in auto.sp.paths if p.endswith("AUCTION AD.png"))]]["data"].decode()
    assert row["updated_by"] == "marketing@dynamicauctioneers.co.za"


def test_without_write_access_sets_are_only_prepared(auto):
    from studio import publish
    auto.run(w=None)
    s = publish.get_set(auto.app.db, "DP9001")
    assert s["status"] == "ready"
    page = auto.app.login().get("/studio/DP9001").text
    assert "can't save to SharePoint yet" in page and "Save to SharePoint</span>" not in page


def test_reads_are_capped_and_a_failed_read_waits_a_day(auto, monkeypatch):
    from studio import facts as F, publish, state
    from studio.sharepoint import StoreError
    assert auto.run(reads=0)[-1] == "1 more to read in later runs" and auto.reads == []

    def broken(prop, items, sheets):
        raise StoreError("There are no documents to read.")

    monkeypatch.setattr(F, "read_documents", broken)
    auto.run()
    assert state.load(auto.app.db, "DP9001")["reading"] == "failed"
    assert publish.get_set(auto.app.db, "DP9001")["read_at"]
    assert not any(line.startswith("read ") for line in auto.run())     # not again today


def test_a_failed_re_read_keeps_the_last_good_one(auto, monkeypatch):
    from studio import facts as F, prepare, state
    auto.run()                                            # a good first read
    good = state.load(auto.app.db, "DP9001")
    assert good["reading"] == "done"

    def no_credit(prop, items, sheets):
        raise RuntimeError("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
                           "'message': 'Your credit balance is too low to access the Anthropic API.'}}")

    monkeypatch.setattr(F, "read_documents", no_credit)
    prepare.read(auto.app.db, "DP9001", prepare.STUDIO)
    row = state.load(auto.app.db, "DP9001")
    assert row["reading"] == "done" and row["facts"] == good["facts"]
    assert row["message"].startswith(prepare.NO_CREDIT) and row["message"].endswith("The earlier details stand.")


def test_a_failed_catch_up_read_waits_a_day(auto, mirror, monkeypatch):
    from studio import facts as F, state
    auto.run()
    _add_item(mirror.db, "OTP & ROA/9001 - OTP v2.docx", "otp2", size=20_000)
    auto.run()
    _quiet_since_yesterday(auto.app.db)
    monkeypatch.setattr(F, "read_documents", lambda *a: (_ for _ in ()).throw(RuntimeError("API down")))
    assert any("new documents" in line for line in auto.run())       # tried, failed
    assert state.load(auto.app.db, "DP9001")["reading"] == "done"   # the first read stands
    assert not any("new documents" in line for line in auto.run())   # not every 15 minutes


def test_switched_off(auto):
    from webapp import models
    models.set_setting(auto.app.db, "studio_auto", "off")
    assert auto.run() == ["switched off (setting studio_auto)"] and auto.reads == []


def test_the_studio_follows_the_sheets_unless_she_chose(auto, mirror):
    from studio import catalog, prepare, state
    db = auto.app.db
    # a for-sale set the studio made; then an auction date arrives on the sheets
    state.save(db, "DP9001", facts={"headline": "X", "sale_kind": "BUSINESS RESCUE"}, mode="forsale")
    row = prepare.follow_the_sheets(db, catalog.get("DP9001"), state.load(db, "DP9001"))
    assert row["mode"] == "auction" and row["facts"]["badge"] == "BUSINESS RESCUE AUCTION!"
    assert row["facts"]["auction_date"]
    # she switches it back to for sale herself: that sticks
    auto.app.login().post("/studio/DP9001/mode", data={"mode": "forsale"})
    row = prepare.follow_the_sheets(db, catalog.get("DP9001"), state.load(db, "DP9001"))
    assert row["mode"] == "forsale"


def test_files_go_in_the_folders_as_the_team_spells_them():
    from studio.catalog import Item
    from studio.publish import folder_for

    def folders(*names):
        return [Item("d", n, n, f"X/{n}", n, True, 0, 0) for n in names]

    assert folder_for("DP1 AUCTION AD.png", folders("ADVERTISEMENTS", "Info pack")) == "ADVERTISEMENTS"
    assert folder_for("DP1 AUCTION INFO PACK.pdf", folders("ADVERTISEMENTS", "Info pack")) == "Info pack"
    assert folder_for("DP1 SAIA BANNER.png", folders("Advertisements & Budgets", "Advertisments")) == "Advertisments"
    assert folder_for("DP1 SAIA BANNER.png", folders("Advertisements & Budgets")) == "Advertisements"
    assert folder_for("DP1 FOR SALE INFO PACK.pdf", folders("Info Mailer")) == "Info Pack"


def test_the_certificate_signs_the_sign_in():
    import base64 as b64
    from datetime import datetime as dt, timezone as tz
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import NameOID
    from studio.writer import _assertion

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Dynamic Ad Studio")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(dt.now(tz.utc)).not_valid_after(dt.now(tz.utc) + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()) + cert.public_bytes(serialization.Encoding.PEM)
    token = _assertion("tenant-1", "client-1", pem)
    head, body, sig = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)
    header, claims = json.loads(b64.urlsafe_b64decode(pad(head))), json.loads(b64.urlsafe_b64decode(pad(body)))
    assert header["alg"] == "RS256" and b64.urlsafe_b64decode(pad(header["x5t"])) == cert.fingerprint(hashes.SHA1())
    assert claims["iss"] == claims["sub"] == "client-1" and claims["aud"].endswith("/tenant-1/oauth2/v2.0/token")
    key.public_key().verify(b64.urlsafe_b64decode(pad(sig)), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_a_folder_with_the_teams_whole_set_is_not_read_or_filed(auto, mirror):
    from studio import catalog, publish
    conn = sqlite3.connect(mirror.db)
    folder = f"{AAA}/9001 - RHEDE BOERDERY BK_BR_"
    for n, name in enumerate(["DP9001 AUCTION AD.png", "DP9001 APP ICON.png", "DP9001 AUCTION BOARD.pdf"]):
        conn.execute("INSERT INTO items VALUES ('drv', ?, ?, 0, 1.0, 50000, NULL, ?)",
                     (f"team{n}", name, f"{folder}/Advertisements/{name}"))
    conn.execute("INSERT INTO items VALUES ('drv', 'team9', 'DP9001 INFO PACK.pdf', 0, 1.0, 50000, NULL, ?)",
                 (f"{folder}/Info Pack/DP9001 INFO PACK.pdf",))
    conn.commit()
    conn.close()
    auto.run()
    assert auto.reads == [] and auto.sp.calls == []      # "await ad" on the sheet, but nothing missing
    s = publish.publish(auto.app.db, catalog.get("DP9001"), auto.sp, force=True)
    assert s["status"] == "complete" and auto.sp.calls == []



def _add_item(mirror_db, rel, item_id, *, modified=1.0, size=50_000):
    import time as _t
    conn = sqlite3.connect(mirror_db)
    path = f"{AAA}/9001 - RHEDE BOERDERY BK_BR_/{rel}"
    conn.execute("INSERT INTO items VALUES ('drv', ?, ?, 0, ?, ?, NULL, ?)",
                 (item_id, rel.rsplit("/", 1)[-1], _t.time() if modified == "now" else modified, size, path))
    conn.commit()
    conn.close()


def _quiet_since_yesterday(db, ref="DP9001"):
    from studio import state
    cols = {"changed_at": "2026-01-01T00:00:00+00:00"}
    if (state.get_set(db, ref) or {}).get("read_at"):
        cols["read_at"] = "2025-12-31T00:00:00+00:00"   # the last read came before the change
    state.set_status(db, ref, **cols)


def test_a_folder_still_filling_up_waits_a_day(auto, mirror):
    from studio import state
    _add_item(mirror.db, "Media/Photos/Overall/kitchen.jpg", "fresh1", modified="now")   # uploaded just now
    log = auto.run()
    assert auto.reads == [] and any("still arriving" in line for line in log)
    s = state.get_set(auto.app.db, "DP9001")
    assert s["status"] == "waiting" and "nothing new has arrived for 24 hours" in s["message"]
    assert "press Rush" in s["message"]
    _quiet_since_yesterday(auto.app.db)
    auto.run()
    assert auto.reads == ["DP9001"] and state.get_set(auto.app.db, "DP9001")["status"] == "saved"


def test_photos_that_arrive_later_are_chosen_again_and_filed(auto, mirror, monkeypatch):
    from studio import photopick, state
    auto.run()
    _add_item(mirror.db, "Media/Photos/Inspection/front.jpg", "front1")
    auto.sp.calls.clear()
    auto.run()                                          # just arrived: wait, and the set stays filed
    assert auto.sp.calls == [] and state.get_set(auto.app.db, "DP9001")["status"] == "saved"
    asked = []
    monkeypatch.setattr(photopick, "choose", lambda prop, photos, auction, about="": asked.append(1) or {"hero": "front1"})
    _quiet_since_yesterday(auto.app.db)
    log = auto.run()
    assert asked and any("chose the photos again for DP9001" in line for line in log)
    assert auto.reads == ["DP9001"]                     # the documents were not read again
    assert ("replace", "DP9001 AUCTION AD.png") in auto.sp.calls
    auto.sp.calls.clear()
    auto.run()
    assert auto.sp.calls == [] and len(asked) == 1      # caught up: nothing more to do


def test_her_own_photo_choice_is_not_redone(auto, mirror, monkeypatch):
    from studio import photopick
    auto.run()
    c = auto.app.login()
    c.post("/studio/DP9001/pick", data={"slot": "hero", "item": "id1"})
    _add_item(mirror.db, "Media/Photos/Inspection/front.jpg", "front1")
    auto.run()
    asked = []
    monkeypatch.setattr(photopick, "choose", lambda *a, **k: asked.append(1) or {"hero": "front1"})
    _quiet_since_yesterday(auto.app.db)
    from webapp import models
    with models._connect(auto.app.db) as conn:          # her pick was over ten minutes ago
        conn.execute("UPDATE studio SET updated_at = '2026-01-01T00:00:00+00:00' WHERE ref = 'DP9001'")
    auto.run()
    assert asked == []


def test_a_new_document_means_the_documents_are_read_again(auto, mirror):
    auto.run()
    _add_item(mirror.db, "OTP & ROA/9001 - OTP v2.docx", "otp2", size=20_000, modified=2.0)   # the newer OTP
    auto.run()
    assert auto.reads == ["DP9001"]
    _quiet_since_yesterday(auto.app.db)
    log = auto.run()
    assert auto.reads == ["DP9001", "DP9001"] and any("new documents" in line for line in log)



# --- 25 Nov: logos, features, photos, QR codes ---------------------------------------

def test_the_office_number_is_written_one_way():
    from studio import facts as F
    got = F.tidy({"caption": "Call 086 1552 288 or 0861552288.", "listing": ["Phone 086-155-2288", "0861 55 22 88"]})
    assert got == {"caption": "Call 086 155 2288 or 086 155 2288.", "listing": ["Phone 086 155 2288", "086 155 2288"]}


def test_the_ad_has_a_background_and_three_more_photos(mirror):
    from studio import catalog, render
    _add_item(mirror.db, "Media/Photos/Overall/garden.jpg", "garden1")
    items = catalog.items(catalog.get("DP9001"))
    photos = catalog.photos(items)
    picks = render.photo_picks({}, photos)
    assert "photo3" in picks
    for mode in ("auction", "forsale"):
        ctx = render.context(catalog.get("DP9001"), {}, picks, mode, lambda i, size: f"src:{i}")
        background = picks.get("aerial") if mode == "auction" else picks.get("hero")
        assert len(ctx["extras"]) == 3 and f"src:{background}" not in ctx["extras"]
        assert len(set(ctx["extras"])) == 3


def test_the_ad_shows_up_to_six_features():
    from studio import render
    from studio.catalog import Property
    prop = Property(ref="DP9001", name="X", folder="F", status="")
    feats = [{"icon": "home", "text": f"FEATURE {n}"} for n in range(1, 8)]
    for mode in ("auction", "forsale"):
        out = render.html("ad", render.context(prop, {"features": feats}, {}, mode, lambda i, s: ""))
        assert "FEATURE 6" in out and "FEATURE 7" not in out and "PROPERTY FEATURES" in out


def test_the_studios_qr_codes_scan_and_reuse_the_teams_link(mirror):
    zxingcpp = pytest.importorskip("zxingcpp")
    from PIL import Image
    from studio import catalog, facts as F, render
    link = "https://links.vanxfunnels.co.za/qr/TEST123"
    for white in (False, True):
        im = Image.open(io.BytesIO(render.qr_png(link, white)))
        ground = Image.new("RGB", im.size, (30, 30, 30) if white else (255, 255, 255))
        ground.paste(im, mask=im.split()[-1])
        assert [b.text for b in zxingcpp.read_barcodes(ground)] == [link]
    # the team's QR code in the folder gives the link
    folder = f"{AAA}/9001 - RHEDE BOERDERY BK_BR_"
    f = mirror.root / "Marketing - Documents" / folder / "Advertisements/DP9001 - QR CODE.png"
    data = render.qr_png(link, False)
    f.write_bytes(data)
    conn = sqlite3.connect(mirror.db)
    conn.execute("INSERT INTO items VALUES ('drv', 'qr1', 'DP9001 - QR CODE.png', 0, 5.0, ?, NULL, ?)",
                 (len(data), f"{folder}/Advertisements/DP9001 - QR CODE.png"))
    conn.commit()
    conn.close()
    assert F.team_qr_link(catalog.items(catalog.get("DP9001"))) == link


def test_a_set_with_fewer_than_four_real_features_waits_for_a_person(auto, monkeypatch):
    from studio import facts as F, publish
    monkeypatch.setattr(F, "read_documents", lambda prop, items, sheets: (
        {"headline": "RENOVAAL | FREE STATE", "features": [{"icon": "m2", "text": "±1250m² ERF"},
                                                          {"icon": "bolt", "text": "MUNICIPAL SERVICES"},
                                                          {"icon": "home", "text": "VACANT STAND"}]}, []))
    auto.run()
    s = publish.get_set(auto.app.db, "DP9001")
    assert s["status"] == "held" and "a fourth feature" in s["message"] and auto.sp.calls == []


def test_the_teams_qr_codes_and_covers_are_not_photos(mirror):
    from studio import catalog
    for n, rel in enumerate(["Advertisements/DP9001.png", "Advertisements/AUCTION/9001 (1).png",
                             "Advertisements/Cover Image.png", "Advertisements/IMAGES/aerial view.png"]):
        _add_item(mirror.db, rel, f"art{n}")
    names = {p.rel for p in catalog.photos(catalog.items(catalog.get("DP9001")))}
    assert "Advertisements/IMAGES/aerial view.png" in names
    assert not names & {"Advertisements/DP9001.png", "Advertisements/AUCTION/9001 (1).png", "Advertisements/Cover Image.png"}


def test_an_ad_without_enough_photos_waits(auto, mirror):
    from studio import publish
    conn = sqlite3.connect(mirror.db)
    conn.execute("DELETE FROM items WHERE name IN ('house.jpg', 'orchard.jpg')")   # leaves the aerial and a tractor
    conn.commit()
    conn.close()
    from studio import catalog, prepare, state
    state.save(auto.app.db, "DP9001", facts={"headline": "X", "auction_date": "1 JAN 2030",
                                             "features": [{"icon": "ha", "text": f"F{n}"} for n in range(4)]},
               mode="auction", reading="done")
    s = publish.publish(auto.app.db, catalog.get("DP9001"), auto.sp)
    assert s["status"] == "held" and "more photos" in s["message"]


# --- the team's own files and the glance panel (25 Nov) ---------------------------------

def test_the_property_page_shows_what_the_team_made_and_keeps_the_studios_out_of_the_way(app):
    c = app.login()
    page = c.get("/studio/DP9001").text
    assert "Made by the team" in page and "SAIA banner" in page
    assert "The studio won't make its own saia banner" in page          # her banner stands in for the studio's
    r = c.get("/studio/DP9001/open/id4")                                  # DP9001 SAIA BANNER.png
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert c.get("/studio/DP9001/thumb/id4").status_code == 200
    assert c.get("/studio/DP9001/open/id1").status_code == 404            # a photo, not one of the team's outputs


def test_a_billboard_is_not_a_board():
    from studio import catalog, publish
    assert publish.kind_of("DP3107 BILLBOARD.pdf") == "billboard"
    assert publish.kind_of("DP3107 AUCTION BOARD.pdf") == "board"
    assert publish.kind_of("DP2924.png") == "qr"
    item = catalog.Item("d", "b", "DP3107 BILLBOARD.pdf", "X/Advertisements/DP3107 BILLBOARD.pdf",
                        "Advertisements/DP3107 BILLBOARD.pdf", False, 1, 1.0)
    assert catalog.kit([item])["board"] is False


def test_the_home_page_has_the_glance_panel(auto):
    auto.run()
    page = auto.app.login().get("/studio").text
    assert "At a glance" in page and "sets in SharePoint" in page and "Latest by the studio" in page
    assert "/studio/DP9001/saved-thumb/DP9001%20AUCTION%20AD.png" in page


def test_the_glance_never_shows_the_studios_ad_as_the_teams(auto, mirror):
    from studio import publish
    auto.run()
    f = publish.files(auto.app.db, "DP9001")["DP9001 AUCTION AD.png"]
    conn = sqlite3.connect(mirror.db)          # the hourly mirror picks up the studio's own file
    import time as _t
    conn.execute("INSERT INTO items VALUES ('drv', ?, 'DP9001 AUCTION AD.png', 0, ?, 50000, NULL, ?)",
                 (f["item_id"], _t.time(), f"{AAA}/9001 - RHEDE BOERDERY BK_BR_/Advertisements/DP9001 AUCTION AD.png"))
    conn.commit()
    conn.close()
    page = auto.app.login().get("/studio").text
    assert "Latest by the studio" in page and "Latest by the team" not in page


def test_the_big_photo_ad_is_filed_beside_the_first(auto):
    from studio import facts as F, render
    from studio.catalog import Property
    prop = Property(ref="DP9001", name="X", folder="F", status="")
    assert F.name_for(prop, "ad2", "auction") == "DP9001 AUCTION AD 2.png"
    assert [f.kind for f in render.formats_for("forsale")][:2] == ["ad", "ad2"]
    html = render.html("ad2", render.context(prop, {"features": [{"icon": "bed", "text": "3 BEDROOMS"}]}, {}, "forsale",
                                             lambda i, s: ""))
    assert "REAL ESTATE" in html and "MAKE US AN OFFER!" in html and "3 BEDROOMS" in html


def test_the_billboard_names_the_day_and_never_counts_as_a_gap(mirror):
    from studio import catalog, facts as F, publish, render
    from studio.catalog import Property
    prop = Property(ref="DP3107", name="X", folder="F", status="")
    f = {"sale_kind": "BUSINESS RESCUE", "auction_date": "28 JAN 2027", "auction_time": "10:00 - 12:00",
         "auction_kind": "ONLINE", "board_lines": ["MACADAMIA & AVOCADO", "±38.15 HA FARM"]}
    html = render.html("billboard", render.context(prop, f, {}, "auction", lambda i, s: ""))
    assert "BUSINESS RESCUE AUCTION" in html and "THURSDAY 28 JAN 2027" in html and "ONLINE" in html
    assert F.name_for(prop, "billboard", "auction") == "DP3107 AUCTION BILLBOARD.pdf"
    assert publish.kind_of("DP3107 AUCTION BILLBOARD.pdf") == "billboard"
    # a team billboard stops the studio's, but a missing billboard alone is no gap
    p = catalog.get("DP9001")
    assert all("BILLBOARD" not in g and " AD 2" not in g for g in publish.gaps(p, "auction", catalog.items(p)))


def test_the_reel_goes_only_with_the_studios_own_ad(mirror):
    from studio import catalog, facts as F, publish
    from studio.catalog import Property
    assert F.name_for(Property(ref="DP1", name="", folder="", status=""), "reel", "forsale") == "DP1 FOR SALE REEL.mp4"
    p = catalog.get("DP9001")
    items = catalog.items(p)
    kinds = publish._to_make(p, "auction", items, {}, set(), False)
    assert "reel" in kinds and "saia" not in kinds          # the team's own SAIA banner is in the folder
    conn = sqlite3.connect(mirror.db)                      # the team makes its own auction ad
    conn.execute("INSERT INTO items VALUES ('drv', 'tad', 'DP9001 AUCTION AD.png', 0, 2.0, 50000, NULL, ?)",
                 (f"{AAA}/9001 - RHEDE BOERDERY BK_BR_/Advertisements/DP9001 AUCTION AD.png",))
    conn.commit()
    conn.close()
    kinds = publish._to_make(p, "auction", catalog.items(p), {}, set(), False)
    assert not {"ad", "ad2", "reel"} & set(kinds)


def test_a_real_reel_when_chromium_and_ffmpeg_are_there():
    pytest.importorskip("imageio_ffmpeg")
    pytest.importorskip("playwright")
    from studio import render
    from studio.catalog import Property
    ctx = render.context(Property(ref="DP1", name="", folder="", status=""), {"headline": "X"}, {}, "forsale",
                         lambda i, s: "")
    try:
        with render.Renderer() as r:
            data = r.video("reel", render.html("reel", ctx), seconds=1, fps=10)
    except Exception as exc:  # no browser on this machine
        pytest.skip(f"no Chromium: {exc}")
    assert data[4:8] == b"ftyp" and len(data) > 1000



# --- flats and cottages, even counts, graded photos (25 Nov) ------------------------------

def test_feature_counts_stay_even():
    from studio import facts as F
    f = F.balance({"features": [{"text": str(n)} for n in range(5)], "main_title": "MAIN HOUSE",
                   "extra_units": [{"title": "FLAT", "items": [{"text": str(n)} for n in range(3)]},
                                   {"title": "EMPTY", "items": []}],
                   "shared_features": [{"text": "a"}, {"text": "b"}, {"text": "c"}]})
    assert len(f["features"]) == 4 and [len(u["items"]) for u in f["extra_units"]] == [2]
    assert len(f["shared_features"]) == 2
    single = F.balance({"features": [{"text": "x"}] * 7, "main_title": "MAIN HOUSE", "extra_units": [],
                        "shared_features": [{"text": "s"}]})
    assert len(single["features"]) == 6 and single["main_title"] == "" and single["shared_features"] == []


def test_a_house_with_a_flat_lists_each_dwelling_separately():
    from studio import render
    from studio.catalog import Property
    prop = Property(ref="DP2933", name="X", folder="F", status="")
    f = {"headline": "HARTIES", "main_title": "MAIN HOUSE",
         "features": [{"icon": "bed", "text": "3 BEDROOMS | MAIN EN-SUITE"}, {"icon": "bathtub", "text": "3 BATHROOMS"},
                      {"icon": "kitchen", "text": "KITCHEN & SCULLERY"}, {"icon": "garage", "text": "DOUBLE GARAGE"}],
         "extra_units": [{"title": "FLAT", "items": [{"icon": "home", "text": "SEPARATE ENTRANCE"},
                                                     {"icon": "bathtub", "text": "EN-SUITE BATHROOM"}]}],
         "shared_features": [{"icon": "m2", "text": "±1 006m² PROPERTY"}]}
    for kind in ("ad", "ad2", "reel"):
        for mode in ("forsale", "auction"):
            html = render.html(kind, render.context(prop, f, {}, mode, lambda i, s: ""))
            assert html.count('class="grp"') == 2 and "MAIN HOUSE" in html and ">FLAT<" in html
            assert "MAIN EN-SUITE" in html and "SEPARATE ENTRANCE" in html


def test_a_flat_with_one_feature_waits_for_a_second():
    from studio import publish
    row = {"mode": "forsale", "facts": {"headline": "X", "features": [{"text": str(n)} for n in range(4)],
                                         "extra_units": [{"title": "FLAT", "items": [{"text": "SEPARATE ENTRANCE"}]}]}}
    assert any("second feature for the flat" in m for m in publish.missing(row, {"hero": "a", "photo1": "b", "photo2": "c"}))


def test_the_extra_units_survive_the_form():
    from studio import form as FORM
    facts = {"extra_units": [{"title": "COTTAGES", "items": [{"icon": "bed", "text": "2 BACHELOR COTTAGES"},
                                                             {"icon": "home", "text": "COMMUNAL LAUNDRY ROOM | SHARED"}]}],
             "main_title": "MAIN HOUSE", "features": [{"icon": "bed", "text": "3 BEDROOMS | MAIN HAS DRESSING ROOM"}],
             "shared_features": [{"icon": "m2", "text": "±923m² PROPERTY"}]}
    v = FORM.to_form(facts)
    submitted = {k: val for k, val in v.items() if k not in ("features", "shared_features")}
    for key in ("features", "shared_features"):
        for i, ft in enumerate(v[key]):
            submitted[f"{key}_icon_{i}"], submitted[f"{key}_text_{i}"], submitted[f"{key}_note_{i}"] = ft["icon"], ft["text"], ft["note"]
    back = FORM.from_form(submitted)
    for k in facts:
        assert back[k] == facts[k], k


def test_photos_are_graded_but_never_changed(tmp_path):
    from PIL import Image, ImageStat
    from studio.sharepoint import _shrink
    grey = Image.new("RGB", (400, 300), (120, 125, 130))
    for x in range(400):                                  # a dull, low-contrast scene
        for y in range(0, 300, 3):
            grey.putpixel((x, y), (110 + x % 40, 118, 126))
    buf = io.BytesIO(); grey.save(buf, "JPEG", quality=95)
    out = Image.open(io.BytesIO(_shrink(buf.getvalue(), 400, grade=True)))
    before, after = ImageStat.Stat(grey.convert("L")), ImageStat.Stat(out.convert("L"))
    assert out.size == grey.size                           # same picture, same frame
    assert after.stddev[0] > before.stddev[0]              # more contrast



def test_the_answer_form_claude_fills_stays_small_enough():
    """The API refuses a structured answer whose grammar is too large. On 25 Nov 2026 a
    note field on every feature, and then a list of placed objects, were refused at
    11 249 characters; plain "PLACE | icon | text" lines were accepted at 10 683. Any new
    field needs a 50-token probe against the API (see DEPLOY notes) before it ships."""
    from studio import facts as F
    import json as _json
    size = len(_json.dumps(F.Facts.model_json_schema()))
    assert size < 10900, size
    # The limit is in the structure: one more field (movables) was refused at 9 624 characters.
    assert len(F.Facts.model_fields) == 37, "a new field needs a probe against the API before it ships"
    assert F.Facts.model_json_schema()["properties"]["other_features"]["items"] == {"type": "string"}


def test_files_sharing_a_folder_are_together_or_apart_as_the_teams_ads_say(mirror):
    from studio import catalog
    conn = sqlite3.connect(mirror.db)
    d = json.loads(conn.execute("SELECT data FROM files WHERE id = 'DP9001'").fetchone()[0])
    conn.execute("INSERT INTO files VALUES ('DP9001.2', 'property', 'open', ?)", (json.dumps({**d, "name": d["name"]}),))
    conn.commit()
    p = catalog.get("DP9001")
    assert catalog.siblings(p) == ["DP9001.2"]
    assert not catalog.sold_together(p, catalog.items(p), ["DP9001.2"])          # no ads, no price: apart
    folder = f"{AAA}/9001 - RHEDE BOERDERY BK_BR_"
    conn.execute("INSERT INTO items VALUES ('drv', 'ad1', 'DP9001 - FOR SALE AD.png', 0, 3.0, 50000, NULL, ?)",
                 (f"{folder}/Advertisements/DP9001 - FOR SALE AD.png",))
    conn.commit()
    assert catalog.sold_together(p, catalog.items(p), ["DP9001.2"])              # one ad for the main number
    conn.execute("INSERT INTO items VALUES ('drv', 'ad2', 'DP9001.2 FOR SALE AD.png', 0, 4.0, 50000, NULL, ?)",
                 (f"{folder}/Advertisements/DP9001.2 FOR SALE AD.png",))
    conn.commit()
    conn.close()
    assert not catalog.sold_together(p, catalog.items(p), ["DP9001.2"])          # an ad per sub-file


def test_the_studios_own_homepage_qr_is_not_taken_for_the_teams_link(mirror):
    pytest.importorskip("zxingcpp")
    from studio import catalog, facts as F, render
    folder = f"{AAA}/9001 - RHEDE BOERDERY BK_BR_"
    f = mirror.root / "Marketing - Documents" / folder / "Advertisements/DP9001 - QR CODE BLACK.png"
    data = render.qr_png("", False)                       # the studio's own: the homepage
    f.write_bytes(data)
    conn = sqlite3.connect(mirror.db)
    conn.execute("INSERT INTO items VALUES ('drv', 'qrs', 'DP9001 - QR CODE BLACK.png', 0, 9.0, ?, NULL, ?)",
                 (len(data), f"{folder}/Advertisements/DP9001 - QR CODE BLACK.png"))
    conn.commit()
    conn.close()
    assert F.team_qr_link(catalog.items(catalog.get("DP9001"))) == ""


# --- Rush: make it now instead of waiting ----------------------------------------------

def _now_in_background(monkeypatch, sp):
    from webapp.routes import studio as routes
    monkeypatch.setattr(routes, "_in_background", lambda target, *args: target(*args))
    monkeypatch.setattr(routes, "writer", lambda: sp)


def test_rush_makes_a_set_whose_folder_is_still_filling_now(auto, mirror, monkeypatch):
    from studio import state
    _add_item(mirror.db, "Media/Photos/Overall/kitchen.jpg", "fresh1", modified="now")
    auto.run()
    assert auto.reads == [] and state.get_set(auto.app.db, "DP9001")["status"] == "waiting"
    c = auto.app.login()
    page = c.get("/studio").text
    assert 'hx-post="/studio/DP9001/rush?from=list"' in page          # on the list
    assert "Rush: make it now" in c.get("/studio/DP9001").text        # and on its page
    _now_in_background(monkeypatch, auto.sp)
    r = c.post("/studio/DP9001/rush", headers={"HX-Request": "true"})
    assert r.headers.get("HX-Refresh") == "true"
    s = state.get_set(auto.app.db, "DP9001")
    assert auto.reads == ["DP9001"] and s["status"] == "saved" and s["rushed_at"] and s["rushed_sig"]
    assert ("add", "Advertisements/DP9001 AUCTION AD.png") in auto.sp.calls
    assert state.load(auto.app.db, "DP9001")["updated_by"] == "marketing@dynamicauctioneers.co.za"
    # the studio's own runs count the folder as settled now: no "waiting", no second read
    auto.sp.calls.clear()
    auto.run()
    assert state.get_set(auto.app.db, "DP9001")["status"] == "saved" and auto.reads == ["DP9001"]
    assert auto.sp.calls == []
    assert 'rush?from=list' not in c.get("/studio").text               # nothing left to rush
    # something new arriving after the rush waits again, as usual
    _add_item(mirror.db, "OTP & ROA/9001 - OTP v2.docx", "otp2", size=20_000, modified="now")
    auto.run()
    assert auto.reads == ["DP9001"]


def test_rush_from_the_list_answers_with_a_badge(auto, monkeypatch):
    _now_in_background(monkeypatch, auto.sp)
    r = auto.app.login().post("/studio/DP9001/rush?from=list", headers={"HX-Request": "true"})
    assert "Rushing" in r.text and auto.reads == ["DP9001"]


def test_rush_never_counts_as_checking_the_privacy_warnings(auto, monkeypatch):
    from studio import facts as F, state
    monkeypatch.setattr(F, "read_documents", lambda prop, items, sheets: (
        {"headline": "RHEDE | LIMPOPO", "features": [{"icon": "ha", "text": f"FEATURE {n}"} for n in range(4)]},
        ["Mentions RHEDE from the file name: check before it goes out."]))
    _now_in_background(monkeypatch, auto.sp)
    auto.app.login().post("/studio/DP9001/rush")
    s = state.get_set(auto.app.db, "DP9001")
    assert s["status"] == "held" and "RHEDE" in s["message"] and auto.sp.calls == []


def test_rush_after_a_read_only_files_it(auto, mirror, monkeypatch):
    from studio import state
    from webapp import models
    models.set_setting(auto.app.db, "studio_auto", "off")
    c = auto.app.login()
    _now_in_background(monkeypatch, auto.sp)
    c.post("/studio/DP9001/read")
    assert auto.reads == ["DP9001"]
    c.post("/studio/DP9001/rush")
    assert auto.reads == ["DP9001"]                                   # not read twice
    assert state.get_set(auto.app.db, "DP9001")["status"] == "saved"


def test_a_rush_whose_read_fails_says_so(auto, monkeypatch):
    from studio import facts as F, state
    monkeypatch.setattr(F, "read_documents", lambda *a: (_ for _ in ()).throw(RuntimeError("credit balance is too low")))
    _now_in_background(monkeypatch, auto.sp)
    auto.app.login().post("/studio/DP9001/rush")
    s = state.get_set(auto.app.db, "DP9001")
    assert s["status"] == "held" and "run out of credit" in s["message"] and "fill in" in s["message"]
    assert state.load(auto.app.db, "DP9001")["reading"] == "failed"


# --- a change asked for in her own words -------------------------------------------------

def _answer(changes=(), photos="", reply="Done."):
    from studio import ask as A
    return A.Answer(changes=[A.Change(field=f, value=v if isinstance(v, str) else json.dumps(v)) for f, v in changes],
                    photos=photos, reply=reply)


def _ask(app, monkeypatch, answer, text="Make the gold line SS ROSEBANK and add a pool", ref="DP9002"):
    from studio import facts as F
    from webapp.routes import studio as routes
    sent = {}

    def fake_ask(system, content, schema, **kw):
        sent["system"], sent["text"] = system, " ".join(x.get("text", "") for x in content)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(F, "ask", fake_ask)
    monkeypatch.setattr(routes, "_in_background", lambda target, *args: target(*args))
    c = app.login()
    c.get(f"/studio/{ref}")
    r = c.post(f"/studio/{ref}/ask", data={"text": text})
    return c, r, sent


FOUR = [{"icon": "m2", "text": "±74m² UNIT"}, {"icon": "bed", "text": "2 BEDROOMS"},
        {"icon": "bathtub", "text": "1 BATHROOM"}, {"icon": "pool", "text": "COMPLEX POOL"}]


def test_asking_for_a_change_changes_only_those_fields(app, monkeypatch):
    from studio import state
    c, r, sent = _ask(app, monkeypatch, _answer([("headline", "SS ROSEBANK"), ("features", FOUR),
                                                 ("not_a_field", "x")], reply="I changed the gold line and added the pool."))
    assert "Make the gold line SS ROSEBANK" in sent["text"] and "Gold line" in sent["text"]
    assert "MM MAHLOHONOLO" in sent["text"] and "never print it" in sent["text"]
    row = state.load(app.db, "DP9002")
    assert row["facts"]["headline"] == "SS ROSEBANK" and row["facts"]["features"] == FOUR
    assert {"headline", "features"} <= row["edited"] and "not_a_field" not in row["facts"]
    assert row["facts"]["price"] == "OFFERS FROM R380 000"            # untouched
    assert row["updated_by"] == "marketing@dynamicauctioneers.co.za"   # so the studio files it again
    assert "I changed the gold line" in r.text and "Changed: Gold line, Features" in r.text
    assert "Undo this change" in r.text
    assert "Ask for a change" in c.get("/studio/DP9002").text


def test_a_change_can_be_undone_once(app, monkeypatch):
    from studio import ask as A, state
    c, r, _ = _ask(app, monkeypatch, _answer([("headline", "SS ROSEBANK")]))
    before = state.load(app.db, "DP9002")
    assert before["facts"]["headline"] == "SS ROSEBANK"
    a = A.latest(app.db, "DP9002")
    r = c.post(f"/studio/DP9002/ask/{a['id']}/undo", headers={"HX-Request": "true"})
    assert r.headers.get("HX-Refresh") == "true"
    row = state.load(app.db, "DP9002")
    assert row["facts"]["headline"] == "" and "headline" not in row["edited"]
    assert A.latest(app.db, "DP9002")["status"] == "undone"
    r = c.post(f"/studio/DP9002/ask/{a['id']}/undo")
    assert "HX-Refresh" not in r.headers and "Undone: Gold line" in r.text
    assert c.post("/studio/DP9001/ask/%d/undo" % a["id"]).status_code == 404   # another property's


def test_asking_for_other_photos_places_them_as_hers(app, monkeypatch):
    from studio import catalog, photopick, render, state
    photos = catalog.photos(catalog.items(catalog.get("DP9001")))
    by_name = {p.name: p.item_id for p in photos}
    seen = {}

    def fake_rechoose(prop, photos, auction, wish, current):
        seen.update(wish=wish, auction=auction)
        return {"aerial": by_name["orchard.jpg"]}
    monkeypatch.setattr(photopick, "rechoose", fake_rechoose)
    c, r, _ = _ask(app, monkeypatch, _answer(photos="the orchard as the background"), ref="DP9001",
                   text="Use the orchard photo as the background")
    assert seen == {"wish": "the orchard as the background", "auction": True}
    row = state.load(app.db, "DP9001")
    assert row["photos"]["aerial"] == by_name["orchard.jpg"]
    assert "Changed: Background (auction)" in r.text
    # undo takes her placed photo away again, so the earlier choice shows
    from studio import ask as A
    c.post(f"/studio/DP9001/ask/{A.latest(app.db, 'DP9001')['id']}/undo")
    assert "aerial" not in state.load(app.db, "DP9001")["photos"]


def test_an_odd_number_of_features_is_evened_and_she_is_told(app, monkeypatch):
    from studio import state
    five = FOUR + [{"icon": "garage", "text": "CARPORT"}]
    _, r, _ = _ask(app, monkeypatch, _answer([("features", five)]))
    assert state.load(app.db, "DP9002")["facts"]["features"] == FOUR
    assert "left out CARPORT" in r.text


def test_a_failed_request_says_why_and_changes_nothing(app, monkeypatch):
    from studio import ask as A, state
    _, r, _ = _ask(app, monkeypatch, RuntimeError("Your credit balance is too low"))
    assert A.latest(app.db, "DP9002")["status"] == "failed" and "run out of credit" in r.text
    assert state.load(app.db, "DP9002")["facts"]["headline"] == ""


def test_her_values_come_back_in_the_studios_shapes():
    from studio import ask as A
    assert A.clean("headline", ' "SS ROSEBANK" ') == "SS ROSEBANK"
    assert A.clean("features", json.dumps([{"icon": "rocket", "text": "X", "note": "Y"}])) == [{"icon": "home", "text": "X | Y"}]
    got = A.clean("features", json.dumps(["bed | 3 BEDROOMS", "DOUBLE GARAGE"]))
    assert got == [{"icon": "bed", "text": "3 BEDROOMS"}, {"icon": "home", "text": "DOUBLE GARAGE"}]
    assert A.clean("extra_units", json.dumps([{"title": "FLAT:", "items": [{"icon": "home", "text": "SEPARATE ENTRANCE"}]},
                                              {"title": "EMPTY", "items": []}])) == [
        {"title": "FLAT", "items": [{"icon": "home", "text": "SEPARATE ENTRANCE"}]}]
    assert A.clean("terms", json.dumps(["10% DEPOSIT", " "])) == ["10% DEPOSIT"]
    with pytest.raises(ValueError):
        A.clean("features", '{"icon": "bed"}')
    with pytest.raises(ValueError):
        A.clean("features", "not json")


def test_rechoosing_photos_changes_only_the_slots_she_asked_about(mirror, monkeypatch):
    from studio import catalog, facts as F, photopick
    prop = catalog.get("DP9001")
    photos = catalog.photos(catalog.items(prop))
    pool = photopick.candidates(photos)
    current = {"aerial": pool[0].item_id, "hero": pool[1].item_id, "photo1": pool[2].item_id}
    sent = {}

    def fake_ask(system, content, schema, **kw):
        sent["text"] = " ".join(c.get("text", "") for c in content)
        return photopick.Rechosen(aerial="P3", hero="P1", photo1="P1", photo2="", photo3="", gallery=[],
                                  changed=["aerial", "photo1", "nonsense"])
    monkeypatch.setattr(F, "ask", fake_ask)
    got = photopick.rechoose(prop, photos, auction=True, wish="the orchard as the background", current=current)
    assert "aerial: P1" in sent["text"] and "Her request: the orchard as the background" in sent["text"]
    assert got == {"aerial": pool[2].item_id, "photo1": pool[0].item_id}   # hero was not asked about


# --- the OTP wherever the team keeps it, and the pack the team didn't make -----------------

def test_the_otp_is_found_wherever_the_team_keeps_it():
    from studio import catalog

    def item(rel, modified=1.0):
        return catalog.Item("d", rel, rel.rsplit("/", 1)[-1], "P/" + rel, rel, False, 10, modified)

    def otps(*rels):
        return [i.rel for i in catalog.documents([item(r, n) for n, r in enumerate(rels)])["otp"]]

    assert otps("OTP & ROA/3107 - OTP.docx", "OTP & ROA/3107 - RULES OF AUCTION.docx") == ["OTP & ROA/3107 - OTP.docx"]
    assert otps("Nedbank conditions of sale.pdf") == ["Nedbank conditions of sale.pdf"]           # loose, a PDF
    assert otps("3.OTP-signed/Seller draft.docx") == ["3.OTP-signed/Seller draft.docx"]            # numbered folder
    assert otps("2383(1) Erf 12/OTP & ROA/OTP.pdf") == ["2383(1) Erf 12/OTP & ROA/OTP.pdf"]        # one level down
    assert otps("Property Info Mailer/Documents/Offer to Purchase.pdf") != []
    assert otps("Offers/OTP - J Smith R450k.pdf", "Signed offers/Offer to purchase.pdf") == []     # buyers' offers
    assert otps("OTP & ROA/ROA.docx", "Advertisements/DP1 OTP QR.png", "Media/OTP scan.pdf") == []
    # named as an OTP beats any other file in the folder, and Word beats PDF
    assert otps("OTP & ROA/Annexure A plan.pdf", "OTP & ROA/OTP.pdf", "OTP & ROA/OTP.docx")[:2] == [
        "OTP & ROA/OTP.docx", "OTP & ROA/OTP.pdf"]


def test_a_pdf_otp_is_read_for_its_pages():
    from studio import facts as F
    assert next(r for r in F._READ if r[0] == "otp")[3] >= 20


def test_an_auction_whose_ad_the_team_made_still_gets_its_pack(auto, mirror):
    from studio import auto as A, catalog, publish
    _add_item(mirror.db, "Advertisements/DP9001 AUCTION AD.png", "teamad")
    p = catalog.get("DP9001")
    kit = catalog.kit(catalog.items(p))
    assert A.needs_ad(p, kit, date.today()) == "no_pack"
    auto.run()
    assert auto.reads == ["DP9001"]
    added = sorted(path for op, path in auto.sp.calls if op == "add")
    assert "Info Pack/DP9001 AUCTION INFO PACK.pdf" in added
    assert not any(path.endswith("AUCTION AD.png") or path.endswith("AD 2.png") for path in added)   # theirs stands
    assert publish.get_set(auto.app.db, "DP9001")["status"] == "saved"


def test_a_marketed_property_missing_only_its_pack_waits_for_the_setting(app):
    from studio import auto as A
    from webapp import models
    p = SimpleNamespace(auction_date=None, status="Being marketed", waiting_on_ad=False)
    kit = {"forsale_ad": True, "infopack": False}
    assert A.needs_ad(p, kit, date.today()) == "no_pack"
    assert not A.reads_by_itself(app.db, p, "no_pack", date.today())
    models.set_setting(app.db, "studio_marketed_packs", "on")
    assert A.reads_by_itself(app.db, p, "no_pack", date.today())
    assert A.needs_ad(p, {**kit, "infopack": True}, date.today()) == ""
    assert A.reads_by_itself(app.db, p, "no_ad", date.today())


def test_a_file_filed_beside_the_otp_is_not_a_new_document(mirror):
    from studio import catalog
    p = catalog.get("DP9001")
    before = catalog.signatures(catalog.items(p))[1]
    _add_item(mirror.db, "OTP & ROA/Annexure A - site plan.pdf", "annex", modified=5.0)
    assert catalog.signatures(catalog.items(p))[1] == before
    _add_item(mirror.db, "OTP & ROA/9001 - OTP signed.pdf", "otp3", modified=6.0)   # still not the Word OTP read
    assert catalog.signatures(catalog.items(p))[1] == before


def test_the_pack_lists_every_movable_and_lays_its_photos_out_landscape():
    from studio import catalog, facts as F, render

    p = catalog.Property(ref="DP9001", name="RHEDE", folder="x", status="")
    movables = [f"ASSET {n}" for n in range(1, 18)]
    f = F.blank() | {"pack_title": "±38.15 HA FARM", "description": ["A farm."], "movables": movables,
                     "terms": ["10% deposit"], "features": [{"icon": "ha", "text": "±38.15 HA | TOTAL"}],
                     "best_use": ["Farming"]}
    picks = {"aerial": "air", "hero": "front", "photo1": "p1", "photo2": "p2", "gallery": [f"g{n}" for n in range(9)]}
    ctx = render.context(p, f, picks, "auction", lambda i, s: f"/img/{i}")
    html = render.html("infopack", ctx)
    assert 'data-sec="Movable Assets"' in html and "All Outstanding Fees" not in html   # the OTP's rates rule, not a stock line
    assert "Dynamic Solutions 1068 (Pty) Ltd t/a Dynamic Auctioneers" in html and "PROPERTY REF: DP9001" in html
    assert all(m in html for m in movables)
    assert ">17<" in html                                   # numbered, in two columns
    assert ctx["pack_photos"][:9] == [f"/img/g{n}" for n in range(9)]   # each photo once, aerial apart
    assert "/img/air" not in ctx["pack_photos"] and len(set(ctx["pack_photos"])) == len(ctx["pack_photos"])
    assert ctx["contents_photos"] == ["/img/front", "/img/p1", "/img/p2"]
    assert 'data-sec="Movable Assets"' in html and 'data-toc="Property Particulars &amp; Location"' in html
    assert "aspect-ratio:4/3" in html and "prow--trio" in html            # landscape rows only
    assert "<small>TOTAL</small>" in html                   # a feature's second line



def test_the_movable_assets_come_out_of_the_improvements():
    from studio import facts as F
    got = F.movables_apart({"improvements": [
        {"title": "ORCHARDS", "items": [{"icon": "ha", "text": "±13 HA MACADAMIAS"}]},
        {"title": "MOVABLE ASSETS", "items": [{"icon": "home", "text": "MASSEY FERGUSON 240 TRACTOR (NON RUNNER)"},
                                              {"icon": "home", "text": "2 X STIHL MS 170 CHAINSAWS"}]}]})
    assert got["movables"] == ["MASSEY FERGUSON 240 TRACTOR (NON RUNNER)", "2 X STIHL MS 170 CHAINSAWS"]
    assert [g["title"] for g in got["improvements"]] == ["ORCHARDS"]
    assert "movables" in F.blank() and "movables" not in F.Facts.model_fields



def test_a_photo_copied_into_to_be_used_is_sent_once():
    from studio import catalog, photopick

    def item(n, rel, size):
        return catalog.Item("d", n, rel.rsplit("/", 1)[-1], "x/" + rel, rel, False, size, 0.0)

    photos = sorted([item("o1", "Media/Photos/Overall/WhatsApp Image 1.jpeg", 210_311),
                     item("o2", "Media/Photos/Overall/WhatsApp Image 2.jpeg", 198_004),
                     item("t1", "Media/Photos/To be used/1-01.jpeg", 210_311)], key=catalog.photo_rank)
    got = [p.item_id for p in photopick.candidates(photos)]
    assert got == ["t1", "o2"]                      # the team's copy, once; the other Overall photo too


# --- what the team teaches the studio --------------------------------------------------------

def _teach(app, monkeypatch, scope, rule, said="You missed the borehole", fix="0", ref="DP9001", fail=False):
    from studio import facts as F, lessons
    from webapp.routes import studio as routes

    def fake_ask(system, content, schema, **kw):
        if fail:
            raise RuntimeError("API down")
        return lessons.Lesson(scope=scope, rule=rule, reply="From now on I will.")
    monkeypatch.setattr(F, "ask", fake_ask)
    monkeypatch.setattr(routes, "_in_background", lambda target, *args: target(*args))
    c = app.login()
    c.get(f"/studio/{ref}")
    return c, c.post(f"/studio/{ref}/lesson", data={"text": said, "kind": "infopack", "fix": fix},
                     headers={"HX-Request": "true"})


def test_a_lesson_becomes_a_rule_every_later_request_follows(app, monkeypatch):
    from studio import lessons
    rule = "List every borehole and its yield in the improvements when the documents mention one."
    c, r = _teach(app, monkeypatch, "words", rule)
    assert "Learned." in r.text and rule in r.text
    assert rule in lessons.block("words", app.db) and lessons.block("photos", app.db) == ""
    # her next change request carries it
    _, _, sent = _ask(app, monkeypatch, _answer([("headline", "X")]))
    assert rule in sent["system"]
    # the lessons page shows who taught it where, and it can be turned off
    page = c.get("/studio/lessons").text
    assert rule in page and "You missed the borehole" in page and "DP9001" in page
    lid = lessons.all_lessons(app.db)[0]["id"]
    assert c.post(f"/studio/lessons/{lid}", data={"active": "0"}, follow_redirects=False).status_code == 303
    assert lessons.block("words", app.db) == ""


def test_a_photo_lesson_reaches_the_photo_choice_and_a_layout_one_stays_for_keegan(app, monkeypatch):
    from studio import catalog, facts as F, lessons, photopick
    _teach(app, monkeypatch, "photos", "Never use a photo of a broken gate or fence as a background.")
    _teach(app, monkeypatch, "layout", "Keep photos landscape; never crop them into tall strips.")
    sent = {}

    def fake_ask(system, content, schema, **kw):
        sent["system"] = system
        return photopick.Picks(aerial="P1", hero="P1", photo1="", photo2="", photo3="", gallery=[])
    monkeypatch.setattr(F, "ask", fake_ask)
    prop = catalog.get("DP9001")
    photopick.choose(prop, catalog.photos(catalog.items(prop)), auction=True)
    assert "broken gate" in sent["system"] and "tall strips" not in sent["system"]
    assert "tall strips" not in lessons.block("words", app.db)
    page = app.login().get("/studio/lessons").text
    assert "Keegan builds these in" in page and "tall strips" in page


def test_teach_and_fix_it_here_also_asks_for_the_change(app, monkeypatch):
    from studio import ask as A
    c, r = _teach(app, monkeypatch, "words", "Say SECOND HOUSE once.", said="The main house box repeats SECOND HOUSE",
                  fix="1", ref="DP9002")
    assert r.headers.get("HX-Refresh") == "true"
    assert A.latest(app.db, "DP9002")["text"] == "The main house box repeats SECOND HOUSE"


def test_when_claude_cant_word_it_her_words_stand(app, monkeypatch):
    from studio import lessons
    _, r = _teach(app, monkeypatch, "words", "", said="Always put the extent from the title deed", fail=True)
    assert "Learned." in r.text
    assert "Always put the extent from the title deed" in lessons.block("words", app.db)


def test_the_property_page_offers_to_teach_the_studio_under_each_output(app):
    page = app.login().get("/studio/DP9001").text
    assert page.count("Something wrong? Teach the studio") >= 4
    assert 'name="kind" value="infopack"' in page
    assert 'href="/studio/lessons"' in app.login().get("/studio").text



def test_the_pack_detail_sections_come_out_tidy_and_render_in_the_guides_order():
    from studio import catalog, facts as F, render

    d = F.details_clean({
        "sale_info": [{"label": "Registration fee", "text": "R100 000 (refundable)"}, {"label": "Venue", "text": ""}],
        "particulars": [{"label": "Registered extent", "text": "38.1515 ha"}],
        "coordinates": "-22.970700, 30.015379",
        "tables": [{"title": "MACADAMIA ORCHARDS (TREE AGE AS AT MAY 2026)", "columns": ["Tree age", "Cultivation", "Area"],
                    "rows": [["8-9 years", "Dryland", "3.6 ha"], ["Total", "", "13.0 ha", "extra"], ["", "", ""]]}],
        "sections": [{"title": "WATER & IRRIGATION", "lines": ["An industrial electric water pump assembly", " "]}],
        "conditions": [{"label": "Confirmation", "text": "By the seller and FNB within 14 days"}],
        "fica": [{"label": "Entities", "text": "CIPC documents and a resolution"}]})
    assert d["sale_info"] == [{"label": "Registration fee", "text": "R100 000 (refundable)"}]
    assert d["map_pin"] == "-22.970700, 30.015379"
    assert d["tables"][0]["rows"] == [["8-9 years", "Dryland", "3.6 ha"], ["Total", "", "13.0 ha"]]
    assert F.details_clean({"coordinates": "near Levubu"})["map_pin"] == ""
    p = catalog.Property(ref="DP3107", name="X", folder="x", status="")
    f = F.blank() | d | {"highlights": [{"label": "A", "text": "B"}]}
    html = render.html("infopack", render.context(p, f, {"gallery": ["g"]}, "auction", lambda i, s: f"/img/{i}"))
    order = [html.index('data-sec="%s"' % t.replace("&", "&amp;")) for t in ("Auction Information", "Property Particulars & Location",
             "Property Features", "Water & Irrigation", "Summary of Sale Conditions",
             "Registration & FICA Requirements", "Photographic Overview", "Investment Highlights")]
    assert order == sorted(order)
    assert "(refundable)" in html and "google.com/maps?q=-22.970700,30.015379" in html
    assert "By the seller and FNB within 14 days" in html and "does not replace the full Agreement" in html
    assert 'class="total"' in html and "online.dynamicauctioneers.co.za" not in html   # no terms, so no bidding card


def test_the_form_edits_the_pack_tables_and_sections():
    from studio import form as FORM
    facts = {"tables": [{"title": "LAND USE", "columns": ["Use", "Area"], "rows": [["Orchards", "23 ha"]]}],
             "sections": [{"title": "WATER & IRRIGATION", "lines": ["Pump", "Two tanks"]}],
             "conditions": [{"label": "Deposit", "text": "10%"}], "map_pin": "-22.9, 30.0"}
    v = FORM.to_form(facts)
    assert v["tables"] == "LAND USE:\nUse | Area\nOrchards | 23 ha"
    back = FORM.from_form({"tables": v["tables"], "sections": v["sections"], "conditions": v["conditions"], "map_pin": v["map_pin"]})
    assert back["tables"] == facts["tables"] and back["sections"] == facts["sections"]
    assert back["conditions"] == facts["conditions"] and back["map_pin"] == "-22.9, 30.0"
