"""Ashby public job boards (api.ashbyhq.com posting API, no key needed)."""

from collections.abc import Iterator
from typing import Any

from jobhunter.models import RawJob
from jobhunter.sources.base import Search
from jobhunter.sources.boards import BoardAdapter, wanted
from jobhunter.sources.text import clean_html, parse_datetime
from jobhunter.sources.watchlist import Company

BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
MODE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}


def _location(j: dict[str, Any]) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    secondary = [s.get("location") for s in j.get("secondaryLocations") or []]
    for loc in [j.get("location"), *secondary]:
        if loc and loc.strip().lower() not in seen:
            seen.add(loc.strip().lower())
            parts.append(loc.strip())
    text = ", ".join(parts)
    country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
    if country and country.lower() not in text.lower():
        text = f"{text}, {country}" if text else country
    label = MODE_LABELS.get(str(j.get("workplaceType") or "").lower())
    if label is None and j.get("isRemote"):
        label = "Remote"
    if label:
        text = f"{text} ({label})" if text else label
    return text


def parse_ashby(j: dict[str, Any], company: Company) -> RawJob:
    description = j.get("descriptionPlain") or clean_html(j.get("descriptionHtml"))
    return RawJob(
        source="ashby",
        source_job_id=str(j.get("id")),
        url=j.get("jobUrl") or "",
        title=" ".join(str(j.get("title") or "").split()),
        company=company.name,
        location_text=_location(j),
        description=" ".join(description.split()) if description else None,
        posted_at=parse_datetime(j.get("publishedAt")),
    )


class AshbyAdapter(BoardAdapter):
    name = "ashby"

    def company_jobs(self, company: Company, search: Search) -> Iterator[RawJob]:
        board = self.get_json(BOARD_URL.format(slug=company.slug))
        for j in board["jobs"]:
            if j.get("isListed") is False:
                continue
            job = parse_ashby(j, company)
            if wanted(job.title, job.location_text, search):
                yield job
