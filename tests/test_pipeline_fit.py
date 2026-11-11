"""Passing jobs are ranked by fit to the user's profile; the digest keeps the chosen level."""

from datetime import UTC, datetime

from sqlalchemy import select

from jobhunter.db import Job, JobScore
from jobhunter.matching.fit import blend
from jobhunter.pipeline import refilter_for_user, run_pipeline, select_digest
from jobhunter.profile import save_profile
from jobhunter.user_settings import load_active_searches, set_user_setting
from tests.test_pipeline import FakeAdapter, raw
from tests.test_pipeline_users import user

NOW = datetime.now(UTC)
EXP = " 0-1 years experience"


def _run(session, *jobs):
    run_pipeline(session, [FakeAdapter("fake", list(jobs))], now=NOW)


def _scores(session, uid) -> dict[str, JobScore]:
    rows = session.execute(select(Job.company, JobScore).join(
        JobScore, (JobScore.job_id == Job.id) & (JobScore.user_id == uid))).all()
    return {company: score for company, score in rows}


def _refilter(session, uid):
    refilter_for_user(session, uid, load_active_searches(session, uid), NOW, 14)


def test_fit_ranks_best_first(db_session):
    uid = user(db_session, 1)
    save_profile(db_session, uid, "Me\nPython and Django developer", NOW)
    _run(db_session, raw(company="A", description="Python and Django." + EXP),
         raw(company="B", description="Java and Spring Boot." + EXP))
    s = _scores(db_session, uid)
    assert s["A"].final_score > s["B"].final_score
    assert s["A"].fit_reason == "Matches Django, Python"
    assert s["B"].fit_reason == "Missing Java, Spring Boot"
    assert s["A"].skill_score == 1.0
    digest = select_digest(db_session, uid, NOW, 14, 25)
    assert [d.company for d in digest] == ["A"]  # B is weak; default level is "good"
    assert digest[0].level == "strong" and digest[0].reason == "Matches Django, Python"


def test_no_profile_keeps_old_scores(db_session):
    uid = user(db_session, 1)
    _run(db_session, raw(company="A", description="Python." + EXP))
    s = _scores(db_session, uid)["A"]
    assert s.final_score == 1.0 and s.fit_reason is None and s.skill_score is None


def test_rescore_keeps_review_until_cleared(db_session):
    uid = user(db_session, 1)
    save_profile(db_session, uid, "Me\nPython and Django", NOW)
    _run(db_session, raw(company="A", description="Python and Django." + EXP))
    row = _scores(db_session, uid)["A"]
    row.llm_fit, row.llm_json = 1, {"fit": 1, "reason": "Too senior for you"}
    db_session.commit()
    _refilter(db_session, uid)
    db_session.expire_all()
    row = _scores(db_session, uid)["A"]
    assert row.final_score == blend(1.0, 1) and row.fit_reason == "Too senior for you"
    save_profile(db_session, uid, "Me\nPython and Django again", NOW)
    _refilter(db_session, uid)
    db_session.expire_all()
    row = _scores(db_session, uid)["A"]
    assert row.final_score == 1.0 and row.fit_reason == "Matches Django, Python"


def test_digest_respects_fit_level(db_session):
    uid = user(db_session, 1)
    save_profile(db_session, uid, "Me\nPython", NOW)
    _run(db_session, raw(company="Strong", description="Python." + EXP),
         raw(company="Good", description="Python and Django." + EXP),
         raw(company="Weak", description="Java, Spring Boot and AWS." + EXP))
    counts = {}
    for level in ("strong", "good", "all"):
        set_user_setting(db_session, uid, "digest_fit", level)
        counts[level] = [d.company for d in select_digest(db_session, uid, NOW, 14, 25)]
    assert counts == {"strong": ["Strong"], "good": ["Strong", "Good"],
                      "all": ["Strong", "Good", "Weak"]}


def test_digest_without_profile_unchanged(db_session):
    uid = user(db_session, 1)
    set_user_setting(db_session, uid, "digest_fit", "strong")
    _run(db_session, raw(company="A", description="Java." + EXP),
         raw(company="B", description="Python." + EXP))
    digest = select_digest(db_session, uid, NOW, 14, 25)
    assert {d.company for d in digest} == {"A", "B"}
    assert all(d.level is None and d.reason is None for d in digest)


def test_jobs_saved_before_skills_existed_are_still_scored(db_session):
    uid = user(db_session, 1)
    save_profile(db_session, uid, "Me\nPython", NOW)
    _run(db_session, raw(company="A", description="Python." + EXP))
    job = db_session.scalars(select(Job)).one()
    job.skills, job.title_skills = None, None
    db_session.commit()
    _refilter(db_session, uid)
    db_session.expire_all()
    assert _scores(db_session, uid)["A"].fit_reason == "Matches Python"
