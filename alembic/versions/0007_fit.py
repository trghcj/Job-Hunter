"""fit scoring: skills on jobs, a reason on each score

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Old rows stay NULL; `python -m jobhunter rescore` fills them.
    op.add_column("jobs", sa.Column("skills", ARRAY(sa.Text)))
    op.add_column("jobs", sa.Column("title_skills", ARRAY(sa.Text)))
    op.add_column("job_scores", sa.Column("fit_reason", sa.Text))


def downgrade() -> None:
    op.drop_column("job_scores", "fit_reason")
    op.drop_column("jobs", "title_skills")
    op.drop_column("jobs", "skills")
