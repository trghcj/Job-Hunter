"""Cover letter and resume tips for one job, written by an AI when the user asks (Phase 6).

Only the latest draft per user per job is kept. Clicking Write or Redo is the user's consent
for that job; at most DAILY_LIMIT attempts per user per IST day are made, counted before the
call so failures count too. The profile goes out without its first line (profile.for_llm),
and the job text is untrusted (llm.client.build_draft_prompt).
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from jobhunter.db import DraftRow, DraftUsage, Job
from jobhunter.llm.client import JobForReview, LLMClient, LLMError
from jobhunter.pipeline import IST, visible_to
from jobhunter.profile import for_llm, load_profile

log = logging.getLogger(__name__)

DAILY_LIMIT = 10
MAX_EDIT = 6000
NO_PROFILE = "Upload your resume on Settings first."
LIMIT_REACHED = "Daily draft limit reached, try again tomorrow."
FAILED = "Couldn't write a draft right now, try again later."


class DraftError(ValueError):
    """A draft could not be made; the message is shown to the user."""


class JobNotFound(DraftError):
    """The job does not exist or this user may not see it."""


@dataclass(frozen=True)
class SavedDraft:
    letter: str
    tips: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


def _day(now: datetime):
    return now.astimezone(IST).date()


def used_today(session: Session, user_id: int, now: datetime) -> int:
    calls = session.scalar(select(DraftUsage.calls).where(
        DraftUsage.user_id == user_id, DraftUsage.day == _day(now)))
    return calls or 0


def _count_attempt(session: Session, user_id: int, now: datetime) -> None:
    stmt = insert(DraftUsage).values(user_id=user_id, day=_day(now), calls=1)
    session.execute(stmt.on_conflict_do_update(
        index_elements=[DraftUsage.user_id, DraftUsage.day],
        set_={"calls": DraftUsage.calls + 1}))
    session.commit()


def _visible_job(session: Session, user_id: int, job_id: int) -> Job:
    job = session.scalar(select(Job).where(Job.id == job_id, visible_to(user_id)))
    if job is None:
        raise JobNotFound("Job not found.")
    return job


def make_draft(session: Session, user_id: int, job_id: int, client: LLMClient,
               now: datetime) -> SavedDraft:
    """Write (or rewrite) this job's draft. Raises DraftError with a message for the user;
    on failure the existing draft is left as it was."""
    job = _visible_job(session, user_id, job_id)
    saved = load_profile(session, user_id)
    if saved is None:
        raise DraftError(NO_PROFILE)
    if used_today(session, user_id, now) >= DAILY_LIMIT:
        raise DraftError(LIMIT_REACHED)
    _count_attempt(session, user_id, now)
    try:
        draft = client.draft(for_llm(saved.text), JobForReview(
            job.id, job.title, job.company, job.location_text, job.description or ""))
    except LLMError as exc:
        log.warning("Draft failed: %s: %s", type(exc).__name__, exc)
        raise DraftError(FAILED) from exc
    values = {"letter": draft.letter, "tips": list(draft.tips), "created_at": now,
              "updated_at": now}
    stmt = insert(DraftRow).values(user_id=user_id, job_id=job_id, **values)
    session.execute(stmt.on_conflict_do_update(
        index_elements=[DraftRow.user_id, DraftRow.job_id], set_=values))
    session.commit()
    return SavedDraft(draft.letter, draft.tips, now, now)


def load_draft(session: Session, user_id: int, job_id: int) -> SavedDraft | None:
    row = session.scalar(select(DraftRow).where(DraftRow.user_id == user_id,
                                                DraftRow.job_id == job_id))
    if row is None:
        return None
    return SavedDraft(row.letter, tuple(row.tips or ()), row.created_at, row.updated_at)


def save_letter(session: Session, user_id: int, job_id: int, letter: str,
                now: datetime) -> bool:
    """Keep the user's edits to the letter; False when there is no draft to edit."""
    result = session.execute(
        update(DraftRow)
        .where(DraftRow.user_id == user_id, DraftRow.job_id == job_id)
        .values(letter=letter.strip()[:MAX_EDIT], updated_at=now))
    session.commit()
    return result.rowcount == 1
