"""Amazon jobs in India (amazon.jobs search JSON, newest first, no key needed).

One adapter, not a watchlist entry: up to a few pages of the newest India jobs per run,
stopping at jobs older than the age limit; titles are pre-filtered locally.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from jobhunter.models import RawJob
from jobhunter.sources.base import Search
from jobhunter.sources.boards import make_client, wanted
from jobhunter.sources.text import clean_html

API_URL = "https://www.amazon.jobs/en/search.json"
PAGE = 100


def _posted(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%B %d, %Y").replace(tzinfo=UTC)
    except ValueError:
        return None


def _location(j: dict[str, Any]) -> str:
    city = str(j.get("city") or "").strip()
    if city.lower() == "virtual":
        return "India (Remote)"
    normalized = str(j.get("normalized_location") or "")
    if normalized.endswith(", IND"):
        return normalized[: -len(", IND")] + ", India"
    return f"{city}, India" if city else "India"


def parse_amazon(j: dict[str, Any]) -> RawJob:
    parts = [j.get("description"), j.get("basic_qualifications"),
             j.get("preferred_qualifications")]
    description = " ".join(clean_html(p) for p in parts if p)
    return RawJob(
        source="amazon",
        source_job_id=str(j.get("id_icims") or j.get("id")),
        url="https://www.amazon.jobs" + str(j.get("job_path") or ""),
        title=" ".join(str(j.get("title") or "").split()),
        company="Amazon",
        location_text=_location(j),
        description=description or None,
        posted_at=_posted(j.get("posted_date")),
    )


class AmazonAdapter:
    name = "amazon"

    def __init__(self, client: httpx.Client | None = None, max_pages: int = 3,
                 max_age_days: int = 14, now: datetime | None = None) -> None:
        self.client = client or make_client()
        self.max_pages = max_pages
        self.max_age_days = max_age_days
        self.now = now
        self._jobs: list[RawJob] | None = None

    def _recent(self) -> list[RawJob]:
        """Newest India jobs, fetched once per run whatever the number of profiles."""
        if self._jobs is not None:
            return self._jobs
        now = self.now or datetime.now(UTC)
        jobs: list[RawJob] = []
        for page in range(self.max_pages):
            resp = self.client.get(API_URL, params={
                "country": "IND", "sort": "recent", "result_limit": PAGE, "offset": page * PAGE,
            })
            resp.raise_for_status()
            rows = resp.json().get("jobs") or []
            for row in rows:
                job = parse_amazon(row)
                if job.posted_at and (now - job.posted_at).days > self.max_age_days:
                    self._jobs = jobs
                    return jobs
                jobs.append(job)
            if len(rows) < PAGE:
                break
        self._jobs = jobs
        return jobs

    def fetch(self, search: Search) -> Iterator[RawJob]:
        for job in self._recent():
            if wanted(job.title, job.location_text, search):
                yield job
