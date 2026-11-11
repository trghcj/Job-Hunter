"""Digest email: build plain HTML + text, send through Resend, then mark jobs as emailed.

Jobs are marked only after Resend accepts the email, so a failed send is retried by the
next run. Error messages never contain the API key.
"""

import html
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

from jobhunter.pipeline import DigestJob
from jobhunter.usage import CallBudget, Limits

log = logging.getLogger(__name__)

PROVIDER = "resend"
LIMITS = Limits(daily=100, monthly=3000, per_run=5)
RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "JobHunter <onboarding@resend.dev>"
IST = timezone(timedelta(hours=5, minutes=30))
MODE_LABELS = {"onsite": "On-site", "hybrid": "Hybrid", "remote": "Remote"}


class ResendError(RuntimeError):
    """Sending failed. Message is safe to log and store."""


class EmailSender(Protocol):
    def send(self, to: list[str], subject: str, html_body: str, text_body: str) -> str: ...


# ------------------------------------------------------------------ building

def _ago(posted: datetime | None, now: datetime) -> str | None:
    if posted is None:
        return None
    days = (now - posted).days
    return "today" if days <= 0 else f"{days}d ago"


def _safe_url(url: str) -> str | None:
    return url if url.startswith(("https://", "http://")) else None


def _details(job: DigestJob, now: datetime) -> list[str]:
    parts = [job.location_text, MODE_LABELS.get(job.work_mode), job.salary_text,
             _ago(job.posted_at, now)]
    return [p for p in parts if p]


FIT_COLOURS = {"strong": ("#E3EFE2", "#2F6B2C"), "good": ("#FBEBD2", "#7A4A0C"),
               "weak": ("#EEEAE4", "#5F574D")}


def _fit_label(job: DigestJob) -> str | None:
    return f"{job.level.capitalize()} fit" if job.level and job.reason else None


def _fit_html(job: DigestJob) -> str:
    label = _fit_label(job)
    if not label:
        return ""
    bg, fg = FIT_COLOURS.get(job.level, FIT_COLOURS["weak"])
    return (f'<div style="font-size:13px;color:#111827;margin-top:4px">'
            f'<span style="background:{bg};color:{fg};font-weight:700;border-radius:999px;'
            f'padding:1px 8px;font-size:12px">{html.escape(label)}</span> '
            f"{html.escape(job.reason)}</div>")


def _job_html(job: DigestJob, now: datetime) -> str:
    e = html.escape
    title = e(job.title)
    url = _safe_url(job.url)
    title_html = f'<a href="{e(url)}" style="color:#1a56db;text-decoration:none">{title}</a>' \
        if url else title
    details = " · ".join(e(d) for d in _details(job, now))
    return (
        '<tr><td style="padding:12px 0;border-bottom:1px solid #e5e7eb">'
        f'<div style="font-size:16px;font-weight:600">{title_html}</div>'
        f'<div style="font-size:14px;color:#111827;margin-top:2px">{e(job.company)}</div>'
        f'<div style="font-size:13px;color:#6b7280;margin-top:2px">{details}</div>'
        f"{_fit_html(job)}"
        f'<div style="font-size:12px;color:#9ca3af;margin-top:2px">'
        f"{'' if _fit_label(job) else f'score {job.score:.2f} · '}via {e(job.source)}</div>"
        "</td></tr>"
    )


def _job_text(job: DigestJob, now: datetime) -> str:
    lines = [f"{job.title} — {job.company}", "  " + " · ".join(_details(job, now))]
    if label := _fit_label(job):
        lines.append(f"  {label} · {job.reason}")
    if url := _safe_url(job.url):
        lines.append(f"  {url}")
    return "\n".join(lines)


def build_digest(jobs: Sequence[DigestJob], now: datetime) -> tuple[str, str, str]:
    """(subject, html, text)."""
    today = now.astimezone(IST)
    noun = "job" if len(jobs) == 1 else "jobs"
    subject = f"JobHunter: {len(jobs)} new {noun} · {today.day} {today:%b}"
    rows = "".join(_job_html(j, now) for j in jobs)
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;'
        'color:#111827">'
        f'<h2 style="font-size:20px;margin:0 0 4px">{html.escape(subject)}</h2>'
        '<p style="font-size:13px;color:#6b7280;margin:0 0 8px">Best matches first.</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>'
        "</div>"
    )
    text_body = subject + "\n\n" + "\n\n".join(_job_text(j, now) for j in jobs)
    return subject, html_body, text_body


# ------------------------------------------------------------------ sending

class ResendSender:
    def __init__(self, api_key: str, from_addr: str = DEFAULT_FROM,
                 client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self.from_addr = from_addr
        self.client = client or httpx.Client(timeout=30)

    def send(self, to: list[str], subject: str, html_body: str, text_body: str) -> str:
        payload = {"from": self.from_addr, "to": to, "subject": subject,
                   "html": html_body, "text": text_body}
        try:
            resp = self.client.post(
                RESEND_URL, json=payload, headers={"Authorization": f"Bearer {self._api_key}"}
            )
        except httpx.HTTPError as exc:
            raise ResendError(f"network error: {type(exc).__name__}") from None
        if resp.status_code >= 300:
            try:
                message = str(resp.json().get("message", ""))
            except ValueError:
                message = resp.text
            message = message.replace(self._api_key, "***")[:300]
            raise ResendError(f"HTTP {resp.status_code}: {message}") from None
        return str(resp.json().get("id", ""))


# ------------------------------------------------------------------ delivery

@dataclass(frozen=True)
class DeliveryResult:
    sent: bool
    count: int = 0
    error: str | None = None
    message_id: str | None = None


def deliver_digest(
    jobs: Sequence[DigestJob],
    sender: EmailSender,
    to: list[str],
    budget: CallBudget,
    now: datetime,
    mark: Callable[[list[int]], None] | None,
) -> DeliveryResult:
    """Send the digest if there is anything new; mark jobs emailed only on success."""
    if not jobs:
        return DeliveryResult(sent=False)
    if not to:
        return DeliveryResult(False, error="no digest recipient; run "
                              "`python -m jobhunter set-email you@example.com`")
    if not budget.try_spend(PROVIDER):
        return DeliveryResult(False, error="Resend email limit reached for today")
    subject, html_body, text_body = build_digest(jobs, now)
    try:
        message_id = sender.send(to, subject, html_body, text_body)
    except ResendError as exc:
        log.error("Digest email failed: %s", exc)
        return DeliveryResult(False, error=str(exc))
    if mark is not None:
        mark([j.job_id for j in jobs])
    log.info("Digest sent with %d jobs", len(jobs))
    return DeliveryResult(True, len(jobs), message_id=message_id)
