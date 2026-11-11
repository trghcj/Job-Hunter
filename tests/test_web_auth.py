"""Google sign-in (Firebase) for the web app, with a fake verifier and accounts."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunter.web.app import create_app  # noqa: E402
from tests.test_web_app import FakeStore  # noqa: E402
from tests.web_fakes import (  # noqa: E402
    FakeAccounts,
    FakeVerifier,
    NoJobs,
    app_settings,
    csrf_from,
    page_csrf,
    sign_in,
)


@pytest.fixture
def accounts():
    return FakeAccounts()


def make_client(accounts, **settings_kw):
    store = FakeStore()
    app = create_app(store_factory=lambda uid: store, jobs_store=NoJobs(),
                     verifier=FakeVerifier(),
                     accounts=accounts, settings=app_settings(**settings_kw))
    return TestClient(app)


@pytest.fixture
def client(accounts):
    return make_client(accounts)


def test_login_page_has_firebase_config(client):
    page = client.get("/login").text
    assert 'data-api-key="AIza-test"' in page and 'data-project-id="job-hunter-test"' in page
    assert 'data-auth-domain="test.firebaseapp.com"' in page and "Continue with Google" in page
    assert '<script type="module" src="/static/login.js">' in page


def test_login_page_when_not_configured(accounts, caplog):
    page = make_client(accounts, firebase_api_key="").get("/login").text
    assert "Sign-in is not set up" in page and "data-api-key" not in page
    assert "FIREBASE_API_KEY" in caplog.text


def test_login_js_served(client):
    r = client.get("/static/login.js")
    assert r.status_code == 200 and "signInWithPopup" in r.text
    assert "https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js" in r.text


def test_session_created_from_token(client):
    r = sign_in(client, "owner")
    assert r.status_code == 200 and r.json() == {"next": "/"}
    assert client.get("/settings").status_code == 200


def test_bad_token_401_and_no_session(client):
    token = csrf_from(client.get("/login").text)
    r = client.post("/auth/session", json={"idToken": "forged", "csrf": token})
    assert r.status_code == 401 and r.json()["error"] == "Sign-in failed, please try again."
    assert client.get("/settings", follow_redirects=False).status_code == 303


def test_session_requires_csrf(client):
    client.get("/login")
    r = client.post("/auth/session", json={"idToken": "good:owner", "csrf": "forged"})
    assert r.status_code == 403
    assert client.get("/settings", follow_redirects=False).status_code == 303


def test_session_rejects_non_json(client):
    client.get("/login")
    assert client.post("/auth/session", content=b"nope").status_code == 400


def test_blocked_outcome_goes_to_blocked(client, accounts):
    accounts.outcome = "blocked"
    assert sign_in(client, "someone").json() == {"next": "/blocked"}
    assert "blocked" in client.get("/blocked").text.lower()


def test_full_outcome_goes_to_full(client, accounts):
    accounts.outcome = "full"
    assert sign_in(client, "someone").json() == {"next": "/full"}
    assert "full" in client.get("/full").text.lower()


def test_blocked_with_existing_session_is_logged_out(client, accounts):
    sign_in(client, "someone")
    accounts.block(accounts.by_uid["someone"])
    r = client.get("/settings", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/blocked"
    assert client.get("/settings", follow_redirects=False).headers["location"] == "/login"


def test_deleted_user_session_is_dropped(client, accounts):
    sign_in(client, "someone")
    accounts.users.clear()
    r = client.get("/settings", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/", "/settings"])
def test_pages_require_sign_in(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_signed_in_user_skips_login_page(client):
    sign_in(client)
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_logout_clears_session(client):
    sign_in(client)
    client.post("/logout", data={"csrf": page_csrf(client)})
    assert client.get("/settings", follow_redirects=False).status_code == 303


def test_login_csp_allows_firebase_only_on_login(client):
    login_csp = client.get("/login").headers["Content-Security-Policy"]
    assert "https://www.gstatic.com" in login_csp
    assert "frame-src https://test.firebaseapp.com https://accounts.google.com" in login_csp
    assert "https://identitytoolkit.googleapis.com" in login_csp
    sign_in(client)
    settings_csp = client.get("/settings").headers["Content-Security-Policy"]
    assert "gstatic" not in settings_csp and "frame-ancestors 'none'" in settings_csp


def test_no_web_password_needed(accounts):
    make_client(accounts)  # starts with only SESSION_SECRET and Firebase settings


def test_refuses_to_start_without_session_secret(accounts):
    with pytest.raises(RuntimeError):
        make_client(accounts, session_secret="short")


def test_session_cookie_lasts_30_days(client):
    r = sign_in(client)
    cookie = r.headers["set-cookie"]
    assert "Max-Age=2592000" in cookie and "HttpOnly" in cookie.replace("httponly", "HttpOnly")
