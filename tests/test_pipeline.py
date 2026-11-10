from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from jobhunter.db import Job, JobScore, Run, SearchProfile
from jobhunter.matching.filters import FilterResult
from jobhunter.models import RawJob
from jobhunter.pipeline import mark_emailed, provisional_score, run_pipeline
from jobhunter.sources.base import Search

NOW = datetime.now(UTC)


def raw(
    title="Backend Engineer",
    company="Acme",
    location="Pune",
    description="0-1 years experience",
    source="fake",
    days_ago=1,
) -> RawJob:
    return RawJob(
        source=source,
        source_job_id=f"{company}-{title}",
        url=f"https://example.com/{company}/{title}".replace(" ", "-"),
        title=title,
        company=company,
        location_text=location,
        description=description,
        posted_at=NOW - timedelta(days=days_ago),
    )


class FakeAdapter:
    def __init__(self, name: str, jobs: list[RawJob], fail_after: int | None = None):
        self.name = name
        self.jobs = jobs
        self.fail_after = fail_after
        self.calls = 0

    def fetch(self, search: Search) -> Iterator[RawJob]:
        self.calls += 1
        for i, job in enumerate(self.jobs):
            if self.fail_after is not None and i == self.fail_after:
                raise RuntimeError("source broke")
            yield job


def add_profile(session, titles=("Backend Engineer",), **kw) -> SearchProfile:
    profile = SearchProfile(
        name="default", titles=list(titles), cities=[], work_modes=["onsite", "hybrid", "remote"],
        min_years=0, max_years=1, exclude_words=["unpaid"], active=True, **kw,
    )
    session.add(profile)
    session.commit()
    return profile


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


# --- pure ------------------------------------------------------------------------

def test_provisional_score_penalties():
    assert provisional_score(FilterResult(True, title_score=1.0)) == 1.0
    assert provisional_score(FilterResult(True, title_score=1.0, experience_unstated=True)) == 0.9
    both = FilterResult(True, title_score=1.0, experience_unstated=True, remote_unconfirmed=True)
    assert provisional_score(both) == 0.8


# --- database ------------------------------------------------------------------------

def test_saves_filters_and_builds_digest(db_session):
    add_profile(db_session)
    jobs = [
        raw(),
        raw(title="Senior Backend Engineer", company="Beta"),
        raw(title="Java Developer", company="Gamma"),
        raw(company="Delta", description="Build APIs."),  # experience unstated -> lower score
    ]
    summary = run_pipeline(db_session, [FakeAdapter("fake", jobs)], now=NOW)

    assert count(db_session, Job) == 4
    assert summary.passed == 2
    assert [d.company for d in summary.digest] == ["Acme", "Delta"]
    reasons = dict(db_session.execute(
        select(Job.company, JobScore.filter_reason).join(JobScore, JobScore.job_id == Job.id)
    ).all())
    assert reasons["Beta"] == "senior title (senior)"
    assert reasons["Gamma"] == "title does not match your titles"


def test_failing_source_does_not_stop_the_run(db_session):
    add_profile(db_session)
    broken = FakeAdapter("broken", [raw(company="X"), raw(company="Y")], fail_after=1)
    good = FakeAdapter("good", [raw(company="Z")])
    summary = run_pipeline(db_session, [broken, good], now=NOW)

    runs = {r.source: r for r in db_session.scalars(select(Run))}
    assert runs["broken"].ok is False and "source broke" in runs["broken"].error
    assert runs["broken"].inserted == 1  # the job fetched before the failure is kept
    assert runs["good"].ok is True and runs["good"].inserted == 1
    assert {d.company for d in summary.digest} == {"X", "Z"}


def test_same_job_from_two_sources_is_stored_once(db_session):
    add_profile(db_session)
    a = FakeAdapter("adzuna", [raw(company="Acme Pvt Ltd", location="Bangalore")])
    b = FakeAdapter("linkedin_email", [raw(company="ACME", location="Bengaluru, India")])
    summary = run_pipeline(db_session, [a, b], now=NOW)

    assert count(db_session, Job) == 1
    assert [s.inserted for s in summary.sources] == [1, 0]
    assert len(summary.digest) == 1


def test_second_run_sends_nothing_new(db_session):
    add_profile(db_session)
    adapter = FakeAdapter("fake", [raw()])
    first = run_pipeline(db_session, [adapter], now=NOW)
    assert len(first.digest) == 1
    mark_emailed(db_session, [d.job_id for d in first.digest], now=NOW)

    second = run_pipeline(db_session, [adapter], now=NOW + timedelta(hours=1))
    assert second.digest == []
    assert second.sources[0].inserted == 0


def test_changing_titles_changes_the_next_run(db_session):
    profile = add_profile(db_session, titles=["Backend Engineer"])
    run_pipeline(db_session, [FakeAdapter("fake", [raw(title="Python Developer")])], now=NOW)
    assert run_pipeline(db_session, [], now=NOW).digest == []

    profile.titles = ["Python Developer"]
    db_session.commit()
    digest = run_pipeline(db_session, [], now=NOW).digest
    assert [d.title for d in digest] == ["Python Developer"]


def test_no_titles_means_no_fetch(db_session):
    add_profile(db_session, titles=[])
    adapter = FakeAdapter("fake", [raw()])
    summary = run_pipeline(db_session, [adapter], now=NOW)
    assert adapter.calls == 0
    assert summary.digest == []


def test_digest_respects_max_jobs(db_session):
    add_profile(db_session)
    jobs = [raw(company=f"C{i}") for i in range(5)]
    summary = run_pipeline(db_session, [FakeAdapter("fake", jobs)], now=NOW, max_jobs=3)
    assert len(summary.digest) == 3
