"""Section A/D — the application email is separate from the login identity.

The live failure: the account email was demo@example.com, a known dev fixture.
fixture_guard correctly refused to put it on a real application, so the answer
set carried NO email at all and Airbnb's Email field stayed blank — with nothing
in the UI explaining why.

An application email is now stored, confirmed and resolved independently.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.applications.answer_vault_service import build_safe_answers
from app.db.base import Base
from app.models import entities as E
from app.profile.emails import resolve_application_email


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
# Precedence
# --------------------------------------------------------------------------- #
def test_confirmed_application_email_wins_over_the_account_email() -> None:
    resolved = resolve_application_email(
        application_email="apply@mailbox.test-domain.co",
        application_email_confirmed=True,
        account_email="login@example.com",
    )
    assert resolved.value == "apply@mailbox.test-domain.co"
    assert resolved.source == "application_email"
    assert resolved.confirmed is True


def test_a_fixture_login_never_reaches_an_application() -> None:
    resolved = resolve_application_email(
        application_email=None, application_email_confirmed=False,
        account_email="demo@example.com",
    )
    assert resolved.value == ""
    assert resolved.usable is False


def test_a_real_account_email_is_proposed_when_no_application_email_exists() -> None:
    resolved = resolve_application_email(
        application_email=None, application_email_confirmed=False,
        account_email="real@mailbox.test-domain.co",
    )
    assert resolved.value == "real@mailbox.test-domain.co"
    assert resolved.source == "account_email"
    # Proposed, not confirmed — the UI still asks for one explicit save.
    assert resolved.confirmed is False


def test_an_unconfirmed_application_email_still_beats_the_account_email() -> None:
    resolved = resolve_application_email(
        application_email="apply@mailbox.test-domain.co", application_email_confirmed=False,
        account_email="other@mailbox.test-domain.co",
    )
    assert resolved.source == "application_email"
    assert resolved.confirmed is False


def test_a_confirmed_application_email_is_never_overwritten_by_a_fixture_login() -> None:
    resolved = resolve_application_email(
        application_email="apply@mailbox.test-domain.co", application_email_confirmed=True,
        account_email="demo@example.com",
    )
    assert resolved.value == "apply@mailbox.test-domain.co"


# --------------------------------------------------------------------------- #
# End-to-end through the REAL answer builder
# --------------------------------------------------------------------------- #
def _profile(db: Session, **overrides) -> E.User:
    user = E.User(email=overrides.pop("account_email", "demo@example.com"), hashed_password="x")
    db.add(user)
    db.flush()
    values = dict(
        user_id=user.id,
        full_name="Chandra Prakash Pandey",
        first_name="Chandra", middle_name="Prakash", last_name="Pandey",
        name_confirmed=True,
        phone="602-816-1309", phone_country_code="+1", phone_country_iso2="US",
        phone_national_number="6028161309", phone_e164="+16028161309",
        location_city="Phoenix", location_state="AZ", location_country="United States",
        linkedin_url="https://linkedin.com/in/example",
        work_authorization="authorized_us", requires_sponsorship=False,
    )
    values.update(overrides)
    db.add(E.UserProfile(**values))
    db.flush()
    return user


def test_demo_login_with_confirmed_application_email_yields_an_email(db: Session) -> None:
    """A fixture LOGIN no longer blocks autofill once an application email exists."""
    user = _profile(
        db, application_email="chandra@mailbox.test-domain.co", application_email_confirmed=True
    )
    answers, unresolved = build_safe_answers(db, user)
    by_key = {a["canonical_key"]: a["value"] for a in answers}

    assert by_key["email"] == "chandra@mailbox.test-domain.co"
    # ...and the demo-email question is gone.
    assert not [u for u in unresolved if u["canonical_key"] == "email"]


def test_a_demo_login_with_no_application_email_still_asks(db: Session) -> None:
    user = _profile(db)
    answers, unresolved = build_safe_answers(db, user)
    by_key = {a["canonical_key"]: a["value"] for a in answers}

    assert "email" not in by_key
    assert [u["canonical_key"] for u in unresolved if u["canonical_key"] == "email"] == ["email"]


def test_a_complete_profile_produces_every_required_autofill_key(db: Session) -> None:
    """Section D — the assertion that would have caught the live failure."""
    user = _profile(
        db, application_email="chandra@mailbox.test-domain.co", application_email_confirmed=True
    )
    answers, _ = build_safe_answers(db, user)
    keys = {a["canonical_key"] for a in answers}

    for required in [
        "first_name", "last_name", "email",
        "phone_country_iso2", "phone_country", "phone_national",
        "city", "linkedin_url", "work_authorization_us", "sponsorship_required_future",
    ]:
        assert required in keys, f"missing required autofill key: {required}"


def test_preferred_first_name_is_never_the_middle_name(db: Session) -> None:
    """The reported corruption: Preferred First Name showed "Prakash"."""
    user = _profile(
        db, application_email="c@mailbox.test-domain.co", application_email_confirmed=True
    )
    by_key = {a["canonical_key"]: a["value"] for a in build_safe_answers(db, user)[0]}

    assert by_key["preferred_first_name"] == "Chandra"
    assert by_key["preferred_first_name"] != "Prakash"
    assert by_key["middle_name"] == "Prakash"
    assert by_key["last_name"] == "Pandey"


def test_an_explicit_preferred_first_name_is_respected(db: Session) -> None:
    user = _profile(
        db, application_email="c@mailbox.test-domain.co", application_email_confirmed=True,
        preferred_first_name="Chan",
    )
    by_key = {a["canonical_key"]: a["value"] for a in build_safe_answers(db, user)[0]}
    assert by_key["preferred_first_name"] == "Chan"


# --------------------------------------------------------------------------- #
# Section A — RFC-reserved domains can never be an application contact address
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "address",
    [
        "someone@example.com",
        "someone@example.net",
        "someone@example.org",
        "someone@example.edu",
        # Subdomains are just as undeliverable as the parent.
        "someone@mail.example.com",
        "someone@careers.jobs.example.org",
        # Case must not matter.
        "someone@EXAMPLE.COM",
        "  someone@Example.Net  ",
        "someone@host.invalid",
        "someone@localhost",
    ],
)
def test_reserved_domains_are_rejected(address: str) -> None:
    from app.profile.emails import is_reserved_email_domain

    assert is_reserved_email_domain(address) is True


@pytest.mark.parametrize(
    "address",
    [
        "someone@gmail.com",
        "someone@company.co.uk",
        # Merely CONTAINING "example" is not the same as being a reserved domain.
        "someone@notexample.com",
        "someone@myexample.org",
        "someone@example.company.com",
    ],
)
def test_real_domains_are_accepted(address: str) -> None:
    from app.profile.emails import is_reserved_email_domain

    assert is_reserved_email_domain(address) is False


def test_a_reserved_domain_is_never_resolved_even_when_confirmed() -> None:
    """A user can confirm an undeliverable address; that does not make it usable."""
    from app.profile.emails import resolve_application_email

    resolved = resolve_application_email(
        application_email="someone@example.com",
        application_email_confirmed=True,
        account_email="also@example.com",
    )
    assert resolved.usable is False
    assert resolved.value == ""


def test_saving_a_reserved_domain_is_rejected_with_an_actionable_message() -> None:
    from pydantic import ValidationError

    from app.profile.emails import RESERVED_EMAIL_MESSAGE
    from app.schemas.profile import UserProfileIn

    with pytest.raises(ValidationError) as exc:
        UserProfileIn(application_email="someone@example.com")
    assert RESERVED_EMAIL_MESSAGE in str(exc.value)
    assert "real email address" in str(exc.value)


def test_a_malformed_address_is_rejected() -> None:
    from pydantic import ValidationError

    from app.schemas.profile import UserProfileIn

    for bad in ["notanemail", "@nolocal.com", "nodomain@"]:
        with pytest.raises(ValidationError):
            UserProfileIn(application_email=bad)


def test_a_real_address_saves_normally() -> None:
    from app.schemas.profile import UserProfileIn

    parsed = UserProfileIn(application_email="  chandra@realmail.co ")
    assert parsed.application_email == "chandra@realmail.co"


def test_readiness_blocks_on_a_reserved_domain() -> None:
    """PROFILE_READY must not be reachable with an undeliverable address."""
    from app.applications.autofill_readiness import evaluate_autofill_readiness

    result = evaluate_autofill_readiness(
        profile={
            "name_confirmed": True, "first_name": "Chandra", "last_name": "Pandey",
            "application_email": "chandra@example.com", "application_email_confirmed": True,
            "phone_e164": "+16028161309", "location_city": "Phoenix",
        },
        account_email="demo@example.com",
        has_resume=True,
    )
    assert result.ready is False
    assert "application_email" in result.missing_required
    assert "Application email" in result.missing_labels


def test_readiness_passes_with_a_real_address() -> None:
    from app.applications.autofill_readiness import evaluate_autofill_readiness

    result = evaluate_autofill_readiness(
        profile={
            "name_confirmed": True, "first_name": "Chandra", "last_name": "Pandey",
            "application_email": "chandra@realmail.co", "application_email_confirmed": True,
            "phone_e164": "+16028161309", "location_city": "Phoenix",
        },
        account_email="demo@example.com",
        has_resume=True,
    )
    assert result.ready is True
    assert result.missing_required == []
