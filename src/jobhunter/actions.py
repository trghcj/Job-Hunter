"""Signed one-click links in the daily email: Save, Applied, Not interested.

A token says "user U, job J, action A", is signed with a key derived from SETTINGS_KEY (which
both the daily run and the web app have) and expires after 30 days. It authorises only that
one action on that one job; it never signs anyone in.
"""

import hashlib
import hmac
import time
from dataclasses import dataclass

from itsdangerous import BadData, TimestampSigner, URLSafeTimedSerializer

ACTIONS = {"save": "saved", "applied": "applied", "not_interested": "hidden"}
MAX_AGE = 30 * 86400
_SALT = "email-actions"


class TokenInvalid(Exception):
    """A changed, expired or unreadable action link."""


@dataclass(frozen=True)
class Action:
    user_id: int
    job_id: int
    action: str


def action_key(settings_key: str) -> bytes:
    """A key for action links only, so the Gmail-password key is never used for signing."""
    return hmac.new(settings_key.encode(), b"email-actions-v1", hashlib.sha256).digest()


def _serializer(key: bytes, now: float | None = None) -> URLSafeTimedSerializer:
    class Signer(TimestampSigner):
        def get_timestamp(self) -> int:
            return int(time.time() if now is None else now)

    return URLSafeTimedSerializer(key, salt=_SALT, signer=Signer)


def make_token(key: bytes, user_id: int, job_id: int, action: str,
               now: float | None = None) -> str:
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    return _serializer(key, now).dumps({"u": user_id, "j": job_id, "a": action})


def read_token(key: bytes, token: str, now: float | None = None) -> Action:
    try:
        data = _serializer(key, now).loads(token, max_age=MAX_AGE)
    except BadData as exc:
        raise TokenInvalid("bad or expired link") from exc
    if not isinstance(data, dict):
        raise TokenInvalid("bad link")
    user_id, job_id, action = data.get("u"), data.get("j"), data.get("a")
    ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (user_id, job_id))
    if not ints or action not in ACTIONS:
        raise TokenInvalid("bad link")
    return Action(user_id, job_id, action)


def action_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/a/{token}"
