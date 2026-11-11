"""Daily Gemini review: only opted-in users with a profile, at most 10 jobs in one call,
failures logged without stopping the run, and the name never sent (fake client)."""

from datetime import UTC, datetime

from sqlalchemy import select

from jobhunter.db import Job, JobScore, LlmUsage, Run
from jobhunter.llm.client import LLMError, Review
from jobhunter.matching.fit import blend
from jobhunter.pipeline import run_pipeline, select_digest
from jobhunter.profile import save_profile
from jobhunter.review import CallBudget, review_top_jobs
from jobhunter.user_settings import set_user_setting
from tests.test_pipeline import FakeAdapter, raw
from tests.test_pipeline_users import user

NOW = datetime.now(UTC)
EXP = " 0-1 years experience"


class FakeLLM:
    provider = "gemini"
    model = "fake-model"

    def __init__(self, fit=5, fail: Exception | None = None):
        self.fit, self.fail, self.calls = fit, fail, []

    def review(self, profile_text, jobs):
        self.calls.append((profile_text, jobs))
        if self.fail:
            raise self.fail
        return [Review(j.id, self.fit, f"Gemini on {j.title}") for j in jobs]


def setup(session, n_jobs=3, opted_in=True, profile=True) -> int:
    uid = user(session, 1)
    if profile:
        save_profile(session, uid, "Asha Verma\nPython and Django developer", NOW)
    set_user_setting(session, uid, "llm_review", opted_in)
    jobs = [raw(company=f"C{i:02d}", description="Python." + EXP) for i in range(n_jobs)]
    run_pipeline(session, [FakeAdapter("fake", jobs)], now=NOW)
    return uid


def scores(session, uid) -> list[JobScore]:
    return session.scalars(select(JobScore).where(JobScore.user_id == uid,
                                                  JobScore.passed_filters.is_(True))).all()


def test_only_opted_in_users_with_profile_are_reviewed(db_session):
    llm = FakeLLM()
    uid = setup(db_session, opted_in=False)
    assert review_top_jobs(db_session, uid, llm, NOW, CallBudget(30)) == 0
    set_user_setting(db_session, uid, "llm_review", True)
    set_user_setting(db_session, uid, "profile", None)
    assert review_top_jobs(db_session, uid, llm, NOW, CallBudget(30)) == 0
    assert llm.calls == []


def test_at_most_ten_jobs_one_call(db_session):
    llm = FakeLLM()
    uid = setup(db_session, n_jobs=12)
    assert review_top_jobs(db_session, uid, llm, NOW, CallBudget(30)) == 10
    assert len(llm.calls) == 1 and len(llm.calls[0][1]) == 10
    assert review_top_jobs(db_session, uid, llm, NOW, CallBudget(30)) == 2  # the rest next time


def test_review_updates_score_and_reason(db_session):
    llm = FakeLLM(fit=1)
    uid = setup(db_session, n_jobs=1)
    local = scores(db_session, uid)[0].final_score
    review_top_jobs(db_session, uid, llm, NOW, CallBudget(30))
    db_session.expire_all()
    row = scores(db_session, uid)[0]
    assert row.llm_fit == 1 and row.final_score == blend(local, 1)
    assert row.fit_reason == "Gemini on Backend Engineer"
    assert row.llm_json == {"fit": 1, "reason": "Gemini on Backend Engineer",
                            "model": "fake-model"}


def test_failure_logs_run_and_keeps_scores(db_session):
    uid = setup(db_session, n_jobs=2)
    before = [(s.job_id, s.final_score) for s in scores(db_session, uid)]
    for failure in (LLMError("Gemini answered HTTP 429."), RuntimeError("bug")):
        assert review_top_jobs(db_session, uid, FakeLLM(fail=failure), NOW, CallBudget(30)) == 0
    db_session.expire_all()
    assert [(s.job_id, s.final_score) for s in scores(db_session, uid)] == before
    runs = db_session.scalars(select(Run).where(Run.source == f"llm:{uid}")).all()
    assert [(r.ok, r.error) for r in runs] == [(False, "Gemini answered HTTP 429."),
                                               (False, "RuntimeError")]


def test_no_client_means_no_reviews_and_no_errors(db_session):
    uid = setup(db_session)
    assert review_top_jobs(db_session, uid, None, NOW, CallBudget(30)) == 0
    assert db_session.scalars(select(Run).where(Run.source.like("llm:%"))).all() == []


def test_budget_exhausted_skips(db_session):
    llm = FakeLLM()
    uid = setup(db_session)
    budget = CallBudget(0)
    assert review_top_jobs(db_session, uid, llm, NOW, budget) == 0 and llm.calls == []
    budget = CallBudget(1)
    review_top_jobs(db_session, uid, llm, NOW, budget)
    assert budget.left == 0


def test_profile_first_line_never_sent(db_session):
    llm = FakeLLM()
    uid = setup(db_session)
    review_top_jobs(db_session, uid, llm, NOW, CallBudget(30))
    profile_text = llm.calls[0][0]
    assert "Asha Verma" not in profile_text and "Django" in profile_text


def test_usage_counted(db_session):
    uid = setup(db_session)
    review_top_jobs(db_session, uid, FakeLLM(), NOW, CallBudget(30))
    review_top_jobs(db_session, uid, FakeLLM(fail=LLMError("x")), NOW, CallBudget(30))
    usage = db_session.scalars(select(LlmUsage)).one()
    assert usage.provider == "gemini" and usage.calls == 1  # the second had nothing left


def test_pipeline_reviews_before_digest(db_session):
    uid = setup(db_session, n_jobs=2)
    first = db_session.scalars(select(Job).where(Job.company == "C01")).one()

    class Picky(FakeLLM):
        def review(self, profile_text, jobs):
            return [Review(j.id, 5 if j.id == first.id else 1, "r") for j in jobs]

    db_session.execute(JobScore.__table__.update().values(llm_fit=None, llm_json=None))
    db_session.commit()
    run_pipeline(db_session, [FakeAdapter("fake", [])], now=NOW, llm=Picky())
    set_user_setting(db_session, uid, "digest_fit", "all")
    digest = select_digest(db_session, uid, NOW, 14, 25)
    assert digest[0].company == "C01"


def test_daily_run_uses_gemini_only_with_a_key():
    import jobhunter.__main__ as cli
    from jobhunter.llm.gemini import GeminiClient
    from jobhunter.settings import Settings

    assert cli._llm_client(Settings(gemini_api_key="")) is None
    client = cli._llm_client(Settings(gemini_api_key="k", gemini_model="gemini-2.5-flash"))
    assert isinstance(client, GeminiClient) and client.model == "gemini-2.5-flash"
