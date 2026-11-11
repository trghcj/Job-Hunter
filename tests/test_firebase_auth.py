"""Firebase ID-token checks, with a test key pair and certificate (no network)."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from jobhunter.accounts import Identity
from jobhunter.web.firebase_auth import FirebaseVerifier, TokenError

PROJECT = "job-hunter-test"
ISSUER = f"https://securetoken.google.com/{PROJECT}"
NOW = 1_800_000_000.0


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    start = datetime.fromtimestamp(NOW, UTC) - timedelta(days=1)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(start).not_valid_after(start + timedelta(days=30))
            .sign(key, hashes.SHA256()))
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


KEY, CERT = _keypair()
OTHER_KEY, _ = _keypair()


class Fetcher:
    def __init__(self, certs=None, max_age=3600):
        self.certs = certs if certs is not None else {"k1": CERT}
        self.max_age = max_age
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return dict(self.certs), self.max_age


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def claims(**over):
    base = {"aud": PROJECT, "iss": ISSUER, "sub": "uid-1", "iat": int(NOW) - 10,
            "exp": int(NOW) + 3600, "auth_time": int(NOW) - 10, "email": "a@gmail.com",
            "email_verified": True, "name": "A Person"}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def token(key=KEY, kid="k1", **over):
    return jwt.encode(claims(**over), key, algorithm="RS256", headers={"kid": kid})


def verifier(fetch=None, clock=None):
    return FirebaseVerifier(PROJECT, fetch=fetch or Fetcher(), clock=clock or Clock())


def test_valid_token():
    assert verifier().verify(token()) == Identity("uid-1", "a@gmail.com", "A Person")


@pytest.mark.parametrize("bad", [
    {"key": OTHER_KEY},
    {"aud": "someone-else"},
    {"iss": "https://securetoken.google.com/someone-else"},
    {"exp": int(NOW) - 3600, "iat": int(NOW) - 7200},
    {"iat": int(NOW) + 3600},
    {"email_verified": False},
    {"sub": ""},
    {"email": None},
], ids=["bad_signature", "wrong_audience", "wrong_issuer", "expired", "issued_in_future",
        "unverified_email", "missing_sub", "missing_email"])
def test_rejected(bad):
    key = bad.pop("key", KEY)
    with pytest.raises(TokenError):
        verifier().verify(token(key=key, **bad))


def test_hs256_token_rejected():
    """Algorithm confusion: HMAC-signed with the public certificate as the secret."""
    import base64
    import hashlib
    import hmac
    import json

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "kid": "k1", "typ": "JWT"}).encode())
    body = b64(json.dumps(claims()).encode())
    sig = hmac.new(CERT.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    forged = f"{head}.{body}.{b64(sig)}"
    with pytest.raises(TokenError):
        verifier().verify(forged)


def test_garbage_rejected():
    with pytest.raises(TokenError):
        verifier().verify("not-a-token")


def test_certs_cached_until_max_age():
    fetch, clock = Fetcher(max_age=100), Clock()
    v = verifier(fetch, clock)
    v.verify(token())
    v.verify(token())
    assert fetch.calls == 1
    clock.t += 101
    v.verify(token(iat=int(clock.t) - 5, exp=int(clock.t) + 3600))
    assert fetch.calls == 2


def test_unknown_kid_refetches_once():
    fetch = Fetcher()
    v = verifier(fetch)
    v.verify(token())
    fetch.certs = {"k2": CERT}
    assert v.verify(token(kid="k2")).uid == "uid-1"
    assert fetch.calls == 2
    with pytest.raises(TokenError):
        v.verify(token(kid="k3"))
    assert fetch.calls == 3


def test_error_message_has_no_token():
    bad = token(aud="someone-else")
    with pytest.raises(TokenError) as exc:
        verifier().verify(bad)
    assert bad not in str(exc.value) and "Sign-in failed" in str(exc.value)


def test_cert_fetch_failure_is_a_token_error():
    def broken():
        raise OSError("network down")

    with pytest.raises(TokenError) as exc:
        FirebaseVerifier(PROJECT, fetch=broken, clock=Clock()).verify(token())
    assert "try again" in str(exc.value)
