"""Separate provider observation, employment evidence, and verification cache.

Revision ID: 0023_people_evidence
Revises: 0022_people_employment_v2
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023_people_evidence"
down_revision = "0022_people_employment_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "professional_person_sources",
        sa.Column("provider_record_observed_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "professional_person_sources",
        sa.Column("provider_employment_updated_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "professional_person_sources",
        sa.Column("employment_verified_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "professional_person_sources",
        sa.Column("employment_source", sa.String(length=80)),
    )
    op.add_column(
        "professional_person_sources",
        sa.Column("exact_company_match", sa.Boolean()),
    )
    op.add_column(
        "professional_person_sources",
        sa.Column("current_role_indicator", sa.Boolean()),
    )
    op.add_column(
        "professional_person_sources",
        sa.Column("conflicting_employer_observed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "people_employment_verification_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("job_postings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "discovery_run_id",
            sa.Integer(),
            sa.ForeignKey("people_discovery_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("cache_key_hash", sa.String(length=64), nullable=False),
        sa.Column("verification_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=60), nullable=False),
        sa.Column(
            "credits_used", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_people_employment_verification_runs_job_id",
        "people_employment_verification_runs",
        ["job_id"],
    )
    op.create_index(
        "ix_people_employment_verification_runs_user_id",
        "people_employment_verification_runs",
        ["user_id"],
    )
    op.create_index(
        "ix_people_employment_verification_runs_discovery_run_id",
        "people_employment_verification_runs",
        ["discovery_run_id"],
    )
    op.create_index(
        "ix_people_employment_verification_runs_category",
        "people_employment_verification_runs",
        ["category"],
    )
    op.create_index(
        "ix_people_employment_verification_runs_cache_key_hash",
        "people_employment_verification_runs",
        ["cache_key_hash"],
    )
    op.create_index(
        "ix_people_employment_verification_runs_verification_version",
        "people_employment_verification_runs",
        ["verification_version"],
    )
    op.create_index(
        "ix_people_employment_verification_runs_status",
        "people_employment_verification_runs",
        ["status"],
    )
    op.create_index(
        "ix_people_employment_verification_runs_started_at",
        "people_employment_verification_runs",
        ["started_at"],
    )
    op.create_index(
        "ix_people_employment_verification_runs_expires_at",
        "people_employment_verification_runs",
        ["expires_at"],
    )
    op.create_index(
        "ix_people_employment_verification_cache",
        "people_employment_verification_runs",
        ["cache_key_hash", "verification_version", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_people_employment_verification_cache",
        table_name="people_employment_verification_runs",
    )
    for name in (
        "ix_people_employment_verification_runs_expires_at",
        "ix_people_employment_verification_runs_started_at",
        "ix_people_employment_verification_runs_status",
        "ix_people_employment_verification_runs_verification_version",
        "ix_people_employment_verification_runs_cache_key_hash",
        "ix_people_employment_verification_runs_category",
        "ix_people_employment_verification_runs_discovery_run_id",
        "ix_people_employment_verification_runs_user_id",
        "ix_people_employment_verification_runs_job_id",
    ):
        op.drop_index(name, table_name="people_employment_verification_runs")
    op.drop_table("people_employment_verification_runs")
    op.drop_column(
        "professional_person_sources", "conflicting_employer_observed_at"
    )
    op.drop_column("professional_person_sources", "current_role_indicator")
    op.drop_column("professional_person_sources", "exact_company_match")
    op.drop_column("professional_person_sources", "employment_source")
    op.drop_column("professional_person_sources", "employment_verified_at")
    op.drop_column(
        "professional_person_sources", "provider_employment_updated_at"
    )
    op.drop_column("professional_person_sources", "provider_record_observed_at")
