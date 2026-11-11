"""Reads and writes the settings page's values in PostgreSQL."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from jobhunter.crypto import CryptoError
from jobhunter.db import Run, SearchProfile
from jobhunter.mail_account import MailView, delete_account, gmail_adapter, mail_view, save_account
from jobhunter.sources.catalog import SOURCE_LABELS, is_enabled
from jobhunter.sources.gmail_alerts import GmailError
from jobhunter.sources.watchlist import companies_from_setting, companies_to_setting
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
    def load_mail(self) -> MailView: ...
    def save_mail(self, address: str, app_password: str | None, senders: list[str]) -> None: ...
    def delete_mail(self) -> None: ...
    def test_mail(self) -> str: ...


def _active_profile(session: Session) -> SearchProfile | None:
    return session.scalars(
        select(SearchProfile).where(SearchProfile.active.is_(True)).order_by(SearchProfile.id)
    ).first()


class DbSettingsStore:
    def __init__(self, session_factory: sessionmaker[Session], settings_key: str = "") -> None:
        self._sessions = session_factory
        self._key = settings_key

    # ---- Gmail account (password is write-only from the page's point of view)

    def load_mail(self) -> MailView:
        with self._sessions() as session:
            return mail_view(session)

    def save_mail(self, address: str, app_password: str | None, senders: list[str]) -> None:
        with self._sessions() as session:
            save_account(session, address, app_password, senders, self._key)

    def delete_mail(self) -> None:
        with self._sessions() as session:
            delete_account(session)

    def test_mail(self) -> str:
        with self._sessions() as session:
            try:
                adapter = gmail_adapter(session, self._key)
                if adapter is None:
                    return "Save a Gmail account first."
                waiting = adapter.count_waiting()
            except (CryptoError, GmailError) as exc:
                return f"Not connected: {exc}"
        return f"Connected. {waiting} job-alert emails are waiting to be read."

    def load(self) -> ProfileForm:
        with self._sessions() as session:
            profile = _active_profile(session)
            if profile is None:
                seed_defaults(session)
                profile = _active_profile(session)
            assert profile is not None
            digest = get_setting(session, "digest", {}) or {}
            to = get_setting(session, "digest_to", []) or []
            sources = get_setting(session, "sources", {}) or {}
            return ProfileForm(
                sources={name: is_enabled(sources, name) for name in SOURCE_LABELS},
                watchlist=companies_from_setting(get_setting(session, "company_watchlist", [])),
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

            sources = dict(get_setting(session, "sources", {}) or {})  # keeps unknown keys
            for name, on in form.sources.items():
                sources[name] = {**(sources.get(name) or {}), "enabled": on}
            set_setting(session, "sources", sources)
            set_setting(session, "company_watchlist", companies_to_setting(form.watchlist))

    def recent_runs(self, limit: int = 8) -> list[RunRow]:
        with self._sessions() as session:
            rows = session.scalars(select(Run).order_by(Run.id.desc()).limit(limit))
            return [
                RunRow(r.source, r.started_at, r.ok, r.fetched, r.inserted, r.error) for r in rows
            ]
