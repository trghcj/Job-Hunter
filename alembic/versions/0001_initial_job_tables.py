"""initial job tables: jobs, job_scores, job_status, runs, llm_usage

Revision ID: 0001
Revises:
Create Date: 2026-11-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("dedupe_key", sa.Text, nullable=False, unique=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_job_id", sa.Text),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("company", sa.Text, nullable=False),
        sa.Column("location_text", sa.Text),
        sa.Column("city", sa.Text),
        sa.Column("country", sa.Text, server_default="IN"),
        sa.Column("work_mode", sa.Text, server_default="unknown"),
        sa.Column("description", sa.Text),
        sa.Column("salary_text", sa.Text),
        sa.Column("posted_at", TS),
        sa.Column("first_seen_at", TS, server_default=sa.func.now()),
        sa.Column("last_seen_at", TS, server_default=sa.func.now()),
    )
    op.create_table(
        "job_scores",
        sa.Column(
            "job_id", sa.BigInteger, sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("passed_filters", sa.Boolean, nullable=False),
        sa.Column("filter_reason", sa.Text),
        sa.Column("embed_score", sa.REAL),
        sa.Column("title_score", sa.REAL),
        sa.Column("skill_score", sa.REAL),
        sa.Column("llm_fit", sa.SmallInteger),
        sa.Column("llm_json", postgresql.JSONB),
        sa.Column("llm_pending", sa.Boolean, server_default=sa.text("false")),
        sa.Column("final_score", sa.REAL),
        sa.Column("scored_at", TS, server_default=sa.func.now()),
    )
    op.create_table(
        "job_status",
        sa.Column(
            "job_id", sa.BigInteger, sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("emailed_at", TS),
        sa.Column("status", sa.Text, server_default="new"),
        sa.Column("notes", sa.Text),
        sa.Column("updated_at", TS, server_default=sa.func.now()),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("started_at", TS, server_default=sa.func.now()),
        sa.Column("finished_at", TS),
        sa.Column("ok", sa.Boolean),
        sa.Column("fetched", sa.Integer),
        sa.Column("inserted", sa.Integer),
        sa.Column("error", sa.Text),
    )
    op.create_table(
        "llm_usage",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("provider", sa.Text, primary_key=True),
        sa.Column("calls", sa.Integer, server_default="0"),
    )
    op.create_index("jobs_first_seen", "jobs", [sa.text("first_seen_at DESC")])
    op.create_index("scores_final", "job_scores", [sa.text("final_score DESC")])


def downgrade() -> None:
    op.drop_index("scores_final", table_name="job_scores")
    op.drop_index("jobs_first_seen", table_name="jobs")
    for table in ("llm_usage", "runs", "job_status", "job_scores", "jobs"):
        op.drop_table(table)
