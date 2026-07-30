"""People discovery results must never be reused across incompatible semantics.

Two Toshiba jobs showed contradictory states: one displayed
"We could not complete this search because the provider request was invalid"
from a discovery run recorded *before* PDL's 404 was understood as "no profiles
matched", while the other showed the corrected no-match copy. The stale run kept
being treated as current because the search fingerprint did not change when the
provider's response semantics did.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.base import Base
from app.models.entities import JobPosting, PeopleDiscoveryRun, User
from app.people.service import (
    CONTRACT_VERSION_KEY,
    PEOPLE_SEARCH_CONTRACT_VERSION,
    query_fingerprint,
    recommendations_payload,
    run_contract_version,
)

# The fingerprint recorded by the build that shipped before PDL 404 semantics
# were corrected. Kept verbatim so this test fails if that history is rewritten.
LEGACY_CONTRACT_VERSION = "pdl-category-search-v2"


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


def _user(db: Session) -> User:
    user = User(email="cache@example.com", hashed_password=hash_password("password123"))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _job(db: Session, external_id: str) -> JobPosting:
    job = JobPosting(
        external_id=external_id,
        title="Software Engineer",
        company="Toshiba Global Commerce Solutions",
        company_domain="toshibacommerce.example",
        location="Durham, North Carolina, United States",
        employment_type="full-time",
        seniority_level="mid",
        application_url=f"https://simplify.jobs/p/{external_id}",
        source_url=f"https://simplify.jobs/p/{external_id}",
        description_raw="Build software.",
        description_clean="Build backend software services with Python.",
        required_skills=["Python"],
        raw_json={},
        hash_for_deduplication=external_id.ljust(64, "0"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _legacy_invalid_run(db: Session, job: JobPosting, user: User) -> PeopleDiscoveryRun:
    """A run exactly as the Jul 28 build wrote it: no contract version at all."""

    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="provider_unavailable",
        provider="pdl",
        query_fingerprint=query_fingerprint(job),
        failure_code="provider_request_invalid",
        safe_failure_message=(
            "We could not complete this search because the provider request was invalid."
        ),
        company_context={"discovery_strategy": "exact"},
        completed_at=datetime.now(UTC) - timedelta(days=1),
        started_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_legacy_run_without_a_contract_version_is_not_served_as_current(
    db: Session,
) -> None:
    user = _user(db)
    job = _job(db, "toshiba-a")
    _legacy_invalid_run(db, job, user)

    payload = recommendations_payload(db, user, job.id)

    assert payload["availability_reason"] != "provider_request_invalid"
    assert "provider request was invalid" not in " ".join(payload["warnings"])
    # The user is offered a fresh search rather than a permanent dead end.
    assert payload["status"] == "stale"
    assert payload["search_scope"]["refresh_eligible"] is True


def test_legacy_run_does_not_block_a_new_discovery(db: Session) -> None:
    """The old run was non-retryable, which pinned the job to the failure."""

    from app.people.service import _current_provider_error_run

    user = _user(db)
    job = _job(db, "toshiba-a2")
    _legacy_invalid_run(db, job, user)

    blocking = _current_provider_error_run(
        db,
        job_id=job.id,
        user_id=user.id,
        fingerprint=query_fingerprint(job),
    )
    assert blocking is None


def test_one_jobs_legacy_result_does_not_contaminate_another(db: Session) -> None:
    user = _user(db)
    job_a = _job(db, "toshiba-a3")
    job_b = _job(db, "toshiba-b3")
    _legacy_invalid_run(db, job_a, user)

    # Job B never ran discovery at all and must be untouched by job A's history.
    payload_b = recommendations_payload(db, user, job_b.id)
    assert payload_b["status"] == "not_started"
    assert payload_b["availability_reason"] == "available"


def test_current_version_no_match_run_is_reused(db: Session) -> None:
    user = _user(db)
    job = _job(db, "toshiba-current")
    db.add(
        PeopleDiscoveryRun(
            job_id=job.id,
            user_id=user.id,
            status="complete",
            provider="pdl",
            query_fingerprint=query_fingerprint(job),
            company_context={
                "discovery_strategy": "exact",
                "search_contract_version": PEOPLE_SEARCH_CONTRACT_VERSION,
            },
            completed_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )
    )
    db.commit()

    payload = recommendations_payload(db, user, job.id)
    assert payload["status"] == "no_reliable_matches"
    assert payload["availability_reason"] == "available"


def test_run_contract_version_reads_legacy_rows_as_legacy(db: Session) -> None:
    user = _user(db)
    job = _job(db, "toshiba-version")
    legacy = _legacy_invalid_run(db, job, user)
    assert run_contract_version(legacy) is None
    assert PEOPLE_SEARCH_CONTRACT_VERSION != LEGACY_CONTRACT_VERSION


def test_contract_version_covers_every_semantics_input() -> None:
    """The version must change when any of its inputs change."""

    from app.people import scoring
    from app.people.pdl_company import PDL_COMPANY_RESOLUTION_VERSION
    from app.people.pdl_query import PDL_QUERY_LADDER_VERSION
    from app.people.providers import PDL_DISCOVERY_STRATEGY_VERSION

    for component in (
        PDL_DISCOVERY_STRATEGY_VERSION,
        PDL_QUERY_LADDER_VERSION,
        PDL_COMPANY_RESOLUTION_VERSION,
        scoring.SCORING_VERSION,
    ):
        assert component in PEOPLE_SEARCH_CONTRACT_VERSION


def test_stored_runs_carry_the_contract_version(db: Session) -> None:
    user = _user(db)
    job = _job(db, "toshiba-store")
    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="complete",
        provider="pdl",
        query_fingerprint=query_fingerprint(job),
        company_context={"search_contract_version": PEOPLE_SEARCH_CONTRACT_VERSION},
        completed_at=datetime.now(UTC),
    )
    db.add(run)
    db.commit()
    stored = db.scalar(select(PeopleDiscoveryRun).where(PeopleDiscoveryRun.id == run.id))
    assert run_contract_version(stored) == PEOPLE_SEARCH_CONTRACT_VERSION


# --------------------------------------------------------------------------- #
# Targeted invalidation
# --------------------------------------------------------------------------- #


class _Args:
    """Stand-in for the parsed argparse namespace."""

    def __init__(self, **overrides) -> None:
        defaults = {
            "provider": None,
            "legacy_only": False,
            "company": None,
            "job_id": None,
            "before_version": None,
            "failure_code": None,
            "created_before": None,
            "dry_run": True,
            "apply": False,
        }
        for key, value in {**defaults, **overrides}.items():
            setattr(self, key, value)


def _current_run(db: Session, job: JobPosting, user: User) -> PeopleDiscoveryRun:
    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="complete",
        provider="pdl",
        query_fingerprint=query_fingerprint(job),
        company_context={CONTRACT_VERSION_KEY: PEOPLE_SEARCH_CONTRACT_VERSION},
        completed_at=datetime.now(UTC),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_dry_run_reports_matches_and_changes_nothing(db: Session) -> None:
    from scripts import invalidate_people_discovery_cache as command

    user = _user(db)
    job = _job(db, "toshiba-inv-1")
    legacy = _legacy_invalid_run(db, job, user)
    before_status = legacy.status
    before_context = dict(legacy.company_context or {})

    rows = command._selected(db, _Args(provider="pdl", legacy_only=True))
    summary = command._summarize(rows)

    assert summary["matched"] == 1
    assert summary["by_contract_version"] == {"legacy_unversioned": 1}
    assert summary["reinterpreted_failure_codes"] == 1
    db.refresh(legacy)
    assert legacy.status == before_status
    assert dict(legacy.company_context or {}) == before_context


def test_legacy_only_leaves_current_version_rows_alone(db: Session) -> None:
    from scripts import invalidate_people_discovery_cache as command

    user = _user(db)
    legacy_job = _job(db, "toshiba-inv-2")
    current_job = _job(db, "toshiba-inv-3")
    _legacy_invalid_run(db, legacy_job, user)
    current = _current_run(db, current_job, user)

    rows = command._selected(db, _Args(provider="pdl", legacy_only=True))
    assert current.id not in {run.id for run in rows}
    assert len(rows) == 1


def test_provider_filter_excludes_other_providers(db: Session) -> None:
    from scripts import invalidate_people_discovery_cache as command

    user = _user(db)
    job = _job(db, "toshiba-inv-4")
    _legacy_invalid_run(db, job, user)
    db.add(
        PeopleDiscoveryRun(
            job_id=job.id,
            user_id=user.id,
            status="provider_unavailable",
            provider="apollo",
            query_fingerprint="apollo-legacy",
            failure_code="provider_schema_error",
            company_context={},
            completed_at=datetime.now(UTC),
        )
    )
    db.commit()

    pdl_rows = command._selected(db, _Args(provider="pdl", legacy_only=True))
    assert {run.provider for run in pdl_rows} == {"pdl"}


def test_job_and_company_filters_narrow_the_selection(db: Session) -> None:
    from scripts import invalidate_people_discovery_cache as command

    user = _user(db)
    job_a = _job(db, "toshiba-inv-5")
    job_b = _job(db, "toshiba-inv-6")
    _legacy_invalid_run(db, job_a, user)
    _legacy_invalid_run(db, job_b, user)

    by_job = command._selected(db, _Args(legacy_only=True, job_id=job_a.id))
    assert {run.job_id for run in by_job} == {job_a.id}

    by_company = command._selected(db, _Args(legacy_only=True, company="Toshiba"))
    assert len(by_company) == 2
    assert command._selected(db, _Args(legacy_only=True, company="Cisco")) == []


def test_failure_code_filter_selects_only_that_code(db: Session) -> None:
    from scripts import invalidate_people_discovery_cache as command

    user = _user(db)
    job = _job(db, "toshiba-inv-7")
    _legacy_invalid_run(db, job, user)

    assert command._selected(
        db, _Args(failure_code="provider_request_invalid")
    )
    assert command._selected(db, _Args(failure_code="provider_timeout")) == []


def test_applying_marks_rows_superseded_and_is_idempotent(db: Session) -> None:
    from scripts import invalidate_people_discovery_cache as command

    user = _user(db)
    job = _job(db, "toshiba-inv-8")
    legacy = _legacy_invalid_run(db, job, user)

    rows = command._selected(db, _Args(provider="pdl", legacy_only=True))
    now = datetime.now(UTC).isoformat()
    for run in rows:
        context = dict(run.company_context or {})
        context[command.INVALIDATED_KEY] = now
        context.pop(CONTRACT_VERSION_KEY, None)
        run.company_context = context
        if run.status not in {"complete", "partial"}:
            run.status = command.INVALIDATED_STATUS
            run.safe_failure_message = None
    db.commit()
    db.refresh(legacy)

    assert legacy.status == command.INVALIDATED_STATUS
    assert legacy.safe_failure_message is None
    # Re-running selects nothing: the marker makes the command idempotent.
    assert command._selected(db, _Args(provider="pdl", legacy_only=True)) == []


def test_invalidated_run_is_not_served(db: Session) -> None:
    from scripts import invalidate_people_discovery_cache as command

    user = _user(db)
    job = _job(db, "toshiba-inv-9")
    legacy = _legacy_invalid_run(db, job, user)
    legacy.company_context = {
        command.INVALIDATED_KEY: datetime.now(UTC).isoformat()
    }
    legacy.status = command.INVALIDATED_STATUS
    legacy.safe_failure_message = None
    db.commit()

    payload = recommendations_payload(db, user, job.id)
    assert payload["availability_reason"] != "provider_request_invalid"
    assert "provider request was invalid" not in " ".join(payload["warnings"])
