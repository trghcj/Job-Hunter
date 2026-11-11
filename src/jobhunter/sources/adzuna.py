"""Adzuna India adapter (official free API, country=in).

Free key limits: 25 calls/min, 250/day, 2,500/month. Every call is reserved from a
CallBudget first; the adapter stops quietly when the budget says no.
Errors never include the request URL, because the URL carries the API key.
"""

import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from jobhunter.models import RawJob
from jobhunter.sources.base import Search
from jobhunter.sources.text import clean_html, parse_datetime
from jobhunter.usage import CallBudget, Limits

log = logging.getLogger(__name__)
# httpx logs full request URLs (including app_key) at INFO. Keep it quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)

PROVIDER = "adzuna"
LIMITS = Limits(daily=250, monthly=2500, per_run=40)
SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/in/search/{page}"


class AdzunaError(RuntimeError):
    """Adzuna call failed. Message is safe to log and store (no key, no URL)."""


def _lakh(value: float) -> str:
    return f"₹{value / 1e5:.1f}".rstrip("0").rstrip(".") + "L"


def _salary_text(r: dict[str, Any]) -> str | None:
    if str(r.get("salary_is_predicted", "0")) == "1":
        return None  # Adzuna's own estimate, not from the employer
    lo, hi = r.get("salary_min"), r.get("salary_max")
    if lo and hi and lo != hi:
        return f"{_lakh(lo)}–{_lakh(hi)} a year"
    if lo or hi:
        return f"{_lakh(lo or hi)} a year"
    return None


def parse_result(r: dict[str, Any]) -> RawJob:
    return RawJob(
        source=PROVIDER,
        source_job_id=str(r["id"]) if r.get("id") is not None else None,
        url=r.get("redirect_url", ""),
        title=clean_html(r.get("title")),
        company=clean_html((r.get("company") or {}).get("display_name")) or "Unknown",
        location_text=(r.get("location") or {}).get("display_name"),
        description=clean_html(r.get("description")) or None,
        posted_at=parse_datetime(r.get("created")),
        salary_text=_salary_text(r),
    )


class AdzunaAdapter:
    name = PROVIDER

    def __init__(
        self,
        app_id: str,
        app_key: str,
        budget: CallBudget,
        client: httpx.Client | None = None,
        results_per_page: int = 50,
        max_pages: int = 2,
        max_days_old: int = 14,
        min_interval: float = 2.5,  # 25 calls/min
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._app_id = app_id
        self._app_key = app_key
        self.budget = budget
        self.client = client or httpx.Client(timeout=30)
        self.results_per_page = results_per_page
        self.max_pages = max_pages
        self.max_days_old = max_days_old
        self.min_interval = min_interval
        self._sleep = sleep
        self._last_call = 0.0

    def fetch(self, search: Search) -> Iterator[RawJob]:
        for title in search.titles:
            for page in range(1, self.max_pages + 1):
                if not self.budget.try_spend(PROVIDER):
                    log.warning("Adzuna call budget used up; stopping early")
                    return
                results = self._get(page, title).get("results") or []
                for r in results:
                    yield parse_result(r)
                if len(results) < self.results_per_page:
                    break

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, page: int, title: str) -> dict[str, Any]:
        self._throttle()
        params = {
            "app_id": self._app_id,
            "app_key": self._app_key,
            "results_per_page": self.results_per_page,
            "title_only": title,
            "max_days_old": self.max_days_old,
            "sort_by": "date",
            "content-type": "application/json",
        }
        try:
            resp = self.client.get(SEARCH_URL.format(page=page), params=params)
        except httpx.HTTPError as exc:
            raise AdzunaError(f"network error: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise AdzunaError(f"HTTP {resp.status_code} for title {title!r}, page {page}") from None
        try:
            return resp.json()
        except ValueError:
            raise AdzunaError("response was not JSON") from None
