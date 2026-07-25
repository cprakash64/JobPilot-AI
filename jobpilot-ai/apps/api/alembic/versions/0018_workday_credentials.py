"""Encrypted optional Workday employer-account credential.

Revision ID: 0018_workday_credentials
Revises: 0017_session_profile_revision
Create Date: 2026-07-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_workday_credentials"
down_revision: str | None = "0017_session_profile_revision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("workday_password_ciphertext", sa.String(length=1000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "workday_password_ciphertext")
