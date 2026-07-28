"""Widen People employment status and add durable provider usage.

Revision ID: 0024_people_persistence_usage
Revises: 0023_people_evidence
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024_people_persistence_usage"
down_revision = "0023_people_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "job_people_candidates",
        "employment_validation_status",
        existing_type=sa.String(length=40),
        type_=sa.String(length=96),
        existing_nullable=False,
        existing_server_default="legacy",
    )
    op.create_table(
        "people_provider_operation_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "idempotency_key",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("job_postings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "discovery_run_id",
            sa.Integer(),
            sa.ForeignKey("people_discovery_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("operation_type", sa.String(length=60), nullable=False),
        sa.Column(
            "request_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("http_outcome", sa.String(length=96), nullable=False),
        sa.Column("credits_reported", sa.Integer()),
        sa.Column("credits_estimated", sa.Integer()),
        sa.Column(
            "budget_units",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "credit_status",
            sa.String(length=30),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("adapter_version", sa.String(length=96), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "request_count >= 1",
            name="ck_people_provider_usage_request_count",
        ),
        sa.CheckConstraint(
            "budget_units >= 0",
            name="ck_people_provider_usage_budget_units",
        ),
        sa.CheckConstraint(
            "credits_reported IS NULL OR credits_reported >= 0",
            name="ck_people_provider_usage_reported_credits",
        ),
        sa.CheckConstraint(
            "credits_estimated IS NULL OR credits_estimated >= 0",
            name="ck_people_provider_usage_estimated_credits",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_people_provider_operation_usage_idempotency",
        ),
    )
    for column in (
        "idempotency_key",
        "user_id",
        "job_id",
        "discovery_run_id",
        "provider",
        "operation_type",
        "occurred_at",
    ):
        op.create_index(
            f"ix_people_provider_operation_usage_{column}",
            "people_provider_operation_usage",
            [column],
        )
    op.create_index(
        "ix_people_provider_usage_budget",
        "people_provider_operation_usage",
        ["user_id", "occurred_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    candidates = sa.table(
        "job_people_candidates",
        sa.column(
            "employment_validation_status",
            sa.String(length=96),
        ),
    )
    too_long = connection.scalar(
        sa.select(sa.func.count())
        .select_from(candidates)
        .where(
            sa.func.length(candidates.c.employment_validation_status) > 40
        )
    )
    if too_long:
        raise RuntimeError(
            "Cannot downgrade employment_validation_status to VARCHAR(40): "
            "values longer than 40 characters exist."
        )

    op.drop_index(
        "ix_people_provider_usage_budget",
        table_name="people_provider_operation_usage",
    )
    for column in reversed((
        "idempotency_key",
        "user_id",
        "job_id",
        "discovery_run_id",
        "provider",
        "operation_type",
        "occurred_at",
    )):
        op.drop_index(
            f"ix_people_provider_operation_usage_{column}",
            table_name="people_provider_operation_usage",
        )
    op.drop_table("people_provider_operation_usage")
    op.alter_column(
        "job_people_candidates",
        "employment_validation_status",
        existing_type=sa.String(length=96),
        type_=sa.String(length=40),
        existing_nullable=False,
        existing_server_default="legacy",
    )
