"""Tokens that connect a browser's JobHunter extension to a user.

The raw token exists only in the extension and, once, on the connect page. The database
keeps its SHA-256 hash, so lookups are by hash and a leaked database holds no usable token.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.db import ExtensionToken, User

MAX_LABEL = 60


@dataclass(frozen=True)
class TokenRow:
    id: int
    label: str
    created_at: datetime | None
    last_used_at: datetime | None


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_token(session: Session, user_id: int, label: str) -> str:
    raw = secrets.token_urlsafe(32)
    session.add(ExtensionToken(user_id=user_id, token_hash=hash_token(raw),
                               label=(label.strip() or "Browser")[:MAX_LABEL],
                               created_at=datetime.now(UTC)))
    session.commit()
    return raw


def user_for_token(session: Session, raw: str, now: datetime) -> int | None:
    """The token's user, or None if unknown, revoked, or the user is blocked."""
    if not raw:
        return None
    row = session.execute(
        select(ExtensionToken, User.blocked)
        .join(User, User.id == ExtensionToken.user_id)
        .where(ExtensionToken.token_hash == hash_token(raw))
    ).first()
    if row is None or row[1]:
        return None
    token = row[0]
    token.last_used_at = now
    session.commit()
    return token.user_id


def list_tokens(session: Session, user_id: int) -> list[TokenRow]:
    rows = session.scalars(select(ExtensionToken).where(ExtensionToken.user_id == user_id)
                           .order_by(ExtensionToken.id))
    return [TokenRow(t.id, t.label, t.created_at, t.last_used_at) for t in rows]


def revoke_token(session: Session, user_id: int, token_id: int) -> bool:
    token = session.get(ExtensionToken, token_id)
    if token is None or token.user_id != user_id:
        return False
    session.delete(token)
    session.commit()
    return True


_OS = (("Android", "Android"), ("Windows", "Windows"), ("Mac OS X", "macOS"),
       ("CrOS", "ChromeOS"), ("Linux", "Linux"))


def label_from_user_agent(ua: str) -> str:
    """A friendly name for a connected browser, e.g. "Chrome on Windows"."""
    if "Chrome/" not in ua:
        return "Browser"
    browser = "Edge" if re.search(r"\bEdg/", ua) else "Chrome"
    system = next((name for marker, name in _OS if marker in ua), "")
    return f"{browser} on {system}" if system else browser
