"""Greenhouse, Lever and Ashby adapters, plus the shared watchlist and India prefilter."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from jobhunter.sources.ashby import AshbyAdapter, parse_ashby
from jobhunter.sources.base import Search
from jobhunter.sources.boards import BoardError, india_or_open_remote
from jobhunter.sources.greenhouse import GreenhouseAdapter
from jobhunter.sources.lever import LeverAdapter, parse_lever
from jobhunter.sources.watchlist import (
    Company,
    format_watchlist,
    load_default_entries,
    load_default_watchlist,
    merge_new_defaults,
    parse_watchlist_text,
)

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- India / remote prefilter -------------------------------------------------------------

@pytest.mark.parametrize(
    "location",
    ["Bengaluru-VTP, India", "Pune", "Remote - India", "India", "Remote (APAC)",
     "Remote, Worldwide", "Hyderabad | Remote"],
)
def test_prefilter_keeps_india_and_open_remote(location):
    assert india_or_open_remote(location)


@pytest.mark.parametrize("location", ["San Francisco, CA", "Remote", "Remote - US", "London", ""])
def test_prefilter_drops_elsewhere_and_plain_remote(location):
    assert not india_or_open_remote(location)


# --- watchlist -------------------------------------------------------------------------------

def test_default_watchlist_is_valid():
    companies = load_default_watchlist()
    assert len(companies) >= 50
    assert {"greenhouse", "lever", "ashby", "workday", "smartrecruiters"} <= {
        c.ats for c in companies
    }
    assert Company("Paytm", "lever", "paytm") in companies


def test_watchlist_text_round_trip():
    text = "Groww | greenhouse | groww\nCRED|lever|cred\n\n  Atlan | ashby | atlan  "
    companies, errors = parse_watchlist_text(text)
    assert errors == []
    assert companies == [
        Company("Groww", "greenhouse", "groww"),
        Company("CRED", "lever", "cred"),
        Company("Atlan", "ashby", "atlan"),
    ]
    assert parse_watchlist_text(format_watchlist(companies))[0] == companies


@pytest.mark.parametrize(
    "line", ["Groww | workday | groww", "Groww | greenhouse", "Groww | lever | bad slug!"]
)
def test_watchlist_rejects_bad_lines(line):
    companies, errors = parse_watchlist_text(line)
    assert companies == [] and len(errors) == 1


def test_merge_new_defaults_adds_only_newer_and_respects_removals():
    a, b, c = (Company("A", "lever", "a"), Company("B", "lever", "b"),
               Company("C", "workday", "c.wd5/Ext"))
    entries = [(a, 1), (b, 1), (c, 2)]
    # user removed B after version 1; version 2 adds C only
    assert merge_new_defaults([a], entries, seen_version=1) == [a, c]
    assert merge_new_defaults([], entries, seen_version=0) == [a, b, c]
    assert merge_new_defaults([a, c], entries, seen_version=1) == [a, c]


def test_default_watchlist_has_version_2_companies():
    entries = load_default_entries()
    assert any(since == 2 and c.ats == "workday" for c, since in entries)
    assert any(c.ats == "smartrecruiters" and c.slug == "Swiggy" for c, _ in entries)


def test_watchlist_drops_duplicates():
    companies, _ = parse_watchlist_text("A | lever | cred\nB | lever | cred")
    assert len(companies) == 1


# --- Greenhouse --------------------------------------------------------------------------------

def greenhouse_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/boards/groww/jobs"):
        return httpx.Response(200, json=load("greenhouse_groww_jobs.json"))
    if "/boards/groww/jobs/" in path:
        return httpx.Response(200, json=load("greenhouse_groww_job_detail.json"))
    return httpx.Response(404)


def test_greenhouse_fetches_detail_only_for_matches():
    first = load("greenhouse_groww_jobs.json")["jobs"][0]
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        return greenhouse_handler(request)

    adapter = GreenhouseAdapter([Company("Groww", "greenhouse", "groww")], client(handler),
                                delay=0)
    jobs = list(adapter.fetch(Search(titles=[first["title"]], max_years=30)))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "greenhouse" and job.company == "Groww"
    assert job.title == first["title"]
    assert job.location_text == "Bengaluru-VTP, India"
    assert "About Groww" in job.description and "<" not in job.description
    assert job.posted_at == datetime.fromisoformat(first["first_published"])
    assert job.url == first["absolute_url"]
    assert sum(1 for c in calls if "/jobs/" in c) == 1  # one detail call, not five


def test_board_prefilter_skips_titles_too_senior_for_you():
    lever = [{"id": "1", "text": "Senior Backend Engineer", "hostedUrl": "u1",
              "categories": {"location": "Pune"}},
             {"id": "2", "text": "Backend Engineer", "hostedUrl": "u2",
              "categories": {"location": "Pune"}}]
    adapter = LeverAdapter([Company("A", "lever", "a")],
                           client(lambda r: httpx.Response(200, json=lever)), delay=0)
    jobs = list(adapter.fetch(Search(titles=["Backend Engineer"], max_years=4)))
    assert [j.url for j in jobs] == ["u2"]


def test_greenhouse_no_title_match_no_detail_calls():
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        return greenhouse_handler(request)

    adapter = GreenhouseAdapter([Company("Groww", "greenhouse", "groww")], client(handler),
                                delay=0)
    assert list(adapter.fetch(Search(titles=["Quantum Chef"]))) == []
    assert calls == ["/v1/boards/groww/jobs"]


# --- one bad board does not stop the others -------------------------------------------------

def test_board_failures_tolerated_until_majority():
    lever_ok = [{"id": "1", "text": "Backend Engineer", "hostedUrl": "https://x/1",
                 "categories": {"location": "Pune"}, "createdAt": 1790573982021}]

    def handler(request):
        if "good" in request.url.path:
            return httpx.Response(200, json=lever_ok)
        return httpx.Response(500)

    search = Search(titles=["Backend Engineer"])
    some_bad = [Company("G", "lever", "good"), Company("B", "lever", "bad1"),
                Company("G2", "lever", "good2")]
    jobs = list(LeverAdapter(some_bad, client(handler), delay=0).fetch(search))
    assert len(jobs) == 2

    mostly_bad = [Company("G", "lever", "good"), Company("B1", "lever", "bad1"),
                  Company("B2", "lever", "bad2")]
    got = []
    with pytest.raises(BoardError, match="2 of 3"):
        for job in LeverAdapter(mostly_bad, client(handler), delay=0).fetch(search):
            got.append(job)
    assert len(got) == 1  # jobs from the good board were still delivered


# --- Lever ----------------------------------------------------------------------------------------

def test_parse_lever_fixture():
    posting = load("lever_cred.json")[0]
    job = parse_lever(posting, Company("CRED", "lever", "cred"))
    assert job.source == "lever" and job.company == "CRED"
    assert job.title == posting["text"]
    assert "hyderabad" in job.location_text.lower()
    assert "On-site" in job.location_text and "India" in job.location_text
    assert job.posted_at == datetime.fromtimestamp(posting["createdAt"] / 1000, tz=UTC)
    assert job.url == posting["hostedUrl"]
    assert job.description and "<" not in job.description


def test_parse_lever_remote_label():
    job = parse_lever({"id": "x", "text": "Dev", "hostedUrl": "u", "workplaceType": "remote",
                       "categories": {"location": "India"}}, Company("A", "lever", "a"))
    assert job.location_text == "India (Remote)"


# --- Ashby --------------------------------------------------------------------------------

def test_parse_ashby_fixture():
    raw = load("ashby_atlan.json")["jobs"][0]
    job = parse_ashby(raw, Company("Atlan", "ashby", "atlan"))
    assert job.source == "ashby" and job.company == "Atlan"
    assert job.title == raw["title"]
    assert job.location_text == "India (Remote)"
    assert job.url == raw["jobUrl"]
    assert job.description.startswith("WHO WE ARE")


def test_ashby_skips_unlisted_and_far_away_jobs():
    board = {"jobs": [
        {"id": "1", "title": "Backend Engineer", "location": "Bengaluru", "isListed": True,
         "jobUrl": "u1", "workplaceType": "Hybrid"},
        {"id": "2", "title": "Backend Engineer", "location": "Bengaluru", "isListed": False,
         "jobUrl": "u2"},
        {"id": "3", "title": "Backend Engineer", "location": "New York", "isListed": True,
         "jobUrl": "u3"},
    ]}
    adapter = AshbyAdapter([Company("A", "ashby", "a")],
                           client(lambda r: httpx.Response(200, json=board)), delay=0)
    jobs = list(adapter.fetch(Search(titles=["Backend Engineer"])))
    assert [j.url for j in jobs] == ["u1"]
    assert jobs[0].location_text == "Bengaluru (Hybrid)"
