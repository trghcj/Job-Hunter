import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from jobhunter.notify.email_resend import (
    ResendError,
    ResendSender,
    build_digest,
    deliver_digest,
)
from jobhunter.pipeline import DigestJob
from jobhunter.usage import MemoryBudget

NOW = datetime(2026, 11, 11, 2, 0, tzinfo=UTC)  # 7:30 IST
SECRET = "re_super_secret"


def digest_job(i: int = 1, **kw) -> DigestJob:
    base = dict(
        job_id=i,
        title="Backend Engineer",
        company="Acme",
        location_text="Pune, Maharashtra",
        work_mode="hybrid",
        url=f"https://www.adzuna.in/details/{i}",
        salary_text="₹4.2L–₹6.2L a year",
        posted_at=NOW - timedelta(days=2),
        source="adzuna",
        score=0.9,
    )
    base.update(kw)
    return DigestJob(**base)


# --- building --------------------------------------------------------------------

def test_subject_counts_jobs_and_uses_ist_date():
    subject, _, _ = build_digest([digest_job(1), digest_job(2)], NOW)
    assert subject == "JobHunter: 2 new jobs · 11 Nov"


def test_singular_subject():
    subject, _, _ = build_digest([digest_job()], NOW)
    assert "1 new job ·" in subject


def test_html_has_links_details_and_escapes_text():
    job = digest_job(title="<script>alert(1)</script> Engineer", company="A & B")
    _, html, text = build_digest([job], NOW)
    assert 'href="https://www.adzuna.in/details/1"' in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "A &amp; B" in html
    assert "₹4.2L–₹6.2L a year" in html
    assert "Hybrid" in html and "2d ago" in html
    assert "https://www.adzuna.in/details/1" in text


def test_unknown_mode_and_missing_salary_are_omitted():
    _, html, _ = build_digest([digest_job(work_mode="unknown", salary_text=None)], NOW)
    assert "Unknown" not in html and "None" not in html


def test_unsafe_url_is_not_linked():
    _, html, _ = build_digest([digest_job(url="javascript:alert(1)")], NOW)
    assert "javascript:" not in html


# --- sending ----------------------------------------------------------------------

def sender_with(handler) -> tuple[ResendSender, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request):
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return ResendSender(SECRET, "JobHunter <onboarding@resend.dev>", client=client), seen


def test_send_posts_to_resend():
    sender, seen = sender_with(lambda r: httpx.Response(200, json={"id": "abc"}))
    msg_id = sender.send(["me@example.com"], "Subj", "<p>hi</p>", "hi")
    req = seen[0]
    assert msg_id == "abc"
    assert str(req.url) == "https://api.resend.com/emails"
    assert req.headers["Authorization"] == f"Bearer {SECRET}"
    body = json.loads(req.content)
    assert body == {
        "from": "JobHunter <onboarding@resend.dev>",
        "to": ["me@example.com"],
        "subject": "Subj",
        "html": "<p>hi</p>",
        "text": "hi",
    }


@pytest.mark.parametrize("status", [401, 403, 422, 429, 500])
def test_send_error_hides_key(status):
    sender, _ = sender_with(lambda r: httpx.Response(status, json={"message": "bad"}))
    with pytest.raises(ResendError) as exc:
        sender.send(["me@example.com"], "s", "h", "t")
    assert str(status) in str(exc.value) and "bad" in str(exc.value)
    assert SECRET not in str(exc.value)


def test_network_error_hides_key():
    def boom(request):
        raise httpx.ConnectError(f"boom {request.headers['Authorization']}")

    sender, _ = sender_with(boom)
    with pytest.raises(ResendError) as exc:
        sender.send(["me@example.com"], "s", "h", "t")
    assert SECRET not in str(exc.value)


# --- delivery flow (no database: callbacks recorded) ----------------------------------

class FakeSender:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[tuple] = []

    def send(self, to, subject, html, text):
        if self.fail:
            raise ResendError("HTTP 500: down")
        self.sent.append((to, subject))
        return "id-1"


def test_deliver_marks_jobs_after_successful_send():
    marked: list[list[int]] = []
    sender = FakeSender()
    result = deliver_digest(
        [digest_job(1), digest_job(2)], sender, ["me@example.com"], MemoryBudget(5), NOW,
        mark=lambda ids: marked.append(list(ids)),
    )
    assert result.sent and result.count == 2
    assert marked == [[1, 2]]
    assert sender.sent[0][0] == ["me@example.com"]


def test_deliver_empty_digest_sends_nothing():
    sender = FakeSender()
    result = deliver_digest([], sender, ["me@example.com"], MemoryBudget(5), NOW, mark=None)
    assert not result.sent and result.error is None and sender.sent == []


def test_deliver_failure_does_not_mark():
    marked: list = []
    result = deliver_digest(
        [digest_job()], FakeSender(fail=True), ["me@example.com"], MemoryBudget(5), NOW,
        mark=marked.append,
    )
    assert not result.sent and "HTTP 500" in result.error
    assert marked == []


def test_deliver_without_recipient_is_an_error():
    result = deliver_digest([digest_job()], FakeSender(), [], MemoryBudget(5), NOW, mark=None)
    assert not result.sent and "set-email" in result.error


def test_deliver_respects_email_budget():
    result = deliver_digest(
        [digest_job()], FakeSender(), ["me@example.com"], MemoryBudget(0), NOW, mark=None
    )
    assert not result.sent and "limit" in result.error
