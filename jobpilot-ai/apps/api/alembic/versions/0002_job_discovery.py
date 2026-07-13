"""job discovery fields

Adds parsing/matching fields to job_postings and job_matches to support
profile-based job discovery, description parsing, and explainable fit scoring.

Revision ID: 0002_job_discovery
Revises: 0001_initial
Create Date: 2026-07-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_job_discovery"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("job_postings", sa.Column("responsibilities", json_type, nullable=True))
    op.add_column("job_postings", sa.Column("degree_requirement", sa.String(length=200), nullable=True))
    op.add_column("job_postings", sa.Column("parse_confidence", sa.Float(), nullable=True))
    op.add_column("job_postings", sa.Column("raw_json", json_type, nullable=True))

    op.add_column("job_matches", sa.Column("fit_label", sa.String(length=40), nullable=True))
    op.add_column("job_matches", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("job_matches", sa.Column("explanation_source", sa.String(length=40), nullable=True))
    op.add_column(
        "job_matches",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_column("job_matches", "updated_at")
    op.drop_column("job_matches", "explanation_source")
    op.drop_column("job_matches", "confidence")
    op.drop_column("job_matches", "fit_label")

    op.drop_column("job_postings", "raw_json")
    op.drop_column("job_postings", "parse_confidence")
    op.drop_column("job_postings", "degree_requirement")
    op.drop_column("job_postings", "responsibilities")
