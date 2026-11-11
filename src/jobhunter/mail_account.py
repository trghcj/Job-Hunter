"""The Gmail account whose job-alert emails are read (one account)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.crypto import decrypt_secret, encrypt_secret
from jobhunter.db import MailAccount
from jobhunter.sources.gmail_alerts import DEFAULT_SENDERS, GmailAlertsAdapter


@dataclass(frozen=True)
class MailView:
    """What the settings page may show: never the password itself."""

    address: str = ""
    has_password: bool = False
    senders: tuple[str, ...] = tuple(DEFAULT_SENDERS)
    last_checked: datetime | None = None


def get_account(session: Session) -> MailAccount | None:
    return session.scalars(select(MailAccount).order_by(MailAccount.id)).first()


def mail_view(session: Session) -> MailView:
    acc = get_account(session)
    if acc is None:
        return MailView()
    return MailView(acc.gmail_address, bool(acc.app_password_enc),
                    tuple(acc.senders or DEFAULT_SENDERS), acc.last_checked)


def save_account(session: Session, address: str, app_password: str | None,
                 senders: list[str], key: str) -> None:
    """Blank app_password keeps the saved one."""
    acc = get_account(session)
    if acc is None:
        if not app_password:
            raise ValueError("Enter the Gmail app password the first time you save.")
        acc = MailAccount(gmail_address=address, app_password_enc=encrypt_secret(app_password, key),
                          senders=senders)
        session.add(acc)
    else:
        acc.gmail_address = address
        acc.senders = senders
        if app_password:
            acc.app_password_enc = encrypt_secret(app_password, key)
    session.commit()


def delete_account(session: Session) -> None:
    acc = get_account(session)
    if acc is not None:
        session.delete(acc)
        session.commit()


def gmail_adapter(session: Session, key: str, **kwargs) -> GmailAlertsAdapter | None:
    """None when no account is saved. Raises CryptoError when the key does not match."""
    acc = get_account(session)
    if acc is None:
        return None
    password = decrypt_secret(acc.app_password_enc, key)
    return GmailAlertsAdapter(acc.gmail_address, password, list(acc.senders or DEFAULT_SENDERS),
                              **kwargs)
