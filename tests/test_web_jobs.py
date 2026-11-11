"""My jobs page routes, with fake stores."""

from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunter.web.app import create_app  # noqa: E402
from jobhunter.web.jobs_store import JobCard  # noqa: E402
from tests.test_web_app import FakeStore  # noqa: E402
from tests.web_fakes import FakeAccounts, FakeVerifier, app_settings, page_csrf, sign_in  # noqa: E402,E501


def card(job_id, title="Backend Engineer", status="new", url="https://jobs.example.com/1"):
    return JobCard(job_id, title, "Acme", "Pune", "hybrid", "adzuna",
                   datetime(2026, 11, 10, tzinfo=UTC), url, status, 1.0)


class FakeJobs:
    def __init__(self):
        self.cards = {"matches": [card(1), card(2, "AI Engineer")], "saved": [], "applied": []}
        self.changes = []
        self.asked = []

    def list(self, user_id, tab, limit=100):
        self.asked.append((user_id, tab))
        return self.cards[tab]

    def set_status(self, user_id, job_id, status):
        if job_id not in (1, 2) or status not in ("new", "saved", "applied", "hidden"):
            return False
        self.changes.append((user_id, job_id, status))
        return True


@pytest.fixture
def jobs():
    return FakeJobs()


@pytest.fixture
def client(jobs):
    app = create_app(store_factory=lambda uid: FakeStore(), jobs_store=jobs,
                     verifier=FakeVerifier(), accounts=FakeAccounts(), settings=app_settings())
    return TestClient(app)


def test_my_jobs_lists_matches(client, jobs):
    sign_in(client)
    page = client.get("/").text
    assert "Backend Engineer" in page and "AI Engineer" in page and "Acme" in page
    assert jobs.asked[-1] == (1, "matches")


def test_tabs(client, jobs):
    sign_in(client)
    client.get("/?tab=saved")
    assert jobs.asked[-1] == (1, "saved")
    client.get("/?tab=bogus")
    assert jobs.asked[-1] == (1, "matches")


def test_set_status(client, jobs):
    sign_in(client)
    token = page_csrf(client, "/")
    r = client.post("/jobs/2/status", data={"csrf": token, "status": "saved", "tab": "matches"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?tab=matches"
    assert jobs.changes == [(1, 2, "saved")]


def test_status_on_someone_elses_private_job_is_404(client, jobs):
    sign_in(client)
    token = page_csrf(client, "/")
    r = client.post("/jobs/77/status", data={"csrf": token, "status": "saved"})
    assert r.status_code == 404 and jobs.changes == []


def test_invalid_status_is_404(client, jobs):
    sign_in(client)
    token = page_csrf(client, "/")
    assert client.post("/jobs/1/status", data={"csrf": token, "status": "x"}).status_code == 404


def test_status_needs_csrf_and_sign_in(client, jobs):
    r = client.post("/jobs/1/status", data={"status": "saved"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    sign_in(client)
    assert client.post("/jobs/1/status", data={"csrf": "forged", "status": "saved"}
                       ).status_code == 403
    assert jobs.changes == []


def test_my_jobs_empty_state(client, jobs):
    jobs.cards["matches"] = []
    sign_in(client)
    page = client.get("/").text
    assert "Add the job titles you want on Settings" in page and 'href="/settings"' in page


def test_job_links_open_in_new_tab_with_noopener(client):
    sign_in(client)
    page = client.get("/").text
    assert 'href="https://jobs.example.com/1" target="_blank" rel="noopener noreferrer"' in page


def test_non_http_job_links_not_rendered_as_links(client, jobs):
    jobs.cards["matches"] = [card(1, url="javascript:alert(1)")]
    sign_in(client)
    assert 'href="javascript:' not in client.get("/").text
