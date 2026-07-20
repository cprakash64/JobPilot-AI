"""Structured identity: given_name/family_name/preferred_name + name_confirmed.

Background: first_name/last_name answers were derived by naively splitting
``full_name`` on whitespace (first token = first name, rest = last name),
which mis-splits any multi-token given name ("Chandra Prakash Pandey" became
first="Chandra" last="Prakash Pandey"). These columns let a user's real
given/family name be stored and confirmed once, then reused verbatim —
never re-derived by guessing.

``name_confirmed`` defaults to false for all existing rows: nothing is
back-filled by guessing at migration time either. Until confirmed, first/last
name are surfaced as an unresolved question (with a naive split only as a
*suggestion*) instead of being auto-filled.

Revision ID: 0010_structured_name
Revises: 0009_scoring_pipeline
Create Date: 2026-07-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_structured_name"
down_revision: str | None = "0009_scoring_pipeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("given_name", sa.String(length=120), nullable=True))
    op.add_column("user_profiles", sa.Column("family_name", sa.String(length=120), nullable=True))
    op.add_column("user_profiles", sa.Column("preferred_name", sa.String(length=120), nullable=True))
    op.add_column(
        "user_profiles",
        sa.Column("name_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "name_confirmed")
    op.drop_column("user_profiles", "preferred_name")
    op.drop_column("user_profiles", "family_name")
    op.drop_column("user_profiles", "given_name")
