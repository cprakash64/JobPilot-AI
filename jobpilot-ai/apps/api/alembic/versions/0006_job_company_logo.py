"""job posting company_domain + company_logo_url

Adds resolved company branding to job_postings so cards can render real company
logos (with an initial-letter fallback). Populated on ingestion and via
`python -m app.jobs.backfill_company_logos` for pre-existing rows.

Revision ID: 0006_job_company_logo
Revises: 0005_document_plain_text_quality
Create Date: 2026-07-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_job_company_logo"
down_revision: str | None = "0005_document_plain_text_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_postings", sa.Column("company_domain", sa.String(length=255), nullable=True))
    op.add_column("job_postings", sa.Column("company_logo_url", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column("job_postings", "company_logo_url")
    op.drop_column("job_postings", "company_domain")
