"""Typed, per-question EEO options; quarantine impossible legacy values.

The old UI offered ONE option list — "Prefer not to answer / Yes / No / Another
option" — for five questions with completely different meanings. So a gender
answer could be stored as "yes", and race/ethnicity as "another_option": strings
that carry no demographic meaning at all.

This migration:

1. Adds the correctly-typed columns (a gender IDENTITY field distinct from sex,
   a multi-select race/ethnicity list, and optional self-description text).
2. Quarantines the meaningless legacy values rather than reinterpreting them.
   "yes" is not a gender and must never become one. Where the user never
   consented to storage, the values are deleted outright; where consent exists,
   they are nulled and flagged so the UI asks the user again.

Nothing is inferred, and no legacy value is mapped onto a new canonical value —
the only legacy strings that survive are ones that already matched a valid
canonical answer.

Revision ID: 0015_eeo_typed_options
Revises: 0014_profile_postal_code
Create Date: 2026-07-19 00:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_eeo_typed_options"
down_revision: str | None = "0014_profile_postal_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.add_column(
        "sensitive_demographics",
        sa.Column("gender_identity", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "sensitive_demographics",
        sa.Column("gender_self_description", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "sensitive_demographics",
        sa.Column("hispanic_or_latino", sa.String(length=64), nullable=True),
    )
    # Multi-select: a person may identify with more than one category, and
    # collapsing that to a single string is what produced "another_option".
    op.add_column(
        "sensitive_demographics",
        sa.Column("race_ethnicity", sa.JSON(), nullable=True),
    )
    op.add_column(
        "sensitive_demographics",
        sa.Column("race_self_description", sa.String(length=200), nullable=True),
    )
    # Set when a legacy value could not be carried across, so the UI can ask the
    # user to re-answer instead of showing them a silently emptied form.
    op.add_column(
        "sensitive_demographics",
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    _migrate_legacy_values()


def _migrate_legacy_values() -> None:
    """Apply the quarantine rule to every pre-0015 row.

    The decision itself lives in app.profile.eeo.classify_legacy_row so it can
    be unit-tested; this function only performs the resulting SQL.
    """
    from app.profile.eeo import classify_legacy_row

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, gender, veteran_status, disability_status, "
            "ethnicity, hispanic_latino_status, consent_to_store "
            "FROM sensitive_demographics"
        )
    ).fetchall()

    for row in rows:
        decision = classify_legacy_row(row._mapping, consented=bool(row.consent_to_store))

        if decision["action"] == "delete":
            connection.execute(
                sa.text("DELETE FROM sensitive_demographics WHERE id = :id"), {"id": row.id}
            )
            continue

        race = decision["race_ethnicity"]
        connection.execute(
            sa.text(
                "UPDATE sensitive_demographics SET "
                "gender_identity = :gender, hispanic_or_latino = :hispanic, "
                "race_ethnicity = CAST(:race AS JSON), veteran_status = :veteran, "
                "disability_status = :disability, gender = NULL, ethnicity = NULL, "
                "hispanic_latino_status = NULL, needs_review = :needs_review "
                "WHERE id = :id"
            ),
            {
                "gender": decision["gender_identity"],
                "hispanic": decision["hispanic_or_latino"],
                "race": None if race is None else json.dumps(race),
                "veteran": decision["veteran_status"],
                "disability": decision["disability_status"],
                "needs_review": decision["needs_review"],
                "id": row.id,
            },
        )


def downgrade() -> None:
    op.drop_column("sensitive_demographics", "needs_review")
    op.drop_column("sensitive_demographics", "race_self_description")
    op.drop_column("sensitive_demographics", "race_ethnicity")
    op.drop_column("sensitive_demographics", "hispanic_or_latino")
    op.drop_column("sensitive_demographics", "gender_self_description")
    op.drop_column("sensitive_demographics", "gender_identity")
