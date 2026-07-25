"""Answer vault scoping: global vs company vs application-specific answers.

Background: a saved answer (e.g. "previously employed here?") must not be
silently reused for a *different* employer, but reusable facts like pronouns
or work authorization should apply everywhere. Adds ``scope`` and
``company_key`` to ``application_answers`` so ``build_safe_answers`` can filter
company-scoped rows to the session's own employer. ``company_key`` defaults to
``""`` (not NULL) for every non-company scope so the replacement unique
constraint reliably prevents duplicate global rows on both SQLite and
Postgres (NULL is not equal to NULL in a unique index).

Existing rows predate scoping and are global answers by definition, so they
backfill to ``scope='global', company_key=''`` — already true via the column
defaults, but set explicitly for clarity and to satisfy the new NOT NULL
constraint on any driver that doesn't apply server_default to existing rows.

Revision ID: 0011_answer_vault_scope
Revises: 0010_structured_name
Create Date: 2026-07-15 00:00:00.000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_answer_vault_scope"
down_revision: str | None = "0010_structured_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "application_answers",
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="global"),
    )
    op.add_column(
        "application_answers",
        sa.Column("company_key", sa.String(length=160), nullable=False, server_default=""),
    )
    op.execute("UPDATE application_answers SET scope = 'global', company_key = '' WHERE scope IS NULL")

    op.drop_constraint("uq_answer_user_key", "application_answers", type_="unique")
    op.create_unique_constraint(
        "uq_answer_user_key_company", "application_answers", ["user_id", "canonical_key", "company_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_answer_user_key_company", "application_answers", type_="unique")
    op.create_unique_constraint("uq_answer_user_key", "application_answers", ["user_id", "canonical_key"])
    op.drop_column("application_answers", "company_key")
    op.drop_column("application_answers", "scope")
