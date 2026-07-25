"""Store the scale used by education GPA values.

Revision ID: 0019_education_gpa_scale
Revises: 0018_workday_credentials
Create Date: 2026-07-24 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_education_gpa_scale"
down_revision: str | None = "0018_workday_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "education",
        sa.Column("gpa_scale", sa.String(length=10), nullable=False, server_default="4.0"),
    )


def downgrade() -> None:
    op.drop_column("education", "gpa_scale")
