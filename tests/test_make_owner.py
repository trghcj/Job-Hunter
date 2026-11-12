"""make-owner: turn the signed-in account with OWNER_EMAIL into the owner when an earlier
sign-in left the owner placeholder (user 1) unclaimed. Real Postgres via db_session."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jobhunter.accounts import Identity, make_owner, sign_in
from jobhunter.db import Job, JobOwner, JobStatus, User, UserSetting
from jobhunter.pipeline import run_pipeline
from jobhunter.user_settings import get_user_setting, set_user_setting
from jobhunter.web.forms import ProfileForm
from jobhunter.web.store import UserStore
from tests.test_pipeline import FakeAdapter, raw

NOW = datetime(2026, 11, 13, 3, 0, tzinfo=UTC)
OWNER = "owner@gmail.com"


def me(db) -> int:
    """The owner's account, created by a sign-in made while OWNER_EMAIL was not set."""
    uid = sign_in(db, Identity("uid-me", "Owner@Gmail.com", "Me"), "", NOW).user_id
    UserStore(sessionmaker(bind=db.get_bind()), uid).save(ProfileForm(
        titles=["Backend Engineer", "Python Developer", "Private Job"], cities=[],
        work_modes=["onsite", "hybrid", "remote"], min_years=0, max_years=2,
        exclude_words=[], digest_to=[OWNER], max_jobs=25, max_age_days=14))
    return uid


def job_ids(db) -> dict[str, int]:
    return {j.title: j.id for j in db.scalars(select(Job))}


def setup(db) -> int:
    """Placeholder history: 2 emailed jobs (one later hidden from the email), 1 private
    Gmail job, an avoid list. My account: one of those jobs saved, my own settings."""
    mine = me(db)
    run_pipeline(db, [FakeAdapter("fake", [raw(title="Backend Engineer"),
                                           raw(title="Python Developer"),
                                           raw(title="Private Job")])], now=NOW)
    ids = job_ids(db)
    db.get(Job, ids["Private Job"]).private = True  # as if found in the placeholder's Gmail
    db.add(JobOwner(user_id=1, job_id=ids["Private Job"]))
    sent = NOW - timedelta(days=1)
    db.add_all([
        JobStatus(user_id=1, job_id=ids["Backend Engineer"], emailed_at=sent, status="new"),
        JobStatus(user_id=1, job_id=ids["Python Developer"], emailed_at=sent, status="hidden"),
        JobStatus(user_id=mine, job_id=ids["Backend Engineer"], status="saved"),
    ])
    db.commit()
    set_user_setting(db, 1, "avoid", {"companies": ["Beta"]})
    set_user_setting(db, 1, "digest_to", ["old@example.com"])
    set_user_setting(db, mine, "digest_to", ["new@example.com"])
    db.commit()
    return mine


def status(db, user_id, job_id):
    return db.scalar(select(JobStatus).where(JobStatus.user_id == user_id,
                                             JobStatus.job_id == job_id))


def test_preview_changes_nothing(db_session):
    mine = setup(db_session)
    fix = make_owner(db_session, OWNER, apply=False)
    assert fix.ok and fix.user_id == mine and not fix.applied
    assert (fix.emailed_marks, fix.private_jobs) == (2, 1)
    db_session.expire_all()
    assert db_session.get(User, 1) is not None and not db_session.get(User, mine).is_owner


def test_apply_moves_history_and_makes_owner(db_session):
    mine = setup(db_session)
    ids = job_ids(db_session)
    fix = make_owner(db_session, OWNER, apply=True)
    assert fix.ok and fix.applied
    db_session.expire_all()
    assert db_session.get(User, 1) is None
    assert db_session.get(User, mine).is_owner
    kept = status(db_session, mine, ids["Backend Engineer"])
    assert kept.status == "saved" and kept.emailed_at is not None   # mine kept, mark added
    copied = status(db_session, mine, ids["Python Developer"])
    assert copied.status == "hidden" and copied.emailed_at is not None
    assert db_session.scalar(select(JobOwner).where(
        JobOwner.user_id == mine, JobOwner.job_id == ids["Private Job"])) is not None
    assert get_user_setting(db_session, mine, "digest_to") == ["new@example.com"]  # mine wins
    assert get_user_setting(db_session, mine, "avoid") == {"companies": ["Beta"]}  # filled in
    assert db_session.scalars(select(UserSetting).where(UserSetting.user_id == 1)).all() == []


def test_later_sign_in_keeps_owner(db_session):
    mine = setup(db_session)
    make_owner(db_session, OWNER, apply=True)
    again = sign_in(db_session, Identity("uid-me", OWNER, "Me"), OWNER, NOW)
    assert again.outcome == "ok" and again.user_id == mine


def test_refuses_without_owner_email(db_session):
    setup(db_session)
    fix = make_owner(db_session, "", apply=True)
    assert not fix.ok and "OWNER_EMAIL" in fix.message
    assert db_session.get(User, 1) is not None


def test_refuses_without_matching_account(db_session):
    fix = make_owner(db_session, OWNER, apply=True)
    assert not fix.ok and "sign in" in fix.message.lower()
    assert db_session.get(User, 1) is not None


def test_refuses_when_owner_already_signed_in(db_session):
    sign_in(db_session, Identity("uid-owner", OWNER, None), OWNER, NOW)  # claims user 1
    fix = make_owner(db_session, OWNER, apply=True)
    assert not fix.ok and "already" in fix.message
    db_session.expire_all()
    assert db_session.get(User, 1).is_owner


def test_refuses_two_matching_accounts(db_session):
    me(db_session)
    sign_in(db_session, Identity("uid-other", OWNER, None), "", NOW)
    fix = make_owner(db_session, OWNER, apply=True)
    assert not fix.ok and "more than one" in fix.message.lower()
    assert db_session.get(User, 1) is not None


def test_cli_preview_then_apply(db_session, monkeypatch, capsys):
    import contextlib

    import jobhunter.__main__ as cli
    from jobhunter.settings import Settings

    mine = setup(db_session)
    monkeypatch.setattr(cli, "_session", lambda: contextlib.nullcontext(db_session))
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(owner_email=OWNER))
    assert cli.main(["make-owner"]) == 0
    out = capsys.readouterr().out
    assert f"account {mine}" in out and "2 emailed" in out and "1 private" in out
    assert "--apply" in out and db_session.get(User, 1) is not None
    assert cli.main(["make-owner", "--apply"]) == 0
    assert "now the owner" in capsys.readouterr().out
    db_session.expire_all()
    assert db_session.get(User, 1) is None
    assert cli.main(["make-owner"]) == 1  # nothing left to fix
