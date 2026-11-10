"""Failure alerts and the weekly heartbeat, so a broken pipeline never stays silent."""

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobhunter.db import Job, JobStatus, Run
from jobhunter.notify.email_resend import IST

STALE_RUNS = 3
HEARTBEAT_WEEKDAY = 6  # Sunday
HEARTBEAT_OVERDUE_DAYS = 8

_URL_PASSWORD = re.compile(r"([a-z][a-z0-9+.-]*://[^:/\s@]+:)([^@\s]+)(@)", re.I)


def redact(text: str, secrets: Iterable[str]) -> str:
    """Strip URL passwords and every known secret (and its pieces) from text."""
    out = _URL_PASSWORD.sub(r"\1***\3", text)
    for secret in secrets:
        if not secret:
            continue
        pieces = {secret, *re.split(r"[^A-Za-z0-9]+", secret)}
        for piece in sorted(pieces, key=len, reverse=True):
            if len(piece) >= 3:
                out = out.replace(piece, "***")
    return out


def _day(now: datetime) -> str:
    d = now.astimezone(IST)
    return f"{d.day} {d:%b}"


def _wrap_html(title: str, body: str) -> str:
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;'
        f'color:#111827"><h2 style="font-size:20px">{html.escape(title)}</h2>{body}</div>'
    )


# ------------------------------------------------------------------ alert

def build_alert(problems: list[str], now: datetime) -> tuple[str, str, str]:
    subject = f"JobHunter: run had problems · {_day(now)}"
    items = "".join(f"<li>{html.escape(p)}</li>" for p in problems)
    body = (
        f"<ul>{items}</ul><p style='color:#6b7280;font-size:13px'>Jobs from sources that "
        "worked were still processed. Check the run log on GitHub Actions for details.</p>"
    )
    text = subject + "\n\n" + "\n".join(f"- {p}" for p in problems)
    return subject, _wrap_html(subject, body), text


# ------------------------------------------------------------------ heartbeat

@dataclass(frozen=True)
class SourceWeek:
    source: str
    runs: int
    ok_runs: int
    fetched: int
    inserted: int
    last3_fetched: list[int] = field(default_factory=list)  # newest first, failed = 0


@dataclass(frozen=True)
class WeeklyStats:
    sources: list[SourceWeek]
    new_jobs: int
    emailed_jobs: int
    digests_sent: int


def heartbeat_due(last_sent: date | None, today: date) -> bool:
    if last_sent == today:
        return False
    if today.weekday() == HEARTBEAT_WEEKDAY:
        return True
    return last_sent is not None and (today - last_sent).days >= HEARTBEAT_OVERDUE_DAYS


def stale_sources(stats: WeeklyStats) -> list[str]:
    return [
        s.source for s in stats.sources
        if len(s.last3_fetched) >= STALE_RUNS and not any(s.last3_fetched[:STALE_RUNS])
    ]


def build_heartbeat(stats: WeeklyStats, now: datetime) -> tuple[str, str, str]:
    subject = f"JobHunter weekly: alive, {stats.new_jobs} new jobs · {_day(now)}"
    digests = "digest" if stats.digests_sent == 1 else "digests"
    lines = [f"{stats.emailed_jobs} jobs emailed in {stats.digests_sent} {digests} this week."]
    for s in stats.sources:
        lines.append(
            f"{s.source}: {s.ok_runs}/{s.runs} runs ok, {s.fetched} fetched, {s.inserted} new"
        )
    if not stats.sources:
        lines.append("No source runs recorded this week.")
    stale = stale_sources(stats)
    if stale:
        lines.append(f"No results for 3 days: {', '.join(stale)}. Check these sources.")
    body = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
    return subject, _wrap_html(subject, body), subject + "\n\n" + "\n".join(lines)


def weekly_stats(session: Session, now: datetime) -> WeeklyStats:
    cutoff = now - timedelta(days=7)
    rows = session.execute(
        select(
            Run.source,
            func.count(),
            func.count().filter(Run.ok.is_(True)),
            func.coalesce(func.sum(Run.fetched), 0),
            func.coalesce(func.sum(Run.inserted), 0),
        )
        .where(Run.started_at >= cutoff, Run.source != "email")
        .group_by(Run.source)
        .order_by(Run.source)
    ).all()

    sources = []
    for source, runs, ok_runs, fetched, inserted in rows:
        last = session.execute(
            select(Run.ok, Run.fetched).where(Run.source == source)
            .order_by(Run.id.desc()).limit(STALE_RUNS)
        ).all()
        last3 = [(f or 0) if ok else 0 for ok, f in last]
        sources.append(SourceWeek(source, runs, ok_runs, int(fetched), int(inserted), last3))

    new_jobs = session.scalar(select(func.count()).where(Job.first_seen_at >= cutoff)) or 0
    emailed = session.scalar(
        select(func.count()).where(JobStatus.emailed_at >= cutoff)
    ) or 0
    digests = session.scalar(
        select(func.count()).where(
            Run.source == "email", Run.ok.is_(True), Run.inserted > 0, Run.started_at >= cutoff
        )
    ) or 0
    return WeeklyStats(sources, int(new_jobs), int(emailed), int(digests))
