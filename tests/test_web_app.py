import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunter.mail_account import MailView  # noqa: E402
from jobhunter.web.app import create_app  # noqa: E402
from jobhunter.web.forms import ProfileForm  # noqa: E402
from jobhunter.web.store import RunRow  # noqa: E402
from tests.web_fakes import (  # noqa: E402
    FakeAccounts,
    FakeVerifier,
    NoJobs,
    app_settings,
    page_csrf,
    sign_in,
)


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

    mail = MailView()
    saved_password = None
    test_result = "Connected. 3 job-alert emails are waiting to be read."

    def load_mail(self):
        return self.mail

    def save_mail(self, address, app_password, senders):
        if app_password is None and not self.mail.has_password:
            raise ValueError("Enter the Gmail app password the first time you save.")
        self.saved_password = app_password or self.saved_password
        self.mail = MailView(address, True, tuple(senders))

    def delete_mail(self):
        self.mail = MailView()

    # ---- extension tokens
    def tokens(self):
        return list(self._tokens.values()) if hasattr(self, "_tokens") else []

    def create_token(self, label):
        from datetime import UTC, datetime

        from jobhunter.extension_tokens import TokenRow

        self._tokens = getattr(self, "_tokens", {})
        token_id = len(self._tokens) + 1
        self._tokens[token_id] = TokenRow(token_id, label, datetime(2026, 11, 11, tzinfo=UTC),
                                          None)
        return f"raw-token-{token_id}-" + "x" * 30

    def revoke_token(self, token_id):
        return getattr(self, "_tokens", {}).pop(token_id, None) is not None

    def test_mail(self):
        return self.test_result


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def client(store):
    app = create_app(store_factory=lambda uid: store, jobs_store=NoJobs(), verifier=FakeVerifier(),
                     accounts=FakeAccounts(), settings=app_settings())
    return TestClient(app)


def login(client) -> str:
    assert sign_in(client).status_code == 200
    return page_csrf(client)


def valid_form(token: str, **overrides) -> dict:
    data = {
        "csrf": token, "titles": "Python Developer\nAI Engineer", "cities": "Pune",
        "work_modes": ["remote", "hybrid"], "min_years": "0", "max_years": "2",
        "exclude_words": "unpaid", "digest_to": "me@example.com", "max_jobs": "20",
        "max_age_days": "10",
    }
    data.update(overrides)
    return data


def test_login_shows_current_settings_and_runs(client):
    login(client)
    page = client.get("/settings").text
    assert "Backend Engineer" in page
    assert "me@example.com" in page
    assert "failed" in page and "HTTP 503" in page


def test_save_settings(client, store):
    token = login(client)
    r = client.post("/settings", data=valid_form(token), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings?saved=1"
    assert store.saves == 1
    assert store.form.titles == ["Python Developer", "AI Engineer"]
    assert store.form.work_modes == ["hybrid", "remote"]
    assert store.form.max_years == 2
    assert "Saved." in client.get("/settings?saved=1").text


def test_invalid_settings_not_saved_and_shown_back(client, store):
    token = login(client)
    r = client.post("/settings", data=valid_form(token, min_years="5", max_years="1"))
    assert r.status_code == 400
    assert "Minimum years cannot be more than maximum years." in r.text
    assert "Python Developer" in r.text  # what you typed is kept
    assert store.saves == 0


def test_gmail_save_never_shows_the_password(client, store):
    token = login(client)
    r = client.post("/gmail", data={"csrf": token, "gmail_address": "me@gmail.com",
                                    "app_password": "abcd efgh ijkl mnop",
                                    "senders": "naukri.com"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/settings?gmail=saved")
    assert store.saved_password == "abcdefghijklmnop"
    page = client.get("/settings?gmail=saved").text
    assert "Gmail saved" in page and "me@gmail.com" in page
    assert "abcdefghijklmnop" not in page and "abcd efgh" not in page
    assert "Leave blank to keep it" in page and "Test connection" in page


def test_gmail_first_save_needs_password(client, store):
    token = login(client)
    r = client.post("/gmail", data={"csrf": token, "gmail_address": "me@gmail.com"})
    assert r.status_code == 400 and "first time" in r.text


def test_gmail_bad_app_password_rejected(client, store):
    token = login(client)
    r = client.post("/gmail", data={"csrf": token, "gmail_address": "me@gmail.com",
                                    "app_password": "my password 123"})
    assert r.status_code == 400 and "16 letters" in r.text
    assert store.saved_password is None


def test_gmail_test_connection_and_remove(client, store):
    store.mail = MailView("me@gmail.com", True)
    token = login(client)
    r = client.post("/gmail/test", data={"csrf": token})
    assert "3 job-alert emails are waiting" in r.text
    client.post("/gmail/delete", data={"csrf": token})
    assert store.mail.has_password is False


def test_gmail_routes_need_login_and_csrf(client):
    r = client.post("/gmail/test", data={}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    login(client)
    assert client.post("/gmail/test", data={"csrf": "forged"}).status_code == 403


def test_save_without_csrf_rejected(client, store):
    login(client)
    r = client.post("/settings", data=valid_form("forged"))
    assert r.status_code == 403 and store.saves == 0


def test_titles_are_escaped(client, store):
    store.form.titles = ["<script>alert(1)</script>"]
    login(client)
    page = client.get("/settings").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_settings_page_has_no_shared_sections(client):
    login(client)
    page = client.get("/settings").text
    assert "Company watchlist" not in page and 'name="sources"' not in page


def test_two_users_see_their_own_settings():
    stores = {}

    def factory(uid):
        return stores.setdefault(uid, FakeStore())

    app = create_app(store_factory=factory, jobs_store=NoJobs(), verifier=FakeVerifier(),
                     accounts=FakeAccounts(), settings=app_settings())
    alice, bob = TestClient(app), TestClient(app)
    sign_in(alice, "alice")
    sign_in(bob, "bob")
    token = page_csrf(alice)
    alice.post("/settings", data=valid_form(token, titles="Data Engineer"))
    assert "Data Engineer" in alice.get("/settings").text
    assert "Data Engineer" not in bob.get("/settings").text
    assert len(stores) == 2


def test_healthz_needs_no_login(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_save_requires_sign_in(client, store):
    r = client.post("/settings", data={"titles": "x"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login" and store.saves == 0


def test_security_headers(client):
    r = client.get("/login")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
