"""My jobs: a user's matches, saved and applied jobs, and status changes."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from jobhunter.db import Job, JobScore, JobStatus
from jobhunter.pipeline import status_join, visible_to
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


class JobsView(Protocol):
    def list(self, user_id: int, tab: str, limit: int = 100) -> list[JobCard]: ...
    def set_status(self, user_id: int, job_id: int, status: str) -> bool: ...


def _card(job: Job, status: str | None, score: float | None) -> JobCard:
    return JobCard(job.id, job.title, job.company, job.location_text, job.work_mode or "unknown",
                   job.source, job.posted_at, job.url, status or "new", score)


class JobsStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def list(self, user_id: int, tab: str, limit: int = 100) -> list[JobCard]:
        with self._sessions() as session:
            if tab in ("saved", "applied"):
                stmt = (select(Job, JobStatus.status, JobScore.final_score)
                        .join(JobStatus, status_join(user_id))
                        .outerjoin(JobScore, and_(JobScore.job_id == Job.id,
                                                  JobScore.user_id == user_id))
                        .where(JobStatus.status == tab, visible_to(user_id))
                        .order_by(JobStatus.updated_at.desc().nulls_last(), Job.id.desc()))
            else:
                max_age = int(get_user_setting(session, user_id, "max_age_days",
                                               DEFAULT_MAX_AGE_DAYS))
                cutoff = datetime.now(UTC) - timedelta(days=max_age)
                stmt = (select(Job, JobStatus.status, JobScore.final_score)
                        .join(JobScore, and_(JobScore.job_id == Job.id,
                                             JobScore.user_id == user_id))
                        .outerjoin(JobStatus, status_join(user_id))
                        .where(JobScore.passed_filters.is_(True), visible_to(user_id),
                               Job.first_seen_at >= cutoff,
                               or_(JobStatus.status.is_(None),
                                   JobStatus.status.not_in(("hidden", "applied"))))
                        .order_by(JobScore.final_score.desc().nulls_last(),
                                  Job.posted_at.desc().nulls_last(), Job.id))
            return [_card(job, status, score)
                    for job, status, score in session.execute(stmt.limit(limit))]

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
