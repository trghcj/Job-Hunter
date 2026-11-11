"""Checks Firebase ID tokens (Google sign-in) without any Firebase private key.

A token is accepted only if it is RS256-signed by one of Google's published
certificates and its claims match this Firebase project. Error messages never contain
the token.
"""

import re
import threading
import time
from collections.abc import Callable

import httpx
import jwt
from cryptography import x509

from jobhunter.accounts import Identity

CERTS_URL = ("https://www.googleapis.com/robot/v1/metadata/x509/"
             "securetoken@system.gserviceaccount.com")
LEEWAY = 60  # seconds of clock difference allowed
DEFAULT_MAX_AGE = 3600

CertFetcher = Callable[[], tuple[dict[str, str], int]]


class TokenError(Exception):
    """Sign-in token refused. The message is safe to show and log."""


def fetch_google_certs() -> tuple[dict[str, str], int]:
    response = httpx.get(CERTS_URL, timeout=10)
    response.raise_for_status()
    match = re.search(r"max-age=(\d+)", response.headers.get("cache-control", ""))
    return response.json(), int(match.group(1)) if match else DEFAULT_MAX_AGE


class FirebaseVerifier:
    def __init__(self, project_id: str, fetch: CertFetcher | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.project_id = project_id
        self._fetch = fetch or fetch_google_certs
        self._clock = clock
        self._keys: dict[str, object] = {}
        self._expires = 0.0
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        try:
            certs, max_age = self._fetch()
        except Exception:
            raise TokenError("Sign-in is unavailable right now, please try again.") from None
        self._keys = {kid: x509.load_pem_x509_certificate(pem.encode()).public_key()
                      for kid, pem in certs.items()}
        self._expires = self._clock() + max_age

    def _key(self, kid: str) -> object:
        with self._lock:
            if self._clock() >= self._expires:
                self._refresh()
            if kid not in self._keys:
                self._refresh()  # Google rotated its keys
            key = self._keys.get(kid)
        if key is None:
            raise TokenError("Sign-in failed, please try again.")
        return key

    def verify(self, token: str) -> Identity:
        failed = TokenError("Sign-in failed, please try again.")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise failed from None
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise failed
        try:
            claims = jwt.decode(
                token, self._key(str(header["kid"])), algorithms=["RS256"],
                audience=self.project_id, issuer=f"https://securetoken.google.com/{self.project_id}",
                options={"require": ["exp", "iat", "sub"], "verify_exp": False,
                         "verify_iat": False, "verify_nbf": False},
            )
        except jwt.PyJWTError:
            raise failed from None
        now = self._clock()
        if not (claims["iat"] <= now + LEEWAY and now < claims["exp"] + LEEWAY):
            raise failed
        verified = claims.get("email_verified") is True
        if not (claims.get("sub") and claims.get("email") and verified):
            raise failed
        return Identity(str(claims["sub"]), str(claims["email"]), claims.get("name"))
