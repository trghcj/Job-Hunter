"""The daily run with several users: shared fetch, per-user filters, private Gmail jobs,
and who gets an email (real Postgres)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

import jobhunter.__main__ as cli
from jobhunter.accounts import Identity, sign_in
from jobhunter.db import Job, JobOwner, JobScore, JobStatus
from jobhunter.pipeline import combined_search, run_pipeline, select_digest
from jobhunter.sources.base import Search
from jobhunter.sources.registry import BrokenSource
from jobhunter.user_settings import set_setting, set_user_setting
from jobhunter.web.forms import ProfileForm
from jobhunter.web.store import UserStore
from tests.test_pipeline import FakeAdapter, raw

NOW = datetime.now(UTC)


def user(db_session, n: int, titles=("Backend Engineer",), cities=(), owner=False) -> int:
    email = "owner@gmail.com" if owner else f"u{n}@gmail.com"
    uid = sign_in(db_session, Identity(f"uid{n}", email, None),
                  "owner@gmail.com" if owner else "", NOW).user_id
    from sqlalchemy.orm import sessionmaker

    UserStore(sessionmaker(bind=db_session.get_bind()), uid).save(ProfileForm(
        titles=list(titles), cities=list(cities), work_modes=["onsite", "hybrid", "remote"],
        min_years=0, max_years=2, exclude_words=[], digest_to=[email], max_jobs=25,
        max_age_days=14))
    return uid


def test_combined_search_orders_by_popularity():
    per_user = {
        1: [Search(titles=["Python Developer", "AI Engineer"], min_years=0, max_years=1)],
        2: [Search(titles=["ai engineer"], min_years=1, max_years=4)],
        3: [Search(titles=["Backend Engineer", "AI Engineer"], min_years=0, max_years=2)],
    }
    s = combined_search(per_user)
    assert s.titles == ["AI Engineer", "Backend Engineer", "Python Developer"]
    assert (s.min_years, s.max_years) == (0, 4)
    assert s.cities == [] and s.exclude_words == [] and set(s.work_modes) == {
        "onsite", "hybrid", "remote"}
    assert combined_search({}) is None


def test_users_without_titles_are_skipped(db_session):
    a = user(db_session, 1)
    b = user(db_session, 2, titles=())
    summary = run_pipeline(db_session, [FakeAdapter("fake", [raw()])], [], now=NOW)
    assert set(summary.users) == {a} and b not in summary.users


def test_job_passes_for_one_user_not_other(db_session):
    a = user(db_session, 1, cities=["Delhi"])
    b = user(db_session, 2, cities=["Pune"])
    summary = run_pipeline(db_session, [FakeAdapter("fake", [raw(location="Gurugram")])], [],
                           now=NOW)
    assert len(summary.users[a].digest) == 1 and summary.users[b].digest == []
    passed = dict(db_session.execute(select(JobScore.user_id, JobScore.passed_filters)).all())
    assert passed == {a: True, b: False}


def test_shared_fetch_runs_each_source_once(db_session):
    user(db_session, 1)
    user(db_session, 2, titles=("Python Developer",))
    adapter = FakeAdapter("fake", [raw()])
    run_pipeline(db_session, [adapter], [], now=NOW)
    assert adapter.calls == 1


def test_gmail_job_private_to_its_user(db_session):
    a, b = user(db_session, 1), user(db_session, 2)
    gmail = FakeAdapter("gmail_alerts", [raw(company="Secret Co")])
    summary = run_pipeline(db_session, [], [(a, gmail)], now=NOW)
    assert [d.company for d in summary.users[a].digest] == ["Secret Co"]
    assert summary.users[b].digest == []
    assert select_digest(db_session, b, NOW, 14, 25) == []
    job = db_session.scalars(select(Job)).one()
    assert job.private is True
    assert db_session.execute(select(JobOwner.user_id)).scalars().all() == [a]
    assert f"gmail_alerts:{a}" in {s.source for s in summary.sources}


def test_private_job_becomes_public_when_seen_publicly(db_session):
    a, b = user(db_session, 1), user(db_session, 2)
    run_pipeline(db_session, [], [(a, FakeAdapter("gmail_alerts", [raw()]))], now=NOW)
    summary = run_pipeline(db_session, [FakeAdapter("adzuna", [raw()])], [],
                           now=NOW + timedelta(minutes=5))
    assert db_session.scalars(select(Job.private)).one() is False
    assert len(summary.users[b].digest) == 1


def test_public_job_stays_public_when_in_gmail(db_session):
    a = user(db_session, 1)
    run_pipeline(db_session, [FakeAdapter("adzuna", [raw()])], [], now=NOW)
    run_pipeline(db_session, [], [(a, FakeAdapter("gmail_alerts", [raw()]))],
                 now=NOW + timedelta(minutes=5))
    assert db_session.scalars(select(Job.private)).one() is False


def test_second_user_same_gmail_job_gets_access(db_session):
    a, b = user(db_session, 1), user(db_session, 2)
    summary = run_pipeline(db_session, [], [(a, FakeAdapter("gmail_alerts", [raw()])),
                                            (b, FakeAdapter("gmail_alerts", [raw()]))], now=NOW)
    assert len(summary.users[a].digest) == 1 and len(summary.users[b].digest) == 1
    assert sorted(db_session.scalars(select(JobOwner.user_id))) == sorted([a, b])


def test_hidden_job_not_in_digest(db_session):
    a = user(db_session, 1)
    run_pipeline(db_session, [FakeAdapter("fake", [raw()])], [], now=NOW)
    job_id = db_session.scalars(select(Job.id)).one()
    db_session.add(JobStatus(user_id=a, job_id=job_id, status="hidden"))
    db_session.commit()
    assert run_pipeline(db_session, [], [], now=NOW).users[a].digest == []


def test_one_users_gmail_failure_does_not_stop_others(db_session):
    a, b = user(db_session, 1), user(db_session, 2)
    summary = run_pipeline(db_session, [], [(a, BrokenSource("gmail_alerts", "bad key")),
                                            (b, FakeAdapter("gmail_alerts", [raw()]))], now=NOW)
    results = {s.source: s for s in summary.sources}
    assert results[f"gmail_alerts:{a}"].ok is False
    assert results[f"gmail_alerts:{b}"].ok is True and len(summary.users[b].digest) == 1


def test_per_user_max_jobs(db_session):
    a = user(db_session, 1)
    set_user_setting(db_session, a, "digest", {"max_jobs": 1})
    jobs = [raw(company=c) for c in ("A", "B", "C")]
    assert len(run_pipeline(db_session, [FakeAdapter("fake", jobs)], [], now=NOW)
               .users[a].digest) == 1


# --- who gets an email -------------------------------------------------------------

class FakeSender:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, html_body, text_body):
        self.sent.append(to)
        return "id"


def _deliver(db_session, monkeypatch):
    sender = FakeSender()
    monkeypatch.setattr(cli, "_email_sender", lambda session: sender)
    summary = run_pipeline(db_session, [FakeAdapter("fake", [raw()])], [], now=NOW)
    problems = cli.deliver_digests(db_session, summary, NOW)
    return sender, problems


def test_only_owner_emailed_without_domain(db_session, monkeypatch):
    user(db_session, 9, owner=True)
    user(db_session, 1)
    sender, problems = _deliver(db_session, monkeypatch)
    assert sender.sent == [["owner@gmail.com"]] and problems == []


def test_all_users_emailed_with_domain(db_session, monkeypatch):
    user(db_session, 9, owner=True)
    user(db_session, 1)
    set_setting(db_session, "digest_from", "JobHunter <jobs@example.com>")
    sender, problems = _deliver(db_session, monkeypatch)
    assert sorted(sender.sent) == [["owner@gmail.com"], ["u1@gmail.com"]] and problems == []


def test_resend_dev_sender_counts_as_no_domain(db_session, monkeypatch):
    user(db_session, 9, owner=True)
    user(db_session, 1)
    set_setting(db_session, "digest_from", "JobHunter <onboarding@resend.dev>")
    sender, _ = _deliver(db_session, monkeypatch)
    assert sender.sent == [["owner@gmail.com"]]


def test_owner_email_run_recorded_without_titles(db_session, monkeypatch):
    """Backups (--once-per-day) must see today's run even if the owner has no titles."""
    from jobhunter.pipeline import ran_successfully_today

    user(db_session, 9, titles=(), owner=True)
    user(db_session, 1)
    _deliver(db_session, monkeypatch)
    assert ran_successfully_today(db_session, NOW)


def test_set_titles_command_edits_the_owners_profile(db_session, monkeypatch):
    from sqlalchemy.orm import Session

    from jobhunter.user_settings import load_active_searches

    other = user(db_session, 1, titles=("AI Engineer",))  # this user's profile is older
    owner = user(db_session, 9, titles=(), owner=True)
    monkeypatch.setattr(cli, "_session", lambda: Session(db_session.get_bind()))
    assert cli.main(["set-titles", "Data Engineer"]) == 0
    db_session.expire_all()
    assert load_active_searches(db_session, owner)[0].titles == ["Data Engineer"]
    assert load_active_searches(db_session, other)[0].titles == ["AI Engineer"]
