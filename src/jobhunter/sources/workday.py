"""Workday careers sites ({tenant}.wdN.myworkdayjobs.com).

Reads the same public JSON the careers page itself loads; no login, no key. To keep
requests few: one call to learn the site's India filter, then India jobs newest first,
stopping at jobs older than the age limit (at most a few pages), and a detail call only
for jobs that already passed the title / place / seniority pre-filter.
"""

import logging
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from jobhunter.models import RawJob
from jobhunter.sources.base import Search
from jobhunter.sources.boards import BoardAdapter, wanted
from jobhunter.sources.text import clean_html, parse_datetime
from jobhunter.sources.watchlist import Company

log = logging.getLogger(__name__)

PAGE = 20  # Workday's maximum page size
MODE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "on-site": "On-site", "onsite": "On-site"}
_DAYS = re.compile(r"(\d+)\+?\s*days?\s*ago", re.I)


def posted_days(text: str | None) -> int | None:
    t = (text or "").lower()
    if "today" in t:
        return 0
    if "yesterday" in t:
        return 1
    m = _DAYS.search(t)
    return int(m.group(1)) if m else None


def _flat_values(facet: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """(facetParameter, value) pairs, following one level of nested facet groups."""
    for v in facet.get("values") or []:
        if "values" in v and v.get("facetParameter"):
            yield from _flat_values(v)
        else:
            yield facet.get("facetParameter", ""), v


def india_facets(facets: list[dict[str, Any]]) -> dict[str, list[str]] | None:
    """The filter that selects India: a country value named exactly 'India' if the site has
    one, otherwise every location value that mentions India."""
    partial: dict[str, list[str]] = {}
    for facet in facets or []:
        for param, v in _flat_values(facet):
            name = str(v.get("descriptor") or "")
            if not v.get("id") or not param:
                continue
            if name.strip().lower() == "india":
                return {param: [v["id"]]}
            if "india" in name.lower():
                partial.setdefault(param, []).append(v["id"])
    if not partial:
        return None
    param = max(partial, key=lambda p: len(partial[p]))
    return {param: partial[param]}


def _parse_slug(slug: str) -> tuple[str, str, str]:
    host_part, site = slug.split("/", 1)
    tenant, host = host_part.split(".", 1)
    return tenant, host, site


class WorkdayAdapter(BoardAdapter):
    name = "workday"

    def __init__(
        self,
        companies: list[Company],
        client: httpx.Client | None = None,
        delay: float = 0.5,
        sleep: Callable[[float], None] | None = None,
        max_pages: int = 5,
        max_age_days: int = 14,
        now: datetime | None = None,
    ) -> None:
        kwargs = {"sleep": sleep} if sleep else {}
        super().__init__(companies, client, delay, **kwargs)
        self.max_pages = max_pages
        self.max_age_days = max_age_days
        self.now = now

    def _detail(self, api: str, path: str) -> dict[str, Any]:
        try:
            return self.get_json(f"{api}{path}").get("jobPostingInfo") or {}
        except (httpx.HTTPError, ValueError):
            log.info("workday detail unavailable: %s", path)
            return {}

    def company_jobs(self, company: Company, search: Search) -> Iterator[RawJob]:
        tenant, host, site = _parse_slug(company.slug)
        api = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
        public = f"https://{tenant}.{host}.myworkdayjobs.com/{site}"
        now = self.now or datetime.now(UTC)

        first = self.post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0,
                                               "searchText": ""})
        facets = india_facets(first.get("facets") or [])
        # With the India filter the list is newest first; keyword search is not.
        body = {"appliedFacets": facets or {}, "searchText": "" if facets else "India"}

        for page in range(self.max_pages):
            data = self.post_json(f"{api}/jobs", {**body, "limit": PAGE, "offset": page * PAGE})
            postings = data.get("jobPostings") or []
            for p in postings:
                days = posted_days(p.get("postedOn"))
                if days is not None and days > self.max_age_days:
                    if facets:
                        return  # newest first: everything after this is older
                    continue
                title = " ".join(str(p.get("title") or "").split())
                location = str(p.get("locationsText") or "")
                if facets and not re.search(r"india", location, re.I):
                    location = f"{location}, India" if location else "India"
                if not wanted(title, location, search):
                    continue
                yield self._job(company, p, title, location, api, public, now, days)
            if len(postings) < PAGE:
                return

    def _job(self, company: Company, p: dict[str, Any], title: str, location: str, api: str,
             public: str, now: datetime, days: int | None) -> RawJob:
        path = str(p.get("externalPath") or "")
        info = self._detail(api, path)
        label = MODE_LABELS.get(str(info.get("remoteType") or "").lower())
        if label:
            location = f"{location} ({label})"
        posted = parse_datetime(info.get("startDate"))
        if posted is None and days is not None:
            posted = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0,
                                                          microsecond=0)
        return RawJob(
            source=self.name,
            source_job_id=str(info.get("jobReqId") or path),
            url=info.get("externalUrl") or f"{public}{path}",
            title=title,
            company=company.name,
            location_text=location,
            description=clean_html(info.get("jobDescription")) or None,
            posted_at=posted,
        )
