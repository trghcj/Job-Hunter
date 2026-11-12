"""Infrastructure secrets from env / .env. User preferences live in the database, not here."""

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = ""
    test_database_url: str = ""  # local Postgres for DB tests; never the hosted database
    adzuna_app_id: str = ""
    adzuna_app_key: SecretStr = SecretStr("")
    resend_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"
    public_url: str = "https://jobhunter-settings.onrender.com"  # builds links in the email
    settings_key: SecretStr = SecretStr("")
    alert_to: str = ""  # optional; failure alerts still reach you when the database is down
    # Web app: Google sign-in through Firebase. These four values are public (they are sent
    # to every browser); the server checks tokens with Google's public certificates.
    firebase_api_key: str = ""
    firebase_auth_domain: str = ""
    firebase_project_id: str = ""
    firebase_app_id: str = ""
    owner_email: str = ""  # this Google account becomes the owner (admin page, old data)
    # Comma-separated email lists. ALLOWED_EMAILS empty = anyone with Google may sign in.
    allowed_emails: str = ""  # invite-only: only these accounts (and the owner)
    admin_emails: str = ""  # these accounts also get the Admin page
    session_secret: SecretStr = SecretStr("")  # 32+ random characters
    web_https_only: bool = True  # secure cookies; set false only for local http testing

    @field_validator("database_url", "test_database_url")
    @classmethod
    def use_psycopg_driver(cls, url: str) -> str:
        return normalize_db_url(url)


def normalize_db_url(url: str) -> str:
    """Neon and Render hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
