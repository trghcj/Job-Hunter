"""Per-browser extension tokens: only a hash is stored; blocked users are refused."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from jobhunter.accounts import Identity, set_blocked, sign_in
from jobhunter.db import ExtensionToken
from jobhunter.extension_tokens import (
    create_token,
    label_from_user_agent,
    list_tokens,
    revoke_token,
    user_for_token,
)

NOW = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)


def new_user(session, n: int) -> int:
    return sign_in(session, Identity(f"uid{n}", f"u{n}@gmail.com", None), "", NOW).user_id


def test_token_round_trip(db_session):
    a = new_user(db_session, 1)
    raw = create_token(db_session, a, "Chrome on Windows")
    assert len(raw) >= 40
    later = NOW + timedelta(hours=1)
    assert user_for_token(db_session, raw, later) == a
    [row] = list_tokens(db_session, a)
    assert row.label == "Chrome on Windows" and row.last_used_at == later


def test_only_hash_stored(db_session):
    a = new_user(db_session, 1)
    raw = create_token(db_session, a, "Chrome on Windows")
    row = db_session.scalars(select(ExtensionToken)).one()
    assert raw not in (row.token_hash, row.label) and len(row.token_hash) == 64


def test_unknown_token_none(db_session):
    new_user(db_session, 1)
    assert user_for_token(db_session, "nope", NOW) is None
    assert user_for_token(db_session, "", NOW) is None


def test_blocked_user_token_refused(db_session):
    a = new_user(db_session, 1)
    raw = create_token(db_session, a, "x")
    set_blocked(db_session, a, True)
    assert user_for_token(db_session, raw, NOW) is None


def test_revoke_only_own(db_session):
    a, b = new_user(db_session, 1), new_user(db_session, 2)
    raw = create_token(db_session, a, "x")
    [row] = list_tokens(db_session, a)
    assert revoke_token(db_session, b, row.id) is False
    assert user_for_token(db_session, raw, NOW) == a
    assert revoke_token(db_session, a, row.id) is True
    assert user_for_token(db_session, raw, NOW) is None


def test_list_tokens_per_user(db_session):
    a, b = new_user(db_session, 1), new_user(db_session, 2)
    create_token(db_session, a, "A1")
    create_token(db_session, a, "A2")
    create_token(db_session, b, "B1")
    assert [t.label for t in list_tokens(db_session, a)] == ["A1", "A2"]
    assert [t.label for t in list_tokens(db_session, b)] == ["B1"]


def test_long_label_trimmed(db_session):
    a = new_user(db_session, 1)
    create_token(db_session, a, "x" * 200)
    assert len(list_tokens(db_session, a)[0].label) == 60


WIN_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
MAC_EDGE = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like "
            "Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")
ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/131.0.0.0 Mobile Safari/537.36")
LINUX_BRAVE = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"


@pytest.mark.parametrize("ua, label", [
    (WIN_CHROME, "Chrome on Windows"),
    (MAC_EDGE, "Edge on macOS"),
    (ANDROID, "Chrome on Android"),
    (LINUX_BRAVE, "Chrome on Linux"),
    ("", "Browser"),
    ("curl/8.0", "Browser"),
])
def test_label_from_user_agent(ua, label):
    assert label_from_user_agent(ua) == label


def test_token_of_user_no_longer_invited_is_refused(db_session):
    from jobhunter.accounts import Access, email_set

    a = new_user(db_session, 1)
    raw = create_token(db_session, a, "Chrome on Windows")
    assert user_for_token(db_session, raw, NOW, Access(allowed=email_set("u1@gmail.com"))) == a
    assert user_for_token(db_session, raw, NOW, Access(allowed=email_set("x@gmail.com"))) is None
