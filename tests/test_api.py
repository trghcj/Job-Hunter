"""/api/v1 for the extension: bearer auth, input checks, rate limit (fake gateway)."""

from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunter.web.api import CheckResult, RateLimiter, Seen  # noqa: E402
from jobhunter.web.app import create_app  # noqa: E402
from jobhunter.web.jobs_store import JobCard  # noqa: E402
from tests.test_web_app import FakeStore  # noqa: E402
from tests.web_fakes import FakeAccounts, FakeVerifier, NoJobs, app_settings, sign_in  # noqa: E402,E501

TOKEN = "t" * 43
JOB = {"url": "https://www.linkedin.com/jobs/view/123/", "title": "Backend Engineer",
       "company": "Acme", "location": "Pune", "description": "0-1 years"}


class FakeApi:
    def __init__(self):
        self.saved = []
        self.checked = []

    def user_for_token(self, raw):
        return 7 if raw == TOKEN else None

    def check(self, user_id, info):
        self.checked.append((user_id, info.title))
        if "Senior" in info.title:
            return CheckResult("no_match", ["senior title (senior)"], Seen(None, False, False))
        return CheckResult("match", [], Seen("saved", True, True))

    def save(self, user_id, req):
        self.saved.append((user_id, req.title, req.status))
        return 42

    def matches(self, user_id, limit):
        card = JobCard(1, "AI Engineer", "Uplers", "India (Remote)", "remote", "adzuna",
                       datetime(2026, 11, 10, tzinfo=UTC), "https://x.example/1", "new", 1.0)
        return [card] * limit

    def me(self, user_id):
        return {"email": "u@gmail.com", "name": "U"}


@pytest.fixture
def api():
    return FakeApi()


def make_client(api, limiter=None):
    app = create_app(store_factory=lambda uid: FakeStore(), jobs_store=NoJobs(), api=api,
                     api_limiter=limiter or RateLimiter(), verifier=FakeVerifier(),
                     accounts=FakeAccounts(), settings=app_settings())
    return TestClient(app)


@pytest.fixture
def client(api):
    return make_client(api)


AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": TOKEN},
                                     {"Authorization": "Basic abc"},
                                     {"Authorization": "Bearer wrong"}])
def test_check_needs_bearer(client, api, headers):
    assert client.post("/api/v1/check", json=JOB, headers=headers).status_code == 401
    assert api.checked == []


def test_check_match(client, api):
    r = client.post("/api/v1/check", json=JOB, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"verdict": "match", "reasons": [],
                        "seen": {"status": "saved", "emailed": True, "listed": True}}
    assert api.checked == [(7, "Backend Engineer")]


def test_check_no_match_reason(client):
    r = client.post("/api/v1/check", json={**JOB, "title": "Senior Backend Engineer"},
                    headers=AUTH)
    assert r.json()["verdict"] == "no_match" and r.json()["reasons"] == ["senior title (senior)"]


@pytest.mark.parametrize("url", ["http://www.linkedin.com/jobs/view/1/",
                                 "https://evil.example/jobs/view/1/",
                                 "https://www.linkedin.com.evil.example/x",
                                 "javascript:alert(1)",
                                 "https://acme.myworkdayjobs.com.evil.example/x"])
def test_check_rejects_foreign_url(client, api, url):
    assert client.post("/api/v1/check", json={**JOB, "url": url}, headers=AUTH).status_code == 422
    assert api.checked == []


@pytest.mark.parametrize("url", ["https://in.indeed.com/viewjob?jk=1",
                                 "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/1",
                                 "https://jobs.lever.co/cred/1",
                                 "https://www.naukri.com/job-listings-x-123"])
def test_check_accepts_supported_hosts(client, url):
    assert client.post("/api/v1/check", json={**JOB, "url": url}, headers=AUTH).status_code == 200


def test_check_rejects_long_title(client, api):
    r = client.post("/api/v1/check", json={**JOB, "title": "x" * 301}, headers=AUTH)
    assert r.status_code == 422 and api.checked == []


def test_check_rejects_long_description(client):
    r = client.post("/api/v1/check", json={**JOB, "description": "x" * 20001}, headers=AUTH)
    assert r.status_code == 422


def test_check_rejects_empty_title(client):
    assert client.post("/api/v1/check", json={**JOB, "title": ""}, headers=AUTH).status_code == 422


def test_save_sets_status(client, api):
    r = client.post("/api/v1/jobs", json={**JOB, "status": "applied"}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"job_id": 42, "status": "applied"}
    assert api.saved == [(7, "Backend Engineer", "applied")]


def test_save_rejects_bad_status(client, api):
    r = client.post("/api/v1/jobs", json={**JOB, "status": "hidden"}, headers=AUTH)
    assert r.status_code == 422 and api.saved == []


def test_matches(client):
    r = client.get("/api/v1/matches?limit=2", headers=AUTH)
    assert r.status_code == 200 and len(r.json()) == 2
    assert r.json()[0] == {"title": "AI Engineer", "company": "Uplers",
                           "location": "India (Remote)", "url": "https://x.example/1",
                           "posted_at": "2026-11-10T00:00:00+00:00"}
    assert len(client.get("/api/v1/matches", headers=AUTH).json()) == 5


@pytest.mark.parametrize("limit", [0, 21, -1])
def test_matches_limit_bounds(client, limit):
    assert client.get(f"/api/v1/matches?limit={limit}", headers=AUTH).status_code == 422


def test_me(client):
    assert client.get("/api/v1/me", headers=AUTH).json() == {"email": "u@gmail.com", "name": "U"}


def test_rate_limited_after_limit(api):
    client = make_client(api, RateLimiter(limit=2, window=3600))
    codes = [client.get("/api/v1/me", headers=AUTH).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_rate_limiter_window_slides():
    now = [0.0]
    limiter = RateLimiter(limit=1, window=10, clock=lambda: now[0])
    assert limiter.allow("k") and not limiter.allow("k")
    now[0] = 11
    assert limiter.allow("k")


def test_api_ignores_session_cookie(client):
    sign_in(client)
    assert client.get("/api/v1/me").status_code == 401


def test_api_no_store(client):
    assert client.get("/api/v1/me", headers=AUTH).headers["Cache-Control"] == "no-store"
