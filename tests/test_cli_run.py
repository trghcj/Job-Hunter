"""The run command must alert (without secrets) even when it cannot reach the database."""

import jobhunter.__main__ as cli
import jobhunter.settings as settings_mod
from jobhunter.settings import Settings


class FakeSender:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, html_body, text_body):
        self.sent.append((to, subject, text_body))
        return "id"


def test_crash_sends_redacted_alert(monkeypatch):
    def broken_session():
        raise SystemExit("could not connect with password Sup3rSecretPw to db")

    sender = FakeSender()
    monkeypatch.setattr(cli, "_session", broken_session)
    monkeypatch.setattr(cli, "_email_sender_no_db", lambda: sender)
    monkeypatch.setattr(cli, "_secrets", lambda: ["Sup3rSecretPw"])
    monkeypatch.setattr(settings_mod, "get_settings", lambda: Settings(alert_to="me@example.com"))

    assert cli.main(["run"]) == 1
    to, subject, text = sender.sent[0]
    assert to == ["me@example.com"]
    assert subject.startswith("JobHunter: run had problems")
    assert "run crashed" in text
    assert "Sup3rSecretPw" not in text


def test_alert_failure_does_not_raise(monkeypatch):
    class Broken:
        def send(self, *a):
            raise RuntimeError("resend down")

    monkeypatch.setattr(cli, "_session", lambda: (_ for _ in ()).throw(SystemExit("db down")))
    monkeypatch.setattr(cli, "_email_sender_no_db", lambda: Broken())
    monkeypatch.setattr(cli, "_secrets", lambda: [])
    monkeypatch.setattr(settings_mod, "get_settings", lambda: Settings(alert_to="me@example.com"))
    assert cli.main(["run"]) == 1
