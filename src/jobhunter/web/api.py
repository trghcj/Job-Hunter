"""JSON API for the JobHunter Chrome extension (/api/v1).

Authenticated only by `Authorization: Bearer <extension token>`; the session cookie is
never used here, so CSRF does not apply. Inputs are size-limited and job URLs must be on
a supported job site.
"""

import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, sessionmaker

from jobhunter.db import Job, JobScore, JobStatus, User
from jobhunter.extension_tokens import hash_token, user_for_token
from jobhunter.models import RawJob
from jobhunter.normalize import normalize_job
from jobhunter.pipeline import best_result, upsert_jobs, visible_to
from jobhunter.user_settings import get_user_setting, load_active_searches
from jobhunter.web.jobs_store import JobCard, JobsStore

SUPPORTED_HOSTS = {
    "www.linkedin.com", "www.naukri.com", "in.indeed.com", "www.indeed.com",
    "boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
    "jobs.smartrecruiters.com",
}
SUPPORTED_SUFFIXES = (".myworkdayjobs.com",)
NO_TITLES = "Add job titles on Settings first"
DEFAULT_MAX_AGE_DAYS = 14


def supported_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (
        host in SUPPORTED_HOSTS or any(host.endswith(s) for s in SUPPORTED_SUFFIXES))


class JobInfo(BaseModel):
    url: str = Field(max_length=2000)
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=20000)

    @field_validator("url")
    @classmethod
    def on_a_job_site(cls, url: str) -> str:
        if not supported_url(url):
            raise ValueError("url must be https on a supported job site")
        return url

    @field_validator("title", "company")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class SaveRequest(JobInfo):
    status: Literal["saved", "applied"]


@dataclass(frozen=True)
class Seen:
    status: str | None
    emailed: bool
    listed: bool


@dataclass(frozen=True)
class CheckResult:
    verdict: Literal["match", "no_match"]
    reasons: list[str]
    seen: Seen


class ApiGateway(Protocol):
    def user_for_token(self, raw: str) -> int | None: ...
    def check(self, user_id: int, info: JobInfo) -> CheckResult: ...
    def save(self, user_id: int, req: SaveRequest) -> int: ...
    def matches(self, user_id: int, limit: int) -> list[JobCard]: ...
    def me(self, user_id: int) -> dict: ...


class RateLimiter:
    """Sliding window: at most `limit` calls per `window` seconds per key (one process)."""

    def __init__(self, limit: int = 300, window: float = 3600,
                 clock=time.monotonic) -> None:
        self.limit, self.window, self._clock = limit, window, clock
        self._calls: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        calls = self._calls.setdefault(key, deque())
        while calls and calls[0] <= now - self.window:
            calls.popleft()
        if len(calls) >= self.limit:
            return False
        calls.append(now)
        return True


def _raw_job(info: JobInfo) -> RawJob:
    return RawJob(source="extension", source_job_id=None, url=info.url, title=info.title,
                  company=info.company, location_text=info.location,
                  description=info.description, posted_at=None)


class DbApi:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def user_for_token(self, raw: str) -> int | None:
        with self._sessions() as session:
            return user_for_token(session, raw, datetime.now(UTC))

    def _seen(self, session: Session, user_id: int, dedupe_key: str) -> Seen:
        job_id = session.scalar(select(Job.id).where(Job.dedupe_key == dedupe_key,
                                                     visible_to(user_id)))
        if job_id is None:
            return Seen(None, False, False)
        status = session.scalars(select(JobStatus).where(
            JobStatus.user_id == user_id, JobStatus.job_id == job_id)).first()
        listed = session.scalar(select(JobScore.passed_filters).where(
            and_(JobScore.user_id == user_id, JobScore.job_id == job_id)))
        return Seen(status.status if status and status.status != "new" else None,
                    bool(status and status.emailed_at), bool(listed))

    def check(self, user_id: int, info: JobInfo) -> CheckResult:
        job = normalize_job(_raw_job(info))
        with self._sessions() as session:
            searches = [s for s in load_active_searches(session, user_id) if s.titles]
            seen = self._seen(session, user_id, job.dedupe_key)
            if not searches:
                return CheckResult("no_match", [NO_TITLES], seen)
            max_age = int(get_user_setting(session, user_id, "max_age_days",
                                           DEFAULT_MAX_AGE_DAYS))
            result = best_result(job, searches, datetime.now(UTC), max_age)
        if result.passed:
            return CheckResult("match", [], seen)
        return CheckResult("no_match", [result.reason] if result.reason else [], seen)

    def save(self, user_id: int, req: SaveRequest) -> int:
        job = normalize_job(_raw_job(req))
        with self._sessions() as session:
            upsert_jobs(session, [job], datetime.now(UTC), private_to=user_id)
            job_id = session.scalar(select(Job.id).where(Job.dedupe_key == job.dedupe_key))
        JobsStore(self._sessions).set_status(user_id, job_id, req.status)
        return job_id

    def matches(self, user_id: int, limit: int) -> list[JobCard]:
        return JobsStore(self._sessions).list(user_id, "matches", limit)

    def me(self, user_id: int) -> dict:
        with self._sessions() as session:
            user = session.get(User, user_id)
            return {"email": user.email, "name": user.name}


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def api_router(gateway: ApiGateway, limiter: RateLimiter) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def current_user(request: Request, response: Response) -> int:
        response.headers["Cache-Control"] = "no-store"
        token = _bearer(request)
        if not token:
            raise HTTPException(401, "Connect the extension first.")
        if not limiter.allow(hash_token(token)):
            raise HTTPException(429, "Too many requests, try again later.")
        user_id = gateway.user_for_token(token)
        if user_id is None:
            raise HTTPException(401, "Connect the extension again.")
        return user_id

    @router.post("/check")
    def check(info: JobInfo, user_id: int = Depends(current_user)) -> dict:
        return asdict(gateway.check(user_id, info))

    @router.post("/jobs")
    def save(req: SaveRequest, user_id: int = Depends(current_user)) -> dict:
        return {"job_id": gateway.save(user_id, req), "status": req.status}

    @router.get("/matches")
    def matches(limit: int = Query(5, ge=1, le=20),
                user_id: int = Depends(current_user)) -> list[dict]:
        return [{"title": c.title, "company": c.company, "location": c.location_text,
                 "url": c.url, "posted_at": c.posted_at.isoformat() if c.posted_at else None}
                for c in gateway.matches(user_id, limit)]

    @router.get("/me")
    def me(user_id: int = Depends(current_user)) -> dict:
        return gateway.me(user_id)

    return router
