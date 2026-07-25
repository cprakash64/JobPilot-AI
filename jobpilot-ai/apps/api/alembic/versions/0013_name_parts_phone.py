"""Full structured identity: first/middle/last + preferred first/last, and
structured phone (country code, ISO2, national number, E.164).

Two changes, both driven by the same live autofill failure:

1. Names. 0010 added given_name/family_name, which is still only a two-way
   split — it cannot represent "Chandra Prakash Pandey" (first=Chandra,
   middle=Prakash, last=Pandey), and it collapsed preferred first/last into one
   free-text ``preferred_name``. The columns are renamed to first_name /
   last_name (matching the canonical answer keys the vault already uses) and
   joined by middle_name, preferred_first_name, preferred_last_name.

   Names are NOT back-filled here. Splitting ``full_name`` is a guess, and
   guessing is what produced last_name="PRAKASH PANDEY" in the first place.
   ``name_confirmed`` stays as-is, so a profile that had already confirmed a
   given/family split keeps it, and everyone else is asked in the review UI.
   ``full_name`` is retained as the backward-compatible display column.

2. Phone. ``phone`` stored free text ("602-816-1309"), which is not enough to
   drive a form that asks for country and national number separately. The
   structured columns ARE back-filled, because normalizing a phone number with
   libphonenumber is a derivation rather than a guess — and the raw ``phone``
   column is left untouched so nothing is lost if a parse was wrong.

Revision ID: 0013_name_parts_phone
Revises: 0012_company_branding
Create Date: 2026-07-18 00:00:00.000000

NOTE: revision ids must fit alembic_version.version_num (varchar(32)).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_name_parts_phone"
down_revision: str | None = "0012_company_branding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- names ------------------------------------------------------------ #
    with op.batch_alter_table("user_profiles") as batch:
        batch.alter_column(
            "given_name", new_column_name="first_name", existing_type=sa.String(length=120)
        )
        batch.alter_column(
            "family_name", new_column_name="last_name", existing_type=sa.String(length=120)
        )

    op.add_column(
        "user_profiles", sa.Column("middle_name", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "user_profiles", sa.Column("preferred_first_name", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "user_profiles", sa.Column("preferred_last_name", sa.String(length=120), nullable=True)
    )

    # --- phone ------------------------------------------------------------ #
    op.add_column(
        "user_profiles", sa.Column("phone_country_code", sa.String(length=8), nullable=True)
    )
    op.add_column(
        "user_profiles", sa.Column("phone_country_iso2", sa.String(length=2), nullable=True)
    )
    op.add_column(
        "user_profiles", sa.Column("phone_national_number", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "user_profiles", sa.Column("phone_e164", sa.String(length=32), nullable=True)
    )

    _withdraw_machine_split_confirmations()
    _backfill_phone()


def _withdraw_machine_split_confirmations() -> None:
    """Un-confirm names that were "confirmed" by the old naive splitter.

    Release 0010 marked ``name_confirmed`` True for splits the user never
    reviewed, so profiles carry first="CHANDRA", last="PRAKASH PANDEY" as if
    the user had approved it. Those confirmations are withdrawn — NOT
    rewritten to a better guess, because a better guess is still a guess. The
    parts are cleared so the review UI asks once, pre-filled with the improved
    suggestion (first/middle/last), and the user's answer is what persists.

    Two-token names and genuinely user-confirmed splits are untouched.
    """
    from app.profile.names import looks_machine_split

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, full_name, first_name, last_name FROM user_profiles "
            "WHERE name_confirmed = true"
        )
    ).fetchall()

    for row in rows:
        if not looks_machine_split(row.full_name or "", row.first_name or "", row.last_name or ""):
            continue
        connection.execute(
            sa.text(
                "UPDATE user_profiles SET name_confirmed = false, "
                "first_name = NULL, last_name = NULL WHERE id = :id"
            ),
            {"id": row.id},
        )


def _backfill_phone() -> None:
    """Normalize existing free-text phone numbers into the structured columns.

    Rows whose phone cannot be parsed into a *valid* number are skipped and keep
    only their raw ``phone`` value — the profile UI then surfaces them for the
    user to fix, which is far better than storing a confidently wrong E.164.
    """
    from app.profile.phone import parse_phone

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, phone, location_country FROM user_profiles "
            "WHERE phone IS NOT NULL AND phone <> ''"
        )
    ).fetchall()

    for row in rows:
        parts = parse_phone(row.phone, _region_hint(row.location_country))
        if not parts.valid:
            continue
        connection.execute(
            sa.text(
                "UPDATE user_profiles SET "
                "phone_country_code = :cc, phone_country_iso2 = :iso, "
                "phone_national_number = :nat, phone_e164 = :e164 "
                "WHERE id = :id"
            ),
            {
                "cc": parts.country_code,
                "iso": parts.country_iso2,
                "nat": parts.national_number,
                "e164": parts.e164,
                "id": row.id,
            },
        )


# Only the unambiguous cases — anything else falls back to US, and an
# unparseable number is simply left for the user rather than mis-homed.
_COUNTRY_TO_REGION = {
    "united states": "US", "usa": "US", "us": "US",
    "canada": "CA", "united kingdom": "GB", "uk": "GB",
    "india": "IN", "australia": "AU", "germany": "DE",
}


def _region_hint(location_country: str | None) -> str:
    return _COUNTRY_TO_REGION.get((location_country or "").strip().lower(), "US")


def downgrade() -> None:
    op.drop_column("user_profiles", "phone_e164")
    op.drop_column("user_profiles", "phone_national_number")
    op.drop_column("user_profiles", "phone_country_iso2")
    op.drop_column("user_profiles", "phone_country_code")
    op.drop_column("user_profiles", "preferred_last_name")
    op.drop_column("user_profiles", "preferred_first_name")
    op.drop_column("user_profiles", "middle_name")

    with op.batch_alter_table("user_profiles") as batch:
        batch.alter_column(
            "first_name", new_column_name="given_name", existing_type=sa.String(length=120)
        )
        batch.alter_column(
            "last_name", new_column_name="family_name", existing_type=sa.String(length=120)
        )
