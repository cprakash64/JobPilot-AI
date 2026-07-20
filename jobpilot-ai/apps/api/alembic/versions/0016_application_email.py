"""Separate the application contact email from the login identity.

Applications were filled with the ACCOUNT email. That conflates two different
things: the address you sign in with, and the address an employer should contact
you on. It also meant a demo/fixture login (demo@example.com) blocked autofill
entirely, because fixture_guard correctly refuses to put a fixture address on a
real application.

``application_email`` is the address used on applications. It is confirmed
explicitly (never inferred), and a confirmed value always takes precedence over
the account email.

Forward-only and purely additive; 0001-0015 are untouched.

Revision ID: 0016_application_email
Revises: 0015_eeo_typed_options
Create Date: 2026-07-19 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_application_email"
down_revision: str | None = "0015_eeo_typed_options"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_profiles", sa.Column("application_email", sa.String(length=320), nullable=True)
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "application_email_confirmed", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "user_profiles",
        sa.Column("application_email_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Seed from the account email ONLY where it is a real address. A fixture
    # login is never promoted into an application email, and nothing is marked
    # confirmed — confirmation requires an explicit save by the user.
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE user_profiles AS p
               SET application_email = u.email
              FROM users AS u
             WHERE u.id = p.user_id
               AND p.application_email IS NULL
               AND u.email IS NOT NULL
               AND lower(u.email) NOT IN (
                   'demo@example.com', 'test@example.com', 'user@example.com'
               )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "application_email_updated_at")
    op.drop_column("user_profiles", "application_email_confirmed")
    op.drop_column("user_profiles", "application_email")
