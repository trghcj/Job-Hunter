"""What the one-click email links (/a/{token}) may do, checked against the database.

A link only acts when its user still exists, is not blocked, is still invited, and can see
the job. It uses the same status change as My jobs.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from jobhunter import avoid as avoid_lists
from jobhunter.accounts import Access
from jobhunter.db import Job, User
from jobhunter.pipeline import visible_to
from jobhunter.profile import load_profile
from jobhunter.web.jobs_store import JobsStore
from jobhunter.web.store import rescore_user


@dataclass(frozen=True)
class JobBrief:
    title: str
    company: str


class DbActions:
    def __init__(self, session_factory: sessionmaker[Session], access: Access) -> None:
        self._sessions = session_factory
        self._access = access

    def _job(self, session: Session, user_id: int, job_id: int) -> Job | None:
        user = session.get(User, user_id)
        if user is None or user.blocked or not self._access.invited(user.email, user.is_owner):
            return None
        return session.scalar(select(Job).where(Job.id == job_id, visible_to(user_id)))

    def apply(self, user_id: int, job_id: int, status: str) -> JobBrief | None:
        """Set the job's status for this user; None when the link may not act."""
        with self._sessions() as session:
            job = self._job(session, user_id, job_id)
            if job is None:
                return None
            brief = JobBrief(job.title, job.company)
        return brief if JobsStore(self._sessions).set_status(user_id, job_id, status) else None

    def _profile_skills(self, session: Session, user_id: int) -> set[str]:
        profile = load_profile(session, user_id)
        return set(profile.skills) if profile else set()

    def reasons_for(self, user_id: int, job_id: int) -> list[str]:
        with self._sessions() as session:
            job = self._job(session, user_id, job_id)
            if job is None:
                return []
            return avoid_lists.reason_options(job, self._profile_skills(session, user_id))

    def add_reason(self, user_id: int, job_id: int, reason: str) -> bool:
        with self._sessions() as session:
            job = self._job(session, user_id, job_id)
            if job is None:
                return False
            added = avoid_lists.add_reason(session, user_id, job, reason,
                                           self._profile_skills(session, user_id))
            if added:
                rescore_user(session, user_id)
            return added
