"""Who gets their digest from their own Gmail, against Postgres (fake SMTP, no network)."""

import smtplib

import pytest
from cryptography.fernet import Fernet

import jobhunter.__main__ as cli
from jobhunter.mail_account import mail_view, save_account, set_send_digest
from jobhunter.pipeline import run_pipeline
from jobhunter.settings import Settings
from tests.test_email_gmail import FakeSMTP
from tests.test_pipeline import FakeAdapter, raw
from tests.test_pipeline_users import NOW, FakeSender, user

KEY = Fernet.generate_key().decode()
PASSWORD = "abcdwxyzabcdwxyz"


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeSMTP.instances, FakeSMTP.next_fail = [], None
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(settings_key=KEY))


def deliver(db, monkeypatch, key=KEY):
    resend = FakeSender()
    monkeypatch.setattr(cli, "_email_sender", lambda session: resend)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(settings_key=key))
    summary = run_pipeline(db, [FakeAdapter("fake", [raw()])], [], now=NOW)
    return resend, cli.deliver_digests(db, summary, NOW)


def gmail(db, uid, address, on=True):
    save_account(db, uid, address, PASSWORD, ["naukri.com"], KEY)
    if on:
        set_send_digest(db, uid, True)


def smtp_sent() -> list[tuple[str, str]]:
    return [(s.logins[0][0], m["To"]) for s in FakeSMTP.instances for m in s.messages]


def test_friend_gets_digest_from_own_gmail_without_domain(db_session, monkeypatch):
    user(db_session, 9, owner=True)
    friend = user(db_session, 1)
    gmail(db_session, friend, "u1@gmail.com")
    resend, problems = deliver(db_session, monkeypatch)
    assert resend.sent == [["owner@gmail.com"]] and problems == []
    assert smtp_sent() == [("u1@gmail.com", "u1@gmail.com")]
    assert FakeSMTP.instances[0].logins == [("u1@gmail.com", PASSWORD)]


def test_off_by_default(db_session, monkeypatch):
    user(db_session, 9, owner=True)
    friend = user(db_session, 1)
    gmail(db_session, friend, "u1@gmail.com", on=False)
    assert mail_view(db_session, friend).send_digest is False
    resend, _ = deliver(db_session, monkeypatch)
    assert resend.sent == [["owner@gmail.com"]] and FakeSMTP.instances == []


def test_flag_without_gmail_account_does_nothing(db_session, monkeypatch):
    user(db_session, 9, owner=True)
    friend = user(db_session, 1)
    set_send_digest(db_session, friend, True)
    resend, problems = deliver(db_session, monkeypatch)
    assert resend.sent == [["owner@gmail.com"]] and FakeSMTP.instances == [] and problems == []


def test_owner_with_flag_uses_gmail(db_session, monkeypatch):
    owner = user(db_session, 9, owner=True)
    gmail(db_session, owner, "owner@gmail.com")
    resend, problems = deliver(db_session, monkeypatch)
    assert resend.sent == [] and problems == []
    assert smtp_sent() == [("owner@gmail.com", "owner@gmail.com")]


def test_refused_password_reported_without_secret(db_session, monkeypatch):
    user(db_session, 9, owner=True)
    friend = user(db_session, 1)
    gmail(db_session, friend, "u1@gmail.com")
    FakeSMTP.next_fail = smtplib.SMTPAuthenticationError(535, PASSWORD.encode())
    resend, problems = deliver(db_session, monkeypatch)
    assert resend.sent == [["owner@gmail.com"]]
    assert problems == [f"user {friend}'s digest email: Gmail refused the app password"]


def test_undecryptable_password_reported_others_still_sent(db_session, monkeypatch):
    user(db_session, 9, owner=True)
    friend = user(db_session, 1)
    gmail(db_session, friend, "u1@gmail.com")
    resend, problems = deliver(db_session, monkeypatch, key=Fernet.generate_key().decode())
    assert resend.sent == [["owner@gmail.com"]] and FakeSMTP.instances == []
    assert len(problems) == 1 and "SETTINGS_KEY" in problems[0] and PASSWORD not in problems[0]
