"""My jobs: a user's matches, saved and applied jobs, and status changes."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from jobhunter.db import Job, JobScore, JobStatus
from jobhunter.matching.fit import LEVEL_MIN, level_of
from jobhunter.pipeline import ist_day_start, status_join, visible_to
from jobhunter.user_settings import get_user_setting

Tab = Literal["matches", "saved", "applied"]
TABS: tuple[str, ...] = ("matches", "saved", "applied")
STATUSES = ("new", "saved", "applied", "hidden")
DEFAULT_MAX_AGE_DAYS = 14


@dataclass(frozen=True)
class JobCard:
    job_id: int
    title: str
    company: str
    location_text: str | None
    work_mode: str
    source: str
    posted_at: datetime | None
    url: str
    status: str
    score: float | None
    level: str | None = None  # strong / good / weak, once the user has a profile
    reason: str | None = None  # why it fits
    status_at: datetime | None = None  # when the user saved / applied / hid it


FIT_FILTERS = ("strong", "good")  # Matches can show only jobs at this level or better


class JobsView(Protocol):
    def list(self, user_id: int, tab: str, limit: int = 100,
             fit: str | None = None) -> list[JobCard]: ...
    def counts(self, user_id: int) -> dict[str, int]: ...
    def new_today(self, user_id: int) -> int: ...
    def set_status(self, user_id: int, job_id: int, status: str) -> bool: ...


def _card(job: Job, status: str | None, score: float | None, reason: str | None,
          status_at: datetime | None) -> JobCard:
    level = level_of(score) if reason and score is not None else None
    return JobCard(job.id, job.title, job.company, job.location_text, job.work_mode or "unknown",
                   job.source, job.posted_at, job.url, status or "new", score, level, reason,
                   status_at)


COLUMNS = (Job, JobStatus.status, JobScore.final_score, JobScore.fit_reason,
           JobStatus.updated_at)


def _tab_query(session: Session, user_id: int, tab: str, fit: str | None = None) -> Select:
    """The jobs on one tab, best first (Matches) or most recent first (Saved, Applied)."""
    if tab in ("saved", "applied"):
        return (select(*COLUMNS)
                .join(JobStatus, status_join(user_id))
                .outerjoin(JobScore, and_(JobScore.job_id == Job.id,
                                          JobScore.user_id == user_id))
                .where(JobStatus.status == tab, visible_to(user_id))
                .order_by(JobStatus.updated_at.desc().nulls_last(), Job.id.desc()))
    max_age = int(get_user_setting(session, user_id, "max_age_days", DEFAULT_MAX_AGE_DAYS))
    cutoff = datetime.now(UTC) - timedelta(days=max_age)
    stmt = (select(*COLUMNS)
            .join(JobScore, and_(JobScore.job_id == Job.id, JobScore.user_id == user_id))
            .outerjoin(JobStatus, status_join(user_id))
            .where(JobScore.passed_filters.is_(True), visible_to(user_id),
                   Job.first_seen_at >= cutoff,
                   or_(JobStatus.status.is_(None),
                       JobStatus.status.not_in(("hidden", "applied"))))
            .order_by(JobScore.final_score.desc().nulls_last(),
                      Job.posted_at.desc().nulls_last(), Job.id))
    if fit in FIT_FILTERS:  # a level exists only once the fit has a reason
        stmt = stmt.where(JobScore.fit_reason.is_not(None),
                          JobScore.final_score >= LEVEL_MIN[fit])
    return stmt

class JobsStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def list(self, user_id: int, tab: str, limit: int = 100,
             fit: str | None = None) -> list[JobCard]:
        with self._sessions() as session:
            stmt = _tab_query(session, user_id, tab, fit).limit(limit)
            return [_card(*row) for row in session.execute(stmt)]

    def counts(self, user_id: int) -> dict[str, int]:
        """How many jobs each tab holds (Matches counts every level)."""
        with self._sessions() as session:
            return {tab: session.scalar(select(func.count()).select_from(
                        _tab_query(session, user_id, tab).order_by(None).subquery())) or 0
                    for tab in TABS}

    def new_today(self, user_id: int) -> int:
        """Matches first seen since midnight India time (the greeting on My jobs)."""
        with self._sessions() as session:
            today = _tab_query(session, user_id, "matches").where(
                Job.first_seen_at >= ist_day_start(datetime.now(UTC)))
            return session.scalar(select(func.count()).select_from(
                today.order_by(None).subquery())) or 0

    def set_status(self, user_id: int, job_id: int, status: str) -> bool:
        """False (nothing changed) for an unknown status or a job this user cannot see."""
        if status not in STATUSES:
            return False
        with self._sessions() as session:
            visible = session.scalar(select(Job.id).where(Job.id == job_id, visible_to(user_id)))
            if visible is None:
                return False
            now = datetime.now(UTC)
            ins = insert(JobStatus).values(user_id=user_id, job_id=job_id, status=status,
                                           updated_at=now)
            session.execute(ins.on_conflict_do_update(
                index_elements=[JobStatus.user_id, JobStatus.job_id],
                set_={"status": status, "updated_at": now}))
            session.commit()
            return True
