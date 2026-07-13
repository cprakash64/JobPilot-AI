"""Add ``applying`` to the ``applicationstatus`` Postgres enum.

Root cause of the assisted-application preparation failure: the SQLAlchemy model
``ApplicationStatus`` gained an ``applying`` member (used to mark a tracker while
an assisted auto-apply session is in progress), but the Postgres enum type
``applicationstatus`` was created in ``0001_initial`` with only the original six
labels. Linking the tracker during session creation therefore tried to insert
``'applying'`` and Postgres rejected it with
``invalid input value for enum applicationstatus: "applying"`` — a ``DataError``
that surfaced to the user as a failed preparation.

The unit tests run on SQLite, where SQLAlchemy renders ``Enum`` as a VARCHAR with
a CHECK constraint derived from the *Python* enum, so ``applying`` was always
accepted there and the gap was invisible to CI.

``ALTER TYPE ... ADD VALUE`` is supported inside a transaction on PostgreSQL 12+
as long as the new label is not *used* in the same transaction (it is not here).
``IF NOT EXISTS`` makes the migration idempotent and safe to re-run.
"""

from __future__ import annotations

from alembic import op

revision: str = "0008_application_status_applying"
down_revision: str | None = "0007_application_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # No-op on non-PostgreSQL backends (e.g. SQLite in tests), which do not use a
    # native enum type for ``ApplicationStatus``.
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TYPE applicationstatus ADD VALUE IF NOT EXISTS 'applying'")


def downgrade() -> None:
    # PostgreSQL cannot drop a single value from an enum type without recreating
    # the type and rewriting every dependent column. Removing ``applying`` would
    # also fail if any row still references it. Downgrade is intentionally a
    # no-op; the extra label is harmless if unused.
    pass
