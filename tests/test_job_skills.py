"""Job skills are extracted once when a job is saved, and the backfill fills old rows."""

from datetime import UTC, datetime

from alembic import command
from sqlalchemy import create_engine, inspect, select

from jobhunter.db import Job
from jobhunter.normalize import normalize_job
from jobhunter.pipeline import backfill_job_skills, upsert_jobs
from tests.test_migration_users import _cfg, schema_url  # noqa: F401  (fixture)
from tests.test_pipeline import raw

NOW = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)


def test_upsert_stores_skills(db_session):
    upsert_jobs(db_session, [normalize_job(raw("React Developer",
                                               description="React, TypeScript, AWS"))], NOW)
    job = db_session.scalars(select(Job)).one()
    assert job.skills == ["AWS", "React", "TypeScript"]
    assert job.title_skills == ["React"]


def test_backfill_fills_missing_only(db_session):
    upsert_jobs(db_session, [normalize_job(raw("Python Developer", company=c,
                                               description="Django and SQL"))
                             for c in ("A", "B", "C")], NOW)
    rows = db_session.scalars(select(Job).order_by(Job.company)).all()
    rows[0].skills, rows[0].title_skills = None, None
    rows[1].skills, rows[1].title_skills = None, None
    rows[2].skills = ["Kept"]
    db_session.commit()
    assert backfill_job_skills(db_session) == 2
    db_session.expire_all()
    rows = db_session.scalars(select(Job).order_by(Job.company)).all()
    assert rows[0].skills == ["Django", "Python", "SQL"] and rows[0].title_skills == ["Python"]
    assert rows[2].skills == ["Kept"]


def test_migration_0007_up_and_down(schema_url):  # noqa: F811
    command.upgrade(_cfg(schema_url), "head")
    engine = create_engine(schema_url)
    cols = {c["name"] for c in inspect(engine).get_columns("jobs")}
    assert {"skills", "title_skills"} <= cols
    assert "fit_reason" in {c["name"] for c in inspect(engine).get_columns("job_scores")}
    engine.dispose()
    command.downgrade(_cfg(schema_url), "0006")
    engine = create_engine(schema_url)
    assert "skills" not in {c["name"] for c in inspect(engine).get_columns("jobs")}
    engine.dispose()


def test_rescore_command_backfills(db_session, monkeypatch, capsys):
    import jobhunter.__main__ as cli

    upsert_jobs(db_session, [normalize_job(raw("Java Developer", description="Spring Boot"))], NOW)
    job = db_session.scalars(select(Job)).one()
    job.skills = None
    db_session.commit()
    monkeypatch.setattr(cli, "_session", lambda: db_session)
    assert cli.main(["rescore"]) == 0
    db_session.expire_all()
    assert db_session.scalars(select(Job)).one().skills == ["Java", "Spring Boot"]
    assert "1 job" in capsys.readouterr().out
