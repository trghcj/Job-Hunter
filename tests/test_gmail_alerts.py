"""Gmail job-alert reading: encryption, parsers, and the IMAP adapter (fake mailbox)."""

from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from jobhunter.crypto import CryptoError, decrypt_secret, encrypt_secret
from jobhunter.sources.base import Search
from jobhunter.sources.email_alerts import parse_alert_email, parse_linkedin, parse_naukri
from jobhunter.sources.gmail_alerts import GmailAlertsAdapter, GmailError, gmail_query

KEY = Fernet.generate_key().decode()
SENT = datetime(2026, 11, 12, 3, 30, tzinfo=UTC)

LINKEDIN_HTML = """
<html><body><table>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4312345678/?trackingId=abc&refId=x">
  <img alt="Acme logo"></a></td>
<td><a href="https://www.linkedin.com/comm/jobs/view/4312345678/?trackingId=abc">Backend Engineer</a>
<p>Acme Technologies · Bengaluru, Karnataka, India</p>
<p>Actively recruiting</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4399999999/?trk=y">Python Developer</a>
<p>Beta Labs</p><p>Pune, Maharashtra, India (Hybrid)</p><p>Easy Apply</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/search/?keywords=backend">See all jobs</a></td></tr>
</table></body></html>
"""

NAUKRI_HTML = """
<html><body>
<a href="https://www.naukri.com/job-listings-python-developer-gamma-soft-noida-0-to-2-years-101125012345?src=alert">
Python Developer</a>
<div>Gamma Soft Pvt Ltd</div><div>0-2 Yrs</div><div>Noida, Delhi / NCR</div><div>3-6 Lacs P.A.</div>
<a href="https://www.naukri.com/job-listings-ai-engineer-delta-ai-bengaluru-1-to-3-years-101125054321">AI Engineer</a>
<div>Delta AI</div><div>1-3 Yrs</div><div>Bengaluru</div><div>Not disclosed</div>
<a href="https://www.naukri.com/mnjuser/recommendedjobs">View all jobs</a>
</body></html>
"""


def message(sender: str, html: str, subject="Jobs for you") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "me@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 12 Nov 2026 03:30:00 +0000"
    msg.set_content("plain version")
    msg.add_alternative(html, subtype="html")
    return msg


# --- encryption ------------------------------------------------------------------------------

def test_encrypt_round_trip_and_wrong_key():
    token = encrypt_secret("abcd efgh ijkl mnop", KEY)
    assert b"abcd" not in token
    assert decrypt_secret(token, KEY) == "abcd efgh ijkl mnop"
    with pytest.raises(CryptoError):
        decrypt_secret(token, Fernet.generate_key().decode())
    with pytest.raises(CryptoError):
        encrypt_secret("x", "")


# --- parsers -----------------------------------------------------------------------------------

def test_parse_linkedin_alert():
    jobs = parse_linkedin(LINKEDIN_HTML, SENT)
    assert [(j.title, j.company, j.location_text) for j in jobs] == [
        ("Backend Engineer", "Acme Technologies", "Bengaluru, Karnataka, India"),
        ("Python Developer", "Beta Labs", "Pune, Maharashtra, India (Hybrid)"),
    ]
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4312345678/"
    assert jobs[0].source == "linkedin_email" and jobs[0].source_job_id == "4312345678"
    assert jobs[0].posted_at == SENT


def test_parse_naukri_alert_keeps_experience_for_the_filter():
    jobs = parse_naukri(NAUKRI_HTML, SENT)
    assert [(j.title, j.company, j.location_text) for j in jobs] == [
        ("Python Developer", "Gamma Soft Pvt Ltd", "Noida, Delhi / NCR"),
        ("AI Engineer", "Delta AI", "Bengaluru"),
    ]
    assert jobs[0].url == ("https://www.naukri.com/job-listings-python-developer-gamma-soft-"
                           "noida-0-to-2-years-101125012345")
    assert "Experience: 0-2 Yrs" in jobs[0].description
    assert jobs[0].salary_text == "3-6 Lacs P.A."
    assert jobs[1].salary_text is None


def test_parse_real_naukri_layout():
    html = (Path(__file__).parent / "fixtures" / "naukri_alert.html").read_text(encoding="utf-8")
    jobs = parse_naukri(html, SENT)
    assert [(j.title, j.company, j.location_text) for j in jobs] == [
        ("Flutter Developer Intern", "Saar It Resources", "Mumbai"),
        ("AI,SLM, LLM,RAG, Full Stack & Mobile Developer and", "Myblocks", "Ernakulam"),
        ("Backend Engineer", "Landmark Techedge", "Delhi Ncr Gurugram"),
    ]
    assert jobs[0].url == ("https://www.naukri.com/jd/job-listings-flutter-developer-intern-"
                           "saar-it-resources-pvt-ltd-mumbai-0-to-1-years-031126500597")
    assert jobs[0].source_job_id == "031126500597"
    assert jobs[0].description == "Experience: 0-1 Yrs. Internship"
    assert jobs[2].description == "Experience: 2-4 Yrs"
    assert jobs[2].salary_text == "6-12 Lacs P.A."


def test_naukri_marketing_email_has_no_jobs():
    html = '<p>Last few days to enroll for Quiz</p><a href="https://www.naukri.com/campus">Go</a>'
    assert parse_naukri(html, SENT) == []


def test_parse_alert_email_routes_by_sender():
    li = parse_alert_email(message("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
                                   LINKEDIN_HTML))
    nk = parse_alert_email(message("Naukri <jobalert@naukri.com>", NAUKRI_HTML))
    other = parse_alert_email(message("someone@example.com", LINKEDIN_HTML))
    assert len(li) == 2 and li[0].posted_at == SENT
    assert len(nk) == 2 and nk[0].source == "naukri_email"
    assert other == []


def test_parse_ignores_emails_without_job_links():
    assert parse_linkedin("<p>Someone viewed your profile</p>", SENT) == []


# --- IMAP adapter --------------------------------------------------------------------------------

class FakeIMAP:
    def __init__(self, messages: dict[bytes, EmailMessage], fail_login=False):
        self.messages = messages
        self.fail_login = fail_login
        self.searched: list[str] = []
        self.labelled: list[tuple[bytes, str]] = []
        self.logged_out = False
        self.fetched: list[str] = []

    def login(self, user, password):
        if self.fail_login:
            import imaplib
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b"ok"]

    def select(self, mailbox, readonly=False):
        return "OK", [b"3"]

    def uid(self, command, *args):
        if command == "SEARCH":
            self.searched.append(args[-1])
            return "OK", [b" ".join(self.messages)]
        if command == "FETCH":
            self.fetched.append(args[1])
            return "OK", [(b"1 (BODY[] {1}", self.messages[args[0]].as_bytes()), b")"]
        if command == "STORE":
            self.labelled.append((args[0], args[2]))
            return "OK", [b""]
        raise AssertionError(command)

    def logout(self):
        self.logged_out = True


def adapter(imap, **kw) -> GmailAlertsAdapter:
    return GmailAlertsAdapter("me@gmail.com", "app pass word xyzw",
                              ["jobalerts-noreply@linkedin.com", "naukri.com"],
                              imap_factory=lambda host, port: imap, **kw)


def test_gmail_query_excludes_processed_label():
    q = gmail_query(["jobalerts-noreply@linkedin.com", "naukri.com"], "JobHunter", 7)
    assert q == ('from:(jobalerts-noreply@linkedin.com OR naukri.com) '
                 '-label:JobHunter newer_than:7d')


def test_adapter_reads_alerts_and_labels_them_once():
    imap = FakeIMAP({
        b"11": message("jobalerts-noreply@linkedin.com", LINKEDIN_HTML),
        b"12": message("jobalert@naukri.com", NAUKRI_HTML),
    })
    a = adapter(imap)
    jobs = list(a.fetch(Search(titles=["x"])))
    assert len(jobs) == 4
    assert {j.source for j in jobs} == {"linkedin_email", "naukri_email"}
    assert imap.labelled == [(b"11,12", "JobHunter")]
    assert imap.fetched == ["(BODY.PEEK[])"] * 2  # leaves the emails unread in your inbox
    assert imap.logged_out
    # a second profile in the same run does not read the mailbox again
    assert len(list(a.fetch(Search(titles=["y"])))) == 4
    assert len(imap.searched) == 1


def test_adapter_dry_run_does_not_label():
    imap = FakeIMAP({b"11": message("jobalerts-noreply@linkedin.com", LINKEDIN_HTML)})
    list(adapter(imap, mark=False).fetch(Search(titles=["x"])))
    assert imap.labelled == []


def test_login_failure_is_reported_without_the_password():
    with pytest.raises(GmailError) as exc:
        list(adapter(FakeIMAP({}, fail_login=True)).fetch(Search(titles=["x"])))
    assert "app pass word" not in str(exc.value)
    assert "app password" in str(exc.value).lower()


def test_count_waiting():
    imap = FakeIMAP({b"1": message("jobalert@naukri.com", NAUKRI_HTML)})
    assert adapter(imap).count_waiting() == 1
    assert imap.labelled == []


def test_ernakulam_counts_as_kochi():
    from jobhunter.normalize import detect_city
    assert detect_city("Ernakulam") == "Kochi"
