"""Remotive public remote-jobs API.

Terms: link back to Remotive and name it as the source (the digest does both), and call at
most a few times a day; this adapter makes one call per run however many profiles exist.
Only roles open to India are kept.
"""

import re
from collections.abc import Iterator
from typing import Any

import httpx

from jobhunter.models import RawJob
from jobhunter.sources.base import Search
from jobhunter.sources.boards import make_client
from jobhunter.sources.text import clean_html, parse_datetime

API_URL = "https://remotive.com/api/remote-jobs"
_OPEN = re.compile(r"\b(worldwide|anywhere|india|apac|asia|global)\b", re.I)


def open_to_india(required_location: str | None) -> bool:
    text = (required_location or "").strip()
    return not text or bool(_OPEN.search(text))


def remote_location(required_location: str | None) -> str:
    text = (required_location or "").strip()
    if "india" in text.lower():
        return "Remote, India"
    return f"Remote ({text or 'Worldwide'})"


class RemotiveAdapter:
    name = "remotive"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or make_client()
        self._jobs: list[dict[str, Any]] | None = None

    def _all_jobs(self) -> list[dict[str, Any]]:
        if self._jobs is None:
            resp = self.client.get(API_URL)
            resp.raise_for_status()
            self._jobs = list(resp.json().get("jobs") or [])
        return self._jobs

    def fetch(self, search: Search) -> Iterator[RawJob]:
        for j in self._all_jobs():
            required = j.get("candidate_required_location")
            if not open_to_india(required):
                continue
            yield RawJob(
                source=self.name,
                source_job_id=str(j.get("id")),
                url=j.get("url") or "",
                title=" ".join(str(j.get("title") or "").split()),
                company=" ".join(str(j.get("company_name") or "Unknown").split()),
                location_text=remote_location(required),
                description=clean_html(j.get("description")) or None,
                posted_at=parse_datetime(j.get("publication_date")),
                salary_text=(j.get("salary") or None),
            )
