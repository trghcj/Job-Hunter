"""Accounts: who may sign in (open, or an invite-only list), the owner claim, admins, the
user cap, and blocking.

A user is a Google (Firebase) account. User 1 is the owner placeholder created by
migration 0005; it holds the pre-accounts data until the owner's first sign-in claims it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from jobhunter.db import (
    OWNER_PLACEHOLDER_ID,
    JobOwner,
    JobStatus,
    MailAccount,
    SearchProfile,
    User,
    UserSetting,
)

MAX_USERS = 50


@dataclass(frozen=True)
class Identity:
    """A verified Google identity (from a Firebase ID token)."""

    uid: str
    email: str
    name: str | None


@dataclass(frozen=True)
class SignIn:
    outcome: Literal["ok", "blocked", "full", "invite-only"]
    user_id: int | None


def _norm(email: str | None) -> str:
    return (email or "").strip().lower()


def email_set(text: str) -> frozenset[str]:
    """A comma-separated list of email addresses, compared without case."""
    return frozenset(e for e in (_norm(part) for part in (text or "").split(",")) if e)


@dataclass(frozen=True)
class Access:
    """Who may use this JobHunter (ALLOWED_EMAILS) and who gets the Admin page (ADMIN_EMAILS).

    An empty allowed list means anyone with Google may sign in. The owner is always allowed
    and always an admin; admins are always allowed.
    """

    owner_email: str = ""
    allowed: frozenset[str] = frozenset()
    admins: frozenset[str] = frozenset()

    def invited(self, email: str | None, is_owner: bool) -> bool:
        if is_owner or not self.allowed:
            return True
        e = _norm(email)
        return bool(e) and (e in self.allowed or e in self.admins or e == _norm(self.owner_email))

    def is_admin(self, email: str | None, is_owner: bool) -> bool:
        return is_owner or (bool(_norm(email)) and _norm(email) in self.admins)


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
            max_users: int = MAX_USERS, access: Access | None = None) -> SignIn:
    access = access or Access(owner_email=owner_email)
    user = session.scalars(select(User).where(User.firebase_uid == identity.uid)).first()
    if user is not None:
        if user.blocked:
            return SignIn("blocked", None)
        if not access.invited(identity.email, user.is_owner):
            return SignIn("invite-only", None)
        _touch(user, identity, now)
        session.commit()
        return SignIn("ok", user.id)

    owner = _claimable_owner(session, identity, owner_email)
    if owner is None and not access.invited(identity.email, is_owner=False):
        return SignIn("invite-only", None)
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


# ---- make-owner: repair an owner placeholder that was never claimed

@dataclass(frozen=True)
class OwnerFix:
    ok: bool
    message: str
    user_id: int | None = None
    emailed_marks: int = 0
    private_jobs: int = 0
    applied: bool = False


def _owner_candidate(session: Session, owner_email: str) -> tuple[User | None, str]:
    if not owner_email.strip():
        return None, "Set OWNER_EMAIL first."
    placeholder = session.get(User, OWNER_PLACEHOLDER_ID)
    if placeholder is None or placeholder.firebase_uid is not None:
        return None, "The owner account is already signed in; nothing to fix."
    found = session.scalars(select(User).where(
        func.lower(User.email) == owner_email.strip().lower(),
        User.firebase_uid.is_not(None), User.id != OWNER_PLACEHOLDER_ID)).all()
    if not found:
        return None, "No account uses OWNER_EMAIL yet; sign in on the website first."
    if len(found) > 1:
        return None, "More than one account uses OWNER_EMAIL; fix this by hand."
    return found[0], ""


def _move_history(session: Session, user_id: int) -> None:
    """Copy the placeholder's emailed marks, private jobs and missing settings to user_id;
    the user's own statuses and settings win."""
    old = OWNER_PLACEHOLDER_ID
    marks = insert(JobStatus).from_select(
        ["user_id", "job_id", "emailed_at", "status", "notes"],
        select(literal(user_id), JobStatus.job_id, JobStatus.emailed_at, JobStatus.status,
               JobStatus.notes).where(JobStatus.user_id == old))
    session.execute(marks.on_conflict_do_update(
        index_elements=[JobStatus.user_id, JobStatus.job_id],
        set_={"emailed_at": func.coalesce(JobStatus.emailed_at, marks.excluded.emailed_at)}))
    session.execute(insert(JobOwner).from_select(
        ["job_id", "user_id"],
        select(JobOwner.job_id, literal(user_id)).where(JobOwner.user_id == old),
    ).on_conflict_do_nothing())
    session.execute(insert(UserSetting).from_select(
        ["user_id", "key", "value"],
        select(literal(user_id), UserSetting.key, UserSetting.value)
        .where(UserSetting.user_id == old),
    ).on_conflict_do_nothing())


def make_owner(session: Session, owner_email: str, apply: bool) -> OwnerFix:
    """Make the signed-in account with OWNER_EMAIL the owner when an earlier sign-in (made
    before OWNER_EMAIL was set) left the placeholder unclaimed. Keeps the placeholder's
    emailed marks, private jobs and missing settings, then deletes it, all in one
    transaction. With apply=False only counts what would change."""
    user, problem = _owner_candidate(session, owner_email)
    if user is None:
        return OwnerFix(False, problem)
    old = OWNER_PLACEHOLDER_ID
    marks = session.scalar(select(func.count()).select_from(JobStatus).where(
        JobStatus.user_id == old, JobStatus.emailed_at.is_not(None))) or 0
    private = session.scalar(select(func.count()).select_from(JobOwner).where(
        JobOwner.user_id == old)) or 0
    if not apply:
        return OwnerFix(True, "Preview only; run again with --apply.", user.id, marks, private)
    _move_history(session, user.id)
    session.execute(delete(User).where(User.id == old))
    user.is_owner = True
    session.commit()
    return OwnerFix(True, "Done: your account is now the owner.", user.id, marks, private,
                    applied=True)
