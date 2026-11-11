"""users: Google sign-in accounts; personal tables get user_id; private jobs

Existing single-user data moves to user 1, the owner placeholder, which the owner's first
Google sign-in claims (OWNER_EMAIL).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PER_USER_TABLES = ("search_profiles", "mail_accounts", "job_scores", "job_status")
MOVED_SETTINGS = ("digest", "digest_to", "max_age_days")


def _user_fk(table: str) -> None:
    op.add_column(table, sa.Column("user_id", sa.Integer, nullable=False, server_default="1"))
    op.alter_column(table, "user_id", server_default=None)
    op.create_foreign_key(f"{table}_user_id_fkey", table, "users", ["user_id"], ["id"],
                          ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("firebase_uid", sa.Text, unique=True),
        sa.Column("email", sa.Text),
        sa.Column("name", sa.Text),
        sa.Column("is_owner", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("blocked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
    )
    op.execute("INSERT INTO users (id, is_owner) VALUES (1, true)")
    op.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), 1)")

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", postgresql.JSONB),
    )
    keys = ", ".join(f"'{k}'" for k in MOVED_SETTINGS)
    op.execute(f"INSERT INTO user_settings (user_id, key, value) "
               f"SELECT 1, key, value FROM app_settings WHERE key IN ({keys})")
    op.execute(f"DELETE FROM app_settings WHERE key IN ({keys})")

    op.add_column("jobs", sa.Column("private", sa.Boolean, nullable=False,
                                    server_default=sa.text("false")))
    op.create_table(
        "job_owners",
        sa.Column("job_id", sa.BigInteger, sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
    )

    for table in PER_USER_TABLES:
        _user_fk(table)
    op.create_unique_constraint("mail_accounts_user_id_key", "mail_accounts", ["user_id"])
    for table in ("job_scores", "job_status"):
        op.drop_constraint(f"{table}_pkey", table, type_="primary")
        op.create_primary_key(f"{table}_pkey", table, ["user_id", "job_id"])


def downgrade() -> None:
    """Back to one user: keeps user 1's rows only."""
    for table in ("job_scores", "job_status"):
        op.execute(f"DELETE FROM {table} WHERE user_id <> 1")
        op.drop_constraint(f"{table}_pkey", table, type_="primary")
        op.create_primary_key(f"{table}_pkey", table, ["job_id"])
    op.drop_constraint("mail_accounts_user_id_key", "mail_accounts", type_="unique")
    for table in PER_USER_TABLES:
        op.execute(f"DELETE FROM {table} WHERE user_id <> 1")
        op.drop_constraint(f"{table}_user_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "user_id")
    op.drop_table("job_owners")
    op.execute("DELETE FROM jobs WHERE private")
    op.drop_column("jobs", "private")
    keys = ", ".join(f"'{k}'" for k in MOVED_SETTINGS)
    op.execute(f"INSERT INTO app_settings (key, value) SELECT key, value FROM user_settings "
               f"WHERE user_id = 1 AND key IN ({keys}) ON CONFLICT (key) DO NOTHING")
    op.drop_table("user_settings")
    op.drop_table("users")
