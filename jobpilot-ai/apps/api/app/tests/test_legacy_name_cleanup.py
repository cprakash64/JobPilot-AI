"""Section H — a stale auto-derived name split must never override the confirmed
structured profile name.

Symptom on the live MongoDB application: the old "Chandra" / "Prakash Pandey"
split kept appearing even though the profile carried a confirmed structured
name. Cause: `build_safe_answers` merged answer-vault rows AFTER the structured
name, so a legacy row overwrote it.

The confirmed name is now three parts — first "Chandra", middle "Prakash",
last "Pandey" — so "Prakash" must appear in neither the first nor the last
name answer.

Values here come from the structured profile record; nothing is hardcoded in
product code.
"""

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.applications.answer_vault_service import (
    build_safe_answers,
    confirm_name,
    invalidate_legacy_name_answers,
)
from app.db.base import Base
from app.models import entities as E


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _user_with_confirmed_name(db: Session) -> E.User:
    user = E.User(email="real.candidate@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    db.add(E.UserProfile(
        user_id=user.id,
        full_name="Chandra Prakash Pandey",
        first_name="Chandra",
        middle_name="Prakash",
        last_name="Pandey",
        name_confirmed=True,
    ))
    db.flush()
    return user


def _legacy_split_rows(db: Session, user_id: int) -> None:
    """The residue of the old whitespace-split behaviour."""
    for key, value in (("first_name", "Chandra"), ("last_name", "Prakash Pandey")):
        db.add(E.ApplicationAnswer(
            user_id=user_id, canonical_key=key, value=value, display_value=value,
            source="auto", scope="global", company_key="", allow_auto_fill=True,
            is_user_verified=False,
        ))
    db.flush()


def test_legacy_vault_split_never_overrides_the_confirmed_structured_name(db: Session) -> None:
    user = _user_with_confirmed_name(db)
    _legacy_split_rows(db, user.id)

    answers, _ = build_safe_answers(db, user, company="MongoDB")
    by_key = {a["canonical_key"]: a["value"] for a in answers}

    assert by_key["first_name"] == "Chandra"
    assert by_key["middle_name"] == "Prakash"
    assert by_key["last_name"] == "Pandey"
    # The old split is gone entirely.
    assert "Prakash Pandey" not in by_key.values()


def test_invalidate_removes_the_stale_rows(db: Session) -> None:
    user = _user_with_confirmed_name(db)
    _legacy_split_rows(db, user.id)

    removed = invalidate_legacy_name_answers(db, user.id)
    db.flush()

    assert removed == 2
    remaining = db.scalars(
        select(E.ApplicationAnswer).where(E.ApplicationAnswer.user_id == user.id)
    ).all()
    assert [r.canonical_key for r in remaining] == []


def test_confirming_the_name_clears_legacy_rows_in_the_same_step(db: Session) -> None:
    user = E.User(email="confirm@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    db.add(E.UserProfile(user_id=user.id, full_name="Chandra Prakash Pandey"))
    db.flush()
    _legacy_split_rows(db, user.id)

    confirm_name(db, user.id, "Chandra", "Pandey", middle_name="Prakash")
    db.flush()

    answers, unresolved = build_safe_answers(db, user, company="MongoDB")
    by_key = {a["canonical_key"]: a["value"] for a in answers}
    assert by_key["first_name"] == "Chandra"
    assert by_key["middle_name"] == "Prakash"
    assert by_key["last_name"] == "Pandey"
    # Only a true full-name field gets every part.
    assert by_key["full_name"] == "Chandra Prakash Pandey"
    # A confirmed name is no longer an unresolved question.
    assert not [u for u in unresolved if u.get("action") == "confirm_name"]


def test_unconfirmed_profile_still_asks_rather_than_using_a_legacy_row(db: Session) -> None:
    user = E.User(email="unconfirmed@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    db.add(E.UserProfile(user_id=user.id, full_name="Chandra Prakash Pandey"))
    db.flush()
    _legacy_split_rows(db, user.id)

    _, unresolved = build_safe_answers(db, user, company="MongoDB")
    asked = [u for u in unresolved if u.get("action") == "confirm_name"]
    assert [u["canonical_key"] for u in asked] == ["first_name", "middle_name", "last_name"]
    # A three-token name is never treated as a settled split.
    assert all(u["suggestion_certain"] is False for u in asked)
