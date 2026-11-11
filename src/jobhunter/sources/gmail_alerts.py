"""Read job-alert emails from your own Gmail over IMAP (app password, no browser login).

Finds alert emails from the configured senders that do not yet carry the processing
label, parses them, and then adds the label so each email is read once. The mailbox is
read once per run however many search profiles exist.
"""

import email
import imaplib
import logging
from collections.abc import Callable, Iterator
from email import policy
from email.message import EmailMessage

from jobhunter.models import RawJob
from jobhunter.sources.base import Search
from jobhunter.sources.email_alerts import parse_alert_email

log = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
DEFAULT_SENDERS = ["jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com", "naukri.com"]
DEFAULT_LABEL = "JobHunter"


class GmailError(RuntimeError):
    """Reading Gmail failed. Message is safe to log (no password)."""


def gmail_query(senders: list[str], label: str, days: int) -> str:
    return f"from:({' OR '.join(senders)}) -label:{label} newer_than:{days}d"


class GmailAlertsAdapter:
    name = "gmail_alerts"

    def __init__(
        self,
        address: str,
        app_password: str,
        senders: list[str],
        imap_factory: Callable[[str, int], imaplib.IMAP4] = imaplib.IMAP4_SSL,
        label: str = DEFAULT_LABEL,
        max_age_days: int = 7,
        mark: bool = True,
    ) -> None:
        self.address = address
        self._password = app_password
        self.senders = senders or DEFAULT_SENDERS
        self._factory = imap_factory
        self.label = label
        self.max_age_days = max_age_days
        self.mark = mark
        self._jobs: list[RawJob] | None = None

    def _connect(self) -> imaplib.IMAP4:
        try:
            imap = self._factory(IMAP_HOST, IMAP_PORT)
        except OSError as exc:
            raise GmailError(f"cannot reach Gmail: {type(exc).__name__}") from None
        try:
            imap.login(self.address, self._password)
        except imaplib.IMAP4.error:
            raise GmailError(
                "Gmail login failed: check the address and the app password "
                "(the account needs 2-Step Verification and an app password)"
            ) from None
        imap.select("INBOX")
        return imap

    def _search(self, imap: imaplib.IMAP4) -> list[bytes]:
        query = gmail_query(self.senders, self.label, self.max_age_days)
        _, data = imap.uid("SEARCH", "X-GM-RAW", f'"{query}"')
        return data[0].split() if data and data[0] else []

    def count_waiting(self) -> int:
        """For the settings page's Test connection button: logs in, counts, changes nothing."""
        imap = self._connect()
        try:
            return len(self._search(imap))
        finally:
            imap.logout()

    def _read(self) -> list[RawJob]:
        if self._jobs is not None:
            return self._jobs
        imap = self._connect()
        jobs: list[RawJob] = []
        try:
            uids = self._search(imap)
            for uid in uids:
                _, data = imap.uid("FETCH", uid, "(RFC822)")
                raw = next((part[1] for part in data if isinstance(part, tuple)), b"")
                msg = email.message_from_bytes(raw, policy=policy.default)
                if isinstance(msg, EmailMessage):
                    jobs += parse_alert_email(msg)
            if self.mark and uids:
                imap.uid("STORE", b",".join(uids), "+X-GM-LABELS", self.label)
            log.info("Gmail: %d alert emails, %d jobs", len(uids), len(jobs))
        except imaplib.IMAP4.error as exc:
            raise GmailError(f"Gmail read failed: {type(exc).__name__}") from None
        finally:
            try:
                imap.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
        self._jobs = jobs
        return jobs

    def fetch(self, search: Search) -> Iterator[RawJob]:
        yield from self._read()
