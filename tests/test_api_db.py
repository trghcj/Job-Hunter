"""DbApi against a real Postgres: the user's own filters, saving, seen, matches."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jobhunter.accounts import Identity, sign_in
from jobhunter.db import Job, JobOwner
from jobhunter.pipeline import run_pipeline
from jobhunter.web.api import DbApi, JobInfo, SaveRequest
from jobhunter.web.forms import ProfileForm
from jobhunter.web.store import UserStore
from tests.test_pipeline import FakeAdapter, raw

NOW = datetime.now(UTC)
INFO = dict(url="https://www.linkedin.com/jobs/view/4485755241/", title="Backend Engineer",
            company="Acme", location="Pune, Maharashtra, India", description="0-1 years")


def user(db_session, n, titles=("Backend Engineer",), max_years=2) -> int:
    uid = sign_in(db_session, Identity(f"uid{n}", f"u{n}@gmail.com", f"U{n}"), "", NOW).user_id
    UserStore(sessionmaker(bind=db_session.get_bind()), uid).save(ProfileForm(
        titles=list(titles), cities=[], work_modes=["onsite", "hybrid", "remote"], min_years=0,
        max_years=max_years, exclude_words=[], digest_to=[], max_jobs=25, max_age_days=14))
    return uid


def api(db_session) -> DbApi:
    return DbApi(sessionmaker(bind=db_session.get_bind()))


def test_dbapi_check_uses_users_filters(db_session):
    a = user(db_session, 1)
    b = user(db_session, 2, titles=("Data Engineer",))
    assert api(db_session).check(a, JobInfo(**INFO)).verdict == "match"
    result = api(db_session).check(b, JobInfo(**INFO))
    assert result.verdict == "no_match" and result.reasons == ["title does not match your titles"]
    assert result.seen.status is None and not result.seen.listed


def test_dbapi_check_does_not_store_anything(db_session):
    a = user(db_session, 1)
    api(db_session).check(a, JobInfo(**INFO))
    assert db_session.scalars(select(Job)).all() == []


def test_dbapi_no_titles_reason(db_session):
    a = user(db_session, 1, titles=())
    result = api(db_session).check(a, JobInfo(**INFO))
    assert result.verdict == "no_match" and result.reasons == ["Add job titles on Settings first"]


def test_dbapi_seen_after_save(db_session):
    a = user(db_session, 1)
    job_id = api(db_session).save(a, SaveRequest(**INFO, status="saved"))
    assert isinstance(job_id, int)
    seen = api(db_session).check(a, JobInfo(**INFO)).seen
    assert seen.status == "saved" and seen.emailed is False


def test_dbapi_saved_job_private_to_user(db_session):
    a, b = user(db_session, 1), user(db_session, 2)
    api(db_session).save(a, SaveRequest(**INFO, status="applied"))
    job = db_session.scalars(select(Job)).one()
    assert job.private is True and job.source == "extension"
    assert db_session.scalars(select(JobOwner.user_id)).all() == [a]
    assert api(db_session).check(b, JobInfo(**INFO)).seen.status is None


def test_dbapi_listed_when_in_my_jobs(db_session):
    a = user(db_session, 1)
    run_pipeline(db_session, [FakeAdapter("fake", [raw(company="Acme", location="Pune")])], [],
                 now=NOW)
    seen = api(db_session).check(a, JobInfo(**INFO)).seen
    assert seen.listed is True and seen.status is None


def test_dbapi_matches_are_users_own(db_session):
    a = user(db_session, 1)
    b = user(db_session, 2, titles=("Data Engineer",))
    run_pipeline(db_session, [FakeAdapter("fake", [raw()])], [], now=NOW)
    assert len(api(db_session).matches(a, 5)) == 1
    assert api(db_session).matches(b, 5) == []


def test_dbapi_me(db_session):
    a = user(db_session, 1)
    assert api(db_session).me(a) == {"email": "u1@gmail.com", "name": "U1"}


def test_check_returns_fit_with_profile(db_session):
    from jobhunter.profile import save_profile

    a = user(db_session, 1)
    save_profile(db_session, a, "Me\nPython and Django", NOW)
    info = JobInfo(**{**INFO, "description": "Python, Django and AWS. 0-1 years"})
    fit = api(db_session).check(a, info).fit
    assert fit["level"] in ("strong", "good", "weak") and 0 <= fit["score"] <= 1
    assert fit["reason"] == "Matches Django, Python · Missing AWS"


def test_check_fit_none_without_profile_or_when_not_a_match(db_session):
    from jobhunter.profile import save_profile

    a = user(db_session, 1)
    assert api(db_session).check(a, JobInfo(**INFO)).fit is None
    save_profile(db_session, a, "Me\nPython", NOW)
    other = JobInfo(**{**INFO, "title": "Data Analyst"})
    assert api(db_session).check(a, other).fit is None
