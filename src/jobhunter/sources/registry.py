"""Builds the enabled source adapters from app_settings['sources'], the company watchlist
and env secrets. A source missing from the settings uses its default (on, except where
it needs something you have not set up yet)."""

import logging
from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

from jobhunter.models import RawJob
from jobhunter.settings import Settings
from jobhunter.sources import adzuna
from jobhunter.sources.amazon import AmazonAdapter
from jobhunter.sources.ashby import AshbyAdapter
from jobhunter.sources.base import Search, SourceAdapter
from jobhunter.sources.catalog import is_enabled
from jobhunter.sources.greenhouse import GreenhouseAdapter
from jobhunter.sources.lever import LeverAdapter
from jobhunter.sources.remoteok import RemoteOKAdapter
from jobhunter.sources.remotive import RemotiveAdapter
from jobhunter.sources.smartrecruiters import SmartRecruitersAdapter
from jobhunter.sources.watchlist import Company
from jobhunter.sources.workday import WorkdayAdapter
from jobhunter.usage import DbBudget

log = logging.getLogger(__name__)

BOARD_ADAPTERS = (
    GreenhouseAdapter, LeverAdapter, AshbyAdapter, WorkdayAdapter, SmartRecruitersAdapter
)


class BrokenSource:
    """Stands in for a source that cannot start, so the run records why and alerts you."""

    def __init__(self, name: str, problem: str) -> None:
        self.name = name
        self.problem = problem

    def fetch(self, search: Search) -> Iterator[RawJob]:
        raise RuntimeError(self.problem)
        yield  # pragma: no cover  (makes this a generator)


def _gmail(session: Session, settings: Settings) -> SourceAdapter | None:
    from jobhunter.crypto import CryptoError
    from jobhunter.db import OWNER_PLACEHOLDER_ID
    from jobhunter.mail_account import gmail_adapter

    try:
        return gmail_adapter(session, OWNER_PLACEHOLDER_ID,
                             settings.settings_key.get_secret_value())
    except CryptoError as exc:
        return BrokenSource("gmail_alerts", str(exc))


def build_adapters(
    session: Session | None,
    settings: Settings,
    sources: dict[str, Any],
    watchlist: list[Company],
) -> list[SourceAdapter]:
    adapters: list[SourceAdapter] = []

    if is_enabled(sources, "adzuna"):
        key = settings.adzuna_app_key.get_secret_value()
        if settings.adzuna_app_id and key and session is not None:
            budget = DbBudget(session, {adzuna.PROVIDER: adzuna.LIMITS})
            adapters.append(adzuna.AdzunaAdapter(settings.adzuna_app_id, key, budget))
        else:
            log.warning("Adzuna is enabled but ADZUNA_APP_ID / ADZUNA_APP_KEY are not set")

    for adapter_cls in BOARD_ADAPTERS:
        if is_enabled(sources, adapter_cls.name):
            board = adapter_cls(watchlist)
            if board.companies:
                adapters.append(board)

    if is_enabled(sources, "remotive"):
        adapters.append(RemotiveAdapter())
    if is_enabled(sources, "remoteok"):
        adapters.append(RemoteOKAdapter())
    if is_enabled(sources, "amazon"):
        adapters.append(AmazonAdapter())
    if is_enabled(sources, "gmail_alerts") and session is not None:
        gmail = _gmail(session, settings)
        if gmail is not None:
            adapters.append(gmail)

    if not adapters:
        log.warning("No job sources are enabled")
    return adapters
