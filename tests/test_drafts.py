"""Drafts against Postgres: make, redo, edit, the daily limit and who may see what."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from jobhunter import profile
from jobhunter.db import DraftRow, Job
from jobhunter.drafts import (
    FAILED,
    LIMIT_REACHED,
    NO_PROFILE,
    DraftError,
    JobNotFound,
    load_draft,
    make_draft,
    save_letter,
    used_today,
)
from jobhunter.llm.client import Draft, LLMError
from jobhunter.pipeline import run_pipeline
from tests.test_pipeline import FakeAdapter, raw
from tests.test_pipeline_users import user

NOW = datetime(2026, 11, 13, 6, 0, tzinfo=UTC)
GOOD = Draft("Dear team", ("Tip one",))


class FakeClient:
    provider = "fake"

    def __init__(self, draft=GOOD, fail=False):
        self.result, self.fail, self.calls = draft, fail, []

    def draft(self, profile_text, job):
        self.calls.append((profile_text, job))
        if self.fail:
            raise LLMError("boom")
        return self.result


def with_profile(db, uid):
    profile.save_profile(db, uid, "Asha Rao\nPython, FastAPI, 1 year", NOW)


def public_job(db) -> int:
    run_pipeline(db, [FakeAdapter("fake", [raw()])], now=NOW)
    return db.scalars(select(Job.id)).one()


def test_make_then_load(db_session):
    uid = user(db_session, 1)
    with_profile(db_session, uid)
    job_id, client = public_job(db_session), FakeClient()
    saved = make_draft(db_session, uid, job_id, client, NOW)
    assert (saved.letter, saved.tips) == ("Dear team", ("Tip one",))
    assert saved.created_at == saved.updated_at == NOW
    sent, job = client.calls[0]
    assert "Asha Rao" not in sent and "Python" in sent and job.title == "Backend Engineer"
    assert load_draft(db_session, uid, job_id) == saved


def test_redo_replaces(db_session):
    uid = user(db_session, 1)
    with_profile(db_session, uid)
    job_id = public_job(db_session)
    make_draft(db_session, uid, job_id, FakeClient(), NOW)
    make_draft(db_session, uid, job_id, FakeClient(Draft("New", ())), NOW + timedelta(hours=1))
    rows = db_session.scalars(select(DraftRow)).all()
    assert len(rows) == 1 and rows[0].letter == "New" and rows[0].tips == []
    assert load_draft(db_session, uid, job_id).tips == ()


def test_save_letter_keeps_tips(db_session):
    uid = user(db_session, 1)
    with_profile(db_session, uid)
    job_id = public_job(db_session)
    assert save_letter(db_session, uid, job_id, "Edited", NOW) is False
    make_draft(db_session, uid, job_id, FakeClient(), NOW)
    later = NOW + timedelta(hours=1)
    assert save_letter(db_session, uid, job_id, "  Edited  ", later) is True
    saved = load_draft(db_session, uid, job_id)
    assert saved.letter == "Edited" and saved.tips == ("Tip one",)
    assert saved.updated_at == later and saved.created_at == NOW
    save_letter(db_session, uid, job_id, "x" * 7000, later)
    assert len(load_draft(db_session, uid, job_id).letter) == 6000


def test_no_profile(db_session):
    uid = user(db_session, 1)
    job_id, client = public_job(db_session), FakeClient()
    with pytest.raises(DraftError, match=NO_PROFILE):
        make_draft(db_session, uid, job_id, client, NOW)
    assert client.calls == [] and used_today(db_session, uid, NOW) == 0


def test_job_not_visible(db_session):
    a, b = user(db_session, 1), user(db_session, 2)
    with_profile(db_session, b)
    run_pipeline(db_session, [], [(a, FakeAdapter("gmail_alerts", [raw()]))], now=NOW)
    private_id = db_session.scalars(select(Job.id)).one()
    for job_id in (private_id, 999999):
        with pytest.raises(JobNotFound):
            make_draft(db_session, b, job_id, FakeClient(), NOW)
    assert used_today(db_session, b, NOW) == 0


def test_limit_resets_on_ist_day(db_session):
    uid = user(db_session, 1)
    with_profile(db_session, uid)
    job_id = public_job(db_session)
    late = datetime(2026, 11, 13, 18, 0, tzinfo=UTC)  # 23:30 IST
    for _ in range(10):
        make_draft(db_session, uid, job_id, FakeClient(), late)
    client = FakeClient()
    with pytest.raises(DraftError, match=LIMIT_REACHED):
        make_draft(db_session, uid, job_id, client, late)
    assert client.calls == []
    next_day = datetime(2026, 11, 13, 18, 40, tzinfo=UTC)  # 00:10 IST, same UTC date
    make_draft(db_session, uid, job_id, client, next_day)
    assert len(client.calls) == 1 and used_today(db_session, uid, next_day) == 1


def test_failed_redo_keeps_old_draft(db_session):
    uid = user(db_session, 1)
    with_profile(db_session, uid)
    job_id = public_job(db_session)
    make_draft(db_session, uid, job_id, FakeClient(), NOW)
    save_letter(db_session, uid, job_id, "Edited", NOW)
    with pytest.raises(DraftError, match=FAILED):
        make_draft(db_session, uid, job_id, FakeClient(fail=True), NOW)
    assert load_draft(db_session, uid, job_id).letter == "Edited"
    assert used_today(db_session, uid, NOW) == 2


def test_other_user_cannot_see_or_save(db_session):
    a, b = user(db_session, 1), user(db_session, 2)
    with_profile(db_session, a)
    job_id = public_job(db_session)
    make_draft(db_session, a, job_id, FakeClient(), NOW)
    assert load_draft(db_session, b, job_id) is None
    assert save_letter(db_session, b, job_id, "Mine now", NOW) is False
    assert load_draft(db_session, a, job_id).letter == "Dear team"
