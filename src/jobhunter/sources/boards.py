"""Shared base for public careers boards (Greenhouse, Lever, Ashby).

Each board lists every job a company has worldwide, so jobs are pre-filtered here on two
cheap checks before anything is stored: the title matches one of your titles, and the job
is in India or remote-open-to-India. One broken board is logged and skipped; the source
fails only when most boards fail.
"""

import logging
import re
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from jobhunter.matching.filters import check_experience, title_matches
from jobhunter.models import RawJob
from jobhunter.normalize import detect_city, detect_country
from jobhunter.sources.base import Search
from jobhunter.sources.watchlist import Company

log = logging.getLogger(__name__)

USER_AGENT = "JobHunter/0.1 (personal job digest)"
# Plain "Remote" on a global company's board almost always means US-remote, so remote
# roles count only when the location says they are open to India or everywhere.
_OPEN_REMOTE = re.compile(r"\b(india|apac|asia|asia[- ]pacific|worldwide|anywhere|global)\b")


class BoardError(RuntimeError):
    """Most boards of a source failed."""


def india_or_open_remote(location: str | None) -> bool:
    text = location or ""
    if detect_country(text, detect_city(text)) == "IN":
        return True
    low = text.lower()
    return "remote" in low and bool(_OPEN_REMOTE.search(low))


def wanted(title: str, location: str | None, search: Search) -> bool:
    """Title matches, place fits, and the title alone is not too senior for your range.
    Checked before any description is fetched, to keep requests few."""
    return (
        title_matches(title, search.titles)
        and india_or_open_remote(location)
        and check_experience(title, None, search.min_years, search.max_years)[0]
    )


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def make_client() -> httpx.Client:
    return httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


class BoardAdapter:
    name = "board"

    def __init__(
        self,
        companies: list[Company],
        client: httpx.Client | None = None,
        delay: float = 0.3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.companies = [c for c in companies if c.ats == self.name]
        self.client = client or make_client()
        self.delay = delay
        self._sleep = sleep

    def company_jobs(self, company: Company, search: Search) -> Iterator[RawJob]:
        raise NotImplementedError

    def get_json(self, url: str, **params: Any) -> Any:
        resp = self.client.get(url, params=params or None)
        resp.raise_for_status()
        return resp.json()

    def post_json(self, url: str, body: dict[str, Any]) -> Any:
        resp = self.client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()

    def fetch(self, search: Search) -> Iterator[RawJob]:
        failures: list[str] = []
        for i, company in enumerate(self.companies):
            if i and self.delay:
                self._sleep(self.delay)
            try:
                yield from self.company_jobs(company, search)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                failures.append(f"{company.slug} ({_describe(exc)})")
                log.warning("%s board %s failed: %s", self.name, company.slug, _describe(exc))
        if failures and len(failures) * 2 > len(self.companies):
            raise BoardError(
                f"{len(failures)} of {len(self.companies)} {self.name} boards failed, "
                f"e.g. {', '.join(failures[:3])}"
            )
