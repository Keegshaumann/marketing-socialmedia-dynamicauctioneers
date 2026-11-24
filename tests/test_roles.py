"""The properties role (D104): each role opens only its own screens. Offline, key-free.

A properties login builds auction proposals, which show sellers' names and ID
numbers, and cannot open the Ad Studio. Marketing and approver logins use the
Ad Studio (D127) and cannot open proposals. Admin opens everything.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from scripts import create_user

PASSWORD = "role-test-pass-123"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "engine.db"))
    monkeypatch.setenv("APP_SECRET", "test-secret-roles")
    monkeypatch.setenv("ENGINE_ALLOW_INSECURE_COOKIE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # No director mirror: the studio list says so instead of failing.
    monkeypatch.setenv("DIRECTOR_DB", str(tmp_path / "no-director.db"))

    from webapp import auth, models
    from webapp.main import create_app

    db = models.init_db()
    models.set_setting(db, "output_root", str(tmp_path / "out"))
    app = create_app()

    def login(role: str):
        email = f"{role}@dynamicauctioneers.co.za"
        if models.get_user(db, email) is None:
            models.create_user(db, email=email, pw_hash=auth.hash_password(PASSWORD), role=role)
        client = TestClient(app)
        resp = client.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=False)
        assert resp.status_code == 303, resp.text
        return client, resp.headers["location"]

    return SimpleNamespace(db=db, login=login)


def _location(client, path):
    resp = client.get(path, follow_redirects=False)
    return resp.status_code, resp.headers.get("location")


def test_a_properties_login_lands_on_proposals_and_stays_there(env):
    client, landing = env.login("properties")
    assert landing == "/proposals"
    assert _location(client, "/") == (303, "/proposals")
    assert _location(client, "/login") == (303, "/proposals")
    assert client.get("/proposals").status_code == 200


def test_a_properties_login_cannot_open_the_studio(env):
    client, _ = env.login("properties")
    for path in ("/studio", "/studio/DP3107", "/studio/DP3107/preview/ad", "/studio/DP3107/download"):
        assert client.get(path, follow_redirects=False).status_code == 403, path
    assert client.post("/studio/DP3107/save", data={"headline": "x"}).status_code == 403
    assert client.post("/studio/DP3107/read").status_code == 403


def test_the_old_marketing_pipeline_is_gone(env):
    client, _ = env.login("admin")
    for path in ("/board", "/intake", "/gates/9001/ads", "/artifacts/9001", "/post/9001", "/settings"):
        assert client.get(path, follow_redirects=False).status_code == 404, path


def test_the_navigation_shows_each_role_its_own_screens(env):
    properties, _ = env.login("properties")
    page = properties.get("/proposals").text
    assert 'href="/proposals"' in page and 'href="/studio"' not in page

    marketing, _ = env.login("marketing")
    studio = marketing.get("/studio").text
    assert 'href="/studio"' in studio and 'href="/proposals"' not in studio


@pytest.mark.parametrize("role", ["marketing", "approver"])
def test_studio_logins_cannot_open_proposals(env, role):
    client, landing = env.login(role)
    assert landing == "/studio"
    assert client.get("/proposals", follow_redirects=False).status_code == 403
    assert client.post("/proposals/new", data={"dp": "9001"}, follow_redirects=False).status_code == 403


def test_admin_opens_both(env):
    client, landing = env.login("admin")
    assert landing == "/studio"
    assert client.get("/studio").status_code == 200
    page = client.get("/proposals").text
    assert 'href="/proposals"' in page and 'href="/studio"' in page


# --- the account script ---------------------------------------------------------------

def test_create_user_makes_a_working_login_and_prints_the_password_once(tmp_path, capsys):
    from webapp import auth, models

    db = str(tmp_path / "engine.db")
    assert create_user.main(["Someone@DynamicAuctioneers.co.za", "properties", "--db", db]) == 0
    out = capsys.readouterr().out
    password = next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("Password: "))

    user = models.get_user(db, "someone@dynamicauctioneers.co.za")
    assert user["role"] == "properties"
    assert auth.verify_password(password, user["pw_hash"])
    assert password not in open(db, "rb").read().decode("latin-1")  # only the hash is stored
    assert len(password) >= 20


def test_create_user_refuses_an_existing_account_unless_reset(tmp_path, capsys):
    from webapp import auth, models

    db = str(tmp_path / "engine.db")
    create_user.main(["someone@dynamicauctioneers.co.za", "properties", "--db", db])
    first = capsys.readouterr().out
    assert create_user.main(["someone@dynamicauctioneers.co.za", "properties", "--db", db]) == 1

    assert create_user.main(["someone@dynamicauctioneers.co.za", "marketing", "--reset", "--db", db]) == 0
    second = capsys.readouterr().out
    new_password = next(line.split(": ", 1)[1] for line in second.splitlines() if line.startswith("Password: "))
    old_password = next(line.split(": ", 1)[1] for line in first.splitlines() if line.startswith("Password: "))
    user = models.get_user(db, "someone@dynamicauctioneers.co.za")
    assert auth.verify_password(new_password, user["pw_hash"])
    assert not auth.verify_password(old_password, user["pw_hash"])
    assert user["role"] == "properties"  # a reset never changes the role


def test_create_user_rejects_an_unknown_role(tmp_path):
    with pytest.raises(SystemExit):
        create_user.main(["someone@dynamicauctioneers.co.za", "superuser", "--db", str(tmp_path / "engine.db")])
