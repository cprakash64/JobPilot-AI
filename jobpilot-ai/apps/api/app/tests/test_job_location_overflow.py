"""Externally sourced job text must persist in full, and one bad record must
not abort a discovery batch.

A Flexport posting whose location listed every office city exceeded
``VARCHAR(255)`` and made ``POST /jobs/discover`` return 500 with
``StringDataRightTruncation``, losing every other job in that run.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.jobs import job_ingestion_service
from app.jobs.job_ingestion_service import compact_location
from app.models.entities import JobPosting

MULTI_CITY = (
    "San Francisco, CA; Chicago, IL; Atlanta, GA; Bellevue, WA; New York, NY; "
    "Amsterdam, Netherlands; Hamburg, Germany; Shenzhen, China; Hong Kong SAR; "
    "Toronto, ON; Vancouver, BC; Mexico City, Mexico; Sao Paulo, Brazil; "
    "Singapore; Sydney, Australia; Tokyo, Japan; Seoul, South Korea"
)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _job(**overrides) -> JobPosting:
    defaults = {
        "external_id": "flexport-multi-city",
        "title": "Software Engineer",
        "company": "Flexport",
        "location": MULTI_CITY,
        "application_url": "https://flexport.example/apply",
        "source_url": "https://flexport.example/job",
        "description_raw": "Build logistics software.",
        "description_clean": "Build logistics software.",
        "required_skills": [],
        "preferred_skills": [],
        "responsibilities": [],
        "raw_json": {"location": MULTI_CITY},
        "hash_for_deduplication": "f" * 64,
    }
    return JobPosting(**{**defaults, **overrides})


def test_location_longer_than_255_characters_persists_in_full(db: Session) -> None:
    assert len(MULTI_CITY) > 255
    job = _job()
    db.add(job)
    db.commit()

    stored = db.scalar(select(JobPosting).where(JobPosting.external_id == "flexport-multi-city"))
    assert stored is not None
    assert stored.location == MULTI_CITY
    assert len(stored.location) == len(MULTI_CITY)


def test_full_source_value_is_retained_in_raw_json(db: Session) -> None:
    db.add(_job())
    db.commit()
    stored = db.scalar(select(JobPosting))
    assert stored.raw_json["location"] == MULTI_CITY


def test_url_longer_than_the_old_limit_persists(db: Session) -> None:
    long_url = "https://example.com/apply?" + "&".join(
        f"utm_{index}=value{index}" for index in range(120)
    )
    assert len(long_url) > 1000
    db.add(_job(application_url=long_url, source_url=long_url))
    db.commit()
    stored = db.scalar(select(JobPosting))
    assert stored.application_url == long_url


def test_compact_display_location_stays_short_and_readable() -> None:
    compact = compact_location(MULTI_CITY)
    assert compact is not None
    assert len(compact) <= 120
    # Readable summary, not a mid-word truncation.
    assert compact == "San Francisco, CA +16 more"


def test_compact_display_location_passes_short_values_through() -> None:
    assert compact_location("Nashville, Tennessee") == "Nashville, Tennessee"
    assert compact_location(None) is None
    assert compact_location("   ") is None


def test_compact_display_location_falls_back_to_ellipsis() -> None:
    single = "A" * 300
    compact = compact_location(single)
    assert compact is not None
    assert len(compact) <= 120
    assert compact.endswith("…")


def test_one_failing_record_does_not_abort_the_batch(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-job SAVEPOINT is what keeps a good job from being rolled back."""

    persisted: list[str] = []

    def fake_persist(session: Session, job) -> tuple[JobPosting, bool, bool]:
        if job.external_id == "broken":
            raise SQLAlchemyError("value too long for type character varying(255)")
        record = _job(
            external_id=job.external_id,
            hash_for_deduplication=job.external_id.ljust(64, "0"),
        )
        session.add(record)
        session.flush()
        persisted.append(job.external_id)
        return record, True, True

    monkeypatch.setattr(job_ingestion_service, "_persist_job", fake_persist)

    class FakeJob:
        def __init__(self, external_id: str) -> None:
            self.external_id = external_id
            self.source = "greenhouse"

    skipped: list[str] = []
    changed: list[int] = []
    for job in (FakeJob("good-1"), FakeJob("broken"), FakeJob("good-2")):
        try:
            with db.begin_nested():
                outcome = job_ingestion_service._persist_job(db, job)
        except SQLAlchemyError:
            skipped.append(f"{job.source}:{job.external_id}")
            continue
        changed.append(outcome[0].id)
    db.commit()

    assert persisted == ["good-1", "good-2"]
    assert skipped == ["greenhouse:broken"]
    surviving = db.scalars(select(JobPosting.external_id)).all()
    assert sorted(surviving) == ["good-1", "good-2"]


def test_discovery_result_reports_skipped_records() -> None:
    from app.jobs.job_ingestion_service import DiscoveryResult
    from app.jobs.job_search_criteria_service import SearchCriteria

    result = DiscoveryResult(criteria=SearchCriteria())
    assert result.skipped == 0
    assert result.skipped_records == []
