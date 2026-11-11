"""Each user's Gmail account whose job-alert emails are read (at most one per user)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.crypto import CryptoError, decrypt_secret, encrypt_secret
from jobhunter.db import MailAccount, User
from jobhunter.sources.base import SourceAdapter
from jobhunter.sources.gmail_alerts import DEFAULT_SENDERS, GmailAlertsAdapter


@dataclass(frozen=True)
class MailView:
    """What the settings page may show: never the password itself."""

    address: str = ""
    has_password: bool = False
    senders: tuple[str, ...] = tuple(DEFAULT_SENDERS)
    last_checked: datetime | None = None


def get_account(session: Session, user_id: int) -> MailAccount | None:
    return session.scalars(select(MailAccount).where(MailAccount.user_id == user_id)).first()


def mail_view(session: Session, user_id: int) -> MailView:
    acc = get_account(session, user_id)
    if acc is None:
        return MailView()
    return MailView(acc.gmail_address, bool(acc.app_password_enc),
                    tuple(acc.senders or DEFAULT_SENDERS), acc.last_checked)


def save_account(session: Session, user_id: int, address: str, app_password: str | None,
                 senders: list[str], key: str) -> None:
    """Blank app_password keeps the saved one."""
    acc = get_account(session, user_id)
    if acc is None:
        if not app_password:
            raise ValueError("Enter the Gmail app password the first time you save.")
        acc = MailAccount(user_id=user_id, gmail_address=address,
                          app_password_enc=encrypt_secret(app_password, key), senders=senders)
        session.add(acc)
    else:
        acc.gmail_address = address
        acc.senders = senders
        if app_password:
            acc.app_password_enc = encrypt_secret(app_password, key)
    session.commit()


def delete_account(session: Session, user_id: int) -> None:
    acc = get_account(session, user_id)
    if acc is not None:
        session.delete(acc)
        session.commit()


def _adapter(acc: MailAccount, key: str, **kwargs) -> GmailAlertsAdapter:
    password = decrypt_secret(acc.app_password_enc, key)
    return GmailAlertsAdapter(acc.gmail_address, password, list(acc.senders or DEFAULT_SENDERS),
                              **kwargs)


def gmail_adapter(session: Session, user_id: int, key: str,
                  **kwargs) -> GmailAlertsAdapter | None:
    """None when the user saved no Gmail. Raises CryptoError when the key does not match."""
    acc = get_account(session, user_id)
    return None if acc is None else _adapter(acc, key, **kwargs)


def gmail_adapters(session: Session, key: str) -> list[tuple[int, SourceAdapter]]:
    """One Gmail reader per user who is not blocked, by user id. A password that cannot
    be decrypted becomes a BrokenSource, so that user's run is recorded as failed."""
    from jobhunter.sources.registry import BrokenSource

    rows = session.scalars(
        select(MailAccount).join(User, User.id == MailAccount.user_id)
        .where(User.blocked.is_(False)).order_by(MailAccount.user_id)
    )
    out: list[tuple[int, SourceAdapter]] = []
    for acc in rows:
        try:
            out.append((acc.user_id, _adapter(acc, key)))
        except CryptoError as exc:
            out.append((acc.user_id, BrokenSource("gmail_alerts", str(exc))))
    return out
