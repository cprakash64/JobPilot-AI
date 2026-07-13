"""assisted auto-apply: application sessions, audit log, answer vault

Adds the assisted-apply copilot tables:
- application_sessions: secure, short-lived, user-owned apply sessions
- application_audit_logs: append-only per-session action trail
- application_answers: verified reusable answer vault

Revision ID: 0007_application_sessions
Revises: 0006_job_company_logo
Create Date: 2026-07-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_application_sessions"
down_revision: str | None = "0006_job_company_logo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")

SESSION_STATUS = (
    "preparing", "ready", "opened", "filling", "review_required",
    "ready_for_review", "completed", "failed", "expired", "cancelled",
)


def upgrade() -> None:
    session_status = sa.Enum(*SESSION_STATUS, name="applicationsessionstatus")

    op.create_table(
        "application_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", session_status, nullable=False, server_default="preparing"),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("ats_type", sa.String(length=40), nullable=True),
        sa.Column("profile_snapshot", json_type, nullable=True),
        sa.Column("job_snapshot", json_type, nullable=True),
        sa.Column("tailored_resume_id", sa.Integer(), sa.ForeignKey("generated_documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tailored_cover_letter_id", sa.Integer(), sa.ForeignKey("generated_documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("generated_answers", json_type, nullable=True),
        sa.Column("unresolved_questions", json_type, nullable=True),
        sa.Column("warnings", json_type, nullable=True),
        sa.Column("launch_token_hash", sa.String(length=64), nullable=True),
        sa.Column("launch_token_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tracker_id", sa.Integer(), sa.ForeignKey("application_tracker.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_application_sessions_user_id", "application_sessions", ["user_id"])
    op.create_index("ix_application_sessions_job_id", "application_sessions", ["job_id"])
    op.create_index("ix_application_sessions_status", "application_sessions", ["status"])
    op.create_index("ix_application_sessions_launch_token_hash", "application_sessions", ["launch_token_hash"])
    op.create_index("ix_application_sessions_expires_at", "application_sessions", ["expires_at"])

    op.create_table(
        "application_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("application_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("field_key", sa.String(length=120), nullable=True),
        sa.Column("source", sa.String(length=60), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("metadata_json", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_application_audit_logs_session_id", "application_audit_logs", ["session_id"])
    op.create_index("ix_application_audit_logs_action_type", "application_audit_logs", ["action_type"])

    op.create_table(
        "application_answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_key", sa.String(length=80), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("display_value", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="user"),
        sa.Column("is_user_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verification_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allow_auto_fill", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "canonical_key", name="uq_answer_user_key"),
    )
    op.create_index("ix_application_answers_user_id", "application_answers", ["user_id"])
    op.create_index("ix_application_answers_canonical_key", "application_answers", ["canonical_key"])


def downgrade() -> None:
    op.drop_table("application_answers")
    op.drop_table("application_audit_logs")
    op.drop_table("application_sessions")
    sa.Enum(name="applicationsessionstatus").drop(op.get_bind(), checkfirst=True)
