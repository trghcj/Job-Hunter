"""Accounts: who may sign in, the owner claim, the user cap, and blocking.

A user is a Google (Firebase) account. User 1 is the owner placeholder created by
migration 0005; it holds the pre-accounts data until the owner's first sign-in claims it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobhunter.db import OWNER_PLACEHOLDER_ID, MailAccount, SearchProfile, User

MAX_USERS = 50


@dataclass(frozen=True)
class Identity:
    """A verified Google identity (from a Firebase ID token)."""

    uid: str
    email: str
    name: str | None


@dataclass(frozen=True)
class SignIn:
    outcome: Literal["ok", "blocked", "full"]
    user_id: int | None


@dataclass(frozen=True)
class UserRow:
    id: int
    email: str | None
    name: str | None
    is_owner: bool
    blocked: bool
    created_at: datetime | None
    last_login_at: datetime | None
    titles: int
    gmail: bool


def get_user(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def _touch(user: User, identity: Identity, now: datetime) -> None:
    user.email = identity.email
    user.name = identity.name
    user.last_login_at = now


def _claimable_owner(session: Session, identity: Identity, owner_email: str) -> User | None:
    if not owner_email or identity.email.strip().lower() != owner_email.strip().lower():
        return None
    owner = session.get(User, OWNER_PLACEHOLDER_ID)
    return owner if owner is not None and owner.firebase_uid is None else None


def _member_count(session: Session) -> int:
    """Signed-up users, not counting the owner."""
    stmt = select(func.count()).select_from(User).where(
        User.firebase_uid.is_not(None), User.is_owner.is_(False))
    return session.scalar(stmt) or 0


def _on_new_user(session: Session, user_id: int) -> None:
    """First-time setup for a user: a default search profile."""
    from jobhunter.user_settings import seed_user

    seed_user(session, user_id)


def sign_in(session: Session, identity: Identity, owner_email: str, now: datetime,
            max_users: int = MAX_USERS) -> SignIn:
    user = session.scalars(select(User).where(User.firebase_uid == identity.uid)).first()
    if user is not None:
        _touch(user, identity, now)
        session.commit()
        return SignIn("blocked", None) if user.blocked else SignIn("ok", user.id)

    owner = _claimable_owner(session, identity, owner_email)
    if owner is not None:
        owner.firebase_uid = identity.uid
        _touch(owner, identity, now)
        session.commit()
        _on_new_user(session, owner.id)
        return SignIn("ok", owner.id)

    if _member_count(session) >= max_users:
        return SignIn("full", None)
    user = User(firebase_uid=identity.uid)
    _touch(user, identity, now)
    session.add(user)
    session.commit()
    _on_new_user(session, user.id)
    return SignIn("ok", user.id)


def set_blocked(session: Session, user_id: int, blocked: bool) -> None:
    user = session.get(User, user_id)
    if user is None:
        raise LookupError(f"no user {user_id}")
    if user.is_owner:
        raise ValueError("You can't block the owner.")
    user.blocked = blocked
    session.commit()


def list_users(session: Session) -> list[UserRow]:
    titles = (select(func.coalesce(func.sum(func.cardinality(SearchProfile.titles)), 0))
              .where(SearchProfile.user_id == User.id, SearchProfile.active.is_(True))
              .scalar_subquery())
    gmail = select(MailAccount.id).where(MailAccount.user_id == User.id).exists()
    stmt = select(User, titles, gmail).order_by(User.id)
    return [
        UserRow(u.id, u.email, u.name, u.is_owner, u.blocked, u.created_at, u.last_login_at,
                int(t), bool(g))
        for u, t, g in session.execute(stmt)
    ]
