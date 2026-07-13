"""generated document plain_text + quality

Adds plain_text (markdown-free copy/export text) and quality (ATS/tailoring
metadata) to generated_documents.

Revision ID: 0005_document_plain_text_quality
Revises: 0004_generated_documents
Create Date: 2026-07-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_document_plain_text_quality"
down_revision: str | None = "0004_generated_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("generated_documents", sa.Column("plain_text", sa.Text(), nullable=True))
    op.add_column("generated_documents", sa.Column("quality", json_type, nullable=True))


def downgrade() -> None:
    op.drop_column("generated_documents", "quality")
    op.drop_column("generated_documents", "plain_text")
