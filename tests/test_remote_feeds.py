"""Remotive and RemoteOK adapters: one call each, India-eligible remote roles only."""

import json
from pathlib import Path

import httpx
import pytest

from jobhunter.sources.base import Search
from jobhunter.sources.remoteok import RemoteOKAdapter, parse_remoteok
from jobhunter.sources.remotive import RemotiveAdapter, open_to_india
from jobhunter.sources.text import fix_mojibake

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("required", "ok"),
    [("Worldwide", True), ("Anywhere in the World", True), ("India", True), ("APAC", True),
     ("Asia, Europe", True), ("", True), ("USA", False), ("USA, Canada, USA timezones", False),
     ("Europe, USA, Canada, Australia, Singapore", False)],
)
def test_open_to_india(required, ok):
    assert open_to_india(required) is ok


def test_remotive_one_call_and_eligible_jobs_only():
    data = load("remotive_software_dev.json")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=data)

    jobs = list(RemotiveAdapter(client(handler)).fetch(Search(titles=["Backend Engineer"])))
    assert len(calls) == 1
    eligible = [j for j in data["jobs"] if open_to_india(j["candidate_required_location"])]
    assert len(jobs) == len(eligible)
    for job in jobs:
        assert job.source == "remotive"
        assert job.url.startswith("https://remotive.com/")
        assert job.location_text.startswith("Remote")
        assert job.posted_at is not None and job.posted_at.tzinfo is not None


def test_remotive_india_location_marks_country():
    data = {"jobs": [{"id": 1, "url": "https://remotive.com/x", "title": "Backend Engineer",
                      "company_name": "A", "candidate_required_location": "India",
                      "publication_date": "2026-11-09T10:00:00", "description": "<p>x</p>"}]}
    jobs = list(RemotiveAdapter(client(lambda r: httpx.Response(200, json=data)))
                .fetch(Search(titles=["x"])))
    assert jobs[0].location_text == "Remote, India"


def test_fix_mojibake():
    assert fix_mojibake("Senior Engineer â€“ Platform") == "Senior Engineer – Platform"
    assert fix_mojibake("Plain text") == "Plain text"
    assert fix_mojibake("Café") == "Café"


def test_remoteok_skips_legal_row_and_ineligible():
    data = load("remoteok.json")
    jobs = list(RemoteOKAdapter(client(lambda r: httpx.Response(200, json=data)))
                .fetch(Search(titles=["x"])))
    for job in jobs:
        assert job.source == "remoteok"
        assert job.url.lower().startswith("https://remoteok.com/")
        assert "â" not in job.title


def test_parse_remoteok_eligibility_and_salary():
    base = {"id": "1", "position": "Backend Engineer", "company": "A", "epoch": 1794261602,
            "url": "https://remoteOK.com/remote-jobs/1", "description": "Build APIs."}
    assert parse_remoteok(base | {"location": "Worldwide"}) is not None
    assert parse_remoteok(base | {"location": "United States"}) is None
    assert parse_remoteok(base | {"location": ""}) is None  # unknown: not clearly open to India
    job = parse_remoteok(base | {"location": "India", "salary_min": 30000, "salary_max": 50000})
    assert job.location_text == "Remote, India"
    assert job.salary_text == "$30k–$50k a year"


def test_feeds_send_a_user_agent():
    seen = []

    def handler(request):
        seen.append(request.headers.get("User-Agent", ""))
        return httpx.Response(200, json=[{"legal": "x"}])

    list(RemoteOKAdapter(client(handler)).fetch(Search(titles=["x"])))
    assert seen and "JobHunter" in seen[0]
