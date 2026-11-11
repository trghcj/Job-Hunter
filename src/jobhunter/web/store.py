"""Reads and writes the web pages' values in PostgreSQL.

UserStore: one signed-in user's own settings, Gmail and run history.
AdminStore: the owner's shared settings (sources, company watchlist) and users.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from jobhunter import accounts
from jobhunter.crypto import CryptoError
from jobhunter.db import Run, SearchProfile
from jobhunter.mail_account import MailView, delete_account, gmail_adapter, mail_view, save_account
from jobhunter.sources.catalog import SOURCE_LABELS, is_enabled
from jobhunter.sources.gmail_alerts import GmailError
from jobhunter.sources.watchlist import Company, companies_from_setting, companies_to_setting
from jobhunter.user_settings import (
    BUILTIN_PROFILE,
    get_setting,
    get_user_setting,
    seed_user,
    set_setting,
    set_user_setting,
)
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


def _active_profile(session: Session, user_id: int) -> SearchProfile:
    def find() -> SearchProfile | None:
        return session.scalars(
            select(SearchProfile)
            .where(SearchProfile.user_id == user_id, SearchProfile.active.is_(True))
            .order_by(SearchProfile.id)
        ).first()

    profile = find()
    if profile is None:
        seed_user(session, user_id)
        profile = find()
    assert profile is not None
    return profile


class UserStore:
    """Everything here is scoped to one user id, fixed at construction."""

    def __init__(self, session_factory: sessionmaker[Session], user_id: int,
                 settings_key: str = "") -> None:
        self._sessions = session_factory
        self.user_id = user_id
        self._key = settings_key

    # ---- Gmail account (password is write-only from the page's point of view)

    def load_mail(self) -> MailView:
        with self._sessions() as session:
            return mail_view(session, self.user_id)

    def save_mail(self, address: str, app_password: str | None, senders: list[str]) -> None:
        with self._sessions() as session:
            save_account(session, self.user_id, address, app_password, senders, self._key)

    def delete_mail(self) -> None:
        with self._sessions() as session:
            delete_account(session, self.user_id)

    def test_mail(self) -> str:
        with self._sessions() as session:
            try:
                adapter = gmail_adapter(session, self.user_id, self._key)
                if adapter is None:
                    return "Save a Gmail account first."
                waiting = adapter.count_waiting()
            except (CryptoError, GmailError) as exc:
                return f"Not connected: {exc}"
        return f"Connected. {waiting} job-alert emails are waiting to be read."

    # ---- search profile and personal settings

    def load(self) -> ProfileForm:
        with self._sessions() as session:
            profile = _active_profile(session, self.user_id)
            digest = get_user_setting(session, self.user_id, "digest", {}) or {}
            to = get_user_setting(session, self.user_id, "digest_to", []) or []
            max_age = get_user_setting(session, self.user_id, "max_age_days", DEFAULT_MAX_AGE_DAYS)
            return ProfileForm(
                titles=list(profile.titles or []),
                cities=list(profile.cities or []),
                work_modes=list(profile.work_modes or BUILTIN_PROFILE["work_modes"]),
                min_years=profile.min_years,
                max_years=profile.max_years,
                exclude_words=list(profile.exclude_words or []),
                digest_to=[to] if isinstance(to, str) else list(to),
                max_jobs=int(digest.get("max_jobs", DEFAULT_MAX_JOBS)),
                max_age_days=int(max_age),
            )

    def save(self, form: ProfileForm) -> None:
        with self._sessions() as session:
            profile = _active_profile(session, self.user_id)
            profile.titles = form.titles
            profile.cities = form.cities
            profile.work_modes = form.work_modes
            profile.min_years = form.min_years
            profile.max_years = form.max_years
            profile.exclude_words = form.exclude_words
            session.commit()

            digest = dict(get_user_setting(session, self.user_id, "digest", {}) or {})
            digest["max_jobs"] = form.max_jobs
            set_user_setting(session, self.user_id, "digest", digest)
            set_user_setting(session, self.user_id, "digest_to", form.digest_to)
            set_user_setting(session, self.user_id, "max_age_days", form.max_age_days)

    def recent_runs(self, limit: int = 8) -> list[RunRow]:
        """Shared runs plus this user's own Gmail and email runs; never other users'."""
        with self._sessions() as session:
            personal = or_(Run.source.like("gmail_alerts:%"), Run.source.like("email:%"))
            mine = Run.source.in_((f"gmail_alerts:{self.user_id}", f"email:{self.user_id}"))
            stmt = (select(Run).where(or_(~personal, mine))
                    .order_by(Run.id.desc()).limit(limit))
            return [RunRow(r.source, r.started_at, r.ok, r.fetched, r.inserted, r.error)
                    for r in session.scalars(stmt)]


class AdminStore:
    """The owner's page: shared sources and watchlist, and the users list."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def load_sources(self) -> dict[str, bool]:
        with self._sessions() as session:
            sources = get_setting(session, "sources", {}) or {}
            return {name: is_enabled(sources, name) for name in SOURCE_LABELS}

    def load_watchlist(self) -> list[Company]:
        with self._sessions() as session:
            return companies_from_setting(get_setting(session, "company_watchlist", []))

    def save_sources_and_watchlist(self, enabled: dict[str, bool],
                                   watchlist: list[Company]) -> None:
        with self._sessions() as session:
            sources = dict(get_setting(session, "sources", {}) or {})  # keeps unknown keys
            for name, on in enabled.items():
                sources[name] = {**(sources.get(name) or {}), "enabled": on}
            set_setting(session, "sources", sources)
            set_setting(session, "company_watchlist", companies_to_setting(watchlist))

    def users(self) -> list[accounts.UserRow]:
        with self._sessions() as session:
            return accounts.list_users(session)

    def set_blocked(self, user_id: int, blocked: bool) -> None:
        with self._sessions() as session:
            accounts.set_blocked(session, user_id, blocked)
