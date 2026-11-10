"""Database fixture: a throwaway schema in TEST_DATABASE_URL (a local Postgres, never the
hosted one). Tests that need it are skipped when TEST_DATABASE_URL is not set."""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from jobhunter.db import Base
from jobhunter.settings import get_settings


@pytest.fixture
def db_session():
    url = get_settings().test_database_url
    if not url:
        pytest.skip("TEST_DATABASE_URL not set (local Postgres for DB tests)")
    schema = f"test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
