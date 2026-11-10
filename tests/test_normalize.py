from datetime import UTC, datetime

import pytest

from jobhunter.models import RawJob
from jobhunter.normalize import (
    detect_city,
    detect_country,
    detect_work_mode,
    make_dedupe_key,
    normalize_job,
)


def raw(**overrides) -> RawJob:
    base = dict(
        source="adzuna",
        source_job_id="123",
        url="https://example.com/job/123",
        title="Python Developer",
        company="Acme Technologies Pvt Ltd",
        location_text="Bengaluru, Karnataka",
        description="Build APIs in Python.",
        posted_at=datetime(2026, 11, 9, tzinfo=UTC),
    )
    base.update(overrides)
    return RawJob(**base)


# --- city ---------------------------------------------------------------

@pytest.mark.parametrize(
    ("location", "city"),
    [
        ("Bengaluru, Karnataka", "Bengaluru"),
        ("Bangalore", "Bengaluru"),
        ("Gurgaon, Haryana", "Gurugram"),
        ("New Delhi", "Delhi"),
        ("Navi Mumbai, Maharashtra", "Navi Mumbai"),
        ("Mumbai", "Mumbai"),
        ("Bombay", "Mumbai"),
        ("Greater Noida", "Greater Noida"),
        ("Noida, Uttar Pradesh", "Noida"),
        ("Madras", "Chennai"),
        ("Trivandrum", "Thiruvananthapuram"),
        ("HYDERABAD", "Hyderabad"),
    ],
)
def test_detect_city_canonical_names(location, city):
    assert detect_city(location) == city


@pytest.mark.parametrize("location", [None, "", "India", "Remote", "London, UK"])
def test_detect_city_none_when_no_known_city(location):
    assert detect_city(location) is None


def test_detect_city_does_not_match_inside_words():
    assert detect_city("Puneet Nagar Road, Somewhere") is None


# --- country ------------------------------------------------------------

@pytest.mark.parametrize(
    ("location", "city", "country"),
    [
        ("Pune, Maharashtra", "Pune", "IN"),
        ("India", None, "IN"),
        ("Remote - India", None, "IN"),
        ("Karnataka", None, "IN"),
        ("London, UK", None, None),
        (None, None, None),
    ],
)
def test_detect_country(location, city, country):
    assert detect_country(location, city) == country


# --- work mode ----------------------------------------------------------

@pytest.mark.parametrize(
    ("title", "location", "description", "mode"),
    [
        ("Backend Engineer (Remote)", "India", "", "remote"),
        ("Backend Engineer", "Remote", "", "remote"),
        ("Backend Engineer", "Pune", "This is a work from home role.", "remote"),
        ("Backend Engineer", "Pune", "Hybrid: 3 days in office.", "hybrid"),
        ("Backend Engineer - Hybrid", "Remote, India", "", "hybrid"),
        ("Backend Engineer", "Pune", "Work from office, 5 days.", "onsite"),
        ("Backend Engineer", "Pune (On-site)", "", "onsite"),
        ("Backend Engineer", "Pune", "Build APIs.", "unknown"),
    ],
)
def test_detect_work_mode(title, location, description, mode):
    assert detect_work_mode(title, location, description) == mode


def test_title_and_location_beat_description():
    # Location says on-site; a passing mention of remote tools in the text must not flip it.
    assert detect_work_mode("Dev", "Pune (On-site)", "Experience with remote debugging") == "onsite"


def test_description_not_remote_is_not_remote():
    assert detect_work_mode("Dev", "Pune", "This is not a remote position.") != "remote"


# --- dedupe key ---------------------------------------------------------

def test_dedupe_key_ignores_case_whitespace_and_legal_suffixes():
    a = make_dedupe_key("Acme Technologies Pvt. Ltd.", "Python  Developer", "Bengaluru")
    b = make_dedupe_key("acme technologies", "python developer", "bengaluru")
    assert a == b
    assert len(a) == 40  # sha1 hex


def test_dedupe_key_differs_by_city_and_title():
    base = make_dedupe_key("Acme", "Python Developer", "Pune")
    assert base != make_dedupe_key("Acme", "Python Developer", "Mumbai")
    assert base != make_dedupe_key("Acme", "Java Developer", "Pune")


def test_dedupe_key_handles_missing_city():
    assert make_dedupe_key("Acme", "Dev", None) == make_dedupe_key("Acme", "Dev", "")


# --- normalize_job ------------------------------------------------------

def test_normalize_job_fills_derived_fields():
    job = normalize_job(raw())
    assert job.city == "Bengaluru"
    assert job.country == "IN"
    assert job.work_mode == "unknown"
    assert job.dedupe_key == make_dedupe_key("Acme Technologies", "Python Developer", "Bengaluru")
    assert job.raw.url == "https://example.com/job/123"


def test_normalize_job_same_job_from_two_sources_gets_same_key():
    adzuna = normalize_job(raw(location_text="Bangalore, Karnataka"))
    linkedin = normalize_job(
        raw(source="linkedin_email", company="ACME TECHNOLOGIES PRIVATE LIMITED",
            location_text="Bengaluru, Karnataka, India")
    )
    assert adzuna.dedupe_key == linkedin.dedupe_key


def test_normalize_job_strips_title_whitespace():
    job = normalize_job(raw(title="  Python   Developer \n"))
    assert job.raw.title == "Python Developer"
