"""The one shared job format every source adapter returns."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class RawJob:
    source: str
    source_job_id: str | None
    url: str
    title: str
    company: str
    location_text: str | None
    description: str | None
    posted_at: datetime | None
    salary_text: str | None = None
