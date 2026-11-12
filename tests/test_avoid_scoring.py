"""Avoided companies and cities hide jobs; avoided skills and rejected roles lower fit."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jobhunter.avoid import add_reason
from jobhunter.db import Job, JobScore
from jobhunter.matching.fit import local_fit
from jobhunter.pipeline import run_pipeline
from jobhunter.profile import save_profile
from jobhunter.web.api import DbApi, JobInfo
from tests.test_pipeline import FakeAdapter, raw
from tests.test_pipeline_users import user

NOW = datetime.now(UTC)
EXP = " 0-1 years experience"


def test_avoided_skills_lower_fit():
    base = local_fit({"Python", "AWS"}, set(), {"Python"}, 1.0, False, False)
    one = local_fit({"Python", "AWS"}, set(), {"Python"}, 1.0, False, False,
                    avoid_skills={"AWS"})
    assert one.score == round(base.score - 0.1, 4)
    assert one.reason == "Matches Python · Missing AWS · Lowered: you avoid AWS"
    many = local_fit({"Python", "AWS", "Go", "Rust", "Kafka"}, set(), {"Python"}, 1.0, False,
                     False, avoid_skills={"AWS", "Go", "Rust", "Kafka"})
    plain = local_fit({"Python", "AWS", "Go", "Rust", "Kafka"}, set(), {"Python"}, 1.0, False,
                      False)
    assert many.score == round(max(plain.score - 0.3, 0.0), 4)
    assert many.reason.endswith("Lowered: you avoid AWS, Go, Kafka")


def test_rejected_role_lowers_fit():
    f = local_fit({"Python"}, set(), {"Python"}, 1.0, False, False, rejected_role=True)
    assert f.score == 0.8 and f.reason == "Matches Python · Lowered: role like one you rejected"


def _scores(session, uid) -> dict[str, JobScore]:
    rows = session.execute(select(Job.company, JobScore).join(
        JobScore, (JobScore.job_id == Job.id) & (JobScore.user_id == uid))).all()
    return {company: score for company, score in rows}


def _run(session, *jobs):
    run_pipeline(session, [FakeAdapter("fake", list(jobs))], now=NOW)


def _job(session, company) -> Job:
    return session.scalars(select(Job).where(Job.company == company)).one()


def test_avoided_company_fails_filters(db_session):
    uid = user(db_session, 1)
    _run(db_session, raw(company="Acme Pvt Ltd", description="Python." + EXP))
    add_reason(db_session, uid, _job(db_session, "Acme Pvt Ltd"), "company", set())
    _run(db_session, raw(company="ACME Private Limited", title="Python Developer",
                         description="Python." + EXP),
         raw(company="Beta", description="Python." + EXP))
    s = _scores(db_session, uid)
    assert s["Acme Pvt Ltd"].passed_filters is False
    assert s["Acme Pvt Ltd"].filter_reason == "you avoid this company"
    assert s["Beta"].passed_filters is True


def test_avoided_city_fails_but_remote_kept(db_session):
    uid = user(db_session, 1)
    _run(db_session, raw(company="A", location="Pune", description="Python." + EXP))
    add_reason(db_session, uid, _job(db_session, "A"), "location", set())
    _run(db_session, raw(company="B", location="Pune", description="Python." + EXP),
         raw(company="C", location="Remote", description="Python. Remote in India." + EXP))
    s = _scores(db_session, uid)
    assert s["B"].passed_filters is False and s["B"].filter_reason == "you avoid Pune"
    assert s["C"].passed_filters is True


def test_skills_and_role_lower_scores_with_profile(db_session):
    uid = user(db_session, 1)
    save_profile(db_session, uid, "Me\nPython", NOW)
    _run(db_session, raw(company="A", description="Python and AWS." + EXP))
    before = _scores(db_session, uid)["A"].final_score
    job = _job(db_session, "A")
    add_reason(db_session, uid, job, "skills", {"Python"})
    add_reason(db_session, uid, job, "role", {"Python"})
    _run(db_session)
    after = _scores(db_session, uid)["A"]
    assert after.final_score == round(before - 0.1 - 0.2, 4)
    assert "Lowered: you avoid AWS" in after.fit_reason
    assert "role like one you rejected" in after.fit_reason


def test_check_api_respects_avoids(db_session):
    uid = user(db_session, 1)
    _run(db_session, raw(company="Acme", description="Python." + EXP))
    add_reason(db_session, uid, _job(db_session, "Acme"), "company", set())
    api = DbApi(sessionmaker(bind=db_session.get_bind()))
    info = JobInfo(url="https://www.linkedin.com/jobs/view/1/", title="Backend Engineer",
                   company="Acme Ltd", location="Pune", description="0-1 years")
    result = api.check(uid, info)
    assert result.verdict == "no_match" and result.reasons == ["you avoid this company"]


def test_no_avoid_unchanged(db_session):
    uid = user(db_session, 1)
    save_profile(db_session, uid, "Me\nPython and Django", NOW)
    _run(db_session, raw(company="A", description="Python and Django." + EXP))
    s = _scores(db_session, uid)["A"]
    assert s.final_score == 1.0 and s.fit_reason == "Matches Django, Python"
