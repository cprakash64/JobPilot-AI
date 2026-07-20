"""Add location_postal_code to user_profiles.

Application forms ask for a ZIP/postal code constantly (it was one of the
required Greenhouse fields left blank in the reported Samsara run), but the
profile had nowhere to store one, so the extension could never fill it.

Purely additive and nullable: no backfill, no data loss, safe to apply while the
previous release is still serving.

Revision ID: 0014_profile_postal_code
Revises: 0013_name_parts_phone
Create Date: 2026-07-19 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_profile_postal_code"
down_revision: str | None = "0013_name_parts_phone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_profiles", sa.Column("location_postal_code", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "location_postal_code")
