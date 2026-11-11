"""Workday, SmartRecruiters and Amazon adapters."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from jobhunter.sources.amazon import AmazonAdapter, parse_amazon
from jobhunter.sources.base import Search
from jobhunter.sources.smartrecruiters import SmartRecruitersAdapter
from jobhunter.sources.watchlist import Company, parse_watchlist_text
from jobhunter.sources.workday import WorkdayAdapter, india_facets, posted_days

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
WIDE = Search(titles=["x"], min_years=0, max_years=30)  # experience never blocks


def load(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- watchlist entries for the new systems ----------------------------------------------

def test_watchlist_accepts_workday_and_smartrecruiters_keeping_case():
    companies, errors = parse_watchlist_text(
        "Nvidia | workday | nvidia.wd5/NVIDIAExternalCareerSite\nSwiggy | smartrecruiters | Swiggy"
    )
    assert errors == []
    assert companies[0].slug == "nvidia.wd5/NVIDIAExternalCareerSite"
    assert companies[1].slug == "Swiggy"


@pytest.mark.parametrize("slug", ["nvidia/External", "nvidia.wd5", "nvidia.wd5/bad site"])
def test_watchlist_rejects_bad_workday_slugs(slug):
    assert parse_watchlist_text(f"N | workday | {slug}")[1]


# --- Workday ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "days"),
    [("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 3 Days Ago", 3),
     ("Posted 30+ Days Ago", 30), ("", None)],
)
def test_posted_days(text, days):
    assert posted_days(text) == days


def test_india_facets_prefers_country():
    facets = load("workday_citi_facets.json")["facets"]
    assert india_facets(facets) == {
        "Country_and_Jurisdiction": ["c4f78be1a8f14da0ab49ce1162348a5e"]
    }


def test_india_facets_falls_back_to_location_values():
    facets = [{"facetParameter": "locationMainGroup", "values": [
        {"descriptor": "India, Bangalore", "id": "a"}, {"descriptor": "Germany, Munich", "id": "b"},
        {"descriptor": "India, Hyderabad", "id": "c"}]}]
    assert india_facets(facets) == {"locationMainGroup": ["a", "c"]}


def test_india_facets_nested_and_missing():
    nested = [{"facetParameter": "locations", "values": [
        {"facetParameter": "locationCountry", "descriptor": "Country",
         "values": [{"descriptor": "India", "id": "in"}, {"descriptor": "Japan", "id": "jp"}]}]}]
    assert india_facets(nested) == {"locationCountry": ["in"]}
    assert india_facets([]) is None


def workday_handler(listing: dict, calls: list):
    facets = load("workday_citi_facets.json")
    detail = load("workday_citi_detail.json")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.method == "POST":
            body = json.loads(request.content)
            if not body["appliedFacets"] and body["searchText"] == "":
                return httpx.Response(200, json=facets)
            return httpx.Response(200, json=listing if body["offset"] == 0 else
                                  {"total": 0, "jobPostings": []})
        return httpx.Response(200, json=detail)
    return handler


def test_workday_uses_india_facet_and_reads_detail():
    listing = load("workday_citi_india.json")
    first = listing["jobPostings"][0]
    calls: list = []
    adapter = WorkdayAdapter([Company("Citi", "workday", "citi.wd5/2")],
                             client(workday_handler(listing, calls)), delay=0, now=NOW)
    jobs = list(adapter.fetch(Search(titles=[first["title"]], min_years=0, max_years=30)))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "workday" and job.company == "Citi" and job.title == first["title"]
    assert job.url.startswith("https://citi.wd5.myworkdayjobs.com/")
    assert "India" in job.location_text and "(Hybrid)" in job.location_text
    assert job.description and "<" not in job.description
    assert job.posted_at == datetime(2026, 11, 11, tzinfo=UTC)
    posts = [json.loads(c[2]) for c in calls if c[0] == "POST"]
    assert posts[1]["appliedFacets"] == {
        "Country_and_Jurisdiction": ["c4f78be1a8f14da0ab49ce1162348a5e"]
    }


def test_workday_stops_at_old_jobs_when_newest_first():
    old = {"title": "Backend Engineer", "externalPath": "/job/x/old_1",
           "locationsText": "Pune Maharashtra India", "postedOn": "Posted 30+ Days Ago"}
    new = old | {"externalPath": "/job/x/new_1", "postedOn": "Posted Today"}
    listing = {"total": 40, "jobPostings": [new] + [old] * 19}
    calls: list = []
    adapter = WorkdayAdapter([Company("Citi", "workday", "citi.wd5/2")],
                             client(workday_handler(listing, calls)), delay=0, now=NOW)
    jobs = list(adapter.fetch(Search(titles=["Backend Engineer"])))
    assert len(jobs) == 1
    pages = [json.loads(c[2])["offset"] for c in calls if c[0] == "POST"][1:]
    assert pages == [0]  # did not ask for page 2


# --- SmartRecruiters -----------------------------------------------------------------------

def test_smartrecruiters_lists_india_and_reads_detail():
    listing = load("smartrecruiters_swiggy.json")
    detail = load("smartrecruiters_swiggy_detail.json")
    first = listing["content"][0]
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json=listing)
        return httpx.Response(200, json=detail)

    adapter = SmartRecruitersAdapter([Company("Swiggy", "smartrecruiters", "Swiggy")],
                                     client(handler), delay=0)
    jobs = list(adapter.fetch(Search(titles=[first["name"]], min_years=0, max_years=30)))
    assert seen[0].url.params["country"] == "in"
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "smartrecruiters" and job.company == "Swiggy"
    assert job.location_text.startswith("Bengaluru") and "India" in job.location_text
    assert job.url == detail["postingUrl"]
    assert job.description and "About the Team" in job.description
    assert job.posted_at == datetime(2026, 11, 11, 7, 21, 11, 252000, tzinfo=UTC)


# --- Amazon ------------------------------------------------------------------------------------

def test_parse_amazon_fixture():
    raw = load("amazon_india.json")["jobs"][0]
    job = parse_amazon(raw)
    assert job.source == "amazon" and job.company == "Amazon"
    assert job.title == raw["title"]
    assert job.location_text == "Bengaluru, Karnataka, India"
    assert job.url == "https://www.amazon.jobs" + raw["job_path"]
    assert job.posted_at == datetime(2026, 11, 11, tzinfo=UTC)
    assert "3+ years" in job.description  # basic qualifications are included


def test_parse_amazon_virtual_is_remote():
    job = parse_amazon({"title": "Recruiter", "city": "Virtual", "job_path": "/en/jobs/1/x",
                        "posted_date": "November 10, 2026", "id_icims": "1"})
    assert job.location_text == "India (Remote)"


def test_amazon_stops_at_old_jobs_and_prefilters_titles():
    fresh = {"title": "Software Development Engineer", "city": "Hyderabad", "state": "TS",
             "job_path": "/en/jobs/2/sde", "posted_date": "November 10, 2026", "id_icims": "2"}
    other = fresh | {"title": "Area Manager", "id_icims": "3", "job_path": "/en/jobs/3/am"}
    old = fresh | {"posted_date": "September 1, 2026", "id_icims": "4", "job_path": "/en/jobs/4/x"}
    pages: list[int] = []

    def handler(request):
        pages.append(int(request.url.params["offset"]))
        return httpx.Response(200, json={"hits": 300, "jobs": [fresh, other] + [old] * 98})

    adapter = AmazonAdapter(client(handler), now=NOW)
    jobs = list(adapter.fetch(Search(titles=["Software Engineer"], max_years=4)))
    assert [j.title for j in jobs] == ["Software Development Engineer"]
    assert pages == [0]
    assert jobs[0].posted_at == NOW.replace(hour=0) - timedelta(days=1)
