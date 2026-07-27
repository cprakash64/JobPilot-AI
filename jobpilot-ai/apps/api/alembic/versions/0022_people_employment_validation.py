"""Add versioned current-employment validation.

Revision ID: 0022_people_employment_v2
Revises: 0021_people_funnel_diagnostics
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022_people_employment_v2"
down_revision = "0021_people_funnel_diagnostics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "professional_people",
        sa.Column(
            "employment_revalidation_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "professional_people",
        sa.Column("employment_conflict_detected_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "job_people_candidates",
        sa.Column(
            "employment_validation_status",
            sa.String(length=40),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "job_people_candidates",
        sa.Column(
            "employment_validation_version",
            sa.String(length=40),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.create_index(
        "ix_job_people_candidates_employment_validation_version",
        "job_people_candidates",
        ["employment_validation_version"],
    )
    op.add_column(
        "job_people_candidates",
        sa.Column("employment_validation_checked_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("job_people_candidates", "employment_validation_checked_at")
    op.drop_index(
        "ix_job_people_candidates_employment_validation_version",
        table_name="job_people_candidates",
    )
    op.drop_column("job_people_candidates", "employment_validation_version")
    op.drop_column("job_people_candidates", "employment_validation_status")
    op.drop_column("professional_people", "employment_conflict_detected_at")
    op.drop_column("professional_people", "employment_revalidation_required")
