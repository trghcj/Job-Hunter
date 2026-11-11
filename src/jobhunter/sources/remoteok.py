"""Remote OK public API (one call per run).

Terms: link back to Remote OK and name it as the source; the digest does both. The first
array element is a legal notice, not a job. Jobs are kept only when their location says
they are open to India or everywhere (an empty location usually means US-only).
"""

from collections.abc import Iterator
from typing import Any

import httpx

from jobhunter.models import RawJob
from jobhunter.sources.base import Search
from jobhunter.sources.boards import USER_AGENT, make_client
from jobhunter.sources.remotive import open_to_india, remote_location
from jobhunter.sources.text import clean_html, fix_mojibake, parse_datetime

API_URL = "https://remoteok.com/api"


def _salary(j: dict[str, Any]) -> str | None:
    lo, hi = j.get("salary_min") or 0, j.get("salary_max") or 0
    if lo and hi:
        return f"${lo // 1000}k–${hi // 1000}k a year"
    if lo or hi:
        return f"${(lo or hi) // 1000}k a year"
    return None


def parse_remoteok(j: dict[str, Any]) -> RawJob | None:
    if "legal" in j or not j.get("id"):
        return None
    location = fix_mojibake(j.get("location")).strip()
    if not location or not open_to_india(location):
        return None
    return RawJob(
        source="remoteok",
        source_job_id=str(j["id"]),
        url=j.get("url") or j.get("apply_url") or "",
        title=" ".join(fix_mojibake(j.get("position")).split()),
        company=" ".join(fix_mojibake(j.get("company")).split()) or "Unknown",
        location_text=remote_location(location),
        description=clean_html(fix_mojibake(j.get("description"))) or None,
        posted_at=parse_datetime(j.get("epoch")) or parse_datetime(j.get("date")),
        salary_text=_salary(j),
    )


class RemoteOKAdapter:
    name = "remoteok"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or make_client()
        self._rows: list[dict[str, Any]] | None = None

    def _all_rows(self) -> list[dict[str, Any]]:
        if self._rows is None:
            resp = self.client.get(API_URL, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            data = resp.json()
            self._rows = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
        return self._rows

    def fetch(self, search: Search) -> Iterator[RawJob]:
        for row in self._all_rows():
            job = parse_remoteok(row)
            if job is not None:
                yield job
