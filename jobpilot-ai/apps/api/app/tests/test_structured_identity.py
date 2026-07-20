"""Sections A + B — structured name parts and structured phone.

Reproduces the live Samsara/Greenhouse failure at the data layer: the profile
held only "CHANDRA PRAKASH PANDEY" and "602-816-1309", and the autofill produced
first="CHANDRA", last="PRAKASH PANDEY" plus a phone the site rejected.

The rule these tests protect: a multi-token name is never SPLIT automatically,
only PROPOSED; a phone number is always normalized rather than concatenated.
"""

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.applications.answer_vault_service import build_safe_answers, confirm_name
from app.db.base import Base
from app.models import entities as E
from app.profile.names import (
    compose_full_name,
    looks_machine_split,
    normalize_display_case,
    resolve_preferred_names,
    suggest_name_parts,
)
from app.profile.phone import parse_phone, parts_from_stored


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


# --------------------------------------------------------------------------- #
# I.1 / I.2 — name parsing proposes, never decides
# --------------------------------------------------------------------------- #
def test_three_token_name_is_proposed_as_first_middle_last() -> None:
    s = suggest_name_parts("Chandra Prakash Pandey")
    assert (s.first_name, s.middle_name, s.last_name) == ("Chandra", "Prakash", "Pandey")


def test_a_three_token_split_is_never_certain() -> None:
    """The exact case that caused the bug: plausible, but still a guess."""
    assert suggest_name_parts("Chandra Prakash Pandey").certain is False
    # A two-token name genuinely is unambiguous.
    assert suggest_name_parts("Priya Sharma").certain is True


def test_all_caps_resume_extraction_is_proposed_in_normal_case() -> None:
    s = suggest_name_parts("CHANDRA PRAKASH PANDEY")
    assert (s.first_name, s.middle_name, s.last_name) == ("Chandra", "Prakash", "Pandey")


def test_normalize_display_case_leaves_mixed_case_names_alone() -> None:
    # Anything the user typed themselves is untouched.
    assert normalize_display_case("Ronald McDonald") == "Ronald McDonald"
    assert normalize_display_case("Vincent van Gogh") == "Vincent van Gogh"
    assert normalize_display_case("O'BRIEN") == "O'Brien"


def test_surname_particles_stay_with_the_family_name() -> None:
    s = suggest_name_parts("Juan de la Cruz")
    assert s.first_name == "Juan"
    assert s.last_name == "de la Cruz"
    assert s.middle_name == ""


def test_comma_form_is_explicit_and_therefore_certain() -> None:
    s = suggest_name_parts("Pandey, Chandra Prakash")
    assert (s.first_name, s.middle_name, s.last_name) == ("Chandra", "Prakash", "Pandey")
    assert s.certain is True


def test_legacy_full_name_never_auto_populates_last_name(db: Session) -> None:
    """I.2 — the core regression. A legacy profile with only full_name must ASK,
    and must never emit "Prakash Pandey" as a last name."""
    user = E.User(email="legacy@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    db.add(E.UserProfile(user_id=user.id, full_name="CHANDRA PRAKASH PANDEY"))
    db.flush()

    answers, unresolved = build_safe_answers(db, user, company="Samsara")
    by_key = {a["canonical_key"]: a["value"] for a in answers}

    assert "last_name" not in by_key
    assert "first_name" not in by_key
    assert "Prakash Pandey" not in by_key.values()

    asked = {u["canonical_key"]: u for u in unresolved if u.get("action") == "confirm_name"}
    assert set(asked) == {"first_name", "middle_name", "last_name"}
    # Pre-filled with the corrected proposal, flagged as needing confirmation.
    assert asked["first_name"]["suggested_value"] == "Chandra"
    assert asked["middle_name"]["suggested_value"] == "Prakash"
    assert asked["last_name"]["suggested_value"] == "Pandey"
    assert asked["last_name"]["suggestion_certain"] is False


def test_looks_machine_split_identifies_the_old_naive_confirmation() -> None:
    """The migration uses this to withdraw confirmations the user never made."""
    assert looks_machine_split("CHANDRA PRAKASH PANDEY", "CHANDRA", "PRAKASH PANDEY") is True
    # A real user correction is left alone.
    assert looks_machine_split("Chandra Prakash Pandey", "Chandra", "Pandey") is False
    # An ordinary two-token name could not have come from the naive rule.
    assert looks_machine_split("Priya Sharma", "Priya", "Sharma") is False


# --------------------------------------------------------------------------- #
# I.1 / I.3 / I.4 — confirmed names fill the right fields
# --------------------------------------------------------------------------- #
def _confirmed_user(db: Session) -> E.User:
    user = E.User(email="chandra@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    db.add(E.UserProfile(user_id=user.id, full_name="CHANDRA PRAKASH PANDEY"))
    db.flush()
    confirm_name(db, user.id, "Chandra", "Pandey", middle_name="Prakash")
    db.flush()
    return user


def test_confirmed_name_maps_each_part_to_its_own_field(db: Session) -> None:
    user = _confirmed_user(db)
    by_key = {a["canonical_key"]: a["value"] for a in build_safe_answers(db, user)[0]}

    assert by_key["first_name"] == "Chandra"
    assert by_key["middle_name"] == "Prakash"
    assert by_key["last_name"] == "Pandey"
    # I.3 — only a TRUE full-name field gets the whole thing.
    assert by_key["full_name"] == "Chandra Prakash Pandey"


def test_preferred_names_fall_back_to_the_legal_name(db: Session) -> None:
    """I.4 — Greenhouse's preferred-name fields say to use the legal name when
    no preferred name exists."""
    user = _confirmed_user(db)
    by_key = {a["canonical_key"]: a["value"] for a in build_safe_answers(db, user)[0]}
    assert by_key["preferred_first_name"] == "Chandra"
    assert by_key["preferred_last_name"] == "Pandey"


def test_an_explicit_preferred_name_wins_over_the_legal_name() -> None:
    first, last = resolve_preferred_names(
        preferred_first_name="Chan",
        preferred_last_name=None,
        first_name="Chandra",
        last_name="Pandey",
    )
    assert (first, last) == ("Chan", "Pandey")


def test_preferred_names_are_not_assumed_when_the_form_does_not_say_so() -> None:
    first, last = resolve_preferred_names(
        preferred_first_name=None,
        preferred_last_name=None,
        first_name="Chandra",
        last_name="Pandey",
        legal_fallback_allowed=False,
    )
    assert (first, last) == ("", "")


def test_confirming_a_name_recomputes_the_display_full_name(db: Session) -> None:
    user = _confirmed_user(db)
    profile = db.query(E.UserProfile).filter_by(user_id=user.id).one()
    assert profile.full_name == "Chandra Prakash Pandey"
    assert profile.name_confirmed is True


def test_compose_full_name_skips_absent_parts() -> None:
    assert compose_full_name("Priya", "", "Sharma") == "Priya Sharma"
    assert compose_full_name("Chandra", "Prakash", "Pandey") == "Chandra Prakash Pandey"


# --------------------------------------------------------------------------- #
# I.5 — phone normalization
# --------------------------------------------------------------------------- #
def test_us_number_is_normalized_to_e164() -> None:
    p = parse_phone("602-816-1309")
    assert p.e164 == "+16028161309"
    assert p.country_code == "+1"
    assert p.country_iso2 == "US"
    assert p.national_number == "6028161309"
    assert p.valid is True


@pytest.mark.parametrize(
    "raw",
    [
        "602-816-1309",
        "(602) 816-1309",
        "6028161309",
        "+1 602 816 1309",
        "+1 (602) 816-1309",
        " 602.816.1309 ",
    ],
)
def test_every_written_form_of_the_same_number_normalizes_identically(raw: str) -> None:
    assert parse_phone(raw).e164 == "+16028161309"


def test_an_explicit_country_prefix_is_not_re_homed_to_the_default_region() -> None:
    p = parse_phone("+44 20 7946 0958", default_region="US")
    assert p.country_code == "+44"
    assert p.country_iso2 == "GB"


def test_an_unparseable_number_is_reported_invalid_rather_than_guessed() -> None:
    p = parse_phone("not a phone")
    assert p.valid is False
    assert p.e164 == ""


def test_national_format_is_available_for_masked_inputs() -> None:
    assert parse_phone("6028161309").national_formatted() == "(602) 816-1309"


def test_legacy_profiles_are_re_derived_from_the_raw_phone_column() -> None:
    """Backward compatibility: structured columns empty, raw ``phone`` present."""
    p = parts_from_stored(
        country_code=None,
        country_iso2=None,
        national_number=None,
        e164=None,
        legacy_phone="602-816-1309",
    )
    assert p.e164 == "+16028161309"
    assert p.national_number == "6028161309"


def test_import_applies_the_split_the_user_confirmed_in_the_review_ui(db: Session) -> None:
    """The resume parser only produces a free-text name; the split comes back
    from the review UI as the user's own answer, so applying it may confirm."""
    from app.routes.profile import _apply_imported_name
    from app.schemas.import_profile import BasicInfoDraft

    profile = E.UserProfile(user_id=1, full_name="CHANDRA PRAKASH PANDEY")
    basic = BasicInfoDraft(first_name="Chandra", middle_name="Prakash", last_name="Pandey")

    _apply_imported_name(profile, basic, overwrite=False)

    assert profile.first_name == "Chandra"
    assert profile.middle_name == "Prakash"
    assert profile.last_name == "Pandey"
    assert profile.full_name == "Chandra Prakash Pandey"
    assert profile.name_confirmed is True


def test_import_without_confirmed_parts_never_guesses_a_split(db: Session) -> None:
    from app.routes.profile import _apply_imported_name
    from app.schemas.import_profile import BasicInfoDraft

    profile = E.UserProfile(user_id=1, full_name="Chandra Prakash Pandey")
    # An older client that sends only full_name.
    _apply_imported_name(profile, BasicInfoDraft(), overwrite=False)

    assert profile.first_name is None
    assert profile.last_name is None
    # Left untouched (the column default applies on flush, so it is not yet False).
    assert not profile.name_confirmed


def test_import_does_not_overwrite_an_already_confirmed_name(db: Session) -> None:
    from app.routes.profile import _apply_imported_name
    from app.schemas.import_profile import BasicInfoDraft

    profile = E.UserProfile(
        user_id=1,
        full_name="Chandra Prakash Pandey",
        first_name="Chandra",
        middle_name="Prakash",
        last_name="Pandey",
        name_confirmed=True,
    )
    _apply_imported_name(
        profile, BasicInfoDraft(first_name="C", last_name="Pandey"), overwrite=False
    )
    assert profile.first_name == "Chandra"


def test_phone_is_published_in_every_shape_a_form_might_ask_for(db: Session) -> None:
    """I.6 / I.7 — the extension SELECTS a shape; it never concatenates, which is
    what would duplicate the "+1"."""
    user = E.User(email="phone@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    db.add(
        E.UserProfile(
            user_id=user.id,
            full_name="Priya Sharma",
            phone="602-816-1309",
            phone_country_code="+1",
            phone_country_iso2="US",
            phone_national_number="6028161309",
            phone_e164="+16028161309",
        )
    )
    db.flush()

    by_key = {a["canonical_key"]: a["value"] for a in build_safe_answers(db, user)[0]}
    assert by_key["phone"] == "+16028161309"
    assert by_key["phone_country"] == "+1"
    assert by_key["phone_country_iso2"] == "US"
    assert by_key["phone_national"] == "6028161309"
    # The national number carries no country prefix — nothing to duplicate.
    assert not by_key["phone_national"].startswith("+")
