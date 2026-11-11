"""Owner-only Admin page: users, blocking, sources and watchlist (fake stores)."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunter.accounts import UserRow  # noqa: E402
from jobhunter.sources.catalog import SOURCE_LABELS  # noqa: E402
from jobhunter.sources.watchlist import Company  # noqa: E402
from jobhunter.web.app import create_app  # noqa: E402
from tests.test_web_app import FakeStore  # noqa: E402
from tests.web_fakes import (  # noqa: E402
    FakeAccounts,
    FakeVerifier,
    NoJobs,
    app_settings,
    page_csrf,
    sign_in,
)

WHEN = datetime(2026, 11, 11, tzinfo=UTC)


class FakeAdmin:
    def __init__(self, accounts: FakeAccounts):
        self.accounts = accounts
        self.sources = dict.fromkeys(SOURCE_LABELS, True)
        self.watchlist = [Company("Groww", "greenhouse", "groww")]
        self.saves = 0

    def load_sources(self):
        return dict(self.sources)

    def load_watchlist(self):
        return list(self.watchlist)

    def save_sources_and_watchlist(self, enabled, watchlist):
        self.sources, self.watchlist = enabled, watchlist
        self.saves += 1

    def users(self):
        return [UserRow(u.id, u.email, u.name, u.is_owner, u.blocked, WHEN, WHEN, 3, False)
                for u in self.accounts.users.values()]

    def set_blocked(self, user_id, blocked):
        if self.accounts.users[user_id].is_owner:
            raise ValueError("You can't block the owner.")
        if blocked:
            self.accounts.block(user_id)


@pytest.fixture
def accounts():
    return FakeAccounts(owner_uid="owner")


@pytest.fixture
def admin(accounts):
    return FakeAdmin(accounts)


@pytest.fixture
def client(accounts, admin):
    app = create_app(store_factory=lambda uid: FakeStore(), jobs_store=NoJobs(),
                     admin_store=admin, verifier=FakeVerifier(), accounts=accounts,
                     settings=app_settings())
    return TestClient(app)


def test_admin_requires_sign_in(client):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_admin_hidden_from_non_owner(client, admin):
    sign_in(client, "someone")
    assert client.get("/admin").status_code == 404
    token = page_csrf(client)
    assert client.post("/admin/sources", data={"csrf": token}).status_code == 404
    assert client.post("/admin/users/1/block", data={"csrf": token, "blocked": "1"}
                       ).status_code == 404
    assert admin.saves == 0


def test_owner_sees_users_and_sources(client, accounts):
    sign_in(client, "owner")
    other = TestClient(client.app)
    sign_in(other, "someone")
    page = client.get("/admin").text
    assert "owner@gmail.com" in page and "someone@gmail.com" in page
    assert "Company watchlist" in page and "Groww | greenhouse | groww" in page


def test_owner_blocks_user(client, accounts):
    other = TestClient(client.app)
    sign_in(other, "someone")
    sign_in(client, "owner")
    uid = accounts.by_uid["someone"]
    r = client.post(f"/admin/users/{uid}/block", data={"csrf": page_csrf(client, "/admin"),
                                                       "blocked": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert accounts.users[uid].blocked
    assert other.get("/", follow_redirects=False).headers["location"] == "/blocked"


def test_owner_cannot_block_self(client):
    sign_in(client, "owner")
    r = client.post("/admin/users/1/block", data={"csrf": page_csrf(client, "/admin"),
                                                  "blocked": "1"})
    assert r.status_code == 400 and "You can&#39;t block the owner." in r.text


def test_admin_saves_sources_and_watchlist(client, admin):
    sign_in(client, "owner")
    data = {"csrf": page_csrf(client, "/admin"), "sources": ["greenhouse", "remotive"],
            "watchlist": "Groww | greenhouse | groww\nCRED | lever | cred"}
    r = client.post("/admin/sources", data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin?saved=1"
    assert admin.sources["greenhouse"] and not admin.sources["adzuna"]
    assert [c.slug for c in admin.watchlist] == ["groww", "cred"]
    assert "2 companies" in client.get("/admin?saved=1").text


def test_bad_watchlist_keeps_what_you_typed(client, admin):
    sign_in(client, "owner")
    data = {"csrf": page_csrf(client, "/admin"), "sources": ["lever"],
            "watchlist": "Groww | workday | groww"}
    r = client.post("/admin/sources", data=data)
    assert r.status_code == 400 and "Watchlist line 1" in r.text
    assert "Groww | workday | groww" in r.text and admin.saves == 0


def test_admin_needs_csrf(client, admin):
    sign_in(client, "owner")
    assert client.post("/admin/sources", data={"csrf": "forged"}).status_code == 403
    assert admin.saves == 0


def test_admin_email_gets_admin_page(client, accounts, admin):
    sign_in(client, "owner")  # the owner's account exists
    sign_in(client, "friend")
    uid = accounts.by_uid["friend"]
    accounts.users[uid] = replace(accounts.users[uid], is_admin=True)
    assert 'href="/admin"' in client.get("/settings").text
    assert client.get("/admin").status_code == 200
    r = client.post("/admin/users/1/block", data={"csrf": page_csrf(client, "/admin"),
                                                   "blocked": "1"}, follow_redirects=False)
    assert not accounts.users[1].blocked and r.status_code in (303, 400)
