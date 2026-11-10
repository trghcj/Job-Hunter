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
    settings_key: SecretStr = SecretStr("")

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
