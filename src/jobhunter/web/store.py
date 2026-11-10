"""Reads and writes the settings page's values in PostgreSQL."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from jobhunter.db import Run, SearchProfile
from jobhunter.user_settings import BUILTIN_PROFILE, get_setting, seed_defaults, set_setting
from jobhunter.web.forms import ProfileForm

DEFAULT_MAX_JOBS = 25
DEFAULT_MAX_AGE_DAYS = 14


@dataclass(frozen=True)
class RunRow:
    source: str
    started_at: datetime | None
    ok: bool | None
    fetched: int | None
    inserted: int | None
    error: str | None


class SettingsStore(Protocol):
    def load(self) -> ProfileForm: ...
    def save(self, form: ProfileForm) -> None: ...
    def recent_runs(self, limit: int = 8) -> list[RunRow]: ...


def _active_profile(session: Session) -> SearchProfile | None:
    return session.scalars(
        select(SearchProfile).where(SearchProfile.active.is_(True)).order_by(SearchProfile.id)
    ).first()


class DbSettingsStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def load(self) -> ProfileForm:
        with self._sessions() as session:
            profile = _active_profile(session)
            if profile is None:
                seed_defaults(session)
                profile = _active_profile(session)
            assert profile is not None
            digest = get_setting(session, "digest", {}) or {}
            to = get_setting(session, "digest_to", []) or []
            return ProfileForm(
                titles=list(profile.titles or []),
                cities=list(profile.cities or []),
                work_modes=list(profile.work_modes or BUILTIN_PROFILE["work_modes"]),
                min_years=profile.min_years,
                max_years=profile.max_years,
                exclude_words=list(profile.exclude_words or []),
                digest_to=[to] if isinstance(to, str) else list(to),
                max_jobs=int(digest.get("max_jobs", DEFAULT_MAX_JOBS)),
                max_age_days=int(get_setting(session, "max_age_days", DEFAULT_MAX_AGE_DAYS)),
            )

    def save(self, form: ProfileForm) -> None:
        with self._sessions() as session:
            profile = _active_profile(session)
            if profile is None:
                seed_defaults(session)
                profile = _active_profile(session)
            assert profile is not None
            profile.titles = form.titles
            profile.cities = form.cities
            profile.work_modes = form.work_modes
            profile.min_years = form.min_years
            profile.max_years = form.max_years
            profile.exclude_words = form.exclude_words
            session.commit()

            digest = dict(get_setting(session, "digest", {}) or {})
            digest["max_jobs"] = form.max_jobs
            set_setting(session, "digest", digest)
            set_setting(session, "digest_to", form.digest_to)
            set_setting(session, "max_age_days", form.max_age_days)

    def recent_runs(self, limit: int = 8) -> list[RunRow]:
        with self._sessions() as session:
            rows = session.scalars(select(Run).order_by(Run.id.desc()).limit(limit))
            return [
                RunRow(r.source, r.started_at, r.ok, r.fetched, r.inserted, r.error) for r in rows
            ]
