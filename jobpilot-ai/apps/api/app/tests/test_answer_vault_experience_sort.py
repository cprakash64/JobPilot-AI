"""Regression tests for the experience sort-key type bug.

`_recent_experiences` used `e.end_date or e.start_date or ""`, mixing
`datetime.date` and `str` in one sort key. Python 3 cannot compare `str` with
`date`, so preparing an application for a user whose experiences had a mix of
dated and undated rows crashed with:

    TypeError: '<' not supported between instances of 'str' and 'datetime.date'

These tests exercise the normalization helper and the real query/sort path, and
assert the whole `build_safe_answers` pipeline completes without raising.
"""

from collections.abc import Generator
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.applications.answer_vault_service import (
    _normalize_experience_date,
    _recent_experiences,
    build_safe_answers,
)
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


def _user(db: Session, email: str = "sortbug@mailbox.test-domain.co") -> E.User:
    user = E.User(email=email, hashed_password="x")
    db.add(user)
    db.flush()
    return user


def _exp(db: Session, user_id: int, *, company: str, title: str = "Engineer",
         start=None, end=None, current: bool = False) -> None:
    db.add(E.Experience(
        user_id=user_id, company=company, title=title,
        start_date=start, end_date=end, currently_working=current,
        bullets=[], technologies=[], measurable_impact=[],
    ))


# --------------------------------------------------------------------------- #
# Normalization helper
# --------------------------------------------------------------------------- #
def test_normalize_handles_none_and_empty():
    assert _normalize_experience_date(None) == date.min
    assert _normalize_experience_date("") == date.min


def test_normalize_handles_date_and_datetime():
    assert _normalize_experience_date(date(2024, 12, 1)) == date(2024, 12, 1)
    assert _normalize_experience_date(datetime(2024, 12, 1, 9, 30)) == date(2024, 12, 1)


def test_normalize_handles_iso_string_and_malformed():
    assert _normalize_experience_date("2026-07-10") == date(2026, 7, 10)
    # A longer ISO datetime string is truncated to its date portion.
    assert _normalize_experience_date("2026-07-10T12:00:00") == date(2026, 7, 10)
    # Malformed / unexpected values fall back to date.min instead of crashing.
    assert _normalize_experience_date("not-a-date") == date.min
    assert _normalize_experience_date(12345) == date.min


# --------------------------------------------------------------------------- #
# The original crash: mixed dated + undated experiences in one sort.
# --------------------------------------------------------------------------- #
def test_recent_experiences_mixes_dated_and_undated_without_typeerror(db: Session):
    """Before the fix this raised TypeError (str vs date) during sorted()."""
    user = _user(db)
    _exp(db, user.id, company="Dated Co", end=date(2024, 12, 1))
    _exp(db, user.id, company="Undated Co")  # no start/end -> was `""`, now date.min
    db.flush()

    result = _recent_experiences(db, user.id)
    companies = [r["company"] for r in result]
    # Both are kept; the dated one ranks ahead of the undated one.
    assert companies == ["Dated Co", "Undated Co"]


def test_recent_experiences_full_ordering(db: Session):
    user = _user(db)
    _exp(db, user.id, company="Current Co", start=date(2025, 1, 1), current=True)
    _exp(db, user.id, company="Recent Co", end=date(2024, 12, 1))
    _exp(db, user.id, company="Older Co", end=date(2023, 6, 1))
    _exp(db, user.id, company="No Dates Co")
    db.flush()

    companies = [r["company"] for r in _recent_experiences(db, user.id)]
    assert companies == ["Current Co", "Recent Co", "Older Co", "No Dates Co"]


def test_legacy_iso_string_dates_sort_with_real_dates_without_crash():
    """Legacy data could surface end_date as an ISO string. Mixed with real
    date objects and undated rows, the normalized key must sort them and never
    raise str-vs-date TypeError. Mirrors the production sort key exactly."""
    from types import SimpleNamespace

    rows = [
        SimpleNamespace(company="String Newer", currently_working=False, start_date=None, end_date="2024-12-01"),
        SimpleNamespace(company="Real Older", currently_working=False, start_date=None, end_date=date(2022, 1, 1)),
        SimpleNamespace(company="Undated", currently_working=False, start_date=None, end_date=None),
    ]
    ordered = sorted(
        rows,
        key=lambda experience: (
            bool(experience.currently_working),
            _normalize_experience_date(experience.end_date or experience.start_date),
        ),
        reverse=True,
    )
    assert [r.company for r in ordered] == ["String Newer", "Real Older", "Undated"]


def test_build_safe_answers_completes_with_mixed_dates(db: Session):
    """End-to-end: the pipeline entry point must not raise TypeError."""
    user = _user(db)
    db.add(E.UserProfile(user_id=user.id, full_name="Dana Lee", skills=["Python"]))
    _exp(db, user.id, company="Current Co", start=date(2025, 1, 1), current=True)
    _exp(db, user.id, company="No Dates Co")  # missing dates must not crash
    db.flush()

    safe, unresolved = build_safe_answers(db, user)
    assert isinstance(safe, list)
    assert isinstance(unresolved, list)
    keys = {a["canonical_key"] for a in safe}
    assert "email" in keys
