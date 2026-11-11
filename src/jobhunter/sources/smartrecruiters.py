"""SmartRecruiters official public Posting API (api.smartrecruiters.com, no key needed).

Lists a company's India postings (country=in), then fetches the job ad only for jobs that
passed the title and seniority pre-filter.
"""

import logging
from collections.abc import Iterator
from typing import Any

import httpx

from jobhunter.models import RawJob
from jobhunter.sources.base import Search
from jobhunter.sources.boards import BoardAdapter, wanted
from jobhunter.sources.text import clean_html, parse_datetime
from jobhunter.sources.watchlist import Company

log = logging.getLogger(__name__)

LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
PAGE = 100
MAX_PAGES = 10
SECTIONS = ("jobDescription", "qualifications", "additionalInformation")


def _location(loc: dict[str, Any]) -> str:
    parts = [loc.get("city"), loc.get("region")]
    text = ", ".join(p for p in parts if p)
    text = f"{text}, India" if text else "India"
    if loc.get("remote"):
        text += " (Remote)"
    elif loc.get("hybrid"):
        text += " (Hybrid)"
    return text


class SmartRecruitersAdapter(BoardAdapter):
    name = "smartrecruiters"

    def _detail(self, url: str) -> dict[str, Any]:
        try:
            return self.get_json(url)
        except (httpx.HTTPError, ValueError):
            log.info("smartrecruiters detail unavailable: %s", url)
            return {}

    def company_jobs(self, company: Company, search: Search) -> Iterator[RawJob]:
        url = LIST_URL.format(slug=company.slug)
        for page in range(MAX_PAGES):
            data = self.get_json(url, country="in", limit=PAGE, offset=page * PAGE)
            postings = data.get("content") or []
            for p in postings:
                title = " ".join(str(p.get("name") or "").split())
                location = _location(p.get("location") or {})
                if wanted(title, location, search):
                    yield self._job(company, p, title, location, url)
            if len(postings) < PAGE or (page + 1) * PAGE >= int(data.get("totalFound") or 0):
                return

    def _job(self, company: Company, p: dict[str, Any], title: str, location: str,
             list_url: str) -> RawJob:
        detail = self._detail(p.get("ref") or f"{list_url}/{p.get('id')}")
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        text = " ".join(clean_html((sections.get(s) or {}).get("text")) for s in SECTIONS)
        return RawJob(
            source=self.name,
            source_job_id=str(p.get("id")),
            url=detail.get("postingUrl")
            or f"https://jobs.smartrecruiters.com/{company.slug}/{p.get('id')}",
            title=title,
            company=company.name,
            location_text=location,
            description=" ".join(text.split()) or None,
            posted_at=parse_datetime(p.get("releasedDate")),
        )
