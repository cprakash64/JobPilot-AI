"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=50)),
        sa.Column("location_city", sa.String(length=120)),
        sa.Column("location_state", sa.String(length=120)),
        sa.Column("location_country", sa.String(length=120)),
        sa.Column("linkedin_url", sa.String(length=500)),
        sa.Column("github_url", sa.String(length=500)),
        sa.Column("portfolio_url", sa.String(length=500)),
        sa.Column("work_authorization", sa.String(length=120)),
        sa.Column("requires_sponsorship", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("open_to_relocation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("target_roles", json_type, nullable=False, server_default="[]"),
        sa.Column("target_levels", json_type, nullable=False, server_default="[]"),
        sa.Column("preferred_locations", json_type, nullable=False, server_default="[]"),
        sa.Column("remote_preference", sa.String(length=50)),
        sa.Column("skills", json_type, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "sensitive_demographics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True),
        sa.Column("gender", sa.String(length=120)),
        sa.Column("veteran_status", sa.String(length=120)),
        sa.Column("disability_status", sa.String(length=120)),
        sa.Column("ethnicity", sa.String(length=120)),
        sa.Column("hispanic_latino_status", sa.String(length=120)),
        sa.Column("consent_to_store", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    for table in ["education", "experience", "projects", "certifications", "awards"]:
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        )

    op.add_column("education", sa.Column("school", sa.String(length=200), nullable=False))
    op.add_column("education", sa.Column("degree", sa.String(length=160)))
    op.add_column("education", sa.Column("major", sa.String(length=160)))
    op.add_column("education", sa.Column("minor", sa.String(length=160)))
    op.add_column("education", sa.Column("start_date", sa.Date()))
    op.add_column("education", sa.Column("end_date", sa.Date()))
    op.add_column("education", sa.Column("gpa", sa.String(length=20)))
    op.add_column("education", sa.Column("honors", json_type, nullable=False, server_default="[]"))
    op.add_column("education", sa.Column("coursework", json_type, nullable=False, server_default="[]"))

    op.add_column("experience", sa.Column("company", sa.String(length=200), nullable=False))
    op.add_column("experience", sa.Column("title", sa.String(length=200), nullable=False))
    op.add_column("experience", sa.Column("location", sa.String(length=200)))
    op.add_column("experience", sa.Column("start_date", sa.Date()))
    op.add_column("experience", sa.Column("end_date", sa.Date()))
    op.add_column("experience", sa.Column("currently_working", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("experience", sa.Column("bullets", json_type, nullable=False, server_default="[]"))
    op.add_column("experience", sa.Column("technologies", json_type, nullable=False, server_default="[]"))
    op.add_column("experience", sa.Column("measurable_impact", json_type, nullable=False, server_default="[]"))

    op.add_column("projects", sa.Column("name", sa.String(length=200), nullable=False))
    op.add_column("projects", sa.Column("description", sa.Text()))
    op.add_column("projects", sa.Column("bullets", json_type, nullable=False, server_default="[]"))
    op.add_column("projects", sa.Column("technologies", json_type, nullable=False, server_default="[]"))
    op.add_column("projects", sa.Column("links", json_type, nullable=False, server_default="[]"))
    op.add_column("projects", sa.Column("start_date", sa.Date()))
    op.add_column("projects", sa.Column("end_date", sa.Date()))

    op.add_column("certifications", sa.Column("name", sa.String(length=200), nullable=False))
    op.add_column("certifications", sa.Column("issuer", sa.String(length=200)))
    op.add_column("certifications", sa.Column("issue_date", sa.Date()))
    op.add_column("certifications", sa.Column("expiration_date", sa.Date()))
    op.add_column("certifications", sa.Column("credential_url", sa.String(length=500)))

    op.add_column("awards", sa.Column("name", sa.String(length=200), nullable=False))
    op.add_column("awards", sa.Column("issuer", sa.String(length=200)))
    op.add_column("awards", sa.Column("date", sa.Date()))
    op.add_column("awards", sa.Column("description", sa.Text()))

    op.create_table(
        "job_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), unique=True),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("supports_api", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("terms_notes", sa.Text()),
    )

    op.create_table(
        "job_postings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("job_sources.id", ondelete="SET NULL")),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=False),
        sa.Column("location", sa.String(length=255)),
        sa.Column("remote_type", sa.String(length=80)),
        sa.Column("employment_type", sa.String(length=80)),
        sa.Column("seniority_level", sa.String(length=80)),
        sa.Column("salary_min", sa.Float()),
        sa.Column("salary_max", sa.Float()),
        sa.Column("currency", sa.String(length=10)),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("application_url", sa.String(length=1000), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("description_raw", sa.Text(), nullable=False, server_default=""),
        sa.Column("description_clean", sa.Text(), nullable=False, server_default=""),
        sa.Column("required_skills", json_type, nullable=False, server_default="[]"),
        sa.Column("preferred_skills", json_type, nullable=False, server_default="[]"),
        sa.Column("years_experience_min", sa.Float()),
        sa.Column("work_authorization_notes", sa.Text()),
        sa.Column("hash_for_deduplication", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("source_id", "external_id", name="uq_job_source_external"),
    )
    op.create_index("ix_job_postings_posted_at", "job_postings", ["posted_at"])
    op.create_index("ix_job_postings_discovered_at", "job_postings", ["discovered_at"])
    op.create_index("ix_job_postings_hash_for_deduplication", "job_postings", ["hash_for_deduplication"])

    op.create_table(
        "job_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id", ondelete="CASCADE"), index=True),
        sa.Column("fit_score", sa.Float(), nullable=False),
        sa.Column("fit_summary", sa.Text(), nullable=False),
        sa.Column("strengths", json_type, nullable=False, server_default="[]"),
        sa.Column("gaps", json_type, nullable=False, server_default="[]"),
        sa.Column("risks", json_type, nullable=False, server_default="[]"),
        sa.Column("recommended_resume_angle", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "job_id", name="uq_job_match_user_job"),
    )

    document_type = postgresql.ENUM(
        "resume",
        "cover_letter",
        "application_answers",
        name="documenttype",
        create_type=False,
    )
    document_format = postgresql.ENUM(
        "docx",
        "pdf",
        "markdown",
        "json",
        name="documentformat",
        create_type=False,
    )
    app_status = postgresql.ENUM(
        "saved",
        "ready_to_apply",
        "applied",
        "interview",
        "rejected",
        "offer",
        name="applicationstatus",
        create_type=False,
    )
    document_type.create(op.get_bind(), checkfirst=True)
    document_format.create(op.get_bind(), checkfirst=True)
    app_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "generated_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id", ondelete="CASCADE"), index=True),
        sa.Column("type", document_type, nullable=False),
        sa.Column("format", document_format, nullable=False),
        sa.Column("content", json_type, nullable=False, server_default="{}"),
        sa.Column("file_path", sa.String(length=1000)),
        sa.Column("model_used", sa.String(length=120)),
        sa.Column("prompt_version", sa.String(length=50), nullable=False, server_default="v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "application_tracker",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id", ondelete="CASCADE"), index=True),
        sa.Column("status", app_status, nullable=False, server_default="saved"),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.Column("follow_up_date", sa.Date()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "job_id", name="uq_tracker_user_job"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("application_tracker")
    op.drop_table("generated_documents")
    op.drop_table("job_matches")
    op.drop_table("job_postings")
    op.drop_table("job_sources")
    op.drop_table("awards")
    op.drop_table("certifications")
    op.drop_table("projects")
    op.drop_table("experience")
    op.drop_table("education")
    op.drop_table("sensitive_demographics")
    op.drop_table("user_profiles")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    postgresql.ENUM(name="applicationstatus", create_type=False).drop(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(name="documentformat", create_type=False).drop(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(name="documenttype", create_type=False).drop(op.get_bind(), checkfirst=True)
