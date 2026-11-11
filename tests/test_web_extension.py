"""Connecting the extension from the web app, and the Settings Extension card."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunter.web.app import create_app  # noqa: E402
from tests.test_web_app import FakeStore  # noqa: E402
from tests.web_fakes import FakeAccounts, FakeVerifier, NoJobs, app_settings, page_csrf, sign_in  # noqa: E402,E501

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")


@pytest.fixture
def stores():
    return {}


@pytest.fixture
def client(stores):
    app = create_app(store_factory=lambda uid: stores.setdefault(uid, FakeStore()),
                     jobs_store=NoJobs(), verifier=FakeVerifier(), accounts=FakeAccounts(),
                     settings=app_settings())
    return TestClient(app, headers={"User-Agent": UA})


def tokens(stores, uid):
    return stores[uid].tokens() if uid in stores else []


def connect(client):
    token = page_csrf(client, "/extension/connect")
    return client.post("/extension/connect", data={"csrf": token})


def test_connect_page_needs_sign_in(client):
    r = client.get("/extension/connect", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = client.post("/extension/connect", data={}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_connect_creates_token_once_per_post(client, stores):
    sign_in(client)
    page = client.get("/extension/connect").text
    assert "Connect this browser" in page and "data-token" not in page
    assert tokens(stores, 1) == []
    r = connect(client)
    assert r.status_code == 200
    assert 'id="jh-connect"' in r.text and 'data-token="raw-token-1-' in r.text
    [row] = stores[1].tokens()
    assert row.label == "Chrome on Windows"


def test_connect_needs_csrf(client, stores):
    sign_in(client)
    client.get("/extension/connect")
    assert client.post("/extension/connect", data={"csrf": "forged"}).status_code == 403
    assert tokens(stores, 1) == []


def test_connect_page_not_cached(client):
    sign_in(client)
    for r in (client.get("/extension/connect"), connect(client)):
        assert r.headers["Cache-Control"] == "no-store"
        assert r.headers["Referrer-Policy"] == "no-referrer"


def test_settings_lists_connected_browsers(client):
    sign_in(client)
    connect(client)
    page = client.get("/settings").text
    assert 'id="extension"' in page and "Chrome on Windows" in page
    assert 'href="/extension/connect"' in page and "Disconnect" in page


def test_token_not_shown_on_settings(client):
    sign_in(client)
    connect(client)
    assert "raw-token-" not in client.get("/settings").text


def test_disconnect(client, stores):
    sign_in(client)
    connect(client)
    r = client.post("/extension/tokens/1/delete", data={"csrf": page_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings#extension"
    assert stores[1].tokens() == []


def test_disconnect_only_own_token(client, stores):
    other = TestClient(client.app)
    sign_in(other, "someone")
    connect(other)
    sign_in(client)
    r = client.post("/extension/tokens/1/delete", data={"csrf": page_csrf(client)})
    assert r.status_code == 404
    assert len(stores[2].tokens()) == 1


def test_disconnect_needs_csrf(client, stores):
    sign_in(client)
    connect(client)
    assert client.post("/extension/tokens/1/delete", data={"csrf": "x"}).status_code == 403
    assert len(stores[1].tokens()) == 1
