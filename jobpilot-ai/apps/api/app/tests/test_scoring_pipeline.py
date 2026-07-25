"""Tests for the background fit-scoring pipeline: state machine, change
detection, idempotency, isolation, version guard, backfill and the scheduler
lock. Uses in-memory SQLite (models drive the schema via create_all)."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.jobs import scoring_service
from app.jobs.job_matching_service import JobView, ProfileView, score_job
from app.jobs.scoring_service import (
    SCORE_VERSION,
    compute_job_content_hash,
    score_jobs_for_user,
    score_users_for_job,
)
from app.models.entities import JobMatch, JobPosting, ScoreState, User, UserProfile


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _make_user(db: Session, email: str, *, with_profile: bool = True) -> int:
    user = User(email=email, hashed_password="x")
    db.add(user)
    db.flush()
    if with_profile:
        db.add(
            UserProfile(
                user_id=user.id,
                target_roles=["Machine Learning Engineer"],
                skills=["Python", "PyTorch", "NLP"],
                remote_preference="remote",
                preferred_locations=["Remote"],
            )
        )
    db.flush()
    return user.id


def _make_job(db: Session, external_id: str = "1", *, title: str = "Machine Learning Engineer") -> JobPosting:
    job = JobPosting(
        external_id=external_id,
        title=title,
        company="Acme",
        location="Remote",
        remote_type="remote",
        employment_type="full-time",
        posted_at=datetime.now(UTC) - timedelta(days=1),
        application_url="https://job-boards.greenhouse.io/acme/1",
        source_url="https://job-boards.greenhouse.io/acme/1",
        description_raw="",
        description_clean="Build ML models. Requirements: Python, PyTorch, NLP.",
        required_skills=["Python", "PyTorch", "NLP"],
        hash_for_deduplication=external_id.zfill(64),
        is_active=True,
        last_seen_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()
    return job


def test_new_job_is_scored_for_active_user(db: Session) -> None:
    user_id = _make_user(db, "u@example.com")
    job = _make_job(db)
    stats = score_users_for_job(db, job.id, [user_id])
    assert stats.scored == 1
    match = db.scalar(select(JobMatch).where(JobMatch.user_id == user_id))
    assert match.score_state == ScoreState.scored.value
    assert match.fit_score is not None
    assert match.score_version == SCORE_VERSION
    assert match.job_content_hash == compute_job_content_hash(job)


def test_incomplete_profile_gets_placeholder(db: Session) -> None:
    user_id = _make_user(db, "np@example.com", with_profile=False)
    job = _make_job(db)
    stats = score_users_for_job(db, job.id, [user_id])
    assert stats.profile_incomplete == 1
    match = db.scalar(select(JobMatch).where(JobMatch.user_id == user_id))
    assert match.score_state == ScoreState.profile_incomplete.value
    assert match.fit_score is None


def test_unchanged_job_is_not_rescored(db: Session) -> None:
    user_id = _make_user(db, "u@example.com")
    _make_job(db)
    first = score_jobs_for_user(db, user_id)
    assert first.scored == 1
    # Second pass with no content/profile change → skipped, not rescored.
    second = score_jobs_for_user(db, user_id)
    assert second.scored == 0
    assert second.skipped == 1


def test_material_change_triggers_rescore(db: Session) -> None:
    user_id = _make_user(db, "u@example.com")
    job = _make_job(db)
    score_jobs_for_user(db, user_id)
    match = db.scalar(select(JobMatch).where(JobMatch.user_id == user_id))
    old_hash = match.job_content_hash

    # Materially change the description/requirements.
    job.description_clean = "Now a Go and Kubernetes role. Requirements: Go, Kubernetes."
    job.required_skills = ["Go", "Kubernetes"]
    db.commit()

    stats = score_jobs_for_user(db, user_id)
    assert stats.scored == 1
    db.refresh(match)
    assert match.job_content_hash != old_hash


def test_irrelevant_change_does_not_rescore(db: Session) -> None:
    user_id = _make_user(db, "u@example.com")
    job = _make_job(db)
    score_jobs_for_user(db, user_id)
    # Changing non-score fields (logo) must not change the content hash.
    job.company_logo_url = "https://logo.example/new.png"
    db.commit()
    stats = score_jobs_for_user(db, user_id)
    assert stats.scored == 0


def test_one_failure_does_not_block_others(db: Session, monkeypatch) -> None:
    user_id = _make_user(db, "u@example.com")
    good = _make_job(db, "1")
    bad = _make_job(db, "2", title="Broken Role")

    real_compute = scoring_service._compute_match

    def flaky(profile_view, job, criteria):
        if job.external_id == "2":
            raise RuntimeError("boom")
        return real_compute(profile_view, job, criteria)

    monkeypatch.setattr(scoring_service, "_compute_match", flaky)

    stats = score_jobs_for_user(db, user_id)
    assert stats.failed == 1
    assert stats.scored == 1
    states = {
        m.job_id: m.score_state
        for m in db.scalars(select(JobMatch).where(JobMatch.user_id == user_id))
    }
    assert states[good.id] == ScoreState.scored.value
    assert states[bad.id] == ScoreState.failed.value


def test_version_guard_blocks_stale_write(db: Session) -> None:
    user_id = _make_user(db, "u@example.com")
    _make_job(db)
    score_jobs_for_user(db, user_id)
    match = db.scalar(select(JobMatch).where(JobMatch.user_id == user_id))
    # Simulate a row scored by a NEWER algorithm version than this worker knows.
    match.score_version = SCORE_VERSION + 5
    match.fit_score = 99.0
    db.commit()

    score_jobs_for_user(db, user_id, force=True)
    db.refresh(match)
    # The stale (older-version) worker must not overwrite the newer score.
    assert match.fit_score == 99.0
    assert match.score_version == SCORE_VERSION + 5


def test_only_missing_skips_already_scored(db: Session) -> None:
    user_id = _make_user(db, "u@example.com")
    _make_job(db)
    score_jobs_for_user(db, user_id)
    # A backfill in only-missing mode should find nothing left to do.
    stats = score_jobs_for_user(db, user_id, only_missing=True)
    assert stats.scored == 0
    assert stats.selected == 0


def test_nullable_job_fields_do_not_crash(db: Session) -> None:
    user_id = _make_user(db, "u@example.com")
    job = JobPosting(
        external_id="9",
        title="Data Engineer",
        company="Acme",
        location=None,
        remote_type=None,
        employment_type=None,
        posted_at=datetime.now(UTC) - timedelta(days=1),
        application_url="https://job-boards.greenhouse.io/acme/9",
        source_url="https://job-boards.greenhouse.io/acme/9",
        description_raw="",
        description_clean="",  # empty description
        required_skills=[],
        hash_for_deduplication="9" * 64,
        is_active=True,
        last_seen_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()
    stats = score_users_for_job(db, job.id, [user_id])
    # Either scored or (eligibility) zeroed, but never crashed / failed.
    assert stats.failed == 0
    match = db.scalar(select(JobMatch).where(JobMatch.user_id == user_id))
    assert match.score_state == ScoreState.scored.value


def test_open_to_relocation_suppresses_domestic_location_mismatch() -> None:
    profile = ProfileView(
        target_roles=["Software Engineer"],
        preferred_locations=["Phoenix, AZ"],
        open_to_relocation=True,
        location_country="United States",
    )
    domestic = score_job(
        profile,
        JobView(title="Software Engineer", location="Cambridge, MA", workplace_type="onsite"),
    )
    assert "Job location differs from your preferred locations" not in domestic.risk_factors
    assert "Job is located in a different country" not in domestic.risk_factors

    international = score_job(
        profile,
        JobView(title="Software Engineer", location="Toronto, Canada", workplace_type="onsite"),
    )
    assert "Job is located in a different country" in international.risk_factors


def test_scheduler_lock_is_single_holder(db: Session, monkeypatch) -> None:
    """The Redis lock returns True for the first holder and False for a
    concurrent second holder (a duplicate scheduler instance must not run)."""
    from app.jobs.scheduled_ingestion import _RedisLock

    store: dict[str, bytes] = {}

    class FakeRedis:
        def set(self, key, value, nx=False, ex=None):
            if nx and key in store:
                return False
            store[key] = value.encode() if isinstance(value, str) else value
            return True

        def get(self, key):
            return store.get(key)

        def delete(self, key):
            store.pop(key, None)

    import redis as redis_mod

    monkeypatch.setattr(redis_mod.Redis, "from_url", classmethod(lambda cls, url: FakeRedis()))

    first = _RedisLock("k", 60)
    second = _RedisLock("k", 60)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
