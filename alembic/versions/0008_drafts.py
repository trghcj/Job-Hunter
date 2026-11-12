"""drafts: a cover letter and resume tips per user per job, and a daily count

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _user_id() -> sa.Column:
    return sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True)


def upgrade() -> None:
    op.create_table(
        "drafts",
        _user_id(),
        sa.Column("job_id", sa.BigInteger, sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("letter", sa.Text, nullable=False),
        sa.Column("tips", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "draft_usage",
        _user_id(),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("calls", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("draft_usage")
    op.drop_table("drafts")
