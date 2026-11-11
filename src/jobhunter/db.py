"""SQLAlchemy engine and ORM models. Schema changes go through Alembic migrations."""

import logging
import time
from datetime import date, datetime

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from jobhunter.settings import get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


OWNER_PLACEHOLDER_ID = 1  # gets the pre-accounts data; claimed by the owner's first sign-in


def _user_id(primary_key: bool = False, unique: bool = False) -> Mapped[int]:
    return mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         primary_key=primary_key, unique=unique)


class User(Base):
    """A Google (Firebase) account. firebase_uid is NULL only for the unclaimed owner."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    firebase_uid: Mapped[str | None] = mapped_column(Text, unique=True)
    email: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    is_owner: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    blocked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSetting(Base):
    """Per-user key/value settings (digest size, digest address, max job age)."""

    __tablename__ = "user_settings"

    user_id: Mapped[int] = _user_id(primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[object] = mapped_column(JSONB)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)
    source: Mapped[str] = mapped_column(Text)
    source_job_id: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(Text)
    location_text: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text, server_default="IN")
    work_mode: Mapped[str | None] = mapped_column(Text, server_default="unknown")
    description: Mapped[str | None] = mapped_column(Text)
    salary_text: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # True while the job is known only from someone's Gmail alerts (see job_owners).
    private: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    __table_args__ = (Index("jobs_first_seen", first_seen_at.desc()),)


class JobOwner(Base):
    """Who may see a private job (one found in their own Gmail alerts)."""

    __tablename__ = "job_owners"

    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = _user_id(primary_key=True)


class JobScore(Base):
    __tablename__ = "job_scores"

    user_id: Mapped[int] = _user_id(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    passed_filters: Mapped[bool] = mapped_column(Boolean)
    filter_reason: Mapped[str | None] = mapped_column(Text)
    embed_score: Mapped[float | None] = mapped_column(REAL)
    title_score: Mapped[float | None] = mapped_column(REAL)
    skill_score: Mapped[float | None] = mapped_column(REAL)
    llm_fit: Mapped[int | None] = mapped_column(SmallInteger)
    llm_json: Mapped[dict | None] = mapped_column(JSONB)
    llm_pending: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    final_score: Mapped[float | None] = mapped_column(REAL)
    scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("scores_final", final_score.desc()),)


class JobStatus(Base):
    __tablename__ = "job_status"

    user_id: Mapped[int] = _user_id(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(Text, server_default="new")
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool | None] = mapped_column(Boolean)
    fetched: Mapped[int | None] = mapped_column(Integer)
    inserted: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    calls: Mapped[int | None] = mapped_column(Integer, server_default="0")


class ApiUsage(Base):
    """Calls per day per rate-limited API, so runs stop before free-tier caps."""

    __tablename__ = "api_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, server_default="0")


class SearchProfile(Base):
    """What to search for. Edited on the settings page; read by every run."""

    __tablename__ = "search_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    name: Mapped[str] = mapped_column(Text)
    titles: Mapped[list[str]] = mapped_column(ARRAY(Text))
    cities: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    work_modes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default="{onsite,hybrid,remote}"
    )
    min_years: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    max_years: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    exclude_words: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class MailAccount(Base):
    """Gmail account for job-alert emails. The app password is stored encrypted only."""

    __tablename__ = "mail_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id(unique=True)
    gmail_address: Mapped[str] = mapped_column(Text)
    app_password_enc: Mapped[bytes] = mapped_column(LargeBinary)
    senders: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExtensionToken(Base):
    """One connected browser (the Chrome extension). Only a SHA-256 hash of the token is kept."""

    __tablename__ = "extension_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    label: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    """Key/value app settings (digest time, score threshold, sources, llm, watchlist)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[object] = mapped_column(JSONB)


def make_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if not url:
        raise SystemExit("DATABASE_URL is not set. Add it to .env.")
    if not url.startswith("postgresql"):
        scheme = url.split(":", 1)[0]
        raise SystemExit(
            f"DATABASE_URL points to '{scheme}', but JobHunter needs PostgreSQL. A DATABASE_URL "
            "set in your terminal overrides .env; clear it with `Remove-Item Env:DATABASE_URL` "
            "(PowerShell) or open a new terminal."
        )
    return create_engine(url, pool_pre_ping=True)


def connect_with_retry(engine: Engine, attempts: int = 3, pause: float = 2.0) -> None:
    """Neon scales to zero; the first connection after idle can time out."""
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            if attempt == attempts:
                raise
            log.warning("DB connect failed (attempt %d/%d), retrying", attempt, attempts)
            time.sleep(pause * attempt)
