"""My jobs queries and status changes (real Postgres)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import sessionmaker

from jobhunter.accounts import Identity, sign_in
from jobhunter.pipeline import run_pipeline
from jobhunter.web.forms import ProfileForm
from jobhunter.web.jobs_store import JobsStore
from jobhunter.web.store import UserStore
from tests.test_pipeline import FakeAdapter, raw

NOW = datetime.now(UTC)


def user(db_session, n: int, titles=("Backend Engineer",)) -> int:
    uid = sign_in(db_session, Identity(f"uid{n}", f"u{n}@gmail.com", None), "", NOW).user_id
    UserStore(sessionmaker(bind=db_session.get_bind()), uid).save(ProfileForm(
        titles=list(titles), cities=[], work_modes=["onsite", "hybrid", "remote"], min_years=0,
        max_years=2, exclude_words=[], digest_to=[], max_jobs=25, max_age_days=14))
    return uid


def store(db_session) -> JobsStore:
    return JobsStore(sessionmaker(bind=db_session.get_bind()))


def test_matches_best_first(db_session):
    a = user(db_session, 1)
    jobs = [raw(company="NoExp", description="Build APIs.", days_ago=1),  # lower score
            raw(company="Old", days_ago=3), raw(company="New", days_ago=1),
            raw(title="Java Developer", company="Miss")]
    run_pipeline(db_session, [FakeAdapter("fake", jobs)], [], now=NOW)
    cards = store(db_session).list(a, "matches")
    assert [c.company for c in cards] == ["New", "Old", "NoExp"]
    assert cards[0].status == "new" and cards[0].url.startswith("https://")


def test_hidden_and_applied_leave_matches(db_session):
    a = user(db_session, 1)
    run_pipeline(db_session, [FakeAdapter("fake", [raw(company=c) for c in "ABC"])], [], now=NOW)
    s = store(db_session)
    ids = {c.company: c.job_id for c in s.list(a, "matches")}
    assert s.set_status(a, ids["A"], "hidden") and s.set_status(a, ids["B"], "applied")
    assert [c.company for c in s.list(a, "matches")] == ["C"]
    assert [c.company for c in s.list(a, "applied")] == ["B"]


def test_saved_tab(db_session):
    a = user(db_session, 1)
    run_pipeline(db_session, [FakeAdapter("fake", [raw(company=c) for c in "AB"])], [], now=NOW)
    s = store(db_session)
    first = s.list(a, "matches")[0]
    assert s.set_status(a, first.job_id, "saved")
    assert [c.job_id for c in s.list(a, "saved")] == [first.job_id]
    assert {c.status for c in s.list(a, "matches")} == {"saved", "new"}  # saved stays listed


def test_emailed_job_still_listed(db_session):
    from jobhunter.pipeline import mark_emailed

    a = user(db_session, 1)
    summary = run_pipeline(db_session, [FakeAdapter("fake", [raw()])], [], now=NOW)
    mark_emailed(db_session, a, [d.job_id for d in summary.users[a].digest], NOW)
    assert len(store(db_session).list(a, "matches")) == 1


def test_other_users_matches_not_listed(db_session):
    a = user(db_session, 1)
    b = user(db_session, 2, titles=("Python Developer",))
    run_pipeline(db_session, [FakeAdapter("fake", [raw()])], [], now=NOW)
    assert len(store(db_session).list(a, "matches")) == 1
    assert store(db_session).list(b, "matches") == []


def test_status_on_someone_elses_private_job_is_refused(db_session):
    a, b = user(db_session, 1), user(db_session, 2)
    run_pipeline(db_session, [], [(a, FakeAdapter("gmail_alerts", [raw()]))], now=NOW)
    job_id = store(db_session).list(a, "matches")[0].job_id
    assert store(db_session).set_status(b, job_id, "saved") is False
    assert store(db_session).list(b, "saved") == []


def test_status_on_missing_job_is_refused(db_session):
    a = user(db_session, 1)
    assert store(db_session).set_status(a, 999999, "saved") is False


def test_invalid_status_refused(db_session):
    a = user(db_session, 1)
    run_pipeline(db_session, [FakeAdapter("fake", [raw()])], [], now=NOW)
    job_id = store(db_session).list(a, "matches")[0].job_id
    assert store(db_session).set_status(a, job_id, "deleted") is False


def test_old_jobs_drop_out_of_matches(db_session):
    a = user(db_session, 1)
    run_pipeline(db_session, [FakeAdapter("fake", [raw()])], [], now=NOW - timedelta(days=20))
    assert store(db_session).list(a, "matches") == []


# ---- fit filter, tab counts and when a job was saved / applied

def _scored(db_session, a, scores: dict[str, tuple[float, str | None]]) -> dict[str, int]:
    """Jobs from companies A, B, ... with the given (score, fit reason)."""
    from sqlalchemy import update

    from jobhunter.db import JobScore

    run_pipeline(db_session, [FakeAdapter("fake", [raw(company=c) for c in scores])], [],
                 now=NOW)
    ids = {c.company: c.job_id for c in store(db_session).list(a, "matches")}
    for company, (score, reason) in scores.items():
        db_session.execute(update(JobScore)
                           .where(JobScore.user_id == a, JobScore.job_id == ids[company])
                           .values(final_score=score, fit_reason=reason))
    db_session.commit()
    return ids


def test_fit_filter(db_session):
    a = user(db_session, 1)
    _scored(db_session, a, {"A": (0.8, "fits"), "B": (0.6, "fits"), "C": (0.3, "weak"),
                            "D": (0.9, None)})  # no reason: no level, so never "strong"
    s = store(db_session)
    assert [c.company for c in s.list(a, "matches", fit="strong")] == ["A"]
    assert [c.company for c in s.list(a, "matches", fit="good")] == ["A", "B"]
    assert len(s.list(a, "matches")) == 4


def test_counts(db_session):
    a = user(db_session, 1)
    ids = _scored(db_session, a, {"A": (0.8, "x"), "B": (0.6, "x"), "C": (0.3, "x")})
    s = store(db_session)
    assert s.counts(a) == {"matches": 3, "saved": 0, "applied": 0}
    s.set_status(a, ids["A"], "saved")    # saved jobs stay in Matches
    s.set_status(a, ids["B"], "applied")  # applied ones leave it
    assert s.counts(a) == {"matches": 2, "saved": 1, "applied": 1}
    b = user(db_session, 2, titles=("Python Developer",))
    assert s.counts(b) == {"matches": 0, "saved": 0, "applied": 0}


def test_status_at_on_cards(db_session):
    a = user(db_session, 1)
    ids = _scored(db_session, a, {"A": (0.8, "x"), "B": (0.6, "x")})
    s = store(db_session)
    assert all(c.status_at is None for c in s.list(a, "matches"))
    s.set_status(a, ids["A"], "saved")
    saved = s.list(a, "saved")[0]
    assert saved.status_at is not None and abs(saved.status_at - datetime.now(UTC)).seconds < 60


def test_new_today_counts_matches_first_seen_today(db_session):
    from sqlalchemy import update

    from jobhunter.db import Job

    a = user(db_session, 1)
    ids = _scored(db_session, a, {"A": (0.8, "x"), "B": (0.6, "x"), "C": (0.3, "x")})
    db_session.execute(update(Job).where(Job.id == ids["C"])
                       .values(first_seen_at=NOW - timedelta(days=2)))
    db_session.commit()
    s = store(db_session)
    assert s.new_today(a) == 2
    s.set_status(a, ids["A"], "hidden")  # hidden jobs are not news
    assert s.new_today(a) == 1
