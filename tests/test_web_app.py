import re

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunter.settings import Settings  # noqa: E402
from jobhunter.web.app import create_app  # noqa: E402
from jobhunter.web.forms import ProfileForm  # noqa: E402
from jobhunter.web.store import RunRow  # noqa: E402

PASSWORD = "correct horse battery staple"


class FakeStore:
    def __init__(self):
        self.form = ProfileForm(
            titles=["Backend Engineer"], cities=[], work_modes=["onsite", "hybrid", "remote"],
            min_years=0, max_years=1, exclude_words=["unpaid"], digest_to=["me@example.com"],
            max_jobs=25, max_age_days=14,
        )
        self.saves = 0

    def load(self):
        return self.form

    def save(self, form):
        self.form = form
        self.saves += 1

    def recent_runs(self, limit=8):
        return [RunRow("adzuna", None, False, 50, 0, "AdzunaError: HTTP 503")]


def settings(**kw) -> Settings:
    base = dict(web_password=PASSWORD, session_secret="s" * 40, web_https_only=False)
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def client(store):
    app = create_app(store=store, settings=settings(), sleep=lambda s: None)
    return TestClient(app)


def csrf_from(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(client) -> str:
    token = csrf_from(client.get("/login").text)
    r = client.post("/login", data={"csrf": token, "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    return csrf_from(client.get("/").text)


def valid_form(token: str, **overrides) -> dict:
    data = {
        "csrf": token, "titles": "Python Developer\nAI Engineer", "cities": "Pune",
        "work_modes": ["remote", "hybrid"], "min_years": "0", "max_years": "2",
        "exclude_words": "unpaid", "digest_to": "me@example.com", "max_jobs": "20",
        "max_age_days": "10",
    }
    data.update(overrides)
    return data


def test_refuses_to_start_without_secrets(store):
    with pytest.raises(RuntimeError):
        create_app(store=store, settings=settings(web_password=""))
    with pytest.raises(RuntimeError):
        create_app(store=store, settings=settings(session_secret="short"))


def test_healthz_needs_no_login(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_settings_require_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = client.post("/", data={"titles": "x"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_wrong_password_rejected(client):
    token = csrf_from(client.get("/login").text)
    r = client.post("/login", data={"csrf": token, "password": "nope"})
    assert r.status_code == 401 and "Wrong password" in r.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_without_csrf_rejected(client):
    client.get("/login")
    r = client.post("/login", data={"password": PASSWORD})
    assert r.status_code == 403


def test_login_shows_current_settings_and_runs(client):
    login(client)
    page = client.get("/").text
    assert "Backend Engineer" in page
    assert "me@example.com" in page
    assert "failed" in page and "HTTP 503" in page


def test_save_settings(client, store):
    token = login(client)
    r = client.post("/", data=valid_form(token), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?saved=1"
    assert store.saves == 1
    assert store.form.titles == ["Python Developer", "AI Engineer"]
    assert store.form.work_modes == ["hybrid", "remote"]
    assert store.form.max_years == 2
    assert "Saved." in client.get("/?saved=1").text


def test_invalid_settings_not_saved_and_shown_back(client, store):
    token = login(client)
    r = client.post("/", data=valid_form(token, min_years="5", max_years="1"))
    assert r.status_code == 400
    assert "Minimum years cannot be more than maximum years." in r.text
    assert "Python Developer" in r.text  # what you typed is kept
    assert store.saves == 0


def test_save_sources_and_watchlist(client, store):
    token = login(client)
    data = valid_form(token, sources_section="1", sources=["greenhouse", "remotive"],
                      watchlist="Groww | greenhouse | groww\nCRED | lever | cred")
    r = client.post("/", data=data, follow_redirects=False)
    assert r.status_code == 303
    assert store.form.sources["greenhouse"] and store.form.sources["remotive"]
    assert not store.form.sources["adzuna"]
    assert [c.slug for c in store.form.watchlist] == ["groww", "cred"]
    page = client.get("/").text
    assert "CRED | lever | cred" in page and "2 companies" in page


def test_bad_watchlist_keeps_what_you_typed(client, store):
    token = login(client)
    data = valid_form(token, sources_section="1", sources=["lever"],
                      watchlist="Groww | workday | groww")
    r = client.post("/", data=data)
    assert r.status_code == 400 and "Watchlist line 1" in r.text
    assert "Groww | workday | groww" in r.text and store.saves == 0


def test_save_without_csrf_rejected(client, store):
    login(client)
    r = client.post("/", data=valid_form("forged"))
    assert r.status_code == 403 and store.saves == 0


def test_titles_are_escaped(client, store):
    store.form.titles = ["<script>alert(1)</script>"]
    login(client)
    page = client.get("/").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_logout(client):
    token = login(client)
    client.post("/logout", data={"csrf": token})
    assert client.get("/", follow_redirects=False).status_code == 303


def test_security_headers(client):
    r = client.get("/login")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
