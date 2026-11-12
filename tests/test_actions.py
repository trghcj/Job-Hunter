"""One-click action tokens: signed, tied to one user/job/action, 30-day expiry."""

import pytest

from jobhunter.actions import (
    MAX_AGE,
    Action,
    TokenInvalid,
    action_key,
    action_url,
    make_token,
    read_token,
)

KEY = action_key("settings-key-for-tests")
T0 = 1_800_000_000.0


def test_round_trip():
    token = make_token(KEY, 7, 42, "save", now=T0)
    assert read_token(KEY, token, now=T0 + 5) == Action(7, 42, "save")


def test_tampered_token_rejected():
    token = make_token(KEY, 7, 42, "applied", now=T0)
    flipped = token[:-3] + ("A" if token[-3] != "A" else "B") + token[-2:]
    with pytest.raises(TokenInvalid):
        read_token(KEY, flipped, now=T0)
    other = make_token(action_key("another-key"), 7, 42, "applied", now=T0)
    with pytest.raises(TokenInvalid):
        read_token(KEY, other, now=T0)
    with pytest.raises(TokenInvalid):
        read_token(KEY, "not-a-token", now=T0)


def test_expiry():
    token = make_token(KEY, 7, 42, "not_interested", now=T0)
    assert read_token(KEY, token, now=T0 + MAX_AGE - 1).action == "not_interested"
    with pytest.raises(TokenInvalid):
        read_token(KEY, token, now=T0 + MAX_AGE + 1)
    assert MAX_AGE == 30 * 86400


def test_unknown_action_rejected():
    with pytest.raises(ValueError):
        make_token(KEY, 7, 42, "delete", now=T0)
    from jobhunter.actions import _serializer

    forged = _serializer(KEY, T0).dumps({"u": 7, "j": 42, "a": "delete"})
    with pytest.raises(TokenInvalid):
        read_token(KEY, forged, now=T0)
    bad_types = _serializer(KEY, T0).dumps({"u": "7", "j": 42, "a": "save"})
    with pytest.raises(TokenInvalid):
        read_token(KEY, bad_types, now=T0)


def test_key_differs_from_settings_key():
    key = action_key("k")
    assert key != b"k" and len(key) == 32
    assert action_key("k") == key and action_key("j") != key


def test_action_url():
    assert action_url("https://x.onrender.com/", "tok") == "https://x.onrender.com/a/tok"
