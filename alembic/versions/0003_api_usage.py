"""api_usage: per-day call counter for rate-limited APIs (Adzuna, Resend)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_usage",
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("calls", sa.Integer, nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("day", "provider"),
    )


def downgrade() -> None:
    op.drop_table("api_usage")
