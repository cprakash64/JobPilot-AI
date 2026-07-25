"""Persisted, normalized company branding (logo pipeline).

Background: company_domain/company_logo_url were resolved and stored
per-job-posting-row, so a company with 50 postings could redundantly resolve
(and, once website-metadata discovery is involved, re-fetch) the same logo 50
times. This adds a company_branding table keyed by normalized company name so
resolution happens once and every job from that employer reuses it.

Revision ID: 0012_company_branding
Revises: 0011_answer_vault_scope
Create Date: 2026-07-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_company_branding"
down_revision: str | None = "0011_answer_vault_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_branding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("normalized_key", sa.String(length=255), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("logo_url", sa.String(length=1000), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="none"),
        sa.Column("resolution_status", sa.String(length=20), nullable=False, server_default="unresolved"),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_company_branding_normalized_key", "company_branding", ["normalized_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_company_branding_normalized_key", table_name="company_branding")
    op.drop_table("company_branding")
