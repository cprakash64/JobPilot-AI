"""generated document tailoring fields

Adds resume/cover-letter tailoring fields to generated_documents: a title,
markdown body, profile/job snapshots (for auditability and guardrails), format
version, docx/pdf paths, and updated_at.

Revision ID: 0004_generated_documents
Revises: 0003_remove_demo_jobs
Create Date: 2026-07-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_generated_documents"
down_revision: str | None = "0003_remove_demo_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("generated_documents", sa.Column("title", sa.String(length=300), nullable=True))
    op.add_column("generated_documents", sa.Column("content_markdown", sa.Text(), nullable=True))
    op.add_column("generated_documents", sa.Column("source_profile_snapshot", json_type, nullable=True))
    op.add_column("generated_documents", sa.Column("job_snapshot", json_type, nullable=True))
    op.add_column("generated_documents", sa.Column("format_version", sa.String(length=20), server_default="v1"))
    op.add_column("generated_documents", sa.Column("docx_file_path", sa.String(length=1000), nullable=True))
    op.add_column("generated_documents", sa.Column("pdf_file_path", sa.String(length=1000), nullable=True))
    op.add_column(
        "generated_documents",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    for column in [
        "updated_at", "pdf_file_path", "docx_file_path", "format_version",
        "job_snapshot", "source_profile_snapshot", "content_markdown", "title",
    ]:
        op.drop_column("generated_documents", column)
