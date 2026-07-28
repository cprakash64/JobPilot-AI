"""Add safe People discovery funnel diagnostics.

Revision ID: 0021_people_funnel_diagnostics
Revises: 0020_people_who_can_help
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021_people_funnel_diagnostics"
down_revision = "0020_people_who_can_help"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "people_discovery_runs",
        sa.Column(
            "company_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "people_discovery_runs",
        sa.Column(
            "category_diagnostics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("people_discovery_runs", "category_diagnostics")
    op.drop_column("people_discovery_runs", "company_context")
