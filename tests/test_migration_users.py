"""Migration 0005 against a real Postgres (TEST_DATABASE_URL): single-user data moves to
the owner placeholder (user 1). Skipped when TEST_DATABASE_URL is unset."""

import uuid

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from jobhunter.__main__ import alembic_config
from jobhunter.db import Base
from jobhunter.settings import get_settings


@pytest.fixture
def schema_url():
    url = get_settings().test_database_url
    if not url:
        pytest.skip("TEST_DATABASE_URL not set (local Postgres for DB tests)")
    schema = f"mig_{uuid.uuid4().hex[:10]}"
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    sep = "&" if "?" in url else "?"
    scoped = f"{url}{sep}options=-csearch_path%3D{schema}"
    try:
        yield scoped
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _cfg(url: str):
    return alembic_config(url)


def test_upgrade_moves_single_user_data_to_owner(schema_url):
    command.upgrade(_cfg(schema_url), "0004")
    engine = create_engine(schema_url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO search_profiles (name, titles) VALUES ('default', '{x}')"))
        c.execute(text("INSERT INTO mail_accounts (gmail_address, app_password_enc) "
                       "VALUES ('a@gmail.com', 'secret')"))
        job_id = c.execute(text(
            "INSERT INTO jobs (dedupe_key, source, url, title, company) "
            "VALUES ('k', 's', 'https://x', 't', 'c') RETURNING id")).scalar_one()
        c.execute(text("INSERT INTO job_scores (job_id, passed_filters) VALUES (:j, true)"),
                  {"j": job_id})
        c.execute(text("INSERT INTO job_status (job_id) VALUES (:j)"), {"j": job_id})
        for key, value in [("digest", '{"max_jobs": 40}'), ("digest_to", '["me@x.com"]'),
                           ("max_age_days", "10"), ("sources", '{"adzuna": {"enabled": true}}')]:
            c.execute(text("INSERT INTO app_settings (key, value) VALUES (:k, CAST(:v AS jsonb))"),
                      {"k": key, "v": value})

    command.upgrade(_cfg(schema_url), "head")

    with engine.connect() as c:
        users = c.execute(text("SELECT id, is_owner, firebase_uid FROM users")).all()
        assert users == [(1, True, None)]
        for table in ("search_profiles", "mail_accounts", "job_scores", "job_status"):
            assert c.execute(text(f"SELECT user_id FROM {table}")).scalars().all() == [1]
        moved = dict(c.execute(
            text("SELECT key, value FROM user_settings WHERE user_id = 1")).all())
        assert moved == {"digest": {"max_jobs": 40}, "digest_to": ["me@x.com"], "max_age_days": 10}
        left = set(c.execute(text("SELECT key FROM app_settings")).scalars())
        assert left == {"sources"}
        assert c.execute(text("SELECT private FROM jobs")).scalar_one() is False
        # a new user gets an id after the placeholder
        new_id = c.execute(text("INSERT INTO users (firebase_uid, email) VALUES ('u', 'e') "
                                "RETURNING id")).scalar_one()
        assert new_id > 1
    engine.dispose()


def test_models_match_migration(schema_url):
    command.upgrade(_cfg(schema_url), "head")
    engine = create_engine(schema_url)
    db = inspect(engine)
    for name, table in Base.metadata.tables.items():
        assert {c["name"] for c in db.get_columns(name)} == {c.name for c in table.columns}, name
    assert db.get_pk_constraint("job_scores")["constrained_columns"] == ["user_id", "job_id"]
    assert db.get_pk_constraint("job_status")["constrained_columns"] == ["user_id", "job_id"]
    engine.dispose()


def test_downgrade_back_to_0004(schema_url):
    command.upgrade(_cfg(schema_url), "head")
    command.downgrade(_cfg(schema_url), "0004")
    engine = create_engine(schema_url)
    db = inspect(engine)
    assert "users" not in db.get_table_names()
    assert "user_id" not in {c["name"] for c in db.get_columns("search_profiles")}
    engine.dispose()
