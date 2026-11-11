"""Daily AI review of each opted-in user's best new jobs (one call per user, at most 10).

Gemini's 1-5 rating is blended into the local fit score and its reason replaces the local
one. Any failure is logged as a `runs` row named llm:<user_id>; local scores stay and the run
carries on.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from jobhunter.db import Job, JobScore, JobStatus, LlmUsage, Run
from jobhunter.llm.client import JobForReview, LLMClient, LLMError, Review
from jobhunter.matching.fit import blend
from jobhunter.pipeline import ist_day_start, status_join, visible_to
from jobhunter.profile import for_llm, get_flag, load_profile

log = logging.getLogger(__name__)

JOBS_PER_REVIEW = 10
DEFAULT_MAX_CALLS = 30


@dataclass
class CallBudget:
    """AI calls still allowed in this run (app setting llm.max_calls_per_run)."""

    left: int


def _candidates(session: Session, user_id: int) -> list[tuple[Job, JobScore]]:
    stmt = (
        select(Job, JobScore)
        .join(JobScore, and_(JobScore.job_id == Job.id, JobScore.user_id == user_id))
        .outerjoin(JobStatus, status_join(user_id))
        .where(JobScore.passed_filters.is_(True), JobScore.llm_fit.is_(None),
               JobStatus.emailed_at.is_(None),
               or_(JobStatus.status.is_(None), JobStatus.status.in_(("new", "saved"))),
               visible_to(user_id))
        .order_by(JobScore.final_score.desc().nulls_last(), Job.id)
        .limit(JOBS_PER_REVIEW)
    )
    return [(job, score) for job, score in session.execute(stmt)]


def _count_call(session: Session, now: datetime, provider: str) -> None:
    day = ist_day_start(now).date()
    stmt = insert(LlmUsage).values(day=day, provider=provider, calls=1)
    session.execute(stmt.on_conflict_do_update(
        index_elements=[LlmUsage.day, LlmUsage.provider],
        set_={"calls": LlmUsage.calls + 1}))
    session.commit()


def _apply(scores: dict[int, JobScore], reviews: list[Review], model: str) -> None:
    for review in reviews:
        row = scores[review.job_id]
        row.llm_fit = review.fit
        row.llm_json = {"fit": review.fit, "reason": review.reason, "model": model}
        row.fit_reason = review.reason
        row.final_score = blend(row.final_score or 0.0, review.fit)


def review_top_jobs(session: Session, user_id: int, client: LLMClient | None,
                    now: datetime, budget: CallBudget) -> int:
    """Review this user's best unreviewed jobs. Returns how many reviews were saved."""
    if client is None or budget.left <= 0 or not get_flag(session, user_id):
        return 0
    profile = load_profile(session, user_id)
    rows = _candidates(session, user_id) if profile else []
    if not rows:
        return 0
    budget.left -= 1
    _count_call(session, now, client.provider)
    jobs = [JobForReview(job.id, job.title, job.company, job.location_text,
                         job.description or "") for job, _ in rows]
    try:
        reviews = client.review(for_llm(profile.text), jobs)
    except Exception as exc:  # one failing AI call must never stop the run
        message = str(exc) if isinstance(exc, LLMError) else type(exc).__name__
        log.warning("AI review for user %s failed: %s", user_id, message)
        session.add(Run(source=f"llm:{user_id}", started_at=now, finished_at=now, ok=False,
                        fetched=len(jobs), inserted=0, error=message))
        session.commit()
        return 0
    _apply({score.job_id: score for _, score in rows}, reviews,
           getattr(client, "model", client.provider))
    session.commit()
    return len(reviews)
