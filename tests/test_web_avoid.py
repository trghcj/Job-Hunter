"""My jobs "Not interested" with reasons, and the Settings "Hidden and lowered" card."""

from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from jobhunter.avoid import Avoid  # noqa: E402
from jobhunter.db import Job, JobScore  # noqa: E402
from jobhunter.pipeline import run_pipeline  # noqa: E402
from jobhunter.web.actions_store import JobBrief  # noqa: E402
from jobhunter.web.app import create_app  # noqa: E402
from jobhunter.web.store import UserStore  # noqa: E402
from tests.test_pipeline import FakeAdapter, raw  # noqa: E402
from tests.test_pipeline_users import user  # noqa: E402
from tests.test_web_app import FakeStore  # noqa: E402
from tests.test_web_jobs import FakeJobs  # noqa: E402
from tests.web_fakes import FakeAccounts, FakeVerifier, app_settings, page_csrf, sign_in  # noqa: E402,E501


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def jobs():
    return FakeJobs()


@pytest.fixture
def client(store, jobs):
    app = create_app(store_factory=lambda uid: store, jobs_store=jobs, verifier=FakeVerifier(),
                     accounts=FakeAccounts(), settings=app_settings())
    return TestClient(app)


def test_not_interested_button_hides_and_offers_reasons(client, jobs):
    sign_in(client)
    page = client.get("/").text
    assert 'action="/jobs/1/not-interested"' in page and "Not interested" in page
    assert ">Hide<" not in page
    r = client.post("/jobs/1/not-interested", data={"csrf": page_csrf(client, "/")})
    assert r.status_code == 200 and jobs.changes[-1] == (1, 1, "hidden")
    assert "Hidden ✓" in r.text and "Wrong skills" in r.text
    assert 'action="/jobs/1/reason"' in r.text and 'name="csrf"' in r.text


def test_not_interested_unknown_job_404(client):
    sign_in(client)
    assert client.post("/jobs/99/not-interested",
                       data={"csrf": page_csrf(client, "/")}).status_code == 404


def test_reason_from_web_recorded(client, store):
    sign_in(client)
    r = client.post("/jobs/1/reason", data={"csrf": page_csrf(client, "/"), "reason": "company"})
    assert r.status_code == 200 and "Similar jobs will be hidden or lowered" in r.text
    assert store.avoid_reasons == [(1, "company")]


def test_settings_card_lists_and_removes(client, store):
    sign_in(client)
    assert "Nothing hidden or lowered yet." in client.get("/settings").text
    store.avoid = Avoid(companies=("Acme <Ltd>",), skills=("AWS",), cities=("Pune",),
                        titles=("Data Entry Operator",))
    page = client.get("/settings").text
    assert 'id="avoid"' in page and "Acme &lt;Ltd&gt;" in page and "Data Entry Operator" in page
    r = client.post("/avoid/remove", data={"csrf": page_csrf(client), "kind": "skills",
                                           "value": "AWS"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings#avoid"
    assert store.avoid.skills == ()


def test_avoid_routes_need_csrf_and_sign_in(client, store, jobs):
    routes = (("/jobs/1/not-interested", {}), ("/jobs/1/reason", {"reason": "company"}),
              ("/avoid/remove", {"kind": "skills", "value": "AWS"}))
    for path, data in routes:
        r = client.post(path, data={"csrf": "x", **data}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
    sign_in(client)
    for path, data in routes:
        assert client.post(path, data={"csrf": "wrong", **data}).status_code == 403
    assert jobs.changes == [] and store.avoid_reasons == []


# ---- UserStore against Postgres

def test_store_reason_rescores_at_once(db_session):
    uid = user(db_session, 1)
    run_pipeline(db_session, [FakeAdapter("fake", [raw(description="Python. 0-1 years")])],
                 now=datetime.now(UTC))
    job = db_session.scalars(select(Job)).one()
    store = UserStore(sessionmaker(bind=db_session.get_bind()), uid)
    brief, reasons = store.reasons_for(job.id)
    assert brief == JobBrief("Backend Engineer", "Acme") and "company" in reasons
    assert store.add_reason(job.id, "company") is True
    db_session.expire_all()
    score = db_session.scalars(select(JobScore).where(JobScore.user_id == uid)).one()
    assert score.passed_filters is False
    assert store.load_avoid().companies == ("Acme",)
    assert store.remove_avoid("companies", "acme") is True
    db_session.expire_all()
    assert db_session.scalars(select(JobScore).where(JobScore.user_id == uid)).one().passed_filters
