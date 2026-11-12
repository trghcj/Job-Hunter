"""Avoid lists built from "Not interested" reasons: only useful reasons, deduplicated,
capped, removable, per user."""

from datetime import UTC, datetime

from jobhunter.accounts import Identity, sign_in
from jobhunter.avoid import (
    MAX_ENTRIES,
    REASONS,
    Avoid,
    add_reason,
    load_avoid,
    reason_options,
    remove_entry,
)
from jobhunter.db import Job

NOW = datetime(2026, 11, 13, 3, 0, tzinfo=UTC)
PROFILE = {"Python", "Django"}


def job(**kw) -> Job:
    base = dict(dedupe_key="k", source="s", url="https://x", title="Backend Engineer",
                company="Acme Pvt Ltd", city="Pune", work_mode="hybrid",
                skills=["AWS", "Django", "Python"], title_skills=[])
    base.update(kw)
    return Job(**base)


def uid(session, n=1) -> int:
    return sign_in(session, Identity(f"uid{n}", f"u{n}@gmail.com", None), "", NOW).user_id


def test_reason_options_only_when_useful():
    assert reason_options(job(), PROFILE) == ["company", "skills", "location", "role"]
    assert "location" not in reason_options(job(work_mode="remote"), PROFILE)
    assert "location" not in reason_options(job(city=None), PROFILE)
    assert "skills" not in reason_options(job(skills=["Python"]), PROFILE)
    assert "skills" not in reason_options(job(skills=None), PROFILE)
    assert list(REASONS) == ["company", "skills", "location", "role"]


def test_add_each_reason(db_session):
    u = uid(db_session)
    j = job()
    for reason in ("company", "skills", "location", "role"):
        assert add_reason(db_session, u, j, reason, PROFILE) is True
    avoid = load_avoid(db_session, u)
    assert avoid.companies == ("Acme Pvt Ltd",) and avoid.avoids_company("ACME")
    assert avoid.avoids_company("Acme Private Limited") and not avoid.avoids_company("Acmex")
    assert avoid.skills == ("AWS",)
    assert avoid.cities == ("Pune",) and avoid.avoided_city("pune", "onsite") == "Pune"
    assert avoid.avoided_city("Pune", "remote") is None
    assert avoid.titles == ("Backend Engineer",)


def test_entries_deduplicated(db_session):
    u = uid(db_session)
    add_reason(db_session, u, job(), "company", PROFILE)
    add_reason(db_session, u, job(company="ACME"), "company", PROFILE)
    add_reason(db_session, u, job(city="pune"), "location", PROFILE)
    add_reason(db_session, u, job(city="Pune"), "location", PROFILE)
    avoid = load_avoid(db_session, u)
    assert avoid.companies == ("Acme Pvt Ltd",) and avoid.cities == ("Pune",)


def test_useless_reason_is_noop(db_session):
    u = uid(db_session)
    assert add_reason(db_session, u, job(work_mode="remote"), "location", PROFILE) is False
    assert add_reason(db_session, u, job(skills=["Python"]), "skills", PROFILE) is False
    assert add_reason(db_session, u, job(), "bogus", PROFILE) is False
    assert load_avoid(db_session, u).is_empty()


def test_cap_100(db_session):
    u = uid(db_session)
    for i in range(MAX_ENTRIES + 5):
        add_reason(db_session, u, job(company=f"Company {i}"), "company", PROFILE)
    assert len(load_avoid(db_session, u).companies) == MAX_ENTRIES == 100


def test_remove_entry(db_session):
    u = uid(db_session)
    add_reason(db_session, u, job(), "skills", PROFILE)
    assert remove_entry(db_session, u, "skills", "aws") is True
    assert remove_entry(db_session, u, "skills", "aws") is False
    assert remove_entry(db_session, u, "bogus", "x") is False
    assert load_avoid(db_session, u).skills == ()


def test_isolated_per_user(db_session):
    a, b = uid(db_session, 1), uid(db_session, 2)
    add_reason(db_session, a, job(), "company", PROFILE)
    assert load_avoid(db_session, b) == Avoid()
