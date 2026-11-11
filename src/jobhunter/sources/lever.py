"""Lever public postings (api.lever.co, no key needed). Descriptions come inline."""

from collections.abc import Iterator
from typing import Any

from jobhunter.models import RawJob
from jobhunter.sources.base import Search
from jobhunter.sources.boards import BoardAdapter, wanted
from jobhunter.sources.text import clean_html, parse_datetime
from jobhunter.sources.watchlist import Company

POSTINGS_URL = "https://api.lever.co/v0/postings/{slug}"
MODE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}


def _location(p: dict[str, Any]) -> str:
    cats = p.get("categories") or {}
    seen: set[str] = set()
    parts: list[str] = []
    for loc in [cats.get("location"), *(cats.get("allLocations") or [])]:
        if loc and loc.strip().lower() not in seen:
            seen.add(loc.strip().lower())
            parts.append(loc.strip())
    text = ", ".join(parts)
    if p.get("country") == "IN" and "india" not in text.lower():
        text = f"{text}, India" if text else "India"
    label = MODE_LABELS.get(str(p.get("workplaceType") or "").lower())
    if label:
        text = f"{text} ({label})" if text else label
    return text


def _description(p: dict[str, Any]) -> str | None:
    parts = [p.get("descriptionPlain"), p.get("descriptionBodyPlain")]
    for block in p.get("lists") or []:
        parts += [block.get("text"), clean_html(block.get("content"))]
    parts.append(p.get("additionalPlain"))
    text = " ".join(" ".join(x.split()) for x in parts if x)
    return text or None


def parse_lever(p: dict[str, Any], company: Company) -> RawJob:
    return RawJob(
        source="lever",
        source_job_id=str(p.get("id")),
        url=p.get("hostedUrl") or "",
        title=" ".join(str(p.get("text") or "").split()),
        company=company.name,
        location_text=_location(p),
        description=_description(p),
        posted_at=parse_datetime(p.get("createdAt"), millis=True),
    )


class LeverAdapter(BoardAdapter):
    name = "lever"

    def company_jobs(self, company: Company, search: Search) -> Iterator[RawJob]:
        postings = self.get_json(POSTINGS_URL.format(slug=company.slug), mode="json")
        if not isinstance(postings, list):
            raise ValueError("unexpected Lever response")
        for p in postings:
            job = parse_lever(p, company)
            if wanted(job.title, job.location_text, search):
                yield job
