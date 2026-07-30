"""Count a user's people-search allowance in actions, not provider credits.

The user-visible limit was charged against
``people_provider_operation_usage.budget_units`` — provider credit units, where
one PDL search costs one unit per record returned. Fourteen deliberate searches
consumed 85 of 100 units in production, so the UI announced the daily limit
after a handful of companies.

This table counts deliberate user actions and nothing else. The provider ledger
is untouched and continues to control operational cost.

Revision ID: 0026_people_user_discovery_quota
Revises: 0025_job_posting_unbounded_text
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0026_people_user_discovery_quota"
down_revision = "0025_job_posting_unbounded_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "people_user_discovery_quota",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Day boundary in the configured reset timezone, as an ISO date string.
        sa.Column("quota_date", sa.String(length=10), nullable=False),
        sa.Column(
            "discoveries_used",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "quota_date", name="uq_people_user_quota_day"),
    )
    op.create_index(
        "ix_people_user_discovery_quota_user_id",
        "people_user_discovery_quota",
        ["user_id"],
    )
    op.create_index(
        "ix_people_user_discovery_quota_quota_date",
        "people_user_discovery_quota",
        ["quota_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_people_user_discovery_quota_quota_date",
        table_name="people_user_discovery_quota",
    )
    op.drop_index(
        "ix_people_user_discovery_quota_user_id",
        table_name="people_user_discovery_quota",
    )
    op.drop_table("people_user_discovery_quota")
