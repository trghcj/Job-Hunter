import io

import pytest
from alembic import command

from jobhunter.__main__ import alembic_config, main
from jobhunter.db import Base
from jobhunter.settings import normalize_db_url

TABLES = ["jobs", "job_scores", "job_status", "runs", "llm_usage"]


def offline_sql() -> str:
    cfg = alembic_config("postgresql+psycopg://")
    cfg.attributes["configure_logger"] = False
    buf = io.StringIO()
    cfg.output_buffer = buf
    command.upgrade(cfg, "head", sql=True)
    return buf.getvalue()


def test_migration_creates_all_five_tables_and_indexes():
    sql = offline_sql()
    for table in TABLES:
        assert f"CREATE TABLE {table} (" in sql
    assert "UNIQUE (dedupe_key)" in sql
    assert "CREATE INDEX jobs_first_seen ON jobs (first_seen_at DESC)" in sql
    assert "CREATE INDEX scores_final ON job_scores (final_score DESC)" in sql
    assert "ON DELETE CASCADE" in sql


def test_orm_models_cover_same_tables():
    expected = [*TABLES, "search_profiles", "app_settings", "api_usage"]
    assert sorted(Base.metadata.tables) == sorted(expected)


@pytest.mark.parametrize(
    "url",
    ["postgres://u:p@h/db", "postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"],
)
def test_db_url_normalized_to_psycopg(url):
    assert normalize_db_url(url) == "postgresql+psycopg://u:p@h/db"


def test_unimplemented_commands_exit_cleanly():
    assert main(["run"]) == 2
