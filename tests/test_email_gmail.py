"""Sending the digest from the user's own Gmail over SMTP (fake server, no network)."""

import smtplib
from datetime import UTC, datetime

import pytest

from jobhunter.notify.email_gmail import GMAIL_PROVIDER, GmailSender, GmailSendError
from jobhunter.notify.email_resend import deliver_digest
from jobhunter.usage import MemoryBudget
from tests.test_email import digest_job

ADDRESS, PASSWORD = "me@gmail.com", "abcdwxyzabcdwxyz"
NOW = datetime(2026, 11, 13, 2, 0, tzinfo=UTC)


class FakeSMTP:
    """Stands in for smtplib.SMTP_SSL; records what happened."""

    instances: list = []

    def __init__(self, host, port, timeout=None, fail=None):
        self.host, self.port, self.logins, self.messages = host, port, [], []
        self.fail = FakeSMTP.next_fail
        FakeSMTP.instances.append(self)

    next_fail = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if isinstance(self.fail, smtplib.SMTPAuthenticationError):
            raise self.fail
        self.logins.append((user, password))

    def send_message(self, msg):
        if self.fail is not None:
            raise self.fail
        self.messages.append(msg)


@pytest.fixture(autouse=True)
def reset():
    FakeSMTP.instances, FakeSMTP.next_fail = [], None


def sender() -> GmailSender:
    return GmailSender(ADDRESS, PASSWORD, smtp_factory=FakeSMTP)


def test_sends_from_own_gmail():
    message_id = sender().send(["me@gmail.com", "other@example.com"], "5 new jobs",
                               "<p>Hi</p>", "Hi")
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 465)
    assert smtp.logins == [(ADDRESS, PASSWORD)]
    msg = smtp.messages[0]
    assert ADDRESS in msg["From"] and msg["To"] == "me@gmail.com, other@example.com"
    assert msg["Subject"] == "5 new jobs" and message_id == msg["Message-ID"]
    parts = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in parts and "text/html" in parts


def test_wrong_password_message_is_safe():
    FakeSMTP.next_fail = smtplib.SMTPAuthenticationError(535, f"bad {PASSWORD}".encode())
    with pytest.raises(GmailSendError) as err:
        sender().send(["me@gmail.com"], "s", "h", "t")
    assert str(err.value) == "Gmail refused the app password"
    assert PASSWORD not in str(err.value) and err.value.__cause__ is None


def test_network_error_message_is_safe():
    FakeSMTP.next_fail = OSError(f"connection reset {PASSWORD}")
    with pytest.raises(GmailSendError) as err:
        sender().send(["me@gmail.com"], "s", "h", "t")
    assert str(err.value) == "Gmail sending failed (OSError)"


def test_deliver_counts_gmail_budget_and_marks_jobs():
    marked, budget = [], MemoryBudget(5)
    spent = []
    budget_try = budget.try_spend
    budget.try_spend = lambda provider: spent.append(provider) or budget_try(provider)
    result = deliver_digest([digest_job(1), digest_job(2)], sender(), [ADDRESS], budget, NOW,
                            mark=marked.extend)
    assert result.sent and result.count == 2 and marked == [1, 2]
    assert spent == [GMAIL_PROVIDER] == ["gmail_smtp"]


def test_deliver_gmail_limit_message():
    result = deliver_digest([digest_job()], sender(), [ADDRESS], MemoryBudget(0), NOW,
                            mark=None)
    assert not result.sent and result.error == "Gmail sending limit reached for today"
    assert FakeSMTP.instances == []


def test_deliver_gmail_failure_keeps_jobs_unmarked():
    FakeSMTP.next_fail = smtplib.SMTPAuthenticationError(535, b"no")
    marked = []
    result = deliver_digest([digest_job()], sender(), [ADDRESS], MemoryBudget(5), NOW,
                            mark=marked.extend)
    assert result.error == "Gmail refused the app password" and marked == []
