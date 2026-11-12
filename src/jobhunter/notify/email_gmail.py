"""Send a user's digest from their own Gmail over SMTP, with the app password they saved
for reading job alerts. Used only when they tick "Send my daily digest from this Gmail".

No domain is needed: the email goes from their Gmail to their own digest address(es).
Error messages never contain the password or the server's reply.
"""

import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from jobhunter.notify.email_resend import SendError
from jobhunter.usage import Limits

GMAIL_PROVIDER = "gmail_smtp"
GMAIL_LIMITS = Limits(daily=50, monthly=1500, per_run=50)
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465


class GmailSendError(SendError):
    """Gmail did not take the email. The message is safe to log and store."""


class GmailSender:
    provider = GMAIL_PROVIDER

    def __init__(self, address: str, app_password: str,
                 smtp_factory: Callable[..., smtplib.SMTP_SSL] | None = None,
                 timeout: float = 30) -> None:
        self.address = address
        self._password = app_password
        self._smtp = smtp_factory or smtplib.SMTP_SSL
        self._timeout = timeout

    def send(self, to: list[str], subject: str, html_body: str, text_body: str) -> str:
        msg = EmailMessage()
        msg["From"] = formataddr(("JobHunter", self.address))
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain="jobhunter.local")
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
        try:
            with self._smtp(SMTP_HOST, SMTP_PORT, timeout=self._timeout) as smtp:
                smtp.login(self.address, self._password)
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            raise GmailSendError("Gmail refused the app password") from None
        except (smtplib.SMTPException, OSError) as exc:
            raise GmailSendError(f"Gmail sending failed ({type(exc).__name__})") from None
        return msg["Message-ID"]
