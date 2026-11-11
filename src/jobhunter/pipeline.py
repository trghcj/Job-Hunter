"""fetch -> normalize -> dedupe -> filter -> score -> pick digest.

No source-specific code lives here. Each adapter runs inside its own try/except and gets
one row in `runs`; a failing source never stops the others.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import ColumnElement, Select, and_, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from jobhunter.db import Job, JobOwner, JobScore, JobStatus, Run
from jobhunter.matching.filters import DEFAULT_MAX_AGE_DAYS, FilterResult, apply_filters
from jobhunter.matching.skills import extract_skills
from jobhunter.models import RawJob
from jobhunter.normalize import NormalizedJob, normalize_job
from jobhunter.sources.base import Search, SourceAdapter
from jobhunter.user_settings import active_user_searches, get_user_setting

log = logging.getLogger(__name__)

BATCH = 200
BACKFILL_BATCH = 500
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
class UserResult:
    scored: int = 0
    passed: int = 0
    digest: list[DigestJob] = field(default_factory=list)


@dataclass
class RunSummary:
    sources: list[SourceResult] = field(default_factory=list)
    users: dict[int, UserResult] = field(default_factory=dict)


def _batches(items: list, size: int = BATCH):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ------------------------------------------------------------------ ingest

def _job_row(j: NormalizedJob, now: datetime, private: bool) -> dict:
    return {
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
        "private": private,
        **job_skill_columns(j.raw.title, j.raw.description),
    }


def job_skill_columns(title: str, description: str | None) -> dict[str, list[str]]:
    """The skills a job names (title + description), and those in its title."""
    title_skills = extract_skills(title)
    return {"skills": sorted(title_skills | extract_skills(description)),
            "title_skills": sorted(title_skills)}


def backfill_job_skills(session: Session) -> int:
    """Fill skills for jobs saved before they were recorded. Returns how many."""
    done = 0
    while True:
        rows = session.scalars(select(Job).where(Job.skills.is_(None)).limit(BACKFILL_BATCH)).all()
        if not rows:
            return done
        for job in rows:
            cols = job_skill_columns(job.title, job.description)
            job.skills, job.title_skills = cols["skills"], cols["title_skills"]
        session.commit()
        done += len(rows)


def upsert_jobs(session: Session, jobs: Sequence[NormalizedJob], now: datetime,
                private_to: int | None = None) -> int:
    """Insert new jobs, bump last_seen_at on known ones. Returns how many were new.

    Public sources make a job public. A job from someone's Gmail (private_to) is visible
    only to them (job_owners) until a public source also finds it; it never turns a public
    job private."""
    rows = [_job_row(j, now, private_to is not None) for j in jobs]
    inserted = 0
    for chunk in _batches(rows):
        ins = insert(Job).values(chunk)
        updates = {"last_seen_at": ins.excluded.last_seen_at,
                   "skills": func.coalesce(Job.skills, ins.excluded.skills),
                   "title_skills": func.coalesce(Job.title_skills, ins.excluded.title_skills)}
        if private_to is None:
            updates["private"] = False
        stmt = ins.on_conflict_do_update(index_elements=[Job.dedupe_key], set_=updates)
        stmt = stmt.returning(Job.id, literal_column("(xmax = 0)"))  # true when inserted
        result = session.execute(stmt).all()
        inserted += sum(1 for _, was_new in result if was_new)
        if private_to is not None and result:
            owners = insert(JobOwner).values(
                [{"job_id": job_id, "user_id": private_to} for job_id, _ in result])
            session.execute(owners.on_conflict_do_nothing())
    session.commit()
    return inserted


def ingest_source(
    session: Session, adapter: SourceAdapter, searches: list[Search], now: datetime,
    private_to: int | None = None,
) -> SourceResult:
    name = adapter.name if private_to is None else f"{adapter.name}:{private_to}"
    result = SourceResult(source=name)
    run = Run(source=name, started_at=now)
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
        log.error("Source %s failed: %s", name, result.error)

    try:
        result.inserted = upsert_jobs(session, list(jobs.values()), now, private_to)
    except Exception as exc:
        session.rollback()
        result.ok = False
        result.error = result.error or f"saving jobs failed: {type(exc).__name__}"
        log.error("Saving jobs from %s failed: %s", name, type(exc).__name__)

    run.finished_at = datetime.now(UTC)
    run.ok, run.error = result.ok, result.error
    run.fetched, run.inserted = result.fetched, result.inserted
    session.commit()
    log.info("%s: fetched %d, new %d", name, result.fetched, result.inserted)
    return result


def combined_search(per_user: dict[int, list[Search]]) -> Search | None:
    """One search for the shared fetch: every user's titles (most common first), the widest
    experience range, all cities and work modes. Each user's own filters run afterwards."""
    counts: dict[str, int] = {}
    spelling: dict[str, str] = {}
    lows: list[int] = []
    highs: list[int] = []
    for searches in per_user.values():
        mine: set[str] = set()
        for search in searches:
            lows.append(search.min_years)
            highs.append(search.max_years)
            for title in search.titles:
                key = " ".join(title.lower().split())
                spelling.setdefault(key, " ".join(title.split()))
                mine.add(key)
        for key in mine:
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    ordered = sorted(counts, key=lambda k: (-counts[k], k))
    return Search(titles=[spelling[k] for k in ordered], cities=[],
                  work_modes=["onsite", "hybrid", "remote"], min_years=min(lows),
                  max_years=max(highs), exclude_words=[])


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


def visible_to(user_id: int) -> ColumnElement[bool]:
    """SQL condition: the job is public, or private to this user."""
    mine = select(JobOwner.job_id).where(JobOwner.job_id == Job.id,
                                         JobOwner.user_id == user_id).exists()
    return or_(Job.private.is_(False), mine)


def status_join(user_id: int) -> ColumnElement[bool]:
    return and_(JobStatus.job_id == Job.id, JobStatus.user_id == user_id)


def _open_for() -> ColumnElement[bool]:
    """Not yet emailed to the joined user and not hidden by them."""
    return and_(JobStatus.emailed_at.is_(None),
                or_(JobStatus.status.is_(None), JobStatus.status != "hidden"))


def visible_jobs(user_id: int) -> Select:
    """Jobs this user may see, with their own status row (if any) outer-joined."""
    return select(Job).outerjoin(JobStatus, status_join(user_id)).where(visible_to(user_id))


def refilter_for_user(
    session: Session, user_id: int, searches: list[Search], now: datetime, max_age_days: int
) -> tuple[int, int]:
    """Re-run this user's filters on every recent job they can see and were not sent,
    so settings changes apply at once."""
    cutoff = now - timedelta(days=max_age_days)
    stmt = visible_jobs(user_id).where(Job.first_seen_at >= cutoff, _open_for())
    rows = []
    for job in session.scalars(stmt):
        result = best_result(_row_to_normalized(job), searches, now, max_age_days)
        rows.append({
            "user_id": user_id,
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
            index_elements=[JobScore.user_id, JobScore.job_id],
            set_={k: ins.excluded[k] for k in updates},
        ))
    session.commit()
    return len(rows), sum(1 for r in rows if r["passed_filters"])


# ------------------------------------------------------------------ digest

def select_digest(
    session: Session, user_id: int, now: datetime, max_age_days: int, max_jobs: int
) -> list[DigestJob]:
    cutoff = now - timedelta(days=max_age_days)
    stmt = (
        select(Job, JobScore.final_score)
        .join(JobScore, and_(JobScore.job_id == Job.id, JobScore.user_id == user_id))
        .outerjoin(JobStatus, status_join(user_id))
        .where(
            JobScore.passed_filters.is_(True),
            _open_for(),
            visible_to(user_id),
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


def mark_emailed(session: Session, user_id: int, job_ids: Sequence[int], now: datetime) -> None:
    if not job_ids:
        return
    ins = insert(JobStatus).values(
        [{"user_id": user_id, "job_id": i, "emailed_at": now, "updated_at": now}
         for i in job_ids]
    )
    session.execute(ins.on_conflict_do_update(
        index_elements=[JobStatus.user_id, JobStatus.job_id],
        set_={"emailed_at": ins.excluded.emailed_at, "updated_at": ins.excluded.updated_at},
    ))
    session.commit()


# ------------------------------------------------------------------ once per day

IST = timezone(timedelta(hours=5, minutes=30))


def ist_day_start(now: datetime) -> datetime:
    """Midnight IST of `now`'s IST date, as a UTC datetime."""
    local = now.astimezone(IST)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(UTC)


def ran_successfully_today(session: Session, now: datetime) -> bool:
    """True if a run already finished its email step without error today (IST).
    Backup schedule times use this so you never get two digests in a day."""
    stmt = select(Run.id).where(
        Run.source == "email",
        Run.ok.is_(True),
        Run.started_at >= ist_day_start(now),
        Run.started_at < ist_day_start(now) + timedelta(days=1),
    )
    return session.scalars(stmt.limit(1)).first() is not None


# ------------------------------------------------------------------ whole run

def _user_limits(session: Session, user_id: int) -> tuple[int, int]:
    digest = get_user_setting(session, user_id, "digest", {}) or {}
    max_age = get_user_setting(session, user_id, "max_age_days", DEFAULT_MAX_AGE_DAYS)
    return int(max_age), int(digest.get("max_jobs", DEFAULT_MAX_JOBS))


def run_pipeline(
    session: Session,
    shared_adapters: Sequence[SourceAdapter],
    gmail: Sequence[tuple[int, SourceAdapter]] = (),
    now: datetime | None = None,
) -> RunSummary:
    """Fetch once for everyone, read each user's Gmail, then filter and pick a digest per
    user. Users without job titles (or blocked) are skipped."""
    now = now or datetime.now(UTC)
    summary = RunSummary()
    per_user = active_user_searches(session)
    combined = combined_search(per_user)
    if combined is None:
        log.warning("No user has job titles; nothing to search for")
        return summary

    for adapter in shared_adapters:
        summary.sources.append(ingest_source(session, adapter, [combined], now))
    for user_id, adapter in gmail:
        if user_id in per_user:
            summary.sources.append(
                ingest_source(session, adapter, [combined], now, private_to=user_id))

    for user_id, searches in per_user.items():
        max_age, max_jobs = _user_limits(session, user_id)
        scored, passed = refilter_for_user(session, user_id, searches, now, max_age)
        digest = select_digest(session, user_id, now, max_age, max_jobs)
        summary.users[user_id] = UserResult(scored, passed, digest)
    return summary
