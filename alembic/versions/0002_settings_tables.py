"""settings tables: search_profiles, app_settings

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT_ARRAY = postgresql.ARRAY(sa.Text)


def upgrade() -> None:
    op.create_table(
        "search_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("titles", TEXT_ARRAY, nullable=False),
        sa.Column("cities", TEXT_ARRAY, server_default="{}"),
        sa.Column("work_modes", TEXT_ARRAY, server_default="{onsite,hybrid,remote}"),
        sa.Column("min_years", sa.SmallInteger, server_default="0"),
        sa.Column("max_years", sa.SmallInteger, server_default="1"),
        sa.Column("exclude_words", TEXT_ARRAY, server_default="{}"),
        sa.Column("active", sa.Boolean, server_default=sa.text("true")),
        sa.CheckConstraint(
            "min_years >= 0 AND min_years <= max_years", name="search_profiles_years_range"
        ),
    )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", postgresql.JSONB, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("search_profiles")
