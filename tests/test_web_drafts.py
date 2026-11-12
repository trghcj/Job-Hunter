"""The cover letter and resume tips page, with fake stores and a fake AI client."""

from dataclasses import replace

import pytest
from markupsafe import escape

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunter.drafts import FAILED, LIMIT_REACHED, NO_PROFILE, DraftError, JobNotFound  # noqa: E402,E501
from jobhunter.web.app import create_app  # noqa: E402
from tests.test_web_app import FakeStore  # noqa: E402
from tests.test_web_jobs import FakeJobs  # noqa: E402
from tests.web_fakes import FakeAccounts, FakeVerifier, app_settings, page_csrf, sign_in  # noqa: E402,E501

REMINDER = "AI can make mistakes. Check every fact before you send it."
AI = object()


@pytest.fixture
def store():
    s = FakeStore()
    s.profile = replace(s.profile, text="Python, FastAPI")
    return s


def make_client(store, draft_client=lambda: AI):
    app = create_app(store_factory=lambda uid: store, jobs_store=FakeJobs(),
                     verifier=FakeVerifier(), accounts=FakeAccounts(), settings=app_settings(),
                     draft_client=draft_client)
    return TestClient(app)


@pytest.fixture
def client(store):
    return make_client(store)


def write(client, job_id=1, follow=False):
    return client.post(f"/jobs/{job_id}/draft", data={"csrf": page_csrf(client, "/")},
                       follow_redirects=follow)


def test_jobs_card_has_draft_link(client):
    sign_in(client)
    assert 'href="/jobs/1/draft"' in client.get("/").text


def test_page_without_profile(client, store):
    store.profile = replace(store.profile, text="")
    sign_in(client)
    page = client.get("/jobs/1/draft").text
    assert NO_PROFILE in page and 'href="/settings#profile"' in page
    assert 'action="/jobs/1/draft"' not in page


def test_page_without_draft(client):
    sign_in(client)
    page = client.get("/jobs/1/draft").text
    assert 'action="/jobs/1/draft"' in page and "Write draft" in page
    assert 'name="csrf"' in page and REMINDER in page and "Backend Engineer" in page


def test_write_then_page_shows_draft(client, store):
    sign_in(client)
    r = write(client)
    assert r.status_code == 303 and r.headers["location"] == "/jobs/1/draft"
    assert store.draft_calls == [(1, AI)]
    page = client.get("/jobs/1/draft").text
    assert "<textarea" in page and "Tip one" in page and REMINDER in page
    for word in ("Save edits", "Copy", "Redo"):
        assert word in page
    assert "13 Nov 2026, 11:30 AM IST" in page


def test_letter_html_escaped(client):
    sign_in(client)
    write(client)
    page = client.get("/jobs/1/draft").text
    assert "Dear &lt;b&gt;team&lt;/b&gt;" in page and "<b>team</b>" not in page


def test_redo_button_has_confirm(client):
    sign_in(client)
    write(client)
    page = client.get("/jobs/1/draft").text
    assert "data-confirm=" in page and '<script src="/static/draft.js"' in page


@pytest.mark.parametrize("message", [LIMIT_REACHED, FAILED])
def test_draft_error_shown(client, store, message):
    sign_in(client)
    store.draft_error = DraftError(message)
    r = write(client)
    assert r.status_code == 200 and escape(message) in r.text


def test_no_client_shows_error_without_counting(store):
    client = make_client(store, draft_client=lambda: None)
    sign_in(client)
    r = write(client)
    assert r.status_code == 200 and escape(FAILED) in r.text and store.draft_calls == []


def test_save_letter(client, store):
    sign_in(client)
    csrf = page_csrf(client, "/")
    r = client.post("/jobs/1/draft/letter", data={"csrf": csrf, "letter": "Edited"},
                    follow_redirects=False)
    assert r.status_code == 404
    write(client)
    r = client.post("/jobs/1/draft/letter", data={"csrf": csrf, "letter": "Edited"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/jobs/1/draft"
    assert store.letters == [(1, "Edited")]


def test_unknown_job_404(client, store):
    sign_in(client)
    assert client.get("/jobs/99/draft").status_code == 404
    assert write(client, 99).status_code == 404
    store.draft_error = JobNotFound("Job not found.")
    assert write(client, 1).status_code == 404


def test_draft_routes_need_csrf_and_sign_in(client, store):
    r = client.get("/jobs/1/draft", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    routes = (("/jobs/1/draft", {}), ("/jobs/1/draft/letter", {"letter": "x"}))
    for path, data in routes:
        r = client.post(path, data={"csrf": "x", **data}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
    sign_in(client)
    for path, data in routes:
        assert client.post(path, data={"csrf": "wrong", **data}).status_code == 403
    assert store.draft_calls == [] and store.letters == []
