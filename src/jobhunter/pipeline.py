"""fetch -> normalize -> dedupe -> filter -> score -> pick digest.

No source-specific code lives here. Each adapter runs inside its own try/except and gets
one row in `runs`; a failing source never stops the others.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from jobhunter.db import Job, JobScore, JobStatus, Run
from jobhunter.matching.filters import DEFAULT_MAX_AGE_DAYS, FilterResult, apply_filters
from jobhunter.models import RawJob
from jobhunter.normalize import NormalizedJob, normalize_job
from jobhunter.sources.base import Search, SourceAdapter
from jobhunter.user_settings import load_active_searches

log = logging.getLogger(__name__)

BATCH = 200
DEFAULT_MAX_JOBS = 25
EXPERIENCE_UNSTATED_PENALTY = 0.1
REMOTE_UNCONFIRMED_PENALTY = 0.1


@dataclass
class SourceResult:
    source: str
    fetched: int = 0
    inserted: int = 0
    ok: bool = True
    error: str | None = None


@dataclass(frozen=True)
class DigestJob:
    job_id: int
    title: str
    company: str
    location_text: str | None
    work_mode: str
    url: str
    salary_text: str | None
    posted_at: datetime | None
    source: str
    score: float


@dataclass
class RunSummary:
    sources: list[SourceResult] = field(default_factory=list)
    scored: int = 0
    passed: int = 0
    digest: list[DigestJob] = field(default_factory=list)


def _batches(items: list, size: int = BATCH):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ------------------------------------------------------------------ ingest

def upsert_jobs(session: Session, jobs: Sequence[NormalizedJob], now: datetime) -> int:
    """Insert new jobs, bump last_seen_at on known ones. Returns how many were new."""
    rows = [
        {
            "dedupe_key": j.dedupe_key,
            "source": j.raw.source,
            "source_job_id": j.raw.source_job_id,
            "url": j.raw.url,
            "title": j.raw.title,
            "company": j.raw.company,
            "location_text": j.raw.location_text,
            "city": j.city,
            "country": j.country,
            "work_mode": j.work_mode,
            "description": j.raw.description,
            "salary_text": j.raw.salary_text,
            "posted_at": j.raw.posted_at,
            "first_seen_at": now,
            "last_seen_at": now,
        }
        for j in jobs
    ]
    inserted = 0
    for chunk in _batches(rows):
        ins = insert(Job).values(chunk)
        stmt = ins.on_conflict_do_update(
            index_elements=[Job.dedupe_key], set_={"last_seen_at": ins.excluded.last_seen_at}
        ).returning(literal_column("(xmax = 0)"))  # true when the row was inserted
        inserted += sum(1 for was_new in session.scalars(stmt) if was_new)
    session.commit()
    return inserted


def ingest_source(
    session: Session, adapter: SourceAdapter, searches: list[Search], now: datetime
) -> SourceResult:
    result = SourceResult(source=adapter.name)
    run = Run(source=adapter.name, started_at=now)
    session.add(run)
    session.commit()

    jobs: dict[str, NormalizedJob] = {}
    try:
        for search in searches:
            for raw in adapter.fetch(search):
                result.fetched += 1
                job = normalize_job(raw)
                jobs.setdefault(job.dedupe_key, job)
    except Exception as exc:  # one broken source must not stop the run
        result.ok, result.error = False, f"{type(exc).__name__}: {exc}"[:500]
        log.error("Source %s failed: %s", adapter.name, result.error)

    try:
        result.inserted = upsert_jobs(session, list(jobs.values()), now)
    except Exception as exc:
        session.rollback()
        result.ok = False
        result.error = result.error or f"saving jobs failed: {type(exc).__name__}"
        log.error("Saving jobs from %s failed: %s", adapter.name, type(exc).__name__)

    run.finished_at = datetime.now(UTC)
    run.ok, run.error = result.ok, result.error
    run.fetched, run.inserted = result.fetched, result.inserted
    session.commit()
    log.info("%s: fetched %d, new %d", adapter.name, result.fetched, result.inserted)
    return result


# ------------------------------------------------------------------ filter + score

def provisional_score(result: FilterResult) -> float:
    """Phase 1 score until embeddings/LLM arrive in Phase 4: penalties only."""
    score = 1.0
    if result.experience_unstated:
        score -= EXPERIENCE_UNSTATED_PENALTY
    if result.remote_unconfirmed:
        score -= REMOTE_UNCONFIRMED_PENALTY
    return round(score, 4)


def _row_to_normalized(row: Job) -> NormalizedJob:
    raw = RawJob(
        source=row.source,
        source_job_id=row.source_job_id,
        url=row.url,
        title=row.title,
        company=row.company,
        location_text=row.location_text,
        description=row.description,
        posted_at=row.posted_at,
        salary_text=row.salary_text,
    )
    return NormalizedJob(raw, row.city, row.country, row.work_mode or "unknown", row.dedupe_key)


def best_result(
    job: NormalizedJob, searches: list[Search], now: datetime, max_age_days: int
) -> FilterResult:
    """A job passes if any active profile accepts it; otherwise report the closest miss."""
    results = [apply_filters(job, s, now=now, max_age_days=max_age_days) for s in searches]
    passed = [r for r in results if r.passed]
    if passed:
        return max(passed, key=provisional_score)
    return max(results, key=lambda r: r.title_score)


def _unsent_recent_jobs(session: Session, cutoff: datetime):
    return (
        select(Job)
        .outerjoin(JobStatus, JobStatus.job_id == Job.id)
        .where(Job.first_seen_at >= cutoff, JobStatus.emailed_at.is_(None))
    )


def refilter_recent(
    session: Session, searches: list[Search], now: datetime, max_age_days: int
) -> tuple[int, int]:
    """Re-run filters on every recent, unsent job, so settings changes apply at once."""
    cutoff = now - timedelta(days=max_age_days)
    rows = []
    for job in session.scalars(_unsent_recent_jobs(session, cutoff)):
        result = best_result(_row_to_normalized(job), searches, now, max_age_days)
        rows.append({
            "job_id": job.id,
            "passed_filters": result.passed,
            "filter_reason": result.reason,
            "title_score": result.title_score,
            "final_score": provisional_score(result) if result.passed else None,
            "scored_at": now,
        })
    for chunk in _batches(rows):
        ins = insert(JobScore).values(chunk)
        updates = ("passed_filters", "filter_reason", "title_score", "final_score", "scored_at")
        session.execute(ins.on_conflict_do_update(
            index_elements=[JobScore.job_id], set_={k: ins.excluded[k] for k in updates}
        ))
    session.commit()
    return len(rows), sum(1 for r in rows if r["passed_filters"])


# ------------------------------------------------------------------ digest

def select_digest(
    session: Session, now: datetime, max_age_days: int, max_jobs: int
) -> list[DigestJob]:
    cutoff = now - timedelta(days=max_age_days)
    stmt = (
        select(Job, JobScore.final_score)
        .join(JobScore, JobScore.job_id == Job.id)
        .outerjoin(JobStatus, JobStatus.job_id == Job.id)
        .where(
            JobScore.passed_filters.is_(True),
            JobStatus.emailed_at.is_(None),
            Job.first_seen_at >= cutoff,
        )
        .order_by(JobScore.final_score.desc(), Job.posted_at.desc().nulls_last(), Job.id)
        .limit(max_jobs)
    )
    return [
        DigestJob(
            job_id=job.id, title=job.title, company=job.company,
            location_text=job.location_text, work_mode=job.work_mode or "unknown",
            url=job.url, salary_text=job.salary_text, posted_at=job.posted_at,
            source=job.source, score=score or 0.0,
        )
        for job, score in session.execute(stmt)
    ]


def mark_emailed(session: Session, job_ids: Sequence[int], now: datetime) -> None:
    if not job_ids:
        return
    ins = insert(JobStatus).values(
        [{"job_id": i, "emailed_at": now, "updated_at": now} for i in job_ids]
    )
    session.execute(ins.on_conflict_do_update(
        index_elements=[JobStatus.job_id],
        set_={"emailed_at": ins.excluded.emailed_at, "updated_at": ins.excluded.updated_at},
    ))
    session.commit()


# ------------------------------------------------------------------ whole run

def run_pipeline(
    session: Session,
    adapters: Sequence[SourceAdapter],
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_jobs: int = DEFAULT_MAX_JOBS,
) -> RunSummary:
    now = now or datetime.now(UTC)
    summary = RunSummary()
    searches = [s for s in load_active_searches(session) if s.titles]
    if not searches:
        log.warning("No active search profile has titles; nothing to search for")
        return summary

    for adapter in adapters:
        summary.sources.append(ingest_source(session, adapter, searches, now))

    summary.scored, summary.passed = refilter_recent(session, searches, now, max_age_days)
    summary.digest = select_digest(session, now, max_age_days, max_jobs)
    return summary
