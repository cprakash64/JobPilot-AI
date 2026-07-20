"""Section A — profile revision and stale-session invalidation.

The live failure: the user corrected their structured name and set an
application email, but the session prepared BEFORE those edits kept serving its
original answer snapshot. The extension filled from that snapshot, so the
employer form still received nothing, and no layer could tell it had gone stale.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.applications.session_refresh import (
    current_profile_revision,
    is_stale,
    refresh_if_stale,
    refresh_session_answers,
)
from app.db.base import Base
from app.models import entities as E
from app.profile.revision import compute_profile_revision


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


COMPLETE = dict(
    full_name="Chandra Prakash Pandey",
    first_name="Chandra", middle_name="Prakash", last_name="Pandey",
    name_confirmed=True,
    application_email="chandra@mailbox.test-domain.co", application_email_confirmed=True,
    phone="602-816-1309", phone_country_code="+1", phone_country_iso2="US",
    phone_national_number="6028161309", phone_e164="+16028161309",
    location_city="Phoenix", location_state="AZ", location_country="United States",
    linkedin_url="https://linkedin.com/in/example",
    work_authorization="authorized_us", requires_sponsorship=False,
)


def _user(db: Session, **overrides) -> tuple[E.User, E.UserProfile]:
    user = E.User(email="demo@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    values = {**COMPLETE, **overrides, "user_id": user.id}
    profile = E.UserProfile(**values)
    db.add(profile)
    db.flush()
    return user, profile


def _session(db: Session, user: E.User, **overrides) -> E.ApplicationSession:
    job = E.JobPosting(
        external_id=f"job-{user.id}",
        title="Engineer", company="Airbnb",
        application_url="https://x.test/1", source_url="https://x.test/1",
        hash_for_deduplication=f"hash-{user.id}",
    )
    db.add(job)
    db.flush()
    values = dict(
        user_id=user.id, job_id=job.id,
        status=E.ApplicationSessionStatus.ready,
        source_url="https://x.test/1",
        job_snapshot={"company": "Airbnb"},
        generated_answers=[{"canonical_key": "first_name", "value": "STALE"}],
        unresolved_questions=[],
        profile_revision=current_profile_revision(db, user.id),
    )
    values.update(overrides)
    session = E.ApplicationSession(**values)
    db.add(session)
    db.flush()
    return session


# --------------------------------------------------------------------------- #
# The revision itself
# --------------------------------------------------------------------------- #
def test_revision_is_deterministic() -> None:
    a = compute_profile_revision(profile=dict(COMPLETE))
    b = compute_profile_revision(profile=dict(COMPLETE))
    assert a == b


def test_changing_an_autofill_fact_changes_the_revision() -> None:
    base = compute_profile_revision(profile=dict(COMPLETE))
    for field, value in [
        ("first_name", "Ada"),
        ("last_name", "Lovelace"),
        ("application_email", "other@mailbox.test-domain.co"),
        ("phone_e164", "+14155550123"),
        ("location_city", "Seattle"),
        ("linkedin_url", "https://linkedin.com/in/other"),
        ("work_authorization", "need_sponsorship_now"),
        ("requires_sponsorship", True),
    ]:
        changed = compute_profile_revision(profile={**COMPLETE, field: value})
        assert changed != base, f"changing {field} must change the revision"


def test_confirming_a_name_changes_the_revision() -> None:
    unconfirmed = compute_profile_revision(profile={**COMPLETE, "name_confirmed": False})
    assert unconfirmed != compute_profile_revision(profile=dict(COMPLETE))


def test_a_cosmetic_field_does_not_change_the_revision() -> None:
    """Autofill cannot use target roles, so editing them must not invalidate
    every prepared session."""
    base = compute_profile_revision(profile=dict(COMPLETE))
    assert compute_profile_revision(profile={**COMPLETE, "target_roles": ["SWE"]}) == base


def test_vault_answers_are_order_independent() -> None:
    one = [{"canonical_key": "a", "value": "1"}, {"canonical_key": "b", "value": "2"}]
    assert compute_profile_revision(profile={}, vault_answers=one) == compute_profile_revision(
        profile={}, vault_answers=list(reversed(one))
    )


def test_a_changed_vault_answer_changes_the_revision() -> None:
    one = [{"canonical_key": "a", "value": "1"}]
    two = [{"canonical_key": "a", "value": "2"}]
    base = compute_profile_revision(profile={}, vault_answers=one)
    other = compute_profile_revision(profile={}, vault_answers=two)
    assert base != other


def test_a_changed_document_selection_changes_the_revision() -> None:
    base = compute_profile_revision(profile=dict(COMPLETE), document_ids={"resume": 1})
    assert compute_profile_revision(profile=dict(COMPLETE), document_ids={"resume": 2}) != base


# --------------------------------------------------------------------------- #
# Staleness
# --------------------------------------------------------------------------- #
def test_a_freshly_created_session_is_not_stale(db: Session) -> None:
    user, _ = _user(db)
    assert is_stale(db, _session(db, user)) is False


def test_editing_the_profile_makes_the_session_stale(db: Session) -> None:
    user, profile = _user(db)
    session = _session(db, user)

    profile.application_email = "changed@mailbox.test-domain.co"
    db.flush()

    assert is_stale(db, session) is True


def test_a_session_prepared_before_revisions_existed_is_stale(db: Session) -> None:
    user, _ = _user(db)
    assert is_stale(db, _session(db, user, profile_revision=None)) is True


def test_a_completed_session_is_never_stale_or_rewritten(db: Session) -> None:
    """It records what was actually sent; rebuilding would rewrite history."""
    user, profile = _user(db)
    session = _session(db, user, status=E.ApplicationSessionStatus.completed)
    original = session.generated_answers

    profile.application_email = "changed@mailbox.test-domain.co"
    db.flush()

    assert is_stale(db, session) is False
    meta = refresh_session_answers(db, session, user, force=True)
    assert meta["refreshed"] is False
    assert meta["reason"] == "session_is_terminal"
    assert session.generated_answers == original


# --------------------------------------------------------------------------- #
# Refresh
# --------------------------------------------------------------------------- #
def test_refresh_rebuilds_the_answers_from_the_current_profile(db: Session) -> None:
    user, profile = _user(db)
    session = _session(db, user)
    assert session.generated_answers[0]["value"] == "STALE"

    profile.first_name = "Ada"
    profile.last_name = "Lovelace"
    profile.full_name = "Ada Lovelace"
    db.flush()

    meta = refresh_if_stale(db, session, user)
    assert meta["refreshed"] is True
    by_key = {a["canonical_key"]: a["value"] for a in session.generated_answers}
    assert by_key["first_name"] == "Ada"
    assert by_key["last_name"] == "Lovelace"
    # The stale snapshot is gone entirely.
    assert "STALE" not in by_key.values()


def test_refresh_stamps_the_current_revision(db: Session) -> None:
    user, profile = _user(db)
    session = _session(db, user)
    profile.location_city = "Seattle"
    db.flush()

    refresh_if_stale(db, session, user)
    assert session.profile_revision == current_profile_revision(db, user.id)
    assert is_stale(db, session) is False


def test_refresh_is_a_no_op_when_already_current(db: Session) -> None:
    user, _ = _user(db)
    session = _session(db, user)
    meta = refresh_if_stale(db, session, user)
    assert meta["refreshed"] is False
    assert meta["reason"] == "already_current"


def test_refresh_metadata_reports_keys_but_never_values(db: Session) -> None:
    user, profile = _user(db)
    session = _session(db, user)
    profile.location_city = "Seattle"
    db.flush()

    meta = refresh_session_answers(db, session, user, force=True)
    assert "first_name" in meta["answer_keys"]
    serialized = repr(meta)
    for secret in ["Chandra", "Pandey", "chandra@mailbox.test-domain.co", "6028161309"]:
        assert secret not in serialized, f"{secret} leaked into refresh metadata"


def test_refreshed_answers_contain_every_required_autofill_key(db: Session) -> None:
    user, profile = _user(db)
    session = _session(db, user, generated_answers=[], profile_revision=None)

    refresh_if_stale(db, session, user)
    keys = {a["canonical_key"] for a in session.generated_answers}
    for required in [
        "first_name", "last_name", "email",
        "phone_country_iso2", "phone_country", "phone_national",
        "city", "linkedin_url", "work_authorization_us",
    ]:
        assert required in keys, f"missing {required} after refresh"
