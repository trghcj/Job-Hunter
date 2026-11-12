"""Save / Applied / Not interested links in the daily email, signed for the right user."""

import re
from datetime import UTC, datetime, timedelta

import jobhunter.__main__ as cli
from jobhunter.actions import action_key, read_token
from jobhunter.notify.email_resend import build_digest
from jobhunter.pipeline import DigestJob
from jobhunter.settings import Settings

NOW = datetime(2026, 11, 13, 2, 0, tzinfo=UTC)
BASE = "https://jh.example.com"
KEY = action_key("k" * 44)


def job(i: int, title="Backend Engineer") -> DigestJob:
    return DigestJob(i, title, "Acme", "Pune", "hybrid", f"https://example.com/{i}", None,
                     NOW - timedelta(days=1), "adzuna", 0.9)


def fake_links(job_id: int) -> dict[str, str]:
    return {a: f"{BASE}/a/{a}-{job_id}?x=1&y=<2>" for a in ("save", "applied", "not_interested")}


def test_digest_has_three_links_per_job():
    _, html, text = build_digest([job(1), job(2)], NOW, links=fake_links)
    for i in (1, 2):
        for action in ("save", "applied", "not_interested"):
            assert f"{BASE}/a/{action}-{i}" in html and f"{BASE}/a/{action}-{i}" in text
    assert ">Save<" in html and ">Applied<" in html and ">Not interested<" in html
    assert "  Not interested: " in text


def test_links_escaped_in_html():
    _, html, _ = build_digest([job(1)], NOW, links=fake_links)
    assert "&amp;y=&lt;2&gt;" in html and "y=<2>" not in html


def test_no_links_without_function():
    _, html, text = build_digest([job(1)], NOW)
    assert "/a/" not in html and "Not interested" not in text


def test_deliver_builds_links_for_that_user():
    settings = Settings(settings_key="k" * 44, public_url=BASE + "/")
    links_a, links_b = cli._action_links(settings, 5), cli._action_links(settings, 6)
    url = links_a(42)["applied"]
    assert url.startswith(f"{BASE}/a/")
    action = read_token(KEY, url.rsplit("/", 1)[1])
    assert (action.user_id, action.job_id, action.action) == (5, 42, "applied")
    assert read_token(KEY, links_b(42)["save"].rsplit("/", 1)[1]).user_id == 6
    assert re.fullmatch(r"[\w\-.]+", url.rsplit("/", 1)[1])


def test_no_links_without_key_or_url():
    assert cli._action_links(Settings(settings_key="", public_url=BASE), 5) is None
    assert cli._action_links(Settings(settings_key="k" * 44, public_url=""), 5) is None
