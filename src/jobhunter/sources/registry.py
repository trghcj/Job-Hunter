"""Builds the enabled source adapters from app_settings['sources'] and env secrets."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from jobhunter.settings import Settings
from jobhunter.sources import adzuna
from jobhunter.sources.base import SourceAdapter
from jobhunter.usage import DbBudget

log = logging.getLogger(__name__)


def _enabled(sources: dict[str, Any], name: str) -> bool:
    return bool((sources.get(name) or {}).get("enabled"))


def build_adapters(
    session: Session, settings: Settings, sources: dict[str, Any]
) -> list[SourceAdapter]:
    adapters: list[SourceAdapter] = []

    if _enabled(sources, "adzuna"):
        key = settings.adzuna_app_key.get_secret_value()
        if settings.adzuna_app_id and key:
            budget = DbBudget(session, {adzuna.PROVIDER: adzuna.LIMITS})
            adapters.append(adzuna.AdzunaAdapter(settings.adzuna_app_id, key, budget))
        else:
            log.warning("Adzuna is enabled but ADZUNA_APP_ID / ADZUNA_APP_KEY are not set")

    if not adapters:
        log.warning("No job sources are enabled")
    return adapters
