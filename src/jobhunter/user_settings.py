"""User settings stored in the database: seeding defaults and loading them for a run.

config.yaml only supplies first-run defaults. After seeding, the database (edited from
the settings page) is the single source of truth.
"""

import copy
import logging
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from jobhunter.db import OWNER_PLACEHOLDER_ID, AppSetting, SearchProfile, User, UserSetting
from jobhunter.sources.base import Search

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"

BUILTIN_PROFILE: dict[str, Any] = {
    "titles": [],
    "cities": [],
    "work_modes": ["onsite", "hybrid", "remote"],
    "min_years": 0,
    "max_years": 1,
    "exclude_words": [],
}
BUILTIN_SETTINGS: dict[str, Any] = {
    "digest": {"hour_ist": 7, "minute": 30, "max_jobs": 25},
    "sources": {"adzuna": {"enabled": True}},
    "llm": {"provider": "gemini", "max_calls_per_run": 30},
    "company_watchlist": [],
}


def load_defaults(path: Path = DEFAULT_CONFIG) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split config.yaml into (search profile defaults, app_settings key/values)."""
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    profile = copy.deepcopy(BUILTIN_PROFILE)
    profile.update(data.pop("search_profile", None) or {})
    if "exclude_words" in data:
        profile["exclude_words"] = list(data.pop("exclude_words") or [])

    settings = copy.deepcopy(BUILTIN_SETTINGS)
    settings.update(data)
    return profile, settings


# Settings each user has for themselves (user_settings); everything else is shared.
USER_KEYS = ("digest", "digest_to", "max_age_days", "profile", "llm_review", "digest_fit",
             "avoid")


def seed_defaults(session: Session, path: Path = DEFAULT_CONFIG) -> None:
    """Insert defaults that are missing. Never overwrites what anyone has saved.
    Shared settings go to app_settings; config.yaml's personal ones go to the owner."""
    profile, settings = load_defaults(path)
    if session.get(User, OWNER_PLACEHOLDER_ID) is not None:
        seed_user(session, OWNER_PLACEHOLDER_ID, profile)
    for key, value in settings.items():
        if key in USER_KEYS:
            stmt = insert(UserSetting).values(user_id=OWNER_PLACEHOLDER_ID, key=key, value=value)
        else:
            stmt = insert(AppSetting).values(key=key, value=value)
        session.execute(stmt.on_conflict_do_nothing())
    session.commit()
    seed_watchlist(session)


def seed_user(session: Session, user_id: int, profile: dict[str, Any] | None = None) -> None:
    """Give a user one active search profile (empty titles by default) if they have none."""
    has = select(func.count()).select_from(SearchProfile).where(SearchProfile.user_id == user_id)
    if session.scalar(has):
        return
    values = copy.deepcopy(profile if profile is not None else BUILTIN_PROFILE)
    session.add(SearchProfile(name="default", active=True, user_id=user_id, **values))
    session.commit()
    log.info("Seeded default search profile for user %d", user_id)


def seed_watchlist(session: Session) -> None:
    """Add default companies from data/company_watchlist.yaml that are newer than the
    version this database has seen. Your edits are kept and removed companies stay removed."""
    from jobhunter.sources.watchlist import (
        companies_from_setting,
        companies_to_setting,
        load_default_entries,
        merge_new_defaults,
    )

    entries = load_default_entries()
    latest = max((since for _, since in entries), default=1)
    seen = get_setting(session, "watchlist_version")
    if seen is None:
        seen = 1 if get_setting(session, "watchlist_seeded") else 0
    if int(seen) >= latest:
        return
    current = companies_from_setting(get_setting(session, "company_watchlist", []))
    merged = merge_new_defaults(current, entries, int(seen))
    set_setting(session, "company_watchlist", companies_to_setting(merged))
    set_setting(session, "watchlist_version", latest)
    set_setting(session, "watchlist_seeded", True)
    log.info("Watchlist: added %d companies (now %d)", len(merged) - len(current), len(merged))


def profile_to_search(row: SearchProfile) -> Search:
    return Search(
        titles=list(row.titles or []),
        cities=list(row.cities or []),
        work_modes=list(row.work_modes or []),
        min_years=row.min_years,
        max_years=row.max_years,
        exclude_words=list(row.exclude_words or []),
    )


def load_active_searches(session: Session, user_id: int) -> list[Search]:
    rows = session.scalars(
        select(SearchProfile)
        .where(SearchProfile.user_id == user_id, SearchProfile.active.is_(True))
        .order_by(SearchProfile.id)
    )
    return [profile_to_search(r) for r in rows]


def active_user_searches(session: Session) -> dict[int, list[Search]]:
    """Searches with titles of every user who is not blocked, by user id (ascending)."""
    rows = session.scalars(
        select(SearchProfile)
        .join(User, User.id == SearchProfile.user_id)
        .where(SearchProfile.active.is_(True), User.blocked.is_(False))
        .order_by(SearchProfile.user_id, SearchProfile.id)
    )
    found: dict[int, list[Search]] = {}
    for row in rows:
        if row.titles:
            found.setdefault(row.user_id, []).append(profile_to_search(row))
    return found


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(AppSetting, key)
    return default if row is None else row.value


def set_setting(session: Session, key: str, value: Any) -> None:
    stmt = insert(AppSetting).values(key=key, value=value)
    session.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value}))
    session.commit()


def get_user_setting(session: Session, user_id: int, key: str, default: Any = None) -> Any:
    row = session.get(UserSetting, (user_id, key))
    return default if row is None else row.value


def set_user_setting(session: Session, user_id: int, key: str, value: Any) -> None:
    stmt = insert(UserSetting).values(user_id=user_id, key=key, value=value)
    session.execute(stmt.on_conflict_do_update(index_elements=["user_id", "key"],
                                               set_={"value": value}))
    session.commit()
