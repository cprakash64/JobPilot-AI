"""Section E boundary: a known seeded demo identity must NEVER reach a real,
externally-hosted employer application as a verified answer — in ANY environment,
including development (the only environment this project currently runs).

Root cause of the original report: ``guard_against_dev_fixture`` only blocked in
``app_env == "production"``, so ``demo@example.com`` flowed straight through as a
verified email answer in dev. The boundary is now automated-test-fixture vs.
real-application, not dev-vs-prod: real sessions leave ``allow_dev_fixtures``
False and the demo email becomes ``missing_information``.
"""

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.applications.answer_vault_service import build_safe_answers
from app.applications.fixture_guard import DEMO_EMAIL_REPLACEMENT_REASON
from app.core.config import settings
from app.db.base import Base
from app.models import entities as E


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _demo_user(db: Session) -> E.User:
    user = E.User(email="demo@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    db.add(E.UserProfile(user_id=user.id, full_name="Chandra Prakash Pandey"))
    db.flush()
    return user


def _real_user(db: Session) -> E.User:
    user = E.User(email="chandra.real@gmail.com", hashed_password="x")
    db.add(user)
    db.flush()
    db.add(E.UserProfile(user_id=user.id, full_name="Chandra Prakash Pandey"))
    db.flush()
    return user


def test_demo_email_never_becomes_a_verified_answer_even_in_development(db: Session) -> None:
    # Force the environment this project actually runs in — the old guard would
    # have let the demo email through here.
    original = settings.app_env
    settings.app_env = "development"
    try:
        answers, unresolved = build_safe_answers(db, _demo_user(db), company="Samsara")
    finally:
        settings.app_env = original

    values = [a["value"] for a in answers]
    keys = {a["canonical_key"] for a in answers}
    assert "demo@example.com" not in values
    assert "email" not in keys  # not auto-fillable

    email_unresolved = [u for u in unresolved if u["canonical_key"] == "email"]
    assert email_unresolved, "the demo email must surface as an unresolved question"
    assert email_unresolved[0]["reason"] == DEMO_EMAIL_REPLACEMENT_REASON
    assert email_unresolved[0]["action"] == "replace_demo_email"


def test_real_user_email_is_a_normal_verified_answer(db: Session) -> None:
    answers, unresolved = build_safe_answers(db, _real_user(db), company="Samsara")
    email = [a for a in answers if a["canonical_key"] == "email"]
    assert email and email[0]["value"] == "chandra.real@gmail.com"
    assert not [u for u in unresolved if u["canonical_key"] == "email"]


def test_automated_fixture_sessions_may_opt_in_to_demo_data(db: Session) -> None:
    answers, _ = build_safe_answers(db, _demo_user(db), allow_dev_fixtures=True)
    values = [a["value"] for a in answers]
    assert "demo@example.com" in values
