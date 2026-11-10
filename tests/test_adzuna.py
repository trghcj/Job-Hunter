import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from jobhunter.matching.filters import apply_filters
from jobhunter.normalize import normalize_job
from jobhunter.sources.adzuna import AdzunaAdapter, AdzunaError, parse_result
from jobhunter.sources.base import Search
from jobhunter.usage import MemoryBudget

FIXTURE = Path(__file__).parent / "fixtures" / "adzuna_in_python_developer.json"
SECRET = "super-secret-key"


def fixture_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def fake_result(i: int) -> dict:
    return {
        "id": str(i),
        "title": f"Python Developer {i}",
        "company": {"display_name": "Acme"},
        "location": {"display_name": "Pune, Maharashtra"},
        "description": "Build APIs.",
        "created": "2026-11-09T10:00:00Z",
        "redirect_url": f"https://www.adzuna.in/details/{i}",
    }


class Recorder:
    """httpx transport that records requests and replies from a handler."""

    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self.handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def adapter(handler, budget=None, **kwargs) -> tuple[AdzunaAdapter, Recorder]:
    rec = Recorder(handler)
    client = httpx.Client(transport=httpx.MockTransport(rec))
    a = AdzunaAdapter(
        app_id="id123",
        app_key=SECRET,
        budget=budget or MemoryBudget(100),
        client=client,
        min_interval=0,
        **kwargs,
    )
    return a, rec


# --- parsing -------------------------------------------------------------------

def test_parse_fixture_first_result():
    job = parse_result(fixture_data()["results"][0])
    assert job.source == "adzuna"
    assert job.source_job_id == "5960512571"
    assert job.title == "Python Developer"
    assert job.company == "Turing"
    assert job.location_text == "Bangalore, Karnataka"
    assert job.posted_at == datetime(2026, 11, 10, 21, 59, 47, tzinfo=UTC)
    assert job.salary_text == "₹4.2L–₹6.2L a year"
    assert job.url.startswith("https://www.adzuna.in/details/5960512571")


def test_parse_strips_html_and_skips_predicted_salary():
    raw = fake_result(1) | {
        "title": "<strong>Python</strong> Developer &amp; Tester",
        "description": "<p>Build&nbsp;APIs</p>",
        "salary_min": 300000,
        "salary_is_predicted": "1",
    }
    job = parse_result(raw)
    assert job.title == "Python Developer & Tester"
    assert job.description == "Build APIs"
    assert job.salary_text is None


def test_parse_missing_company_and_date():
    raw = fake_result(1)
    del raw["company"], raw["created"]
    job = parse_result(raw)
    assert job.company == "Unknown"
    assert job.posted_at is None


# --- requests and paging -----------------------------------------------------------

def test_request_targets_india_with_title_and_freshness():
    a, rec = adapter(lambda r: httpx.Response(200, json={"results": []}), max_days_old=7)
    list(a.fetch(Search(titles=["Python Developer"])))
    req = rec.requests[0]
    assert req.url.path == "/v1/api/jobs/in/search/1"
    assert req.url.params["title_only"] == "Python Developer"
    assert req.url.params["max_days_old"] == "7"
    assert req.url.params["app_id"] == "id123"
    assert req.url.params["sort_by"] == "date"


def test_paginates_until_short_page():
    def handler(request):
        page = int(request.url.path.rsplit("/", 1)[1])
        n = 50 if page == 1 else 3
        results = [fake_result(page * 100 + i) for i in range(n)]
        return httpx.Response(200, json={"results": results})

    a, rec = adapter(handler, max_pages=5)
    jobs = list(a.fetch(Search(titles=["Python Developer"])))
    assert len(jobs) == 53
    assert len(rec.requests) == 2


def test_one_query_per_title():
    a, rec = adapter(lambda r: httpx.Response(200, json={"results": [fake_result(1)]}))
    list(a.fetch(Search(titles=["Python Developer", "Backend Engineer"])))
    assert [r.url.params["title_only"] for r in rec.requests] == [
        "Python Developer",
        "Backend Engineer",
    ]


def test_stops_when_budget_is_spent():
    full_page = {"results": [fake_result(i) for i in range(50)]}
    budget = MemoryBudget(1)
    a, rec = adapter(lambda r: httpx.Response(200, json=full_page), budget=budget, max_pages=5)
    jobs = list(a.fetch(Search(titles=["Python Developer", "Backend Engineer"])))
    assert len(rec.requests) == 1
    assert len(jobs) == 50


# --- errors never leak the key --------------------------------------------------------

@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_error_hides_key(status):
    a, _ = adapter(lambda r: httpx.Response(status, text="nope"))
    with pytest.raises(AdzunaError) as exc:
        list(a.fetch(Search(titles=["Python Developer"])))
    assert str(status) in str(exc.value)
    assert SECRET not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_network_error_hides_key():
    def boom(request):
        raise httpx.ConnectError(f"failed for {request.url}")

    a, _ = adapter(boom)
    with pytest.raises(AdzunaError) as exc:
        list(a.fetch(Search(titles=["Python Developer"])))
    assert SECRET not in str(exc.value)


# --- fixture through the real normalizer and filters ---------------------------------

def test_fixture_through_filters_for_fresher_search():
    search = Search(titles=["Python Developer"], min_years=0, max_years=1)
    now = datetime(2026, 11, 11, tzinfo=UTC)
    kept = []
    for raw in fixture_data()["results"]:
        job = normalize_job(parse_result(raw))
        if apply_filters(job, search, now=now).passed:
            kept.append(job.raw.title)
    assert "Python Developer" in kept
    assert not any(t.lower().startswith(("lead", "senior")) for t in kept)
