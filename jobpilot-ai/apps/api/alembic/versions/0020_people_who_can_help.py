"""People Who Can Help canonical, recommendation, run, and feedback storage.

Revision ID: 0020_people_who_can_help
Revises: 0019_education_gpa_scale
"""
# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020_people_who_can_help"
down_revision: str | None = "0019_education_gpa_scale"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "professional_people",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_full_name", sa.String(255), nullable=False),
        sa.Column("normalized_full_name", sa.String(255), nullable=False),
        sa.Column("current_company_name", sa.String(255), nullable=False),
        sa.Column("current_company_domain", sa.String(255)),
        sa.Column("current_title", sa.String(255), nullable=False),
        sa.Column("normalized_title", sa.String(255), nullable=False),
        sa.Column("department", sa.String(120)),
        sa.Column("seniority", sa.String(80)),
        sa.Column("professional_location", sa.String(255)),
        sa.Column("linkedin_url", sa.String(1000)),
        sa.Column("linkedin_url_normalized", sa.String(1000), unique=True),
        sa.Column("professional_email_ciphertext", sa.String(2000)),
        sa.Column("professional_email_hash", sa.String(64), unique=True),
        sa.Column("email_verification_status", sa.String(30), nullable=False, server_default="not_requested"),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("employment_last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_professional_people_normalized_full_name", "professional_people", ["normalized_full_name"])
    op.create_index("ix_professional_people_current_company_domain", "professional_people", ["current_company_domain"])
    op.create_table(
        "professional_person_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("professional_people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_person_id", sa.String(255), nullable=False),
        sa.Column("source_profile_url", sa.String(1000)),
        sa.Column("source_last_updated_at", sa.DateTime(timezone=True)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("normalized_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("field_provenance", postgresql.JSONB(), nullable=False),
        sa.Column("redacted_payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("provider", "provider_person_id", name="uq_people_source_identity"),
    )
    op.create_index("ix_professional_person_sources_person_id", "professional_person_sources", ["person_id"])
    op.create_table(
        "job_people_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("professional_people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_category", sa.String(40), nullable=False),
        sa.Column("category_score", sa.Float(), nullable=False),
        sa.Column("data_confidence", sa.Float(), nullable=False),
        sa.Column("current_employment_confidence", sa.Float(), nullable=False),
        sa.Column("recommendation_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("recommendation_limitations", postgresql.JSONB(), nullable=False),
        sa.Column("scoring_version", sa.String(40), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "person_id", "candidate_category", name="uq_job_person_category"),
    )
    op.create_index("ix_job_people_candidates_job_id", "job_people_candidates", ["job_id"])
    op.create_index("ix_job_people_candidates_person_id", "job_people_candidates", ["person_id"])
    op.create_index("ix_job_people_candidates_candidate_category", "job_people_candidates", ["candidate_category"])
    op.create_index("ix_job_people_candidates_expires_at", "job_people_candidates", ["expires_at"])
    op.create_index("ix_job_people_candidates_scoring_version", "job_people_candidates", ["scoring_version"])
    op.create_index("ix_job_people_fresh", "job_people_candidates", ["job_id", "expires_at"])
    op.create_table(
        "user_job_people_recommendations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_people_candidate_id", sa.Integer(), sa.ForeignKey("job_people_candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship_type", sa.String(40)),
        sa.Column("shared_school", sa.String(255)),
        sa.Column("shared_employer", sa.String(255)),
        sa.Column("connection_strength", sa.Float(), nullable=False, server_default="0"),
        sa.Column("personalized_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("personalized_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("viewed_at", sa.DateTime(timezone=True)),
        sa.Column("saved_at", sa.DateTime(timezone=True)),
        sa.Column("contacted_at", sa.DateTime(timezone=True)),
        sa.Column("suppressed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "job_id", "job_people_candidate_id", name="uq_user_job_people_candidate"),
    )
    for column in ("user_id", "job_id", "job_people_candidate_id"):
        op.create_index(f"ix_user_job_people_recommendations_{column}", "user_job_people_recommendations", [column])
    op.create_table(
        "people_discovery_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("query_fingerprint", sa.String(64), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("records_searched", sa.Integer(), nullable=False),
        sa.Column("records_enriched", sa.Integer(), nullable=False),
        sa.Column("provider_credits_used", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(60)),
        sa.Column("safe_failure_message", sa.String(255)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    for column in ("job_id", "user_id", "status", "query_fingerprint"):
        op.create_index(f"ix_people_discovery_runs_{column}", "people_discovery_runs", [column])
    op.create_index("ix_people_run_cache", "people_discovery_runs", ["job_id", "query_fingerprint", "completed_at"])
    op.create_table(
        "people_recommendation_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recommendation_id", sa.Integer(), sa.ForeignKey("user_job_people_recommendations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relevance_rating", sa.String(30)),
        sa.Column("employment_current_rating", sa.String(30)),
        sa.Column("information_correct_rating", sa.String(30)),
        sa.Column("contacted", sa.Boolean(), nullable=False),
        sa.Column("received_response", sa.Boolean(), nullable=False),
        sa.Column("incorrect_reason", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_people_recommendation_feedback_user_id", "people_recommendation_feedback", ["user_id"])
    op.create_index("ix_people_recommendation_feedback_recommendation_id", "people_recommendation_feedback", ["recommendation_id"])


def downgrade() -> None:
    op.drop_table("people_recommendation_feedback")
    op.drop_table("people_discovery_runs")
    op.drop_table("user_job_people_recommendations")
    op.drop_table("job_people_candidates")
    op.drop_table("professional_person_sources")
    op.drop_table("professional_people")
