"""Sign-in outcomes, owner claim, user cap, blocking (real Postgres via db_session)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from jobhunter.accounts import Identity, get_user, list_users, set_blocked, sign_in
from jobhunter.db import User

NOW = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)
OWNER = "owner@gmail.com"


def ident(n: int, email: str | None = None) -> Identity:
    return Identity(uid=f"uid{n}", email=email or f"user{n}@gmail.com", name=f"User {n}")


def users(session) -> int:
    return session.scalar(select(func.count()).select_from(User))


def test_new_user_created(db_session):
    result = sign_in(db_session, ident(1), OWNER, NOW)
    assert result.outcome == "ok" and result.user_id not in (None, 1)
    user = get_user(db_session, result.user_id)
    assert user.email == "user1@gmail.com" and user.last_login_at == NOW and not user.is_owner


def test_second_sign_in_reuses_user(db_session):
    first = sign_in(db_session, ident(1), OWNER, NOW)
    before = users(db_session)
    again = sign_in(db_session, ident(1, "renamed@gmail.com"), OWNER, NOW)
    assert again.user_id == first.user_id and users(db_session) == before
    assert get_user(db_session, first.user_id).email == "renamed@gmail.com"


def test_owner_claims_placeholder(db_session):
    result = sign_in(db_session, ident(9, "Owner@Gmail.com"), OWNER, NOW)
    assert result == result.__class__("ok", 1)
    owner = get_user(db_session, 1)
    assert owner.firebase_uid == "uid9" and owner.is_owner


def test_owner_email_ignored_when_unset(db_session):
    result = sign_in(db_session, ident(9, OWNER), "", NOW)
    assert result.outcome == "ok" and result.user_id != 1
    assert get_user(db_session, 1).firebase_uid is None


def test_placeholder_claimed_only_once(db_session):
    sign_in(db_session, ident(9, OWNER), OWNER, NOW)
    other = sign_in(db_session, ident(8, OWNER), OWNER, NOW)  # another account, same email
    assert other.user_id != 1


def test_full_at_cap(db_session):
    sign_in(db_session, ident(1), OWNER, NOW, max_users=2)
    sign_in(db_session, ident(2), OWNER, NOW, max_users=2)
    before = users(db_session)
    result = sign_in(db_session, ident(3), OWNER, NOW, max_users=2)
    assert result.outcome == "full" and result.user_id is None
    assert users(db_session) == before


def test_existing_user_gets_in_when_full(db_session):
    sign_in(db_session, ident(1), OWNER, NOW, max_users=1)
    assert sign_in(db_session, ident(1), OWNER, NOW, max_users=1).outcome == "ok"


def test_owner_gets_in_when_full(db_session):
    sign_in(db_session, ident(1), OWNER, NOW, max_users=1)
    result = sign_in(db_session, ident(9, OWNER), OWNER, NOW, max_users=1)
    assert result.outcome == "ok" and result.user_id == 1


def test_blocked_user_refused(db_session):
    uid = sign_in(db_session, ident(1), OWNER, NOW).user_id
    set_blocked(db_session, uid, True)
    assert sign_in(db_session, ident(1), OWNER, NOW).outcome == "blocked"
    set_blocked(db_session, uid, False)
    assert sign_in(db_session, ident(1), OWNER, NOW).outcome == "ok"


def test_cannot_block_owner(db_session):
    with pytest.raises(ValueError):
        set_blocked(db_session, 1, True)


def test_list_users(db_session):
    sign_in(db_session, ident(9, OWNER), OWNER, NOW)
    sign_in(db_session, ident(1), OWNER, NOW)
    rows = list_users(db_session)
    assert [r.email for r in rows] == [OWNER, "user1@gmail.com"]
    assert rows[0].is_owner and rows[0].titles >= 0 and rows[0].gmail is False


# ---- invite-only list and admins (ALLOWED_EMAILS, ADMIN_EMAILS)

from jobhunter.accounts import Access, email_set  # noqa: E402

FRIEND = "friend@gmail.com"
INVITE = Access(owner_email=OWNER, allowed=email_set(FRIEND))


def test_email_set_parses_comma_list():
    assert email_set(" Friend@Gmail.com, b@x.in ,,") == frozenset({FRIEND, "b@x.in"})
    assert email_set("") == frozenset()


def test_invited_user_signs_in(db_session):
    result = sign_in(db_session, ident(1, "Friend@gmail.com"), OWNER, NOW, access=INVITE)
    assert result.outcome == "ok"


def test_uninvited_user_refused_and_not_created(db_session):
    before = users(db_session)
    result = sign_in(db_session, ident(1), OWNER, NOW, access=INVITE)
    assert result.outcome == "invite-only" and result.user_id is None
    assert users(db_session) == before


def test_owner_always_gets_in(db_session):
    assert sign_in(db_session, ident(9, OWNER), OWNER, NOW, access=INVITE).user_id == 1
    assert sign_in(db_session, ident(9, OWNER), OWNER, NOW, access=INVITE).outcome == "ok"


def test_user_removed_from_list_is_refused(db_session):
    sign_in(db_session, ident(1), OWNER, NOW)  # signed up while sign-in was open
    assert sign_in(db_session, ident(1), OWNER, NOW, access=INVITE).outcome == "invite-only"


def test_admins_are_invited(db_session):
    access = Access(owner_email=OWNER, allowed=email_set(FRIEND), admins=email_set("boss@x.in"))
    assert sign_in(db_session, ident(1, "boss@x.in"), OWNER, NOW, access=access).outcome == "ok"


def test_empty_list_keeps_sign_in_open():
    assert Access().invited("anyone@x.in", is_owner=False)


def test_is_admin():
    access = Access(owner_email=OWNER, admins=email_set("Friend@gmail.com"))
    assert access.is_admin(FRIEND, is_owner=False)
    assert access.is_admin("whoever@x.in", is_owner=True)
    assert not access.is_admin("other@x.in", is_owner=False)
    assert not access.is_admin(None, is_owner=False)
