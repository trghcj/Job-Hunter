"""Greenhouse public job boards (boards-api.greenhouse.io, no key needed).

The list endpoint has no descriptions, so the description is fetched only for jobs that
already passed the title and India/remote pre-filter.
"""

import logging
from collections.abc import Iterator

import httpx

from jobhunter.models import RawJob
from jobhunter.sources.base import Search
from jobhunter.sources.boards import BoardAdapter, wanted
from jobhunter.sources.text import clean_html, parse_datetime
from jobhunter.sources.watchlist import Company

log = logging.getLogger(__name__)

LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"


class GreenhouseAdapter(BoardAdapter):
    name = "greenhouse"

    def _description(self, company: Company, job_id: object) -> str | None:
        try:
            detail = self.get_json(DETAIL_URL.format(slug=company.slug, job_id=job_id))
        except (httpx.HTTPError, ValueError):
            log.info("greenhouse %s job %s: description unavailable", company.slug, job_id)
            return None
        return clean_html(detail.get("content")) or None

    def company_jobs(self, company: Company, search: Search) -> Iterator[RawJob]:
        data = self.get_json(LIST_URL.format(slug=company.slug))
        for j in data["jobs"]:
            title = clean_html(j.get("title"))
            location = (j.get("location") or {}).get("name") or ""
            if not wanted(title, location, search):
                continue
            yield RawJob(
                source=self.name,
                source_job_id=str(j["id"]),
                url=j.get("absolute_url") or "",
                title=title,
                company=company.name,
                location_text=location,
                description=self._description(company, j["id"]),
                posted_at=parse_datetime(j.get("first_published") or j.get("updated_at")),
            )
