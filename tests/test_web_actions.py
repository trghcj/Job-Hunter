"""One-click action pages (/a/{token}): GET shows, POST acts; refused when the token is bad,
the job is not visible, or the user is blocked or no longer invited."""

from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from jobhunter.accounts import Access, email_set, set_blocked  # noqa: E402
from jobhunter.actions import action_key, make_token  # noqa: E402
from jobhunter.avoid import load_avoid  # noqa: E402
from jobhunter.db import Job, JobStatus  # noqa: E402
from jobhunter.pipeline import run_pipeline  # noqa: E402
from jobhunter.web.actions_store import DbActions, JobBrief  # noqa: E402
from jobhunter.web.app import create_app  # noqa: E402
from tests.test_pipeline import FakeAdapter, raw  # noqa: E402
from tests.test_pipeline_users import user  # noqa: E402
from tests.test_web_app import FakeStore  # noqa: E402
from tests.web_fakes import FakeAccounts, FakeVerifier, NoJobs, app_settings  # noqa: E402

SETTINGS_KEY = "x" * 44
KEY = action_key(SETTINGS_KEY)
EXPIRED = "This link has expired, open My jobs instead."


class FakeActions:
    def __init__(self):
        self.applied, self.reasons, self.refuse = [], [], False

    def apply(self, user_id, job_id, status):
        if self.refuse:
            return None
        self.applied.append((user_id, job_id, status))
        return JobBrief("Backend <b>Engineer</b>", "Acme")

    def reasons_for(self, user_id, job_id):
        return ["company", "skills", "role"]

    def add_reason(self, user_id, job_id, reason):
        self.reasons.append((user_id, job_id, reason))
        return True


@pytest.fixture
def actions():
    return FakeActions()


@pytest.fixture
def client(actions):
    app = create_app(store_factory=lambda uid: FakeStore(), jobs_store=NoJobs(),
                     verifier=FakeVerifier(), accounts=FakeAccounts(), actions=actions,
                     settings=app_settings(settings_key=SETTINGS_KEY))
    return TestClient(app)


def token(action="save", user_id=3, job_id=9):
    return make_token(KEY, user_id, job_id, action)


def test_get_changes_nothing(client, actions):
    r = client.get(f"/a/{token()}")
    assert r.status_code == 200 and actions.applied == []
    assert 'id="act-form"' in r.text and 'src="/static/act.js"' in r.text
    assert 'name="confirm" value="1"' in r.text


def test_post_applies_each_action(client, actions):
    for action, status, copy in (("save", "saved", "Saved ✓"),
                                 ("applied", "applied", "Marked as applied ✓"),
                                 ("not_interested", "hidden", "Hidden ✓")):
        r = client.post(f"/a/{token(action)}", data={"confirm": "1"})
        assert r.status_code == 200 and copy in r.text
        assert actions.applied[-1] == (3, 9, status)
    assert "Backend &lt;b&gt;Engineer&lt;/b&gt;" in r.text


def test_post_needs_confirm(client, actions):
    assert client.post(f"/a/{token()}").status_code == 400
    assert actions.applied == []


def test_repeat_post_is_harmless(client, actions):
    for _ in range(2):
        assert client.post(f"/a/{token()}", data={"confirm": "1"}).status_code == 200
    assert actions.applied == [(3, 9, "saved"), (3, 9, "saved")]


def test_expired_or_bad_token_page(client, actions):
    for path in ("/a/garbage", f"/a/{token()[:-2]}xx"):
        r = client.get(path)
        assert r.status_code == 400 and EXPIRED in r.text
        assert client.post(path, data={"confirm": "1"}).status_code == 400
    actions.refuse = True
    r = client.post(f"/a/{token()}", data={"confirm": "1"})
    assert r.status_code == 400 and EXPIRED in r.text


def test_not_interested_offers_reasons_and_records_one(client, actions):
    t = token("not_interested")
    page = client.post(f"/a/{t}", data={"confirm": "1"}).text
    assert "Don&#39;t like this company" in page and "Wrong skills" in page
    assert "Wrong location" not in page  # not offered for this job
    r = client.post(f"/a/{t}/reason", data={"reason": "company"})
    assert r.status_code == 200 and "Similar jobs will be hidden or lowered" in r.text
    assert actions.reasons == [(3, 9, "company")]
    assert client.post(f"/a/{token('save')}/reason", data={"reason": "company"}).status_code == 400


def test_no_cookie_and_headers(client):
    for r in (client.get(f"/a/{token()}"), client.post(f"/a/{token()}", data={"confirm": "1"})):
        assert "set-cookie" not in r.headers
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["referrer-policy"] == "no-referrer"


def test_act_js_submits_form(client):
    js = client.get("/static/act.js")
    assert js.status_code == 200 and "act-form" in js.text and "submit()" in js.text


def test_no_settings_key_means_every_link_refused(actions):
    app = create_app(store_factory=lambda uid: FakeStore(), jobs_store=NoJobs(),
                     verifier=FakeVerifier(), accounts=FakeAccounts(), actions=actions,
                     settings=app_settings(settings_key=""))
    forged = make_token(action_key(""), 3, 9, "save")
    assert TestClient(app).get(f"/a/{forged}").status_code == 400


# ---- DbActions against Postgres

NOW = datetime.now(UTC)


def setup(db_session):
    a, b = user(db_session, 1), user(db_session, 2)
    run_pipeline(db_session, [FakeAdapter("fake", [raw(description="Python. 0-1 years")])],
                 now=NOW)
    job = db_session.scalars(select(Job)).one()
    return a, b, job


def db_actions(db_session, access=None) -> DbActions:
    return DbActions(sessionmaker(bind=db_session.get_bind()), access or Access())


def test_db_apply_sets_status(db_session):
    a, _, job = setup(db_session)
    brief = db_actions(db_session).apply(a, job.id, "saved")
    assert brief == JobBrief("Backend Engineer", "Acme")
    status = db_session.scalars(select(JobStatus.status).where(JobStatus.user_id == a)).one()
    assert status == "saved"


def test_job_not_visible_refused(db_session):
    a, _, job = setup(db_session)
    job.private = True
    db_session.commit()
    assert db_actions(db_session).apply(a, job.id, "saved") is None
    assert db_actions(db_session).apply(a, 999999, "saved") is None


def test_blocked_or_uninvited_user_refused(db_session):
    a, b, job = setup(db_session)
    set_blocked(db_session, a, True)
    assert db_actions(db_session).apply(a, job.id, "saved") is None
    invite_only = Access(allowed=email_set("someone@else.com"))
    assert db_actions(db_session, invite_only).apply(b, job.id, "saved") is None
    assert db_actions(db_session).apply(b, job.id, "saved") is not None


def test_db_reason_recorded_and_rescored(db_session):
    a, _, job = setup(db_session)
    acts = db_actions(db_session)
    assert "company" in acts.reasons_for(a, job.id)
    assert acts.add_reason(a, job.id, "company") is True
    assert load_avoid(db_session, a).companies == ("Acme",)
    db_session.expire_all()
    from jobhunter.db import JobScore

    score = db_session.scalars(select(JobScore).where(JobScore.user_id == a)).one()
    assert score.passed_filters is False and score.filter_reason == "you avoid this company"
