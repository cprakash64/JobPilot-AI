"""Hold complete externally sourced job text instead of guessing a maximum.

A Flexport posting whose location listed every office city exceeded
``VARCHAR(255)`` and made ``POST /jobs/discover`` fail the whole batch with
``StringDataRightTruncation``. Truncating would have silently corrupted the
source data, so the columns that hold externally supplied prose and URLs become
``TEXT`` and keep the complete value. A separate short ``location_display`` is
derived for cards.

Identifiers, controlled enums, and domains stay bounded on purpose.

Revision ID: 0025_job_posting_unbounded_text
Revises: 0024_people_persistence_usage
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0025_job_posting_unbounded_text"
down_revision = "0024_people_persistence_usage"
branch_labels = None
depends_on = None

# (column, previous length). Every one carries values JobPilot does not author.
_WIDENED: tuple[tuple[str, int], ...] = (
    ("title", 255),
    ("company", 255),
    ("location", 255),
    ("company_logo_url", 1000),
    ("application_url", 1000),
    ("source_url", 1000),
    ("degree_requirement", 200),
)

_NOT_NULL = frozenset({"title", "company", "application_url", "source_url"})


def upgrade() -> None:
    for column, length in _WIDENED:
        op.alter_column(
            "job_postings",
            column,
            existing_type=sa.String(length=length),
            type_=sa.Text(),
            existing_nullable=column not in _NOT_NULL,
        )
    op.add_column(
        "job_postings",
        sa.Column("location_display", sa.String(length=120), nullable=True),
    )
    # Backfill the display label from data that already fits, so existing rows
    # render identically until their next ingestion refresh.
    op.execute(
        """
        UPDATE job_postings
        SET location_display = location
        WHERE location IS NOT NULL AND char_length(location) <= 120
        """
    )


def downgrade() -> None:
    op.drop_column("job_postings", "location_display")
    for column, length in _WIDENED:
        # Downgrading narrows the column again. Any row longer than the old
        # limit is truncated here rather than failing the migration; that is
        # the unavoidable cost of going back to a bounded type, and it is why
        # the upgrade direction is the supported one.
        op.execute(
            f"UPDATE job_postings "
            f"SET {column} = LEFT({column}, {length}) "
            f"WHERE {column} IS NOT NULL AND char_length({column}) > {length}"
        )
        op.alter_column(
            "job_postings",
            column,
            existing_type=sa.Text(),
            type_=sa.String(length=length),
            existing_nullable=column not in _NOT_NULL,
        )
