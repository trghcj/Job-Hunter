"""Call budgets for rate-limited free APIs. Ask before every call; stop when refused."""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from jobhunter.db import ApiUsage


class CallBudget(Protocol):
    def try_spend(self, provider: str) -> bool:
        """Reserve one call. False means do not call."""
        ...


class MemoryBudget:
    """In-memory budget for tests and dry runs."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def try_spend(self, provider: str) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


@dataclass(frozen=True)
class Limits:
    daily: int
    monthly: int
    per_run: int


class DbBudget:
    """Counts calls in api_usage (UTC days). The daily increment is a single atomic upsert,
    so two overlapping runs cannot both take the last call."""

    def __init__(self, session: Session, limits: dict[str, Limits], today: date | None = None):
        self.session = session
        self.limits = limits
        self.today = today or datetime.now(UTC).date()
        self.used: Counter[str] = Counter()

    def _month_total(self, provider: str) -> int:
        stmt = select(func.coalesce(func.sum(ApiUsage.calls), 0)).where(
            ApiUsage.provider == provider, ApiUsage.day >= self.today.replace(day=1)
        )
        return int(self.session.scalar(stmt) or 0)

    def try_spend(self, provider: str) -> bool:
        limits = self.limits[provider]
        if self.used[provider] >= limits.per_run:
            return False
        if self._month_total(provider) >= limits.monthly:
            return False
        stmt = (
            insert(ApiUsage)
            .values(day=self.today, provider=provider, calls=1)
            .on_conflict_do_update(
                index_elements=["day", "provider"],
                set_={"calls": ApiUsage.calls + 1},
                where=ApiUsage.calls < limits.daily,
            )
            .returning(ApiUsage.calls)
        )
        granted = self.session.execute(stmt).first() is not None
        self.session.commit()
        if granted:
            self.used[provider] += 1
        return granted
