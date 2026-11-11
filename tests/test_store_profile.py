"""UserStore profile methods against Postgres: saving re-scores at once; the PDF's text is
cleaned; choices are validated."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jobhunter.db import JobScore
from jobhunter.pipeline import run_pipeline
from jobhunter.web.store import UserStore
from tests.test_pipeline import FakeAdapter, raw
from tests.test_pipeline_users import user

NOW = datetime.now(UTC)
RESUME = (Path(__file__).parent / "fixtures" / "resume_synthetic.pdf").read_bytes()


def store_for(session, uid) -> UserStore:
    return UserStore(sessionmaker(bind=session.get_bind()), uid)


def reasons(session, uid) -> list[str | None]:
    return list(session.scalars(select(JobScore.fit_reason).where(
        JobScore.user_id == uid, JobScore.passed_filters.is_(True))))


def test_saving_profile_rescores_at_once(db_session):
    uid = user(db_session, 1)
    jobs = [raw(description="Python. 0-1 years experience")]
    run_pipeline(db_session, [FakeAdapter("fake", jobs)],
                 now=NOW)
    assert reasons(db_session, uid) == [None]
    store_for(db_session, uid).save_profile_text("Me\nPython developer")
    db_session.expire_all()
    assert reasons(db_session, uid) == ["Matches Python"]


def test_upload_resume_stores_clean_text_and_skills(db_session):
    uid = user(db_session, 1)
    store = store_for(db_session, uid)
    store.upload_resume(RESUME)
    view = store.load_profile()
    assert "Django" in view.text and "@example.com" not in view.text
    assert "98765" not in view.text and "github.com" not in view.text
    assert {"Django", "Python", "React", "SQL"} <= set(view.skills)
    assert view.llm_review is False and view.digest_fit == "good"


def test_options_saved_and_checked(db_session):
    uid = user(db_session, 1)
    store = store_for(db_session, uid)
    store.save_profile_options(True, "strong")
    view = store.load_profile()
    assert view.llm_review is True and view.digest_fit == "strong"
    with pytest.raises(ValueError):
        store.save_profile_options(False, "bogus")
