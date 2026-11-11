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

from jobhunter.db import AppSetting, SearchProfile
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
    "min_final_score": 0.55,
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


def seed_defaults(session: Session, path: Path = DEFAULT_CONFIG) -> None:
    """Insert defaults that are missing. Never overwrites what the user has saved."""
    profile, settings = load_defaults(path)

    if session.scalar(select(func.count()).select_from(SearchProfile)) == 0:
        session.add(SearchProfile(name="default", active=True, **profile))
        log.info("Seeded default search profile")

    for key, value in settings.items():
        stmt = insert(AppSetting).values(key=key, value=value).on_conflict_do_nothing()
        session.execute(stmt)
    session.commit()
    seed_watchlist(session)


def seed_watchlist(session: Session) -> None:
    """Fill an empty company watchlist from data/company_watchlist.yaml, once ever, so a
    list you clear on purpose stays cleared."""
    from jobhunter.sources.watchlist import companies_to_setting, load_default_watchlist

    if get_setting(session, "watchlist_seeded"):
        return
    if not get_setting(session, "company_watchlist"):
        companies = load_default_watchlist()
        set_setting(session, "company_watchlist", companies_to_setting(companies))
        log.info("Seeded company watchlist with %d companies", len(companies))
    set_setting(session, "watchlist_seeded", True)


def profile_to_search(row: SearchProfile) -> Search:
    return Search(
        titles=list(row.titles or []),
        cities=list(row.cities or []),
        work_modes=list(row.work_modes or []),
        min_years=row.min_years,
        max_years=row.max_years,
        exclude_words=list(row.exclude_words or []),
    )


def load_active_searches(session: Session) -> list[Search]:
    rows = session.scalars(
        select(SearchProfile).where(SearchProfile.active.is_(True)).order_by(SearchProfile.id)
    )
    return [profile_to_search(r) for r in rows]


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(AppSetting, key)
    return default if row is None else row.value


def set_setting(session: Session, key: str, value: Any) -> None:
    stmt = insert(AppSetting).values(key=key, value=value)
    session.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value}))
    session.commit()
