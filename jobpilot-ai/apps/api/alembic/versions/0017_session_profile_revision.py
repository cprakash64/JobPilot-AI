"""Track which profile revision a prepared application session was built from.

The live failure: a user fixed their name and set an application email, but the
already-prepared session kept serving the answer snapshot captured before those
edits, so the employer form still received nothing — and nothing anywhere could
tell that the snapshot had gone stale.

``profile_revision`` records the revision the session's answers were built from
(see app/profile/revision.py). Comparing it with the profile's current revision
is what makes staleness detectable, and therefore fixable.

Existing rows get NULL, which compares unequal to any real revision — so every
session prepared before this migration is treated as stale and rebuilt once on
next open. That is the safe direction: rebuilding is cheap, serving a stale
answer set is the bug.

Revision ID: 0017_session_profile_revision
Revises: 0016_application_email
Create Date: 2026-07-19 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_session_profile_revision"
down_revision: str | None = "0016_application_email"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "application_sessions",
        sa.Column("profile_revision", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "application_sessions",
        sa.Column("answers_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("application_sessions", "answers_refreshed_at")
    op.drop_column("application_sessions", "profile_revision")
