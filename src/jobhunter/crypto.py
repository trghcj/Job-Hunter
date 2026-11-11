"""Encrypt secrets saved in the database (the Gmail app password) with SETTINGS_KEY.

SETTINGS_KEY lives only in environment secrets (.env, GitHub Actions, Render); the
database holds ciphertext only. Errors never include the secret.
"""

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(RuntimeError):
    pass


def _fernet(key: str) -> Fernet:
    if not key:
        raise CryptoError("SETTINGS_KEY is not set")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise CryptoError("SETTINGS_KEY is not a valid key") from None


def encrypt_secret(plain: str, key: str) -> bytes:
    return _fernet(key).encrypt(plain.encode("utf-8"))


def decrypt_secret(token: bytes, key: str) -> str:
    try:
        return _fernet(key).decrypt(bytes(token)).decode("utf-8")
    except InvalidToken:
        raise CryptoError(
            "the saved Gmail app password was encrypted with a different SETTINGS_KEY; "
            "save it again on the settings page"
        ) from None
