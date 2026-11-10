from datetime import UTC, datetime, timedelta

import pytest

from jobhunter.matching.filters import (
    apply_filters,
    check_experience,
    excluded_word,
    location_ok,
    title_matches,
    title_score,
)
from jobhunter.models import RawJob
from jobhunter.normalize import normalize_job
from jobhunter.sources.base import Search

NOW = datetime(2026, 11, 10, tzinfo=UTC)


def job(title="Python Developer", location="Pune", description="Build APIs.", posted_days_ago=1):
    posted = None if posted_days_ago is None else NOW - timedelta(days=posted_days_ago)
    return normalize_job(
        RawJob(
            source="adzuna",
            source_job_id="1",
            url="https://example.com/1",
            title=title,
            company="Acme",
            location_text=location,
            description=description,
            posted_at=posted,
        )
    )


def search(**overrides) -> Search:
    base = dict(titles=["Python Developer", "Backend Engineer"], exclude_words=["unpaid"])
    base.update(overrides)
    return Search(**base)


# --- title ---------------------------------------------------------------

@pytest.mark.parametrize(
    "title",
    [
        "Python Developer",
        "Junior Python Developer",
        "Python Engineer",
        "Python Programmer (Django)",
        "Backend Developer",
        "Back-End Engineer - Node.js",
        "Back end Software Engineer",
    ],
)
def test_title_matches_synonyms(title):
    assert title_matches(title, ["Python Developer", "Backend Engineer"])


@pytest.mark.parametrize("title", ["Java Developer", "Frontend Engineer", "Python Trainer"])
def test_title_does_not_match(title):
    assert not title_matches(title, ["Python Developer", "Backend Engineer"])


def test_sde_matches_software_engineer():
    assert title_matches("SDE 1", ["Software Engineer"])
    assert title_matches("Software Development Engineer I", ["Software Engineer"])


def test_title_score_partial():
    assert title_score("Python Developer", ["Python Developer"]) == 1.0
    assert title_score("Java Developer", ["Python Developer"]) == 0.5
    assert title_score("Anything", []) == 0.0


# --- exclude words -------------------------------------------------------

def test_excluded_word_found_in_title_or_description():
    assert excluded_word("Python Intern", "", ["intern"]) == "intern"
    assert excluded_word("Python Developer", "This is an UNPAID role", ["unpaid"]) == "unpaid"


def test_excluded_word_respects_word_boundaries():
    assert excluded_word("Internal Tools Developer", "", ["intern"]) is None


# --- location ------------------------------------------------------------

def test_india_onsite_ok():
    ok, reason, remote_unconfirmed = location_ok(job(location="Pune"))
    assert ok and reason is None and not remote_unconfirmed


def test_remote_without_india_kept_with_flag():
    ok, _, remote_unconfirmed = location_ok(job(location="Remote"))
    assert ok and remote_unconfirmed


def test_remote_india_not_flagged():
    ok, _, remote_unconfirmed = location_ok(job(location="Remote, India"))
    assert ok and not remote_unconfirmed


@pytest.mark.parametrize(
    "description",
    [
        "This role is US only.",
        "Open to EU residents only.",
        "Candidates must be located in the United States.",
        "Only accepting candidates based in Canada.",
    ],
)
def test_remote_excluding_india_dropped(description):
    ok, reason, _ = location_ok(job(location="Remote", description=description))
    assert not ok and reason == "remote, not open to India"


def test_non_india_onsite_dropped():
    ok, reason, _ = location_ok(job(location="London, UK"))
    assert not ok and reason == "not in India"


# --- experience ------------------------------------------------------------

@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("Python Developer", "Experience: 0-1 years"),
        ("Python Developer", "We need 0 to 1 year of experience."),
        ("Python Developer", "0–2 yrs exp"),
        ("Python Developer", "Freshers welcome!"),
        ("Graduate Python Developer", ""),
        ("Python Developer Trainee", ""),
        ("Python Developer", "1+ years of experience in Django"),
    ],
)
def test_experience_kept(title, description):
    ok, reason, unstated = check_experience(title, description, min_years=0, max_years=1)
    assert ok, reason
    assert not unstated


@pytest.mark.parametrize(
    ("title", "description", "reason_part"),
    [
        ("Senior Python Developer", "", "senior"),
        ("Sr. Python Developer", "", "sr"),
        ("Python Tech Lead", "", "lead"),
        ("Engineering Manager", "", "manager"),
        ("Python Developer", "Minimum 3 years of experience required.", "3"),
        ("Python Developer", "Experience: 2-5 years", "2"),
        ("Python Developer", "At least 2 years experience with AWS.", "2"),
        ("Python Developer", "10+ yrs of exp", "10"),
    ],
)
def test_experience_dropped(title, description, reason_part):
    ok, reason, _ = check_experience(title, description, min_years=0, max_years=1)
    assert not ok
    assert reason_part in reason


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer II (Backend)",
        "Software Development Engineer III (Java Backend)",
        "MTS-2 Software Engineer - Backend",
        "SDE 2",
        "Intermediate Backend Engineer, Authorization",
        "Mid-Level Backend Engineer",
    ],
)
def test_level_titles_dropped_for_freshers(title):
    ok, reason, _ = check_experience(title, "", min_years=0, max_years=1)
    assert not ok and reason.startswith("level title")


@pytest.mark.parametrize("title", ["Software Engineer I (Backend)", "SDE 1", "Backend Engineer"])
def test_entry_level_titles_kept(title):
    ok, _, _ = check_experience(title, "", min_years=0, max_years=1)
    assert ok


def test_level_titles_allowed_for_experienced_search():
    ok, _, _ = check_experience("Software Engineer II", "", min_years=2, max_years=5)
    assert ok


def test_experience_unstated_kept_with_flag():
    ok, _, unstated = check_experience("Python Developer", "Build APIs.", 0, 1)
    assert ok and unstated


def test_years_without_experience_context_ignored():
    ok, _, unstated = check_experience(
        "Python Developer", "We have been in business for 25 years.", 0, 1
    )
    assert ok and unstated


def test_senior_allowed_when_user_wants_experience():
    ok, _, _ = check_experience("Senior Python Developer", "5+ years experience", 3, 8)
    assert ok


def test_too_junior_for_experienced_user():
    ok, reason, _ = check_experience("Python Developer", "Experience: 0-1 years", 3, 8)
    assert not ok and "below" in reason


# --- apply_filters -----------------------------------------------------------

def test_apply_filters_passes_good_job():
    result = apply_filters(job(description="0-1 years experience"), search(), now=NOW)
    assert result.passed and result.reason is None
    assert not result.experience_unstated and not result.remote_unconfirmed


@pytest.mark.parametrize(
    ("kwargs", "search_overrides", "reason_part"),
    [
        ({"title": "Java Developer"}, {}, "title"),
        ({"description": "unpaid internship"}, {}, "unpaid"),
        ({"location": "Berlin, Germany"}, {}, "not in India"),
        ({"location": "Mumbai"}, {"cities": ["Pune", "Bengaluru"]}, "city"),
        ({"location": "Remote"}, {"work_modes": ["onsite"]}, "work mode"),
        ({"description": "3+ years of experience"}, {}, "years"),
        ({"posted_days_ago": 30}, {}, "older"),
    ],
)
def test_apply_filters_drops(kwargs, search_overrides, reason_part):
    result = apply_filters(job(**kwargs), search(**search_overrides), now=NOW)
    assert not result.passed
    assert reason_part in result.reason


def test_city_filter_does_not_drop_remote_jobs():
    result = apply_filters(job(location="Remote, India"), search(cities=["Pune"]), now=NOW)
    assert result.passed


def test_unknown_work_mode_passes_mode_filter():
    result = apply_filters(job(location="Pune"), search(work_modes=["remote"]), now=NOW)
    assert result.passed


def test_no_titles_means_nothing_passes():
    result = apply_filters(job(), search(titles=[]), now=NOW)
    assert not result.passed and "no target titles" in result.reason


def test_missing_posted_date_passes_recency():
    assert apply_filters(job(posted_days_ago=None), search(), now=NOW).passed
