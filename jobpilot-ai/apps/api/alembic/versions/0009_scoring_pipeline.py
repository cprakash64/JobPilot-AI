"""Add fit-score lifecycle state to job_matches, expiry columns to job_postings,
and an ingestion_runs table.

Background: fit scores were only ever written for the single user who triggered
discovery/refresh, and a match row required a non-null ``fit_score``. Newly
ingested jobs therefore had no match row for other users and rendered as
"Not scored" forever. This migration makes ``fit_score``/``fit_summary`` nullable
so a match can exist in a transient/pending state, and adds the columns the
background scoring pipeline needs (state, version, hashes, attempts).

Existing match rows already carry a computed number, so they are backfilled to
``score_state = 'scored'`` with ``score_version = 0`` to avoid a mass rescore.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009_scoring_pipeline"
down_revision: str | None = "0008_application_status_applying"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- job_matches: make score nullable + add lifecycle columns ---
    op.alter_column("job_matches", "fit_score", existing_type=sa.Float(), nullable=True)
    op.alter_column("job_matches", "fit_summary", existing_type=sa.Text(), nullable=True)

    op.add_column(
        "job_matches",
        sa.Column("score_state", sa.String(length=30), nullable=False, server_default="pending"),
    )
    op.add_column(
        "job_matches",
        sa.Column("score_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("job_matches", sa.Column("scoring_error_code", sa.String(length=60), nullable=True))
    op.add_column(
        "job_matches",
        sa.Column("scoring_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("job_matches", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_matches", sa.Column("profile_version", sa.String(length=64), nullable=True))
    op.add_column("job_matches", sa.Column("job_content_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_job_matches_score_state", "job_matches", ["score_state"])

    # Existing rows already hold a real number → mark them scored, not pending.
    op.execute(
        "UPDATE job_matches SET score_state = 'scored', scored_at = updated_at "
        "WHERE fit_score IS NOT NULL"
    )

    # --- job_postings: expiry / freshness tracking ---
    op.add_column("job_postings", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "job_postings",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.create_index("ix_job_postings_last_seen_at", "job_postings", ["last_seen_at"])
    op.create_index("ix_job_postings_is_active", "job_postings", ["is_active"])
    op.execute("UPDATE job_postings SET last_seen_at = discovered_at WHERE last_seen_at IS NULL")

    # --- ingestion_runs ---
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trigger", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sources_attempted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sources_succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sources_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_expired", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scoring_tasks_queued", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.JSON(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
    )
    op.create_index("ix_ingestion_runs_trigger", "ingestion_runs", ["trigger"])
    op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"])


def downgrade() -> None:
    op.drop_table("ingestion_runs")

    op.drop_index("ix_job_postings_is_active", table_name="job_postings")
    op.drop_index("ix_job_postings_last_seen_at", table_name="job_postings")
    op.drop_column("job_postings", "is_active")
    op.drop_column("job_postings", "last_seen_at")

    op.drop_index("ix_job_matches_score_state", table_name="job_matches")
    for col in (
        "job_content_hash",
        "profile_version",
        "scored_at",
        "scoring_attempts",
        "scoring_error_code",
        "score_version",
        "score_state",
    ):
        op.drop_column("job_matches", col)
    op.alter_column("job_matches", "fit_summary", existing_type=sa.Text(), nullable=False)
    op.alter_column("job_matches", "fit_score", existing_type=sa.Float(), nullable=False)
