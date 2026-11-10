"""Contract every job source implements."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from jobhunter.models import RawJob


@dataclass
class Search:
    """One saved search profile, loaded from the database at run time."""

    titles: list[str]
    cities: list[str] = field(default_factory=list)  # empty = all India
    work_modes: list[str] = field(default_factory=lambda: ["onsite", "hybrid", "remote"])
    min_years: int = 0
    max_years: int = 1
    exclude_words: list[str] = field(default_factory=list)


class SourceAdapter(Protocol):
    name: str

    def fetch(self, search: Search) -> Iterable[RawJob]: ...
