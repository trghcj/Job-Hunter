"""mail_accounts: the Gmail account whose job-alert emails are read

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("gmail_address", sa.Text, nullable=False),
        sa.Column("app_password_enc", sa.LargeBinary, nullable=False),  # Fernet, SETTINGS_KEY
        sa.Column("senders", postgresql.ARRAY(sa.Text), server_default="{}"),
        sa.Column("last_checked", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("mail_accounts")
