from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import (
    CompanyBranding,
    JobPeopleCandidate,
    JobPosting,
    PeopleDiscoveryRun,
    PeopleEmploymentVerificationRun,
    PeopleProviderOperationUsage,
    ProfessionalPerson,
    User,
    UserJobPeopleRecommendation,
    UserProfile,
)
from app.people import providers
from app.people.bulk_capability import (
    bulk_capability_state,
    clear_local_bulk_capabilities,
)
from app.people.employment_validation import (
    EMPLOYMENT_VALIDATION_VERSION,
    EmploymentValidationStatus,
    validate_current_employment,
)
from app.people.feature_flags import configuration_summary
from app.people.intelligence import (
    expand_titles,
    extract_job_people_profile,
    resolve_company_identity,
    validate_company_domain,
)
from app.people.provider_usage import (
    ProviderUsageContext,
    ProviderUsageRecorder,
    operation_idempotency_key,
    reconcile_unknown_operations,
)
from app.people.providers import ApolloPeopleProvider, MockPeopleProvider, ProviderUnavailable
from app.people.schemas import (
    EmailVerificationResult,
    OutreachDraftRequest,
    PeopleSearchQuery,
    PersonEnrichmentRequest,
    ProviderPerson,
    ProviderUsage,
    WorkEmailResult,
)
from app.people.scoring import (
    candidate_rejection_reasons,
    confidence,
    explanations,
    score_candidate,
)
from app.people.security import is_professional_email, safe_profile_url
from app.people.service import (
    allocate_enrichment_targets,
    build_category_search_queries,
    find_email,
    outreach_draft,
    recommendations_payload,
)
from app.people.title_ontology import normalize_title, title_similarity


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield session
    finally:
        app.dependency_overrides.clear()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _job() -> JobPosting:
    return JobPosting(
        external_id="people-job-1",
        title="Machine Learning Engineer",
        company="Acme AI",
        company_domain="acme.example",
        location="New York, NY",
        employment_type="full-time",
        seniority_level="mid",
        application_url="https://acme.example/jobs/1",
        source_url="https://acme.example/jobs/1",
        description_raw="Build machine learning systems.",
        description_clean="Build machine learning systems with Python.",
        required_skills=["Python", "Machine Learning"],
        hash_for_deduplication="a" * 64,
    )


def _records() -> list[ProviderPerson]:
    now = datetime.now(UTC)
    common = {
        "provider": "mock",
        "current_company_name": "Acme AI",
        "current_company_domain": "acme.example",
        "location": "New York, NY",
        "employment_verified_at": now,
    }
    return [
        ProviderPerson(
            **common,
            provider_person_id="recruiter-1",
            full_name="Rita Recruiter",
            current_title="Senior Technical Recruiter",
            department="Talent",
            linkedin_url="https://www.linkedin.com/in/rita-recruiter",
        ),
        ProviderPerson(
            **common,
            provider_person_id="manager-1",
            full_name="Morgan Manager",
            current_title="Machine Learning Engineering Manager",
            department="Engineering",
            linkedin_url="https://www.linkedin.com/in/morgan-manager",
        ),
        ProviderPerson(
            **common,
            provider_person_id="engineer-1",
            full_name="Erin Engineer",
            current_title="Staff Machine Learning Engineer",
            department="Engineering",
            linkedin_url="https://www.linkedin.com/in/erin-engineer",
        ),
    ]


def test_job_intelligence_title_expansion_and_domain_validation() -> None:
    job = _job()
    profile = extract_job_people_profile(job)
    assert profile.role_family == "machine_learning"
    assert profile.department == "Engineering"
    assert "Technical Recruiter" in profile.recruiter_titles
    assert "Machine Learning Engineering Manager" in profile.hiring_manager_titles
    assert validate_company_domain("https://www.acme.example/about") == "acme.example"
    assert validate_company_domain("gmail.com") is None
    recruiters, managers, team = expand_titles(job.title, profile.role_family)
    assert recruiters and managers and team


@pytest.mark.parametrize(
    ("title", "description", "expected_family"),
    [
        ("Software Engineer Intern", "Build APIs.", "software_engineering"),
        ("Applied AI Engineer", "Build machine learning systems.", "machine_learning"),
        ("Embedded Firmware Engineer", "Develop RTOS software.", "embedded_systems"),
        ("Senior Product Manager", "Own product strategy.", "product"),
        ("Financial Analyst", "Support accounting and finance.", "finance"),
        ("Clinical Systems Engineer", "Improve healthcare devices.", "healthcare"),
    ],
)
def test_versioned_title_ontology_role_family_regressions(
    title: str, description: str, expected_family: str
) -> None:
    job = _job()
    job.title = title
    job.description_clean = description
    assert extract_job_people_profile(job).role_family == expected_family


def test_title_ontology_normalizes_semantic_variants() -> None:
    assert normalize_title("Sr. Software Development Mgr") == "senior software engineering manager"
    assert title_similarity("Campus Talent Partner", ["University Recruiter"]) >= 0.5
    assert title_similarity("Agentic AI Engineering Manager", ["Applied AI Manager"]) >= 0.6


def test_category_queries_are_staged_and_use_correct_filters() -> None:
    job = _job()
    job.title = "Software Engineer Intern"
    job.description_clean = (
        "Build backend services, application APIs, and software platforms."
    )
    profile = extract_job_people_profile(job)
    recruiter_queries = build_category_search_queries(profile, "likely_recruiter")
    manager_queries = build_category_search_queries(profile, "potential_hiring_manager")
    referrer_queries = build_category_search_queries(profile, "potential_referrer")
    assert {query.title_group for query in recruiter_queries} == {
        "specialist", "broad", "early_career"
    }
    assert all(query.location_filter_mode == "soft" for query in recruiter_queries)
    assert all(query.location_filter_mode == "soft" for query in manager_queries)
    assert {value for query in manager_queries for value in query.seniorities} == {
        "manager", "director", "head", "vp"
    }
    assert not (
        {title for query in recruiter_queries for title in query.titles}
        & {title for query in referrer_queries for title in query.titles}
    )
    referral_titles = {
        title
        for query in referrer_queries
        for title in query.titles
    }
    assert {
        "Software Engineer",
        "Software Developer",
        "Backend Engineer",
        "Frontend Engineer",
        "Full Stack Engineer",
        "Platform Engineer",
        "Application Engineer",
        "Systems Engineer",
        "Senior Software Engineer",
        "Staff Software Engineer",
        "Principal Software Engineer",
    } <= referral_titles
    assert not any(
        marker in title.lower()
        for title in referral_titles
        for marker in (
            "recruiter",
            "manager",
            "director",
            "head of",
            "vice president",
            "chief ",
        )
    )


def test_company_resolver_ignores_aggregator_and_requires_parent_evidence(db: Session) -> None:
    job = _job()
    job.company = "Acme Robotics"
    job.company_domain = None
    job.application_url = "https://simplify.jobs/p/acme-role"
    job.raw_json = {"company_url": "https://simplify.jobs/c/acme"}
    identity = resolve_company_identity(db, job)
    assert identity.canonical_domain is None
    assert identity.parent_domain is None
    profile = extract_job_people_profile(job, db)
    assert build_category_search_queries(
        profile, "likely_recruiter", related_company=True
    ) == []

    db.add(CompanyBranding(
        normalized_key="acme robotics",
        canonical_name="Acme Robotics",
        domain="robotics.acme.example",
        source="catalog",
        resolution_status="resolved",
    ))
    db.flush()
    identity = resolve_company_identity(db, job)
    assert identity.canonical_domain == "robotics.acme.example"
    assert identity.parent_domain == "acme.example"
    assert identity.evidence_source == "company_branding_catalog"


def test_category_enrichment_reservations_and_reallocation() -> None:
    records = _records()
    candidates = {
        "likely_recruiter": [(90.0, "likely_recruiter", records[0], None, None)],
        "potential_hiring_manager": [
            (89.0, "potential_hiring_manager", records[1], None, None)
        ],
        "potential_referrer": [
            (score, "potential_referrer", ProviderPerson(
                **records[2].model_dump(exclude={"provider_person_id"}),
                provider_person_id=f"referrer-{index}",
            ), None, None)
            for index, score in enumerate((88.0, 87.0, 86.0, 85.0), start=1)
        ],
    }
    selected = allocate_enrichment_targets(
        candidates,
        total=4,
        reservations={
            "likely_recruiter": 2,
            "potential_hiring_manager": 1,
            "potential_referrer": 1,
        },
    )
    assert [item[1] for item in selected].count("likely_recruiter") == 1
    assert [item[1] for item in selected].count("potential_hiring_manager") == 1
    assert [item[1] for item in selected].count("potential_referrer") == 2


def test_startup_with_few_employees_reallocates_only_available_candidates() -> None:
    job = _job()
    job.company = "Small Startup"
    job.company_domain = "small-startup.example"
    profile = extract_job_people_profile(job)
    assert profile.parent_company_domain is None
    assert build_category_search_queries(
        profile, "likely_recruiter", related_company=True
    ) == []

    sparse = {
        "likely_recruiter": [],
        "potential_hiring_manager": [],
        "potential_referrer": [
            (75.0, "potential_referrer", _records()[2], None, None)
        ],
    }
    selected = allocate_enrichment_targets(
        sparse,
        total=8,
        reservations={
            "likely_recruiter": 3,
            "potential_hiring_manager": 3,
            "potential_referrer": 2,
        },
    )
    assert [item[1] for item in selected] == ["potential_referrer"]


def test_category_thresholds_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.people import service

    monkeypatch.setattr(settings, "people_min_recruiter_relevance", 71.0)
    monkeypatch.setattr(settings, "people_min_manager_relevance", 72.0)
    monkeypatch.setattr(settings, "people_min_referrer_relevance", 73.0)
    assert service._category_threshold("likely_recruiter") == 71.0
    assert service._category_threshold("potential_hiring_manager") == 72.0
    assert service._category_threshold("potential_referrer") == 73.0


def test_secondary_verification_caps_are_independent_by_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    monkeypatch.setattr(settings, "people_employment_verification_max_recruiters", 1)
    monkeypatch.setattr(settings, "people_employment_verification_max_managers", 2)
    monkeypatch.setattr(settings, "people_employment_verification_max_referrers", 3)

    assert service._employment_verification_cap("likely_recruiter") == 1
    assert service._employment_verification_cap("potential_hiring_manager") == 2
    assert service._employment_verification_cap("potential_referrer") == 3


def test_generic_related_parent_employee_is_suppressed() -> None:
    job = _job()
    job.company = "Acme Commerce Solutions"
    job.company_domain = "commerce.acme.example"
    profile = extract_job_people_profile(job)
    person = ProviderPerson(
        provider="mock",
        provider_person_id="parent-generic",
        full_name="Generic Employee",
        current_company_name="Acme",
        current_company_domain="acme.example",
        current_title="Software Test Engineer",
        employment_verified_at=datetime.now(UTC),
        linkedin_url="https://www.linkedin.com/in/generic-employee",
    )
    relevance = score_candidate("potential_referrer", person, profile)
    reasons = candidate_rejection_reasons(
        "potential_referrer",
        person,
        profile,
        relevance=relevance,
        data_confidence=confidence(person),
        relevance_threshold=60,
        confidence_threshold=0.5,
    )
    assert "weak_company_confidence" in reasons
    assert (
        "weak_role_similarity" in reasons
        or "below_relevance_threshold" in reasons
    )


def test_stale_people_fingerprint_refresh_is_scoped_to_selected_job(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.people import service

    job = _job()
    other_job = _job()
    other_job.external_id = "people-job-2"
    other_job.hash_for_deduplication = "b" * 64
    user = User(email="refresh@example.com", hashed_password=hash_password("password123"))
    db.add_all([job, other_job, user])
    db.commit()
    legacy = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="complete",
        provider="cache",
        query_fingerprint="legacy-v1-fingerprint",
        company_context={"scoring_version": "people-v1"},
        completed_at=datetime.now(UTC),
    )
    other_run = PeopleDiscoveryRun(
        job_id=other_job.id,
        user_id=user.id,
        status="complete",
        provider="cache",
        query_fingerprint="other-job-fingerprint",
        completed_at=datetime.now(UTC),
    )
    db.add_all([legacy, other_run])
    db.commit()

    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    provider = MockPeopleProvider([])
    monkeypatch.setattr(service, "get_people_provider", lambda: provider)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    before = client.get(f"/jobs/{job.id}/people", headers=headers)
    assert before.json()["status"] == "stale"
    refreshed = client.post(f"/jobs/{job.id}/people/discover", headers=headers)
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "no_reliable_matches"
    current = db.query(PeopleDiscoveryRun).filter(
        PeopleDiscoveryRun.job_id == job.id
    ).order_by(PeopleDiscoveryRun.id.desc()).first()
    assert current is not None
    assert current.query_fingerprint != legacy.query_fingerprint
    assert current.company_context["scoring_version"].startswith("people-v2")
    assert db.get(PeopleDiscoveryRun, other_run.id) is not None


def test_secondary_verification_flag_invalidates_only_unresolved_no_match_cache(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    job = _job()
    user = User(
        email="secondary-cache@example.com",
        hashed_password=hash_password("password123"),
    )
    db.add_all([job, user])
    db.flush()
    monkeypatch.setattr(
        settings, "people_employment_secondary_verification_enabled", False
    )
    fingerprint = service.query_fingerprint(job)
    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="complete",
        provider="apollo",
        query_fingerprint=fingerprint,
        company_context={"secondary_employment_verification_enabled": False},
        completed_at=datetime.now(UTC),
    )
    db.add(run)
    db.commit()
    assert service._fresh_no_match_run(
        db, job_id=job.id, user_id=user.id, fingerprint=fingerprint
    ) is not None

    monkeypatch.setattr(
        settings, "people_employment_secondary_verification_enabled", True
    )
    assert service.query_fingerprint(job) == fingerprint
    assert service._fresh_no_match_run(
        db, job_id=job.id, user_id=user.id, fingerprint=fingerprint
    ) is None
    run.company_context = {"secondary_employment_verification_enabled": True}
    db.commit()
    assert service._fresh_no_match_run(
        db, job_id=job.id, user_id=user.id, fingerprint=fingerprint
    ) is not None


def test_apollo_adapter_version_refreshes_cached_schema_error(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.people import service

    job = _job()
    user = User(
        email="adapter-refresh@example.com",
        hashed_password=hash_password("password123"),
    )
    db.add_all([job, user])
    db.flush()
    monkeypatch.setattr(settings, "people_primary_provider", "apollo")
    monkeypatch.setattr(
        service, "APOLLO_ENRICHMENT_ADAPTER_VERSION", "apollo-enrichment-v2"
    )
    broken_fingerprint = service.query_fingerprint(job)
    db.add(PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="provider_unavailable",
        provider="apollo",
        query_fingerprint=broken_fingerprint,
        failure_code="provider_schema_error",
        safe_failure_message="The people provider returned an unsupported response.",
        completed_at=datetime.now(UTC),
    ))
    db.commit()

    monkeypatch.setattr(
        service, "APOLLO_ENRICHMENT_ADAPTER_VERSION", "apollo-enrichment-v3"
    )
    payload = recommendations_payload(db, user, job.id)

    assert service.query_fingerprint(job) != broken_fingerprint
    assert payload["status"] == "stale"
    assert payload["availability_reason"] == "provider_schema_error"
    assert payload["retry_eligible"] is True
    assert payload["search_scope"]["refresh_eligible"] is True


def test_v3_no_match_becomes_read_only_stale_then_current_no_match(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    job = _job()
    user = User(
        email="complete-strategy-refresh@example.com",
        hashed_password=hash_password("password123"),
    )
    db.add_all([job, user])
    db.flush()
    assert providers.APOLLO_ENRICHMENT_ADAPTER_VERSION == (
        "apollo-enrichment-v4-complete-person"
    )
    assert providers.APOLLO_BULK_CAPABILITY_VERSION == "apollo-enrichment-v4"
    monkeypatch.setattr(settings, "people_primary_provider", "apollo")
    monkeypatch.setattr(
        service,
        "APOLLO_ENRICHMENT_ADAPTER_VERSION",
        "apollo-enrichment-v3",
    )
    old_fingerprint = service.query_fingerprint(job, "exact")
    db.add(
        PeopleDiscoveryRun(
            job_id=job.id,
            user_id=user.id,
            status="complete",
            provider="apollo",
            query_fingerprint=old_fingerprint,
            company_context={
                "provider_adapter_version": "apollo-enrichment-v3",
                "discovery_strategy": "exact",
            },
            completed_at=datetime.now(UTC),
        )
    )
    db.commit()

    monkeypatch.setattr(
        service,
        "APOLLO_ENRICHMENT_ADAPTER_VERSION",
        providers.APOLLO_ENRICHMENT_ADAPTER_VERSION,
    )
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    provider = MockPeopleProvider([])
    monkeypatch.setattr(service, "get_people_provider", lambda: provider)
    single_query = PeopleSearchQuery(
        category="likely_recruiter",
        company_name="Acme AI",
        company_domain="acme.example",
        titles=["Technical Recruiter"],
        limit=1,
    )
    monkeypatch.setattr(
        service,
        "build_category_search_queries",
        lambda _profile, category, **_kwargs: (
            [single_query] if category == "likely_recruiter" else []
        ),
    )
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {create_access_token(str(user.id))}"
    }

    opened = client.get(f"/jobs/{job.id}/people", headers=headers)
    refreshed_page = client.get(f"/jobs/{job.id}/people", headers=headers)

    assert opened.json()["status"] == "stale"
    assert opened.json()["warnings"] == [
        "Contact discovery has been upgraded. Refresh to check again."
    ]
    assert opened.json()["search_scope"]["refresh_eligible"] is True
    assert opened.json()["search_scope"]["broaden_eligible"] is False
    assert refreshed_page.json() == opened.json()
    assert provider.requests == 0

    explicit_refresh = client.post(
        f"/jobs/{job.id}/people/discover",
        headers=headers,
    )
    assert explicit_refresh.status_code == 200
    assert explicit_refresh.json()["status"] == "no_reliable_matches"
    assert explicit_refresh.json()["search_scope"]["broaden_eligible"] is True
    assert provider.requests == 1
    assert (
        db.query(PeopleDiscoveryRun)
        .filter(PeopleDiscoveryRun.job_id == job.id)
        .count()
        == 2
    )

    repeated_refresh = client.post(
        f"/jobs/{job.id}/people/discover",
        headers=headers,
    )
    assert repeated_refresh.status_code == 200
    assert provider.requests == 1
    assert (
        db.query(PeopleDiscoveryRun)
        .filter(PeopleDiscoveryRun.job_id == job.id)
        .count()
        == 2
    )


def test_verified_v3_recommendations_remain_read_only_and_available(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    job = _job()
    user = User(
        email="verified-strategy-compatible@example.com",
        hashed_password=hash_password("password123"),
    )
    db.add_all([job, user])
    db.commit()
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    monkeypatch.setattr(settings, "people_primary_provider", "apollo")
    monkeypatch.setattr(settings, "people_min_relevance_score", 0)
    monkeypatch.setattr(settings, "people_min_recruiter_relevance", 0)
    monkeypatch.setattr(settings, "people_min_manager_relevance", 0)
    monkeypatch.setattr(settings, "people_min_referrer_relevance", 0)
    monkeypatch.setattr(settings, "people_min_data_confidence", 0)
    monkeypatch.setattr(
        service,
        "APOLLO_ENRICHMENT_ADAPTER_VERSION",
        "apollo-enrichment-v3",
    )
    provider = MockPeopleProvider(_records())
    monkeypatch.setattr(service, "get_people_provider", lambda: provider)
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {create_access_token(str(user.id))}"
    }

    old_strategy = client.post(
        f"/jobs/{job.id}/people/discover",
        headers=headers,
    )
    assert old_strategy.status_code == 200
    assert any(old_strategy.json()["categories"].values())
    requests_after_discovery = provider.requests

    monkeypatch.setattr(
        service,
        "APOLLO_ENRICHMENT_ADAPTER_VERSION",
        providers.APOLLO_ENRICHMENT_ADAPTER_VERSION,
    )
    opened = client.get(f"/jobs/{job.id}/people", headers=headers)
    browser_refresh = client.get(f"/jobs/{job.id}/people", headers=headers)

    assert opened.status_code == 200
    assert opened.json()["status"] == "complete"
    assert any(opened.json()["categories"].values())
    assert opened.json()["availability_reason"] == "available"
    assert opened.json()["search_scope"]["broaden_eligible"] is False
    assert browser_refresh.json()["status"] == "complete"
    assert provider.requests == requests_after_discovery


def test_adapter_change_waits_for_one_explicit_retry_mutation(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    job = _job()
    user = User(
        email="adapter-explicit-retry@example.com",
        hashed_password=hash_password("password123"),
    )
    db.add_all([job, user])
    db.flush()
    monkeypatch.setattr(settings, "people_primary_provider", "apollo")
    monkeypatch.setattr(
        service, "APOLLO_ENRICHMENT_ADAPTER_VERSION", "apollo-enrichment-v2"
    )
    db.add(PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="provider_unavailable",
        provider="apollo",
        query_fingerprint=service.query_fingerprint(job),
        failure_code="provider_schema_error",
        safe_failure_message="The people provider returned an unsupported response.",
        completed_at=datetime.now(UTC),
    ))
    db.commit()
    monkeypatch.setattr(
        service, "APOLLO_ENRICHMENT_ADAPTER_VERSION", "apollo-enrichment-v3"
    )
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    provider = MockPeopleProvider([])
    monkeypatch.setattr(service, "get_people_provider", lambda: provider)
    single_query = PeopleSearchQuery(
        category="likely_recruiter",
        company_name="Acme AI",
        company_domain="acme.example",
        titles=["Technical Recruiter"],
        limit=1,
    )
    monkeypatch.setattr(
        service,
        "build_category_search_queries",
        lambda _profile, category, **_kwargs: (
            [single_query] if category == "likely_recruiter" else []
        ),
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    loaded = client.get(f"/jobs/{job.id}/people", headers=headers)
    refreshed = client.get(f"/jobs/{job.id}/people", headers=headers)
    assert loaded.json()["status"] == "stale"
    assert refreshed.json()["status"] == "stale"
    assert provider.requests == 0

    retried = client.post(f"/jobs/{job.id}/people/discover", headers=headers)
    assert retried.status_code == 200
    assert provider.requests == 1


def test_current_no_match_and_controlled_broaden_are_each_idempotent(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.people import service

    job = _job()
    user = User(email="broaden@example.com", hashed_password=hash_password("password123"))
    db.add_all([job, user])
    db.commit()
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    provider = MockPeopleProvider([])
    monkeypatch.setattr(service, "get_people_provider", lambda: provider)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    not_eligible = client.post(f"/jobs/{job.id}/people/broaden", headers=headers)
    assert not_eligible.status_code == 409
    assert provider.requests == 0

    exact = client.post(f"/jobs/{job.id}/people/discover", headers=headers)
    assert exact.status_code == 200
    assert exact.json()["status"] == "no_reliable_matches"
    assert exact.json()["search_scope"]["broaden_eligible"] is True
    exact_requests = provider.requests
    exact_run_count = db.query(PeopleDiscoveryRun).filter(
        PeopleDiscoveryRun.job_id == job.id
    ).count()

    cached_exact = client.post(f"/jobs/{job.id}/people/discover", headers=headers)
    assert cached_exact.status_code == 200
    assert provider.requests == exact_requests
    assert db.query(PeopleDiscoveryRun).filter(
        PeopleDiscoveryRun.job_id == job.id
    ).count() == exact_run_count

    broadened = client.post(f"/jobs/{job.id}/people/broaden", headers=headers)
    assert broadened.status_code == 200
    assert broadened.json()["status"] == "no_reliable_matches"
    assert broadened.json()["search_scope"]["broaden_attempted"] is True
    assert broadened.json()["search_scope"]["broaden_eligible"] is False
    broadened_requests = provider.requests
    broadened_run = db.query(PeopleDiscoveryRun).filter(
        PeopleDiscoveryRun.job_id == job.id
    ).order_by(PeopleDiscoveryRun.id.desc()).first()
    assert broadened_run is not None
    assert broadened_run.company_context["discovery_strategy"] == "broadened"
    assert all(
        category["discovery_strategy"] == "broadened"
        for category in broadened_run.category_diagnostics.values()
    )

    cached_broadened = client.post(f"/jobs/{job.id}/people/broaden", headers=headers)
    assert cached_broadened.status_code == 200
    assert provider.requests == broadened_requests


def test_scoring_confidence_explanations_and_security() -> None:
    profile = extract_job_people_profile(_job())
    recruiter = _records()[0]
    assert score_candidate("likely_recruiter", recruiter, profile) >= 60
    assert confidence(recruiter) >= 0.5
    reasons, limitations = explanations("likely_recruiter", recruiter, profile)
    assert any("recruit" in reason.lower() for reason in reasons)
    assert any("not been confirmed" in limitation for limitation in limitations)
    assert safe_profile_url("javascript:alert(1)") is None
    assert safe_profile_url("https://www.linkedin.com/in/rita-recruiter")
    assert is_professional_email("rita@acme.example", "acme.example")
    assert not is_professional_email("rita@gmail.com", "acme.example")


def test_apollo_search_uses_current_endpoint_and_partial_search_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append((method, url, kwargs))
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "total_entries": 1,
                    "people": [{
                        "id": "bbbbbbbbbbbbbbbbbbbbbbbb",
                        "person_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
                        "contact_id": "cccccccccccccccccccccccc",
                        "first_name": "Avery",
                        "last_name_obfuscated": "Ex***e",
                        "title": "Software Engineering Manager",
                        "organization": {"name": "Acme"},
                    }],
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    providers._CIRCUITS.clear()
    provider = ApolloPeopleProvider("configured-without-reading-runtime-secret")
    rows = asyncio.run(provider.search_people(PeopleSearchQuery(
        category="potential_hiring_manager",
        company_name="Acme",
        company_domain="acme.example",
        titles=["Software Engineering Manager"],
        seniorities=["manager", "director"],
        limit=1,
    )))

    assert len(rows) == 1
    method, url, kwargs = calls[0]
    assert method == "POST"
    assert url == "https://api.apollo.io/api/v1/mixed_people/api_search"
    assert kwargs["json"]["q_organization_domains_list"] == ["acme.example"]
    assert kwargs["json"]["person_seniorities"] == ["manager", "director"]
    assert "q_organization_domains" not in kwargs["json"]
    assert kwargs["headers"]["x-api-key"] == "configured-without-reading-runtime-secret"
    assert rows[0].provider_record_observed_at is not None
    assert rows[0].provider_person_id == "bbbbbbbbbbbbbbbbbbbbbbbb"
    assert rows[0].employment_verified_at is None
    assert rows[0].employment_source == "provider_current_listing"
    assert provider.search_identifier_safe_metrics == {
        "records_with_id": 1,
        "records_with_person_id": 1,
        "records_with_contact_id": 1,
        "accepted_identifier_count": 1,
        "rejected_identifier_count": 0,
        "identifier_type_distribution": {"string": 1},
        "identifier_length_distribution": {"24": 1},
    }


def test_apollo_search_accepts_documented_id_without_person_id_and_logs_only_shape(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    documented_id = "apollo_person-ABC_123"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "people": [{
                        "id": documented_id,
                        "first_name": "Private",
                        "last_name_obfuscated": "Pe***n",
                        "title": "Engineering Manager",
                        "organization": {"name": "Acme"},
                    }]
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = ApolloPeopleProvider("configured-without-reading-runtime-secret")
    with caplog.at_level(logging.INFO, logger="jobpilot.people.provider"):
        rows = asyncio.run(provider.search_people(PeopleSearchQuery(
            category="potential_hiring_manager",
            company_name="Acme",
            company_domain="acme.example",
            titles=["Engineering Manager"],
            limit=1,
        )))

    assert [row.provider_person_id for row in rows] == [documented_id]
    assert provider.search_identifier_safe_metrics == {
        "records_with_id": 1,
        "records_with_person_id": 0,
        "records_with_contact_id": 0,
        "accepted_identifier_count": 1,
        "rejected_identifier_count": 0,
        "identifier_type_distribution": {"string": 1},
        "identifier_length_distribution": {str(len(documented_id)): 1},
    }
    assert documented_id not in caplog.text
    assert "Private" not in caplog.text
    assert "configured-without-reading-runtime-secret" not in caplog.text


def test_apollo_search_rejects_contact_organization_and_person_id_as_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "people": [
                        {
                            "person_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
                            "contact_id": "bbbbbbbbbbbbbbbbbbbbbbbb",
                            "title": "Engineering Manager",
                            "first_name": "Redacted",
                            "organization": {"name": "Acme"},
                        },
                        {
                            "organization_id": "cccccccccccccccccccccccc",
                            "account_id": "dddddddddddddddddddddddd",
                            "title": "Director of Engineering",
                            "first_name": "Redacted",
                            "organization": {"name": "Acme"},
                        },
                    ]
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = ApolloPeopleProvider("configured-without-reading-runtime-secret")
    with pytest.raises(ProviderUnavailable) as raised:
        asyncio.run(provider.search_people(PeopleSearchQuery(
            category="potential_hiring_manager",
            company_name="Acme",
            company_domain="acme.example",
            titles=["Engineering Manager"],
            limit=2,
        )))

    assert raised.value.reason == "provider_schema_error"
    assert provider.search_identifier_safe_metrics["records_with_id"] == 0
    assert provider.search_identifier_safe_metrics["records_with_person_id"] == 1
    assert provider.search_identifier_safe_metrics["records_with_contact_id"] == 1
    assert provider.search_identifier_safe_metrics["accepted_identifier_count"] == 0
    assert provider.search_identifier_safe_metrics["rejected_identifier_count"] == 2


def test_apollo_422_is_reported_as_provider_schema_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            return httpx.Response(
                422,
                request=httpx.Request(method, url),
                json={"provider_payload": "must not be surfaced"},
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    providers._CIRCUITS.clear()
    provider = ApolloPeopleProvider("configured-without-reading-runtime-secret")
    query = PeopleSearchQuery(
        category="likely_recruiter",
        company_name="Acme",
        company_domain="acme.example",
        titles=["Technical Recruiter"],
        limit=1,
    )
    for _ in range(4):
        with pytest.raises(ProviderUnavailable) as raised:
            asyncio.run(provider.search_people(query))
        assert raised.value.reason == "provider_schema_error"
        assert raised.value.http_status == 422
    assert providers._CIRCUITS.get("apollo", (0, None)) == (0, None)


def test_transient_failures_open_circuit_after_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("timed out", request=httpx.Request(method, url))

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: TimeoutClient())
    providers._CIRCUITS.clear()
    provider = ApolloPeopleProvider("configured-without-reading-runtime-secret")
    query = PeopleSearchQuery(
        category="likely_recruiter",
        company_name="Acme",
        company_domain="acme.example",
        titles=["Technical Recruiter"],
        limit=1,
    )
    for _ in range(3):
        with pytest.raises(ProviderUnavailable) as raised:
            asyncio.run(provider.search_people(query))
        assert raised.value.reason == "provider_timeout"
    with pytest.raises(ProviderUnavailable) as circuit:
        asyncio.run(provider.search_people(query))
    assert circuit.value.reason == "provider_circuit_open"
    assert calls == 3


def test_apollo_bulk_enrichment_and_specific_safe_failure_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        httpx.Response(
            200,
            request=httpx.Request("POST", "https://api.apollo.io"),
            json={
                "credits_consumed": 1,
                "matches": [{
                    "id": "aaaaaaaaaaaaaaaaaaaaaaaa",
                    "name": "Avery Example",
                    "title": "Engineering Manager",
                    "organization": {"name": "Acme", "primary_domain": "acme.example"},
                }],
            },
        ),
        httpx.Response(
            403,
            request=httpx.Request("POST", "https://api.apollo.io"),
            json={"safe": "ignored"},
        ),
    ]
    calls: list[tuple[str, str, dict]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append((method, url, kwargs))
            return responses.pop(0)

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    providers._CIRCUITS.clear()
    provider = ApolloPeopleProvider("configured-without-reading-runtime-secret")
    enriched = asyncio.run(provider.enrich_people([
        PersonEnrichmentRequest(
            provider_person_id="aaaaaaaaaaaaaaaaaaaaaaaa"
        )
    ]))
    assert len(enriched) == 1
    assert calls[0][0:2] == (
        "POST", "https://api.apollo.io/api/v1/people/bulk_match"
    )
    assert calls[0][2]["json"] == {
        "details": [{"id": "aaaaaaaaaaaaaaaaaaaaaaaa"}]
    }

    with pytest.raises(ProviderUnavailable) as raised:
        asyncio.run(provider.search_people(PeopleSearchQuery(
            category="likely_recruiter",
            company_name="Acme",
            company_domain="acme.example",
            titles=["Technical Recruiter"],
            limit=1,
        )))
    assert raised.value.reason == "provider_forbidden"
    assert raised.value.http_status == 403
    assert raised.value.provider == "apollo"


def test_eight_valid_apollo_ids_use_one_bulk_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    identifiers = [f"{index + 100:024x}" for index in range(8)]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append(kwargs)
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "matches": [
                        {
                            "id": identifier,
                            "name": "Redacted Person",
                            "title": "Engineer",
                            "organization": {
                                "name": "Acme",
                                "primary_domain": "acme.example",
                            },
                        }
                        for identifier in identifiers
                    ]
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = ApolloPeopleProvider("configured-without-reading-runtime-secret")
    enriched = asyncio.run(provider.enrich_people([
        PersonEnrichmentRequest(provider_person_id=value)
        for value in identifiers
    ]))

    assert len(calls) == 1
    assert len(calls[0]["json"]["details"]) == 8
    assert len(enriched) == 8


def test_apollo_bulk_preserves_documented_id_and_avoids_empty_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    documented_id = "Apollo_Search-ID_123"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append(kwargs)
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "matches": [{
                        "id": documented_id,
                        "name": "Redacted Person",
                        "title": "Engineering Manager",
                        "organization": {"name": "Acme"},
                    }]
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = ApolloPeopleProvider("configured-without-reading-runtime-secret")
    empty = asyncio.run(provider.enrich_people([
        PersonEnrichmentRequest(provider_person_id=""),
        PersonEnrichmentRequest(provider_person_id="placeholder"),
    ]))
    enriched = asyncio.run(provider.enrich_people([
        PersonEnrichmentRequest(provider_person_id=documented_id)
    ]))

    assert empty == []
    assert len(calls) == 1
    assert calls[0]["json"] == {"details": [{"id": documented_id}]}
    assert "params" not in calls[0]
    assert [person.provider_person_id for person in enriched] == [documented_id]


def test_apollo_bulk_enrichment_filters_deduplicates_and_chunks_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append((method, url, kwargs))
            identifiers = [item["id"] for item in kwargs["json"]["details"]]
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "credits_consumed": len(identifiers),
                    "matches": [
                        {
                            "id": identifier,
                            "name": "Redacted Person",
                            "title": "Engineering Manager",
                            "organization": {
                                "name": "Acme",
                                "primary_domain": "acme.example",
                            },
                        }
                        for identifier in identifiers
                    ],
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    providers._CIRCUITS.clear()
    provider = ApolloPeopleProvider("configured-without-reading-runtime-secret")
    identifiers = [f"{index:024x}" for index in range(12)]
    requests = [
        PersonEnrichmentRequest(provider_person_id=value)
        for value in [
            *identifiers,
            identifiers[0],
            "",
            "not an apollo id",
            "abcd********************",
        ]
    ]
    requests.append(
        PersonEnrichmentRequest.model_construct(provider_person_id=None)
    )

    enriched = asyncio.run(provider.enrich_people(requests))

    assert [len(call[2]["json"]["details"]) for call in calls] == [10, 2]
    assert len(enriched) == 12
    assert provider.credits == 12
    assert all(call[0] == "POST" for call in calls)
    assert all(
        call[1] == "https://api.apollo.io/api/v1/people/bulk_match"
        for call in calls
    )
    assert all(set(call[2]["json"]) == {"details"} for call in calls)
    assert all(
        set(detail) == {"id"}
        for call in calls
        for detail in call[2]["json"]["details"]
    )
    assert all("reveal_personal_emails" not in call[2]["json"] for call in calls)
    assert all("reveal_phone_number" not in call[2]["json"] for call in calls)
    assert all("params" not in call[2] for call in calls)
    assert all(call[2]["headers"]["Content-Type"] == "application/json" for call in calls)
    assert all(set(call[2]["headers"]) == {"x-api-key", "Content-Type", "Accept"} for call in calls)
    assert provider.enrichment_safe_metrics["duplicate_provider_person_id"] == 1
    assert provider.enrichment_safe_metrics["invalid_provider_person_id"] == 4
    assert provider.enrichment_safe_metrics["blank_provider_person_id"] == 1
    assert provider.enrichment_safe_metrics["malformed_provider_person_id"] == 1
    assert provider.enrichment_safe_metrics["placeholder_provider_person_id"] == 1
    assert provider.enrichment_safe_metrics["null_provider_person_id"] == 1


def test_apollo_bulk_422_uses_bounded_single_fallback_and_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    valid_identifier = "aaaaaaaaaaaaaaaaaaaaaaaa"
    rejected_identifier = "bbbbbbbbbbbbbbbbbbbbbbbb"
    calls: list[tuple[str, str, dict]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/bulk_match"):
                return httpx.Response(
                    422,
                    request=httpx.Request(method, url),
                    json={
                        "detail": [{
                            "type": "missing",
                            "loc": ["body", "details", 0, "id"],
                            "msg": "Private Person must never be logged",
                            "input": rejected_identifier,
                            "ctx": {"expected": "string"},
                        }]
                    },
                )
            assert method == "GET"
            assert "params" not in kwargs
            assert "json" not in kwargs
            if url.endswith(f"/people/{valid_identifier}"):
                return httpx.Response(
                    200,
                    request=httpx.Request(method, url),
                    json={
                        "person": {
                            "id": valid_identifier,
                            "name": "Private Person",
                            "title": "Engineering Manager",
                            "organization": {
                                "name": "Acme",
                                "primary_domain": "acme.example",
                            },
                        },
                        "credits_consumed": 1,
                    },
                )
            return httpx.Response(
                422,
                request=httpx.Request(method, url),
                json={
                    "errors": [{
                        "code": "invalid_id",
                        "path": "id",
                        "input": rejected_identifier,
                    }]
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    providers._CIRCUITS.clear()
    provider = ApolloPeopleProvider("configured-without-reading-runtime-secret")
    with caplog.at_level(logging.INFO, logger="jobpilot.people.provider"):
        enriched = asyncio.run(provider.enrich_people([
            PersonEnrichmentRequest(provider_person_id=valid_identifier),
            PersonEnrichmentRequest(provider_person_id=rejected_identifier),
        ]))

    assert [person.provider_person_id for person in enriched] == [valid_identifier]
    assert len(calls) == 3
    assert calls[0][1].endswith("/people/bulk_match")
    assert all(call[1].startswith("https://api.apollo.io/api/v1/people/") for call in calls[1:])
    assert all(call[0] == "GET" for call in calls[1:])
    assert all("json" not in call[2] for call in calls[1:])
    assert provider.enrichment_rejection_reason(rejected_identifier) == (
        "provider_schema_error"
    )
    assert provider.enrichment_safe_metrics["bulk_payload_validation_failed"] == 1
    assert provider.enrichment_safe_metrics["provider_schema_error"] == 1
    assert providers._CIRCUITS.get("apollo", (0, None)) == (0, None)
    assert "body.details.*.id" in caplog.text
    assert "expected_types=['string']" in caplog.text
    assert "error_scope=record_level" in caplog.text
    assert "Private Person" not in caplog.text
    assert rejected_identifier not in caplog.text
    assert "configured-without-reading-runtime-secret" not in caplog.text


def test_apollo_complete_person_get_normalizes_without_request_body_or_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = "cccccccccccccccccccccccc"
    calls: list[tuple[str, str, dict]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append((method, url, kwargs))
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "person": {
                        "id": identifier,
                        "name": "Synthetic Person",
                        "title": "Engineering Manager",
                        "linkedin_url": "https://www.linkedin.com/in/synthetic",
                        "last_refreshed_at": "2026-07-01T00:00:00Z",
                        "organization": {
                            "name": "Example Company",
                            "primary_domain": "example.test",
                        },
                        "employment_history": [
                            {"organization_name": "Example Company"},
                            {"organization_name": "Former Example"},
                        ],
                    }
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = ApolloPeopleProvider("synthetic-test-key")

    person = asyncio.run(
        provider.complete_person_by_id(
            PersonEnrichmentRequest(provider_person_id=identifier)
        )
    )

    assert person is not None
    assert person.provider_person_id == identifier
    assert person.current_company_domain == "example.test"
    assert person.previous_employers == ["Former Example"]
    assert person.employment_source == "complete_person_by_id"
    assert person.provider_record_observed_at == datetime(
        2026, 7, 1, tzinfo=UTC
    )
    assert person.provider_employment_updated_at is None
    assert person.employment_verified_at is None
    assert calls == [
        (
            "GET",
            f"https://api.apollo.io/api/v1/people/{identifier}",
            {
                "headers": {
                    "x-api-key": "synthetic-test-key",
                    "Accept": "application/json",
                }
            },
        )
    ]


def test_apollo_complete_person_path_is_url_encoded() -> None:
    assert providers._complete_person_url("synthetic/id?value") == (
        "https://api.apollo.io/api/v1/people/synthetic%2Fid%3Fvalue"
    )


@pytest.mark.parametrize(
    ("status_code", "expected_reason"),
    [
        (400, "provider_request_invalid"),
        (403, "provider_master_key_required_or_forbidden"),
        (422, "provider_schema_error"),
        (429, "provider_rate_limited"),
    ],
)
def test_apollo_complete_person_maps_safe_errors(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_reason: str,
) -> None:
    identifier = "dddddddddddddddddddddddd"
    calls = 0

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            nonlocal calls
            calls += 1
            return httpx.Response(
                status_code,
                request=httpx.Request(method, url),
                json={"message": "redacted"},
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = ApolloPeopleProvider("synthetic-test-key")

    with pytest.raises(ProviderUnavailable) as caught:
        asyncio.run(
            provider.complete_person_by_id(
                PersonEnrichmentRequest(provider_person_id=identifier)
            )
        )

    assert caught.value.reason == expected_reason
    if status_code == 403:
        with pytest.raises(ProviderUnavailable) as cached:
            asyncio.run(
                provider.complete_person_by_id(
                    PersonEnrichmentRequest(provider_person_id=identifier)
                )
            )
        assert cached.value.reason == expected_reason
        assert calls == 1


def test_apollo_complete_person_not_found_and_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = "eeeeeeeeeeeeeeeeeeeeeeee"
    responses = [
        httpx.Response(
            200,
            request=httpx.Request(
                "GET", f"https://api.apollo.io/api/v1/people/{identifier}"
            ),
            json={"person": None},
        ),
        httpx.Response(
            200,
            request=httpx.Request(
                "GET", "https://api.apollo.io/api/v1/people/ffffffffffffffffffffffff"
            ),
            content=b"not-json",
        ),
    ]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **_kwargs):
            return responses.pop(0)

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = ApolloPeopleProvider("synthetic-test-key")

    assert asyncio.run(
        provider.complete_person_by_id(
            PersonEnrichmentRequest(provider_person_id=identifier)
        )
    ) is None
    with pytest.raises(
        ProviderUnavailable, match="provider_response_invalid"
    ):
        asyncio.run(
            provider.complete_person_by_id(
                PersonEnrichmentRequest(
                    provider_person_id="ffffffffffffffffffffffff"
                )
            )
        )


def test_temporarily_rejected_bulk_uses_category_caps_dedupes_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifiers = [
        "111111111111111111111111",
        "222222222222222222222222",
        "333333333333333333333333",
        "444444444444444444444444",
    ]
    calls: list[str] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            calls.append(url)
            identifier = url.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "person": {
                        "id": identifier,
                        "name": "Synthetic Person",
                        "title": "Engineering Manager",
                        "organization": {
                            "name": "Example Company",
                            "primary_domain": "example.test",
                        },
                    }
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(
        providers, "bulk_capability_state", lambda *_args: "temporarily_rejected"
    )
    provider = ApolloPeopleProvider("synthetic-test-key")
    requests = [
        PersonEnrichmentRequest(
            provider_person_id=identifiers[0],
            category="likely_recruiter",
            rank_score=99,
        ),
        PersonEnrichmentRequest(
            provider_person_id=identifiers[1],
            category="likely_recruiter",
            rank_score=90,
        ),
        PersonEnrichmentRequest(
            provider_person_id=identifiers[0],
            category="potential_hiring_manager",
            rank_score=95,
        ),
        PersonEnrichmentRequest(
            provider_person_id=identifiers[2],
            category="potential_hiring_manager",
            rank_score=89,
        ),
        PersonEnrichmentRequest(
            provider_person_id=identifiers[3],
            category="potential_referrer",
            rank_score=88,
        ),
    ]

    first = asyncio.run(provider.enrich_people(requests))
    second = asyncio.run(provider.enrich_people(requests))

    assert len(first) == 3
    assert len(second) == 3
    assert len(calls) == 3
    assert not any(url.endswith("/bulk_match") for url in calls)
    assert provider.enrichment_safe_metrics["bulk_capability_skipped"] == 1
    assert provider.enrichment_safe_metrics["complete_person_cache_hit"] == 3


def test_complete_person_logs_exclude_credentials_and_personal_fields(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    identifier = "abababababababababababab"
    secret = "synthetic-secret-that-must-not-be-logged"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            return httpx.Response(
                403,
                request=httpx.Request(method, url),
                json={
                    "name": "Private Person",
                    "email": "private@example.test",
                    "linkedin_url": "https://www.linkedin.com/in/private",
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = ApolloPeopleProvider(secret)
    with caplog.at_level(logging.INFO, logger="jobpilot.people.provider"):
        with pytest.raises(ProviderUnavailable):
            asyncio.run(
                provider.complete_person_by_id(
                    PersonEnrichmentRequest(provider_person_id=identifier)
                )
            )

    assert secret not in caplog.text
    assert identifier not in caplog.text
    assert "Private Person" not in caplog.text
    assert "private@example.test" not in caplog.text
    assert "linkedin.com" not in caplog.text


def test_apollo_422_without_safe_field_path_uses_request_level_reason() -> None:
    response = httpx.Response(
        422,
        request=httpx.Request("POST", "https://api.apollo.io/api/v1/people/bulk_match"),
        json={"message": "Private response content must not be retained"},
    )

    metadata = providers._safe_apollo_validation_metadata(response)

    assert metadata == {
        "error_types": ["provider_bulk_validation_failed"],
        "field_paths": [],
        "expected_types": [],
        "missing_required": False,
        "error_scope": "request_level",
    }
    unparseable = httpx.Response(
        422,
        request=response.request,
        text="not-json and not retained",
    )
    assert providers._safe_apollo_validation_metadata(unparseable) == metadata


def test_apollo_enrichment_requires_id_correlation_and_preserves_request_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_identifier = "111111111111111111111111"
    second_identifier = "222222222222222222222222"
    unexpected_identifier = "333333333333333333333333"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            def row(identifier: str, title: str) -> dict:
                return {
                    "id": identifier,
                    "name": "Redacted Person",
                    "title": title,
                    "organization": {
                        "name": "Acme",
                        "primary_domain": "acme.example",
                    },
                }

            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "data": {
                        "credits_consumed": 2,
                        "people": [
                            row(second_identifier, "Second Title"),
                            row(unexpected_identifier, "Wrong Person"),
                            row(first_identifier, "First Title"),
                        ],
                    }
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = ApolloPeopleProvider("configured-without-reading-runtime-secret")

    enriched = asyncio.run(provider.enrich_people([
        PersonEnrichmentRequest(provider_person_id=first_identifier),
        PersonEnrichmentRequest(provider_person_id=second_identifier),
    ]))

    assert [person.provider_person_id for person in enriched] == [
        first_identifier,
        second_identifier,
    ]
    assert [person.current_title for person in enriched] == [
        "First Title",
        "Second Title",
    ]
    assert unexpected_identifier not in {
        person.provider_person_id for person in enriched
    }
    assert provider.enrichment_safe_metrics["enrichment_correlation_failed"] == 1
    assert provider.credits == 2


def test_provider_failure_log_contains_only_safe_diagnostic_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.people.service import _log_provider_failure

    with caplog.at_level(logging.WARNING, logger="jobpilot.people"):
        _log_provider_failure(
            ProviderUnavailable(
                "provider_forbidden",
                provider="apollo",
                http_status=403,
                duration_ms=12.345,
            ),
            discovery_run_id=77,
        )
    message = caplog.messages[-1]
    assert "reason=provider_forbidden" in message
    assert "provider=apollo" in message
    assert "http_status=403" in message
    assert "duration_ms=12.35" in message
    assert "discovery_run_id=77" in message


def test_discovery_cache_actions_and_cross_user_denial(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job()
    first = User(email="first@example.com", hashed_password=hash_password("password123"))
    second = User(email="second@example.com", hashed_password=hash_password("password123"))
    db.add_all([job, first, second])
    db.commit()
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    monkeypatch.setattr(settings, "people_min_relevance_score", 50.0)
    monkeypatch.setattr(settings, "people_network_matching_enabled", False)
    monkeypatch.setattr(settings, "people_email_discovery_enabled", True)
    monkeypatch.setattr(settings, "people_outreach_drafting_enabled", True)
    from app.people import service

    provider = MockPeopleProvider(_records())
    monkeypatch.setattr(service, "get_people_provider", lambda: provider)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(first.id))}"}

    initial = client.get(f"/jobs/{job.id}/people", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["status"] == "not_started"

    discovered = client.post(f"/jobs/{job.id}/people/discover", headers=headers)
    assert discovered.status_code == 200
    body = discovered.json()
    assert body["status"] in {"complete", "partial"}
    assert body["categories"]["likely_recruiters"]
    assert body["categories"]["potential_hiring_managers"]
    recommendation = body["categories"]["likely_recruiters"][0]
    assert recommendation["limitations"]
    assert recommendation["category_label"] == "Likely recruiter"
    discovery_run = db.query(PeopleDiscoveryRun).filter(
        PeopleDiscoveryRun.job_id == job.id,
    ).order_by(PeopleDiscoveryRun.id.desc()).first()
    assert discovery_run is not None
    assert set(discovery_run.category_diagnostics) == {
        "likely_recruiter", "potential_hiring_manager", "potential_referrer"
    }
    assert discovery_run.category_diagnostics["likely_recruiter"][
        "selected_for_enrichment"
    ] >= 1
    assert discovery_run.company_context["canonical_company_domain"] == "acme.example"
    monkeypatch.setattr(settings, "app_env", "development")
    diagnostics = client.get(f"/jobs/{job.id}/people/diagnostics", headers=headers)
    assert diagnostics.status_code == 200
    assert diagnostics.json()["discovery_run_id"] == discovery_run.id
    assert "categories" in diagnostics.json()
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.get(f"/jobs/{job.id}/people/diagnostics", headers=headers).status_code == 404

    request_count = provider.requests
    cached = client.post(f"/jobs/{job.id}/people/discover", headers=headers)
    assert cached.status_code == 200
    assert provider.requests == request_count

    recommendation_id = recommendation["recommendation_id"]

    class FakeEmailProvider:
        credits = 2

        def __init__(self) -> None:
            self.find_calls = 0

        async def find_work_email(self, request):
            self.find_calls += 1
            return WorkEmailResult(
                status="unknown",
                email="rita@acme.example",
                professional=True,
                provider="mock-email",
            )

        async def verify_work_email(self, email):
            assert email == "rita@acme.example"
            return EmailVerificationResult(
                status="verified", provider="mock-email", verified_at=datetime.now(UTC)
            )

    email_provider = FakeEmailProvider()
    monkeypatch.setattr(service, "get_email_provider", lambda: email_provider)
    email = client.post(
        f"/jobs/{job.id}/people/{recommendation_id}/email", headers=headers
    )
    assert email.status_code == 200
    assert email.json()["status"] == "verified"
    assert email.json()["professional_email"] == "rita@acme.example"
    cached_email = client.post(
        f"/jobs/{job.id}/people/{recommendation_id}/email", headers=headers
    )
    assert cached_email.status_code == 200
    assert email_provider.find_calls == 1

    draft = client.post(
        f"/jobs/{job.id}/people/{recommendation_id}/outreach-draft",
        headers=headers,
        json={"draft_type": "recruiter_introduction"},
    )
    assert draft.status_code == 200
    assert draft.json()["requires_user_review"] is True
    assert draft.json()["sent"] is False
    assert "assigned" not in draft.json()["draft"].lower()

    saved = client.post(
        f"/jobs/{job.id}/people/{recommendation_id}/save", headers=headers
    )
    assert saved.json() == {"saved": True}
    contacted = client.post(
        f"/jobs/{job.id}/people/{recommendation_id}/contacted", headers=headers
    )
    assert contacted.json() == {"contacted": True}

    other_headers = {"Authorization": f"Bearer {create_access_token(str(second.id))}"}
    denied = client.post(
        f"/jobs/{job.id}/people/{recommendation_id}/save", headers=other_headers
    )
    assert denied.status_code == 404

    feedback = client.post(
        f"/jobs/{job.id}/people/{recommendation_id}/feedback",
        headers=headers,
        json={
            "information_correct_rating": "incorrect",
            "incorrect_reason": "Employment is outdated.",
        },
    )
    assert feedback.status_code == 200
    assert feedback.json()["suppressed"] is True


def test_employment_validation_suppresses_newer_conflict_former_and_parent() -> None:
    profile = extract_job_people_profile(_job())
    now = datetime.now(UTC)
    exact = ProviderPerson(
        **_records()[2].model_dump(exclude={"employment_verified_at"}),
        employment_verified_at=now - timedelta(days=10),
    )
    conflict = validate_current_employment(
        exact,
        profile,
        prior_observations=[{
            "company_domain": "new-employer.example",
            "verified_at": now.isoformat(),
        }],
        now=now,
    )
    assert conflict.status == "conflicting_current_employment"
    assert conflict.rejection_codes == ["current_employment_conflict"]

    former = exact.model_copy(update={
        "current_company_name": "Other Company",
        "current_company_domain": "other.example",
        "previous_employers": ["Acme AI"],
    })
    former_result = validate_current_employment(former, profile, now=now)
    assert former_result.status == "former_employee"
    assert former_result.rejection_codes == ["former_employee"]

    related_profile = profile.model_copy(update={
        "company_domain": "commerce.acme.example",
        "parent_company_domain": "acme.example",
    })
    related = exact.model_copy(update={
        "current_company_name": "Acme",
        "current_company_domain": "acme.example",
    })
    related_result = validate_current_employment(related, related_profile, now=now)
    assert related_result.status == "confirmed_related_company"
    assert related_result.rejection_codes == ["related_company_only"]


def test_discovery_persists_conflicting_employer_suppression_diagnostics(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    job = _job()
    user = User(
        email="conflicting-complete-profile@example.com",
        hashed_password=hash_password("password123"),
    )
    db.add_all([job, user])
    db.flush()
    now = datetime.now(UTC)
    person = _records()[0].model_copy(
        update={
            "employment_verified_at": None,
            "provider_record_observed_at": now,
            "employment_source": "complete_person_by_id",
        }
    )
    canonical = service._person_for_provider(db, person)
    canonical.employment_revalidation_required = True
    canonical.employment_conflict_detected_at = now + timedelta(minutes=1)
    db.commit()

    class ConflictProvider(MockPeopleProvider):
        bulk_capability_state = "temporarily_rejected"

        async def search_people(self, query):
            self.requests += 1
            return [person] if query.category == "likely_recruiter" else []

        async def enrich_people(self, people):
            return [person]

    provider = ConflictProvider()
    monkeypatch.setattr(service, "get_people_provider", lambda: provider)
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    monkeypatch.setattr(settings, "people_primary_provider", "apollo")
    monkeypatch.setattr(settings, "people_min_relevance_score", 0)
    monkeypatch.setattr(settings, "people_min_recruiter_relevance", 0)
    monkeypatch.setattr(settings, "people_min_data_confidence", 0)
    single_query = PeopleSearchQuery(
        category="likely_recruiter",
        company_name="Acme AI",
        company_domain="acme.example",
        titles=["Technical Recruiter"],
        limit=1,
    )
    monkeypatch.setattr(
        service,
        "build_category_search_queries",
        lambda _profile, category, **_kwargs: (
            [single_query] if category == "likely_recruiter" else []
        ),
    )
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {create_access_token(str(user.id))}"
    }

    result = client.post(
        f"/jobs/{job.id}/people/discover",
        headers=headers,
    )

    assert result.status_code == 200
    assert result.json()["status"] == "no_reliable_matches"
    assert result.json()["categories"]["likely_recruiters"] == []
    run = db.scalar(
        select(PeopleDiscoveryRun)
        .where(PeopleDiscoveryRun.job_id == job.id)
        .order_by(PeopleDiscoveryRun.id.desc())
    )
    assert run is not None
    recruiter_diagnostics = run.category_diagnostics["likely_recruiter"]
    assert recruiter_diagnostics["employment_validation_outcomes"] == {
        "conflicting_current_employment": 1
    }
    assert recruiter_diagnostics["rejection_reason_counts"][
        "current_employment_conflict"
    ] == 1
    suppressed = db.scalar(
        select(UserJobPeopleRecommendation).where(
            UserJobPeopleRecommendation.job_id == job.id
        )
    )
    assert suppressed is not None
    assert suppressed.suppressed_at is not None
    candidate = db.get(
        JobPeopleCandidate,
        suppressed.job_people_candidate_id,
    )
    assert candidate is not None
    assert (
        candidate.employment_validation_status
        == "conflicting_current_employment"
    )
    assert candidate.recommendation_limitations == [
        "current_employment_conflict"
    ]
    assert service._fresh_candidates(db, job.id, user.id) == []


def test_provider_observation_is_current_listing_not_independent_verification() -> None:
    profile = extract_job_people_profile(_job())
    observed_at = datetime.now(UTC)
    person = _records()[2].model_copy(update={
        "employment_verified_at": None,
        "provider_record_observed_at": observed_at,
        "employment_source": "provider_current_listing",
        "current_role_indicator": True,
    })

    result = validate_current_employment(person, profile, now=observed_at)

    assert result.status == "exact_company_current_but_unverified_freshness"
    assert result.verified_at is None
    assert result.rejection_codes == []


def test_secondary_verification_is_separately_budgeted_and_cached(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.people import service

    user = User(email="verification@example.com", hashed_password=hash_password("password123"))
    job = _job()
    db.add_all([user, job])
    db.flush()
    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="running",
        provider="apollo",
        query_fingerprint="v" * 64,
    )
    db.add(run)
    db.commit()
    profile = extract_job_people_profile(job)
    primary = _records()[2].model_copy(update={
        "provider": "apollo",
        "provider_person_id": "apollo-engineer-1",
        "employment_verified_at": None,
        "provider_record_observed_at": datetime.now(UTC),
        "linkedin_url": "https://www.linkedin.com/in/erin-engineer",
    })

    class FakeSecondaryProvider:
        calls = 0

        async def search_people(self, _query):
            self.calls += 1
            return [primary.model_copy(update={
                "provider": "pdl",
                "provider_person_id": "pdl-engineer-1",
                "provider_record_observed_at": datetime.now(UTC),
            })]

        async def get_usage(self):
            return ProviderUsage(provider="pdl", credits_used=1, requests=1)

    provider = FakeSecondaryProvider()
    monkeypatch.setattr(service, "PDLPeopleProvider", lambda: provider)
    monkeypatch.setattr(settings, "people_employment_verification_daily_credit_budget", 5)
    monkeypatch.setattr(settings, "people_employment_verification_per_user_daily_limit", 5)

    first = asyncio.run(service._secondary_employment_validation(
        db, user.id, job.id, run.id, primary, profile, "potential_referrer"
    ))
    db.commit()
    second = asyncio.run(service._secondary_employment_validation(
        db, user.id, job.id, run.id, primary, profile, "potential_referrer"
    ))

    assert first is not None
    assert first[0].status == "confirmed_exact_company_verified"
    assert first[0].verified_at is not None
    assert second is not None
    assert second[0].status == "confirmed_exact_company_verified"
    assert provider.calls == 1
    verification = db.query(PeopleEmploymentVerificationRun).one()
    assert verification.credits_used == 1
    assert run.provider_credits_used == 0


def test_secondary_verification_conflict_suppresses_candidate(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.people import service

    user = User(
        email="conflict-verification@example.com",
        hashed_password=hash_password("password123"),
    )
    job = _job()
    db.add_all([user, job])
    db.flush()
    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="running",
        provider="apollo",
        query_fingerprint="c" * 64,
    )
    db.add(run)
    db.commit()
    profile = extract_job_people_profile(job)
    primary = _records()[2].model_copy(update={
        "provider": "apollo",
        "provider_person_id": "apollo-conflict-1",
        "employment_verified_at": None,
        "provider_record_observed_at": datetime.now(UTC),
        "linkedin_url": "https://www.linkedin.com/in/erin-engineer",
    })

    class ConflictingSecondaryProvider:
        async def search_people(self, _query):
            return [primary.model_copy(update={
                "provider": "pdl",
                "provider_person_id": "pdl-conflict-1",
                "current_company_name": "Other Company",
                "current_company_domain": "other.example",
            })]

        async def get_usage(self):
            return ProviderUsage(provider="pdl", credits_used=1, requests=1)

    monkeypatch.setattr(service, "PDLPeopleProvider", ConflictingSecondaryProvider)
    monkeypatch.setattr(settings, "people_employment_verification_daily_credit_budget", 5)
    monkeypatch.setattr(settings, "people_employment_verification_per_user_daily_limit", 5)

    result = asyncio.run(service._secondary_employment_validation(
        db, user.id, job.id, run.id, primary, profile, "potential_referrer"
    ))

    assert result is not None
    assert result[0].status == "conflicting_current_employment"
    assert result[0].rejection_codes == ["current_employment_conflict"]


def test_provider_budget_block_is_non_retryable_and_not_persisted(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "people_primary_provider", "apollo")
    user = User(email="budget-block@example.com", hashed_password=hash_password("password123"))
    job = _job()
    db.add_all([user, job])
    db.flush()
    db.add(PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="complete",
        provider="apollo",
        query_fingerprint="old-budget-run",
        provider_credits_used=1,
        completed_at=datetime.now(UTC),
    ))
    db.commit()
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    monkeypatch.setattr(settings, "people_daily_credit_budget", 1)
    monkeypatch.setattr(settings, "people_per_user_daily_limit", 10)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
    before = db.query(PeopleDiscoveryRun).count()

    response = client.post(f"/jobs/{job.id}/people/discover", headers=headers)

    assert response.status_code == 429
    assert response.json()["detail"]["availability_reason"] == "provider_budget_exceeded"
    assert response.json()["detail"]["retryable"] is False
    assert db.query(PeopleDiscoveryRun).count() == before


def test_provider_circuit_open_response_observes_retry_timing(
    db: Session,
) -> None:
    from app.people import service

    user = User(email="circuit-state@example.com", hashed_password=hash_password("password123"))
    job = _job()
    db.add_all([user, job])
    db.flush()
    db.add(PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="provider_unavailable",
        provider="apollo",
        query_fingerprint=service.query_fingerprint(job, "exact"),
        failure_code="provider_circuit_open",
        safe_failure_message=(
            "People search is temporarily paused after repeated provider failures."
        ),
        completed_at=datetime.now(UTC),
    ))
    db.commit()

    payload = recommendations_payload(db, user, job.id)

    assert payload["status"] == "provider_unavailable"
    assert payload["availability_reason"] == "provider_circuit_open"
    assert payload["retry_eligible"] is False
    assert 1 <= payload["retry_after_seconds"] <= 60
    assert payload["retry_eligible_at"] is not None
    assert payload["search_scope"]["refresh_eligible"] is False


def test_provider_schema_error_reopen_and_refresh_are_read_only(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    monkeypatch.setattr(settings, "people_primary_provider", "apollo")
    user = User(
        email="schema-cache@example.com",
        hashed_password=hash_password("password123"),
    )
    job = _job()
    db.add_all([user, job])
    db.flush()
    db.add(PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="provider_unavailable",
        provider="apollo",
        query_fingerprint=service.query_fingerprint(job, "exact"),
        failure_code="provider_schema_error",
        safe_failure_message="The people provider returned an unsupported response.",
        company_context={
            "provider_adapter_version": providers.APOLLO_ENRICHMENT_ADAPTER_VERSION,
            "provider_error_retry_policy": "adapter_version_change_required",
            "retry_eligible_at": None,
        },
        completed_at=datetime.now(UTC),
    ))
    db.commit()
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    provider = MockPeopleProvider([])
    monkeypatch.setattr(service, "get_people_provider", lambda: provider)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    first = client.get(f"/jobs/{job.id}/people", headers=headers)
    reopened = client.get(f"/jobs/{job.id}/people", headers=headers)
    explicit_but_ineligible = client.post(
        f"/jobs/{job.id}/people/discover",
        headers=headers,
    )

    assert first.status_code == 200
    assert reopened.status_code == 200
    assert explicit_but_ineligible.status_code == 200
    assert first.json()["status"] == "provider_unavailable"
    assert first.json()["retry_eligible"] is False
    assert provider.requests == 0
    assert db.query(PeopleDiscoveryRun).filter(
        PeopleDiscoveryRun.job_id == job.id
    ).count() == 1


def test_later_success_supersedes_cached_provider_error(db: Session) -> None:
    from app.people import service

    user = User(
        email="provider-error-superseded@example.com",
        hashed_password=hash_password("password123"),
    )
    job = _job()
    db.add_all([user, job])
    db.flush()
    fingerprint = service.query_fingerprint(job, "exact")
    db.add(PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="provider_unavailable",
        provider="apollo",
        query_fingerprint=fingerprint,
        failure_code="provider_schema_error",
        completed_at=datetime.now(UTC) - timedelta(minutes=1),
    ))
    db.flush()
    db.add(PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="complete",
        provider="apollo",
        query_fingerprint=fingerprint,
        completed_at=datetime.now(UTC),
    ))
    db.commit()

    assert service._current_provider_error_run(
        db,
        job_id=job.id,
        user_id=user.id,
        fingerprint=fingerprint,
    ) is None


def test_transient_provider_error_requires_explicit_retry_after_timestamp(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    monkeypatch.setattr(settings, "people_primary_provider", "pdl")
    user = User(
        email="timed-provider-retry@example.com",
        hashed_password=hash_password("password123"),
    )
    job = _job()
    db.add_all([user, job])
    db.flush()
    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="provider_unavailable",
        provider="pdl",
        query_fingerprint=service.query_fingerprint(job, "exact"),
        failure_code="provider_timeout",
        safe_failure_message="The people search provider took too long to respond.",
        company_context={
            "provider_error_retry_policy": "bounded_explicit_retry",
            "retry_eligible_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
        completed_at=datetime.now(UTC),
    )
    db.add(run)
    db.commit()
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    provider = MockPeopleProvider([])
    monkeypatch.setattr(service, "get_people_provider", lambda: provider)
    single_query = PeopleSearchQuery(
        category="likely_recruiter",
        company_name="Acme AI",
        company_domain="acme.example",
        titles=["Technical Recruiter"],
        limit=1,
    )
    monkeypatch.setattr(
        service,
        "build_category_search_queries",
        lambda _profile, category, **_kwargs: (
            [single_query] if category == "likely_recruiter" else []
        ),
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    too_early = client.post(f"/jobs/{job.id}/people/discover", headers=headers)
    assert too_early.status_code == 200
    assert too_early.json()["retry_eligible"] is False
    assert provider.requests == 0

    run.company_context = {
        **run.company_context,
        "retry_eligible_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    db.commit()
    retried = client.post(f"/jobs/{job.id}/people/discover", headers=headers)

    assert retried.status_code == 200
    assert provider.requests == 1


def _persist_recommendation(
    db: Session,
    *,
    status: str = "confirmed_exact_company_verified",
    revalidation_required: bool = False,
    category: str = "likely_recruiter",
) -> tuple[User, JobPosting, ProfessionalPerson, UserJobPeopleRecommendation]:
    user = User(
        email=f"{category}-{status}@example.com",
        hashed_password=hash_password("password123"),
    )
    job = _job()
    job.external_id = f"{category}-{status}"
    job.hash_for_deduplication = hashlib.sha256(job.external_id.encode()).hexdigest()
    person = ProfessionalPerson(
        canonical_full_name="Rita Recruiter",
        normalized_full_name="rita recruiter",
        current_company_name="Acme AI",
        current_company_domain="acme.example",
        current_title="Senior Technical Recruiter",
        normalized_title="senior technical recruiter",
        employment_last_verified_at=datetime.now(UTC),
        employment_revalidation_required=revalidation_required,
        employment_conflict_detected_at=(
            datetime.now(UTC) if revalidation_required else None
        ),
    )
    db.add_all([user, job, person])
    db.flush()
    candidate = JobPeopleCandidate(
        job_id=job.id,
        person_id=person.id,
        candidate_category=category,
        category_score=88,
        data_confidence=0.9,
        current_employment_confidence=0.95,
        employment_validation_status=status,
        employment_validation_version=EMPLOYMENT_VALIDATION_VERSION,
        employment_validation_checked_at=datetime.now(UTC),
        recommendation_reasons=["Exact current company confirmed."],
        recommendation_limitations=[],
        scoring_version="people-v2:people-title-v2",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(candidate)
    db.flush()
    recommendation = UserJobPeopleRecommendation(
        user_id=user.id,
        job_id=job.id,
        job_people_candidate_id=candidate.id,
        personalized_reasons=[],
        personalized_score=88,
    )
    db.add(recommendation)
    db.commit()
    return user, job, person, recommendation


def test_later_successful_results_supersede_an_old_no_match_state(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    user, job, _person, _recommendation = _persist_recommendation(db)
    fingerprint = service.query_fingerprint(job, "exact")
    db.add_all([
        PeopleDiscoveryRun(
            job_id=job.id,
            user_id=user.id,
            status="complete",
            provider="apollo",
            query_fingerprint=fingerprint,
            records_searched=10,
            records_enriched=0,
            completed_at=datetime.now(UTC) - timedelta(minutes=2),
        ),
        PeopleDiscoveryRun(
            job_id=job.id,
            user_id=user.id,
            status="complete",
            provider="apollo",
            query_fingerprint=fingerprint,
            records_searched=10,
            records_enriched=1,
            completed_at=datetime.now(UTC),
        ),
    ])
    db.commit()
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")

    payload = service.recommendations_payload(db, user, job.id)

    assert payload["status"] == "complete"
    assert len(payload["categories"]["likely_recruiters"]) == 1
    assert payload["categories"]["potential_hiring_managers"] == []
    assert payload["categories"]["potential_referrers"] == []


def test_old_cache_is_hidden_and_email_is_blocked_for_employment_conflict(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.people import service

    user, job, person, recommendation = _persist_recommendation(
        db,
        revalidation_required=True,
    )
    candidate = db.get(
        JobPeopleCandidate, recommendation.job_people_candidate_id
    )
    assert candidate is not None
    candidate.employment_validation_version = "people-employment-v1"
    db.commit()
    assert service._fresh_candidates(db, job.id, user.id) == []

    provider_called = False

    def unexpected_provider():
        nonlocal provider_called
        provider_called = True
        raise AssertionError("Hunter must not be called for an employment conflict")

    monkeypatch.setattr(settings, "people_email_discovery_enabled", True)
    monkeypatch.setattr(service, "get_email_provider", unexpected_provider)
    result = asyncio.run(find_email(
        db, user, job.id, recommendation.id
    ))
    assert result["status"] == "employment_conflict"
    assert result["professional_email"] is None
    assert provider_called is False
    assert person.professional_email_ciphertext is None
    monkeypatch.setattr(settings, "people_outreach_drafting_enabled", True)
    with pytest.raises(HTTPException) as blocked_draft:
        outreach_draft(
            db,
            user,
            job.id,
            recommendation.id,
            OutreachDraftRequest(
                draft_type="recruiter_introduction",
                message_type="linkedin_message",
            ),
        )
    assert blocked_draft.value.status_code == 409


def test_hunter_is_explicit_cached_and_never_displays_risky_email(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.people import service

    user, job, person, recommendation = _persist_recommendation(db)

    class RiskyEmailProvider:
        credits = 2
        find_calls = 0
        verify_calls = 0

        async def find_work_email(self, request):
            self.find_calls += 1
            assert request.company_domain == "acme.example"
            return WorkEmailResult(
                status="unknown",
                email="private-address@acme.example",
                professional=True,
                provider="hunter",
            )

        async def verify_work_email(self, email):
            self.verify_calls += 1
            return EmailVerificationResult(
                status="risky",
                provider="hunter",
                verified_at=datetime.now(UTC),
            )

    provider = RiskyEmailProvider()
    monkeypatch.setattr(settings, "people_email_discovery_enabled", True)
    monkeypatch.setattr(settings, "people_email_daily_credit_budget", 20)
    monkeypatch.setattr(settings, "people_email_per_user_daily_limit", 10)
    monkeypatch.setattr(service, "get_email_provider", lambda: provider)
    assert provider.find_calls == 0

    with caplog.at_level(logging.INFO):
        first = asyncio.run(find_email(db, user, job.id, recommendation.id))
        second = asyncio.run(find_email(db, user, job.id, recommendation.id))

    assert first["status"] == second["status"] == "risky"
    assert first["professional_email"] is None
    assert provider.find_calls == 1
    assert provider.verify_calls == 1
    assert person.professional_email_ciphertext is None
    assert person.professional_email_hash is None
    assert "private-address@" not in caplog.text


def test_hunter_may_run_for_exact_current_unverified_employment(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    user, job, _person, recommendation = _persist_recommendation(
        db,
        status="exact_company_current_but_unverified_freshness",
    )

    class NotFoundEmailProvider:
        calls = 0

        async def find_work_email(self, request):
            self.calls += 1
            assert request.company_domain == "acme.example"
            return WorkEmailResult(
                status="not_found",
                email=None,
                professional=True,
                provider="hunter",
            )

        async def verify_work_email(self, _email):
            raise AssertionError("There is no email to verify")

    provider = NotFoundEmailProvider()
    monkeypatch.setattr(settings, "people_email_discovery_enabled", True)
    monkeypatch.setattr(settings, "people_email_daily_credit_budget", 20)
    monkeypatch.setattr(settings, "people_email_per_user_daily_limit", 10)
    monkeypatch.setattr(service, "get_email_provider", lambda: provider)

    result = asyncio.run(find_email(db, user, job.id, recommendation.id))

    assert result["status"] == "not_found"
    assert provider.calls == 1


def test_hunter_remains_blocked_for_a_former_employee(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    user, job, _person, recommendation = _persist_recommendation(
        db,
        status="former_employee",
    )
    provider_called = False

    def unexpected_provider():
        nonlocal provider_called
        provider_called = True
        raise AssertionError("Hunter must not be called for a former employee")

    monkeypatch.setattr(settings, "people_email_discovery_enabled", True)
    monkeypatch.setattr(service, "get_email_provider", unexpected_provider)

    result = asyncio.run(find_email(db, user, job.id, recommendation.id))

    assert result["status"] == "identity_uncertain"
    assert result["professional_email"] is None
    assert provider_called is False


def test_lowercase_display_names_are_corrected_without_rewriting_mixed_case(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    user, job, person, _recommendation = _persist_recommendation(db)
    person.canonical_full_name = "rita recruiter"
    person.normalized_full_name = "rita recruiter"
    db.commit()
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")

    payload = recommendations_payload(db, user, job.id)

    assert payload["categories"]["likely_recruiters"][0]["full_name"] == (
        "Rita Recruiter"
    )
    assert service._display_name("iPhone McTest") == "iPhone McTest"
    assert service._display_name("single") == "single"


def test_grounded_drafts_differ_by_category_and_respect_linkedin_limit(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "people_outreach_drafting_enabled", True)
    user, job, person, recruiter = _persist_recommendation(db)
    db.add(UserProfile(
        user_id=user.id,
        full_name="Casey Candidate",
        skills=["Python", "Machine Learning"],
    ))
    manager_candidate = JobPeopleCandidate(
        job_id=job.id,
        person_id=person.id,
        candidate_category="potential_hiring_manager",
        category_score=90,
        data_confidence=0.9,
        current_employment_confidence=0.95,
        employment_validation_status="confirmed_exact_company_verified",
        employment_validation_version=EMPLOYMENT_VALIDATION_VERSION,
        employment_validation_checked_at=datetime.now(UTC),
        recommendation_reasons=[],
        recommendation_limitations=[],
        scoring_version="people-v2:people-title-v2",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    referrer_candidate = JobPeopleCandidate(
        job_id=job.id,
        person_id=person.id,
        candidate_category="potential_referrer",
        category_score=85,
        data_confidence=0.9,
        current_employment_confidence=0.95,
        employment_validation_status="confirmed_exact_company_verified",
        employment_validation_version=EMPLOYMENT_VALIDATION_VERSION,
        employment_validation_checked_at=datetime.now(UTC),
        recommendation_reasons=[],
        recommendation_limitations=[],
        scoring_version="people-v2:people-title-v2",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add_all([manager_candidate, referrer_candidate])
    db.flush()
    manager = UserJobPeopleRecommendation(
        user_id=user.id,
        job_id=job.id,
        job_people_candidate_id=manager_candidate.id,
        personalized_reasons=[],
        personalized_score=90,
    )
    referrer = UserJobPeopleRecommendation(
        user_id=user.id,
        job_id=job.id,
        job_people_candidate_id=referrer_candidate.id,
        personalized_reasons=[],
        personalized_score=85,
    )
    db.add_all([manager, referrer])
    db.commit()

    recruiter_draft = outreach_draft(
        db,
        user,
        job.id,
        recruiter.id,
        OutreachDraftRequest(
            draft_type="recruiter_introduction",
            message_type="email",
        ),
    )
    manager_draft = outreach_draft(
        db,
        user,
        job.id,
        manager.id,
        OutreachDraftRequest(
            draft_type="potential_hiring_manager_introduction",
            message_type="linkedin_message",
        ),
    )
    referrer_draft = outreach_draft(
        db,
        user,
        job.id,
        referrer.id,
        OutreachDraftRequest(
            draft_type="referrer_introduction",
            message_type="linkedin_connection_note",
        ),
    )
    assert "recruiting team" in recruiter_draft["body"]
    assert "engineering function" in manager_draft["body"]
    assert "perspective" in referrer_draft["body"]
    assert 90 <= len(recruiter_draft["body"].split()) <= 150
    assert 60 <= len(manager_draft["body"].split()) <= 110
    assert len(referrer_draft["body"]) <= 300
    assert recruiter_draft["assumptions"] == []
    assert manager_draft["assumptions"] == []
    assert "referral_willingness_unconfirmed" in referrer_draft[
        "omitted_uncertain_facts"
    ]
    all_text = " ".join(
        draft["body"]
        for draft in (recruiter_draft, manager_draft, referrer_draft)
    ).lower()
    assert "mutual connection" not in all_text
    assert "will refer" not in all_text
    assert "i hope this message finds you well" not in all_text


def test_feature_disabled_returns_safe_visible_availability_reason(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(email="disabled@example.com", hashed_password=hash_password("password123"))
    job = _job()
    db.add_all([user, job])
    db.commit()
    monkeypatch.setattr(settings, "people_recommendations_enabled", False)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
    response = client.get(f"/jobs/{job.id}/people", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
    assert response.json()["availability_reason"] == "globally_disabled"
    assert client.post(f"/jobs/{job.id}/people/discover", headers=headers).status_code == 404


def test_cohort_exclusion_is_distinct_from_global_disable(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(email="outside@example.com", hashed_password=hash_password("password123"))
    job = _job()
    db.add_all([user, job])
    db.commit()
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "internal")
    monkeypatch.setattr(settings, "people_internal_emails", ["inside@example.com"])
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    response = client.get(f"/jobs/{job.id}/people", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
    assert response.json()["availability_reason"] == "not_in_rollout"
    assert client.post(f"/jobs/{job.id}/people/discover", headers=headers).status_code == 404


def test_configuration_summary_reports_booleans_without_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_email_discovery_enabled", True)
    monkeypatch.setattr(settings, "people_primary_provider", "pdl")
    monkeypatch.setattr(settings, "people_pdl_discovery_enabled", True)
    monkeypatch.setattr(settings, "pdl_api_key", "do-not-log-pdl-key")
    monkeypatch.setattr(settings, "people_data_encryption_key", "do-not-log-encryption-key")
    monkeypatch.setattr(settings, "people_pdl_daily_credit_budget", 100)
    monkeypatch.setattr(settings, "people_pdl_per_user_daily_limit", 5)
    monkeypatch.setattr(settings, "people_rollout_mode", "beta")
    monkeypatch.setattr(settings, "app_env", "development")

    summary = configuration_summary(settings)

    assert summary == {
        "recommendations_enabled": True,
        "email_discovery_enabled": True,
        "primary_provider_configured": True,
        "encryption_key_configured": True,
        "global_budget_configured": True,
        "per_user_budget_configured": True,
        "rollout_mode": "beta",
        "environment": "development",
    }
    assert "do-not-log" not in repr(summary)


def test_all_employment_statuses_fit_widened_schema_and_exact_regression_value(
    db: Session,
) -> None:
    status_column = JobPeopleCandidate.__table__.c.employment_validation_status
    supported = set(get_args(EmploymentValidationStatus))
    longest = "exact_company_current_but_unverified_freshness"

    assert longest in supported
    assert len(longest) == 46
    assert status_column.type.length == 96
    assert max(map(len, supported)) <= status_column.type.length

    _persist_recommendation(db, status=longest)
    db.commit()
    persisted = db.scalar(
        select(JobPeopleCandidate).where(
            JobPeopleCandidate.employment_validation_status == longest
        )
    )
    assert persisted is not None
    assert persisted.employment_validation_status == longest


def test_persistence_usage_migration_is_sequential_and_downgrade_fails_closed() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0024_people_persistence_usage.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "0024_people_persistence_usage"' in migration
    assert 'down_revision = "0023_people_evidence"' in migration
    assert "sa.String(length=96)" in migration
    assert "values longer than 40 characters exist" in migration
    assert "people_provider_operation_usage" in migration


def test_persistence_usage_migration_refuses_unsafe_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0024_people_persistence_usage.py"
    )
    spec = importlib.util.spec_from_file_location(
        "people_persistence_usage_migration",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class UnsafeConnection:
        def scalar(self, _statement):
            return 1

    class GuardedOperations:
        def get_bind(self):
            return UnsafeConnection()

        def __getattr__(self, name):
            raise AssertionError(
                f"destructive downgrade operation ran before guard: {name}"
            )

    monkeypatch.setattr(migration, "op", GuardedOperations())

    with pytest.raises(RuntimeError, match="longer than 40"):
        migration.downgrade()


def test_provider_usage_is_idempotent_private_and_survives_main_rollback(
    db: Session,
) -> None:
    user = User(
        email="durable-usage@example.com",
        hashed_password=hash_password("password123"),
    )
    job = _job()
    db.add_all([user, job])
    db.flush()
    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="running",
        provider="apollo",
        query_fingerprint="durable-usage",
    )
    db.add(run)
    db.commit()
    context = ProviderUsageContext(
        user_id=user.id,
        job_id=job.id,
        discovery_run_id=run.id,
        adapter_version="apollo-enrichment-v3",
    )
    factory = sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        expire_on_commit=False,
    )
    recorder = ProviderUsageRecorder(context, session_factory=factory)
    key = operation_idempotency_key(
        context,
        provider="apollo",
        operation_type="bulk_enrichment",
        ordinal=1,
    )

    assert recorder.start(
        idempotency_key=key,
        provider="apollo",
        operation_type="bulk_enrichment",
    )
    assert not recorder.start(
        idempotency_key=key,
        provider="apollo",
        operation_type="bulk_enrichment",
    )
    recorder.finish(
        idempotency_key=key,
        http_outcome="http_422",
        credits_reported=None,
    )
    original_title = job.title
    job.title = "transaction that will be rolled back"
    db.flush()
    db.rollback()

    row = db.scalar(
        select(PeopleProviderOperationUsage).where(
            PeopleProviderOperationUsage.idempotency_key == key
        )
    )
    assert row is not None
    assert row.request_count == 1
    assert row.http_outcome == "http_422"
    assert row.credits_reported is None
    assert row.credits_estimated == 1
    assert row.credit_status == "estimated"
    assert db.get(JobPosting, job.id).title == original_title
    assert {
        column.name for column in row.__table__.columns
    }.isdisjoint({
        "provider_person_id",
        "name",
        "email",
        "profile_url",
        "provider_payload",
    })


def test_complete_person_usage_is_estimated_once_and_survives_rollback(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = "cdcdcdcdcdcdcdcdcdcdcdcd"
    user = User(
        email="complete-usage@example.com",
        hashed_password=hash_password("password123"),
    )
    job = _job()
    db.add_all([user, job])
    db.flush()
    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="running",
        provider="apollo",
        query_fingerprint="complete-usage",
    )
    db.add(run)
    db.commit()

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "person": {
                        "id": identifier,
                        "name": "Synthetic Person",
                        "title": "Engineering Manager",
                        "organization": {
                            "name": "Example Company",
                            "primary_domain": "example.test",
                        },
                    }
                },
            )

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = ApolloPeopleProvider("synthetic-test-key")
    provider.configure_usage(
        ProviderUsageContext(
            user_id=user.id,
            job_id=job.id,
            discovery_run_id=run.id,
            adapter_version=providers.APOLLO_ENRICHMENT_ADAPTER_VERSION,
        ),
        session_factory=sessionmaker(
            bind=db.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        ),
    )

    assert asyncio.run(
        provider.complete_person_by_id(
            PersonEnrichmentRequest(provider_person_id=identifier)
        )
    ) is not None
    job.title = "rolled back"
    db.flush()
    db.rollback()

    usage = db.scalar(
        select(PeopleProviderOperationUsage).where(
            PeopleProviderOperationUsage.discovery_run_id == run.id,
            PeopleProviderOperationUsage.operation_type
            == "complete_person_by_id",
        )
    )
    assert usage is not None
    assert usage.http_outcome == "http_200"
    assert usage.credits_reported is None
    assert usage.credits_estimated == 1
    assert usage.credit_status == "estimated"
    assert usage.budget_units == 1


def test_unknown_reconciled_usage_is_not_zero_and_counts_against_budget(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    monkeypatch.setattr(settings, "people_primary_provider", "apollo")
    user = User(
        email="reconciled-budget@example.com",
        hashed_password=hash_password("password123"),
    )
    job = _job()
    db.add_all([user, job])
    db.flush()
    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="persistence_error",
        provider="apollo",
        query_fingerprint="reconciled-usage",
        failure_code="recommendation_commit_failed",
    )
    db.add(run)
    db.commit()
    context = ProviderUsageContext(
        user_id=user.id,
        job_id=job.id,
        discovery_run_id=run.id,
        adapter_version="apollo-enrichment-v3",
    )
    factory = sessionmaker(bind=db.get_bind(), autoflush=False)

    first = reconcile_unknown_operations(
        context=context,
        provider="apollo",
        operation_counts={"people_search": 2},
        safe_http_outcomes={"people_search": "http_200"},
        session_factory=factory,
    )
    second = reconcile_unknown_operations(
        context=context,
        provider="apollo",
        operation_counts={"people_search": 2},
        safe_http_outcomes={"people_search": "http_200"},
        session_factory=factory,
    )

    assert first == 2
    assert second == 0
    rows = db.scalars(select(PeopleProviderOperationUsage)).all()
    assert len(rows) == 2
    assert all(row.credits_reported is None for row in rows)
    assert all(row.credits_estimated is None for row in rows)
    assert all(row.credit_status == "unknown" for row in rows)
    assert sum(row.budget_units for row in rows) == 2

    monkeypatch.setattr(settings, "people_daily_credit_budget", 2)
    monkeypatch.setattr(settings, "people_per_user_daily_limit", 10)
    with pytest.raises(HTTPException) as blocked:
        service._budget_check(db, user.id)
    assert blocked.value.detail["availability_reason"] == "provider_budget_exceeded"


def test_bulk_capability_temporarily_skips_after_repeated_request_level_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = "aaaaaaaaaaaaaaaaaaaaaaaa"
    calls: list[str] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            calls.append(url)
            if url.endswith("/bulk_match"):
                return httpx.Response(
                    422,
                    request=httpx.Request(method, url),
                    json={"message": "not retained"},
                )
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "person": {
                        "id": identifier,
                        "name": "Private Person",
                        "title": "Technical Recruiter",
                        "organization": {
                            "name": "Acme",
                            "primary_domain": "acme.example",
                        },
                    }
                },
            )

    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(settings, "people_apollo_bulk_rejection_threshold", 2)
    clear_local_bulk_capabilities()
    request = [PersonEnrichmentRequest(provider_person_id=identifier)]

    first = ApolloPeopleProvider("synthetic-test-key")
    assert len(asyncio.run(first.enrich_people(request))) == 1
    account_scope = first._bulk_account_scope
    assert (
            bulk_capability_state(
                "apollo", "apollo-enrichment-v4", account_scope
            )
        == "unknown"
    )
    second = ApolloPeopleProvider("synthetic-test-key")
    assert len(asyncio.run(second.enrich_people(request))) == 1
    assert (
            bulk_capability_state(
                "apollo", "apollo-enrichment-v4", account_scope
            )
        == "temporarily_rejected"
    )
    third = ApolloPeopleProvider("synthetic-test-key")
    assert len(asyncio.run(third.enrich_people(request))) == 1

    assert sum(url.endswith("/bulk_match") for url in calls) == 2
    assert sum("/api/v1/people/" in url and not url.endswith("/bulk_match") for url in calls) == 1
    assert third.enrichment_safe_metrics["bulk_capability_skipped"] == 1
    assert (
        bulk_capability_state(
            "apollo", "apollo-enrichment-v4", account_scope
        )
        == "temporarily_rejected"
    )


def test_bulk_422_successful_single_fallback_persists_normally(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    identifier = "aaaaaaaaaaaaaaaaaaaaaaaa"
    job = _job()
    user = User(
        email="fallback-persistence@example.com",
        hashed_password=hash_password("password123"),
    )
    db.add_all([job, user])
    db.commit()
    query = PeopleSearchQuery(
        category="likely_recruiter",
        company_name="Acme AI",
        company_domain="acme.example",
        titles=["Technical Recruiter"],
        limit=1,
    )

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            person = {
                "id": identifier,
                "name": "Private Person",
                "title": "Technical Recruiter",
                "linkedin_url": "https://www.linkedin.com/in/private-person",
                "organization": {
                    "name": "Acme AI",
                    "primary_domain": "acme.example",
                },
            }
            if url.endswith("/api_search"):
                return httpx.Response(
                    200,
                    request=httpx.Request(method, url),
                    json={"people": [person]},
                )
            if url.endswith("/bulk_match"):
                return httpx.Response(
                    422,
                    request=httpx.Request(method, url),
                    json={"message": "not retained"},
                )
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={"person": person, "credits_consumed": 1},
            )

    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(
        service,
        "build_category_search_queries",
        lambda _profile, category, **_kwargs: (
            [query] if category == "likely_recruiter" else []
        ),
    )
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_primary_provider", "apollo")
    monkeypatch.setattr(settings, "people_apollo_discovery_enabled", True)
    monkeypatch.setattr(settings, "people_apollo_diagnostic_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "internal")
    monkeypatch.setattr(
        settings,
        "people_internal_emails",
        [user.email],
    )
    monkeypatch.setattr(settings, "apollo_api_key", "synthetic-test-key")
    monkeypatch.setattr(settings, "people_min_relevance_score", 0)
    monkeypatch.setattr(settings, "people_min_recruiter_relevance", 0)
    monkeypatch.setattr(settings, "people_min_data_confidence", 0)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    response = client.post(f"/jobs/{job.id}/people/discover", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "complete"
    assert len(response.json()["categories"]["likely_recruiters"]) == 1
    run = db.scalar(
        select(PeopleDiscoveryRun)
        .where(PeopleDiscoveryRun.job_id == job.id)
        .order_by(PeopleDiscoveryRun.id.desc())
    )
    assert run.status == "complete"
    assert run.records_enriched == 1
    assert run.company_context["provider_enrichment_safe_metrics"][
        "bulk_payload_validation_failed"
    ] == 1
    assert db.scalar(
        select(JobPeopleCandidate).where(
            JobPeopleCandidate.job_id == job.id
        )
    ) is not None


def test_database_failure_is_cached_as_persistence_error_and_usage_survives(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    user = User(
        email="persistence-error@example.com",
        hashed_password=hash_password("password123"),
    )
    job = _job()
    db.add_all([user, job])
    db.commit()

    class DurableMock(MockPeopleProvider):
        def configure_usage(self, context, *, session_factory):
            self.context = context
            self.recorder = ProviderUsageRecorder(
                context,
                session_factory=session_factory,
            )
            self.ordinal = 0

        def record(self, operation_type: str) -> None:
            self.ordinal += 1
            key = operation_idempotency_key(
                self.context,
                provider="mock",
                operation_type=operation_type,
                ordinal=self.ordinal,
            )
            assert self.recorder.start(
                idempotency_key=key,
                provider="mock",
                operation_type=operation_type,
            )
            self.recorder.finish(
                idempotency_key=key,
                http_outcome="http_200",
                credits_reported=0,
            )

        async def search_people(self, query):
            self.record("people_search")
            return await super().search_people(query)

        async def enrich_people(self, people):
            self.record("single_person_enrichment")
            return await super().enrich_people(people)

    provider = DurableMock(_records())
    monkeypatch.setattr(service, "get_people_provider", lambda: provider)
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    monkeypatch.setattr(settings, "people_primary_provider", "mock")
    monkeypatch.setattr(settings, "people_min_relevance_score", 0)
    monkeypatch.setattr(settings, "people_min_recruiter_relevance", 0)
    monkeypatch.setattr(settings, "people_min_manager_relevance", 0)
    monkeypatch.setattr(settings, "people_min_referrer_relevance", 0)
    monkeypatch.setattr(settings, "people_min_data_confidence", 0)

    def fail_persistence(*_args, **_kwargs):
        raise SQLAlchemyError("synthetic storage failure")

    monkeypatch.setattr(service, "_person_for_provider", fail_persistence)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    response = client.post(f"/jobs/{job.id}/people/discover", headers=headers)
    provider_requests = provider.requests
    reopened = client.get(f"/jobs/{job.id}/people", headers=headers)
    refreshed = client.get(f"/jobs/{job.id}/people", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "persistence_error"
    assert (
        response.json()["availability_reason"]
        == "recommendation_commit_failed"
    )
    assert response.json()["categories"] == {
        "likely_recruiters": [],
        "potential_hiring_managers": [],
        "potential_referrers": [],
    }
    assert reopened.json()["status"] == "persistence_error"
    assert refreshed.json()["status"] == "persistence_error"
    assert provider.requests == provider_requests
    run = db.scalar(
        select(PeopleDiscoveryRun)
        .where(PeopleDiscoveryRun.job_id == job.id)
        .order_by(PeopleDiscoveryRun.id.desc())
    )
    assert run.status == "persistence_error"
    assert run.failure_code == "recommendation_commit_failed"
    assert run.company_context["pipeline_outcomes"]["persistence"] == "failed"
    assert db.scalar(
        select(func.count(PeopleProviderOperationUsage.id)).where(
            PeopleProviderOperationUsage.discovery_run_id == run.id
        )
    ) > 0


def test_pdl_search_is_the_profile_operation_and_reuses_normalized_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **kwargs):
            calls.append((method, url, kwargs))
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "data": [
                        {
                            "id": "pdl-record-1",
                            "full_name": "Private Person",
                            "job_title": "Engineering Manager",
                            "job_title_role": "engineering",
                            "job_title_levels": ["manager"],
                            "job_company_name": "Acme AI",
                            "job_company_website": (
                                "https://www.acme.example"
                            ),
                            "job_last_changed": "2026-06-01T00:00:00Z",
                            "job_last_verified": "2026-06-15T00:00:00Z",
                            "linkedin_url": (
                                "https://www.linkedin.com/in/private-person"
                            ),
                            "experience": [
                                {
                                    "company": {"name": "Acme AI"},
                                    "end_date": None,
                                },
                                {
                                    "company": {"name": "Former Co"},
                                    "end_date": "2024-01",
                                },
                            ],
                        }
                    ]
                },
            )

    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(settings, "people_pdl_results_per_query", 10)
    provider = providers.PDLPeopleProvider(api_key="synthetic-test-key")
    query = PeopleSearchQuery(
        category="potential_hiring_manager",
        company_name="Acme AI",
        company_domain="acme.example",
        titles=["Engineering Manager"],
        seniorities=["manager"],
        limit=20,
    )

    searched = asyncio.run(provider.search_people(query))
    enriched = asyncio.run(
        provider.enrich_people(
            [
                PersonEnrichmentRequest(
                    provider_person_id="pdl-record-1",
                    category="potential_hiring_manager",
                )
            ]
        )
    )

    assert len(calls) == 1
    method, url, kwargs = calls[0]
    assert method == "POST"
    assert url == "https://api.peopledatalabs.com/v5/person/search"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["json"]["size"] == 10
    assert "job_company_website='acme.example'" in kwargs["json"]["sql"]
    assert "job_company_name='Acme AI'" in kwargs["json"]["sql"]
    assert len(searched) == len(enriched) == 1
    assert enriched[0].provider_person_id == searched[0].provider_person_id
    assert enriched[0].current_company_domain == "acme.example"
    assert enriched[0].employment_verified_at is None
    assert enriched[0].provider_employment_updated_at == datetime(
        2026, 6, 15, tzinfo=UTC
    )
    assert enriched[0].previous_employers == ["Former Co"]
    assert provider.requests == 1
    assert provider.credits == 1


def test_pdl_search_never_returns_more_than_its_bounded_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "data": [
                        {
                            "id": f"bounded-record-{index}",
                            "full_name": f"Synthetic Person {index}",
                            "job_title": "Software Engineer",
                            "job_company_name": "Acme AI",
                            "job_company_website": "acme.example",
                        }
                        for index in range(20)
                    ]
                },
            )

    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeClient(),
    )
    provider = providers.PDLPeopleProvider(api_key="synthetic-test-key")
    results = asyncio.run(
        provider.search_people(
            PeopleSearchQuery(
                category="potential_referrer",
                company_name="Acme AI",
                company_domain="acme.example",
                titles=["Software Engineer"],
                limit=4,
            )
        )
    )

    assert len(results) == 4
    assert provider.last_search_raw_count == 20
    assert provider.last_search_normalized_count == 4


def test_normal_discovery_cannot_select_apollo_without_diagnostic_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "people_primary_provider", "apollo")
    monkeypatch.setattr(settings, "people_apollo_discovery_enabled", False)
    monkeypatch.setattr(settings, "people_apollo_diagnostic_enabled", False)
    monkeypatch.setattr(settings, "people_rollout_mode", "internal")

    with pytest.raises(ProviderUnavailable) as exc_info:
        providers.get_people_provider()

    assert exc_info.value.reason == "provider_not_configured"
    assert exc_info.value.provider == "apollo"


def test_same_name_company_and_title_do_not_merge_without_strong_identity() -> None:
    from app.people.service import deduplicate

    base = _records()[0].model_copy(
        update={"provider": "pdl", "linkedin_url": None}
    )
    first = base.model_copy(update={"provider_person_id": "pdl-one"})
    second = base.model_copy(update={"provider_person_id": "pdl-two"})

    assert len(deduplicate([first, second])) == 2


def test_clear_recruiter_stays_in_recruiter_despite_cross_category_score() -> None:
    from app.people import service

    person = _records()[0].model_copy(
        update={
            "provider": "pdl",
            "provider_person_id": "pdl-one-category",
        }
    )
    candidates = {
        "likely_recruiter": [
            (70.0, "likely_recruiter", person, None, None)
        ],
        "potential_hiring_manager": [],
        "potential_referrer": [
            (85.0, "potential_referrer", person, None, None)
        ],
    }

    assigned = service._strongest_category_candidates(candidates)

    assert assigned["likely_recruiter"] == [
        (70.0, "likely_recruiter", person, None, None)
    ]
    assert assigned["potential_hiring_manager"] == []
    assert assigned["potential_referrer"] == []


def test_clear_individual_contributor_stays_in_referral_category() -> None:
    from app.people import service

    person = _records()[2].model_copy(
        update={
            "provider": "pdl",
            "provider_person_id": "pdl-referral-category",
        }
    )
    assigned = service._strongest_category_candidates(
        {
            "likely_recruiter": [],
            "potential_hiring_manager": [
                (90.0, "potential_hiring_manager", person, None, None)
            ],
            "potential_referrer": [
                (75.0, "potential_referrer", person, None, None)
            ],
        }
    )

    assert assigned["potential_hiring_manager"] == []
    assert len(assigned["potential_referrer"]) == 1


def test_pdl_primary_discovery_persists_categories_without_apollo_or_hunter(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.people import service

    job = _job()
    user = User(
        email="pdl-primary-test@example.com",
        hashed_password=hash_password("password123"),
    )
    db.add_all([job, user])
    db.commit()
    provider_calls: list[str] = []
    provider_sizes: list[int] = []
    private_ids = {
        "Technical Recruiter": "pdl-private-recruiter",
        "Engineering Manager": "pdl-private-manager",
        "Software Engineer": "pdl-private-referrer",
    }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **kwargs):
            provider_calls.append(url)
            provider_sizes.append(kwargs["json"]["size"])
            sql = kwargs["json"]["sql"]
            matched_titles = [
                value
                for value in private_ids
                if f"'{value}'" in sql
            ]
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "data": [
                        {
                            "id": private_ids[title],
                            "full_name": f"Synthetic {title}",
                            "job_title": title,
                            "job_title_role": (
                                "human_resources"
                                if title == "Technical Recruiter"
                                else "engineering"
                            ),
                            "job_title_levels": (
                                ["manager"]
                                if title == "Engineering Manager"
                                else ["senior"]
                            ),
                            "job_company_name": "Acme AI",
                            "job_company_website": "acme.example",
                            "job_last_changed": datetime.now(UTC).isoformat(),
                            "linkedin_url": (
                                "https://www.linkedin.com/in/synthetic-person"
                                if title == "Technical Recruiter"
                                else "https://profiles.invalid/not-allowed"
                            ),
                        }
                        for title in matched_titles
                    ]
                },
            )

    queries = {
        "likely_recruiter": PeopleSearchQuery(
            category="likely_recruiter",
            company_name="Acme AI",
            company_domain="acme.example",
            titles=["Technical Recruiter"],
            limit=1,
        ),
        "potential_hiring_manager": PeopleSearchQuery(
            category="potential_hiring_manager",
            company_name="Acme AI",
            company_domain="acme.example",
            titles=["Engineering Manager"],
            seniorities=["manager"],
            limit=1,
        ),
        "potential_referrer": PeopleSearchQuery(
            category="potential_referrer",
            company_name="Acme AI",
            company_domain="acme.example",
            titles=["Software Engineer"],
            limit=1,
        ),
    }
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(
        service,
        "build_category_search_queries",
        lambda _profile, category, **_kwargs: [queries[category]],
    )
    monkeypatch.setattr(
        providers,
        "ApolloPeopleProvider",
        lambda *_args, **_kwargs: pytest.fail(
            "normal PDL discovery constructed Apollo"
        ),
    )
    monkeypatch.setattr(
        service,
        "get_email_provider",
        lambda: pytest.fail("People discovery constructed Hunter"),
    )
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_rollout_mode", "all")
    monkeypatch.setattr(settings, "people_primary_provider", "pdl")
    monkeypatch.setattr(settings, "people_pdl_discovery_enabled", True)
    monkeypatch.setattr(settings, "pdl_api_key", "synthetic-test-key")
    monkeypatch.setattr(settings, "people_pdl_daily_credit_budget", 100)
    monkeypatch.setattr(settings, "people_pdl_per_user_daily_limit", 20)
    monkeypatch.setattr(settings, "people_email_discovery_enabled", False)
    monkeypatch.setattr(settings, "people_pdl_fallback_enabled", False)
    monkeypatch.setattr(settings, "people_network_matching_enabled", False)
    monkeypatch.setattr(
        settings,
        "people_employment_secondary_verification_enabled",
        False,
    )
    monkeypatch.setattr(settings, "people_min_relevance_score", 0)
    monkeypatch.setattr(settings, "people_min_recruiter_relevance", 0)
    monkeypatch.setattr(settings, "people_min_manager_relevance", 0)
    monkeypatch.setattr(settings, "people_min_referrer_relevance", 0)
    monkeypatch.setattr(settings, "people_min_data_confidence", 0)
    db.add(
        PeopleDiscoveryRun(
            job_id=job.id,
            user_id=user.id,
            status="complete",
            provider="apollo",
            query_fingerprint="obsolete-apollo-no-match",
            company_context={
                "provider_adapter_version": (
                    providers.APOLLO_ENRICHMENT_ADAPTER_VERSION
                ),
                "discovery_strategy": "exact",
            },
            completed_at=datetime.now(UTC),
        )
    )
    db.commit()
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {create_access_token(str(user.id))}"
    }
    caplog.set_level(logging.INFO)

    stale = client.get(f"/jobs/{job.id}/people", headers=headers)
    assert stale.json()["status"] == "stale"
    assert stale.json()["warnings"] == [
        "Contact discovery has been upgraded. Refresh to check again."
    ]
    assert provider_calls == []
    discovered = client.post(
        f"/jobs/{job.id}/people/discover",
        headers=headers,
    )
    calls_after_discovery = len(provider_calls)
    reopened = client.get(f"/jobs/{job.id}/people", headers=headers)
    refreshed = client.get(f"/jobs/{job.id}/people", headers=headers)
    repeated_post = client.post(
        f"/jobs/{job.id}/people/discover",
        headers=headers,
    )

    assert discovered.status_code == 200
    assert discovered.json()["status"] == "complete"
    assert {
        category: len(discovered.json()["categories"][category])
        for category in (
            "likely_recruiters",
            "potential_hiring_managers",
            "potential_referrers",
        )
    } == {
        "likely_recruiters": 1,
        "potential_hiring_managers": 1,
        "potential_referrers": 1,
    }
    assert discovered.json()["categories"]["likely_recruiters"][0][
        "professional_profile_url"
    ] == "https://www.linkedin.com/in/synthetic-person"
    assert discovered.json()["categories"]["potential_hiring_managers"][0][
        "professional_profile_url"
    ] is None
    assert len(provider_calls) == calls_after_discovery == 3
    assert provider_sizes == [4, 4, 8]
    assert sum(provider_sizes) == 16
    assert all(
        url == "https://api.peopledatalabs.com/v5/person/search"
        for url in provider_calls
    )
    assert reopened.json()["status"] == "complete"
    assert refreshed.json()["status"] == "complete"
    assert repeated_post.json()["status"] == "complete"
    usage_rows = db.scalars(
        select(PeopleProviderOperationUsage).where(
            PeopleProviderOperationUsage.provider == "pdl"
        )
    ).all()
    assert len(usage_rows) == 3
    assert all(row.credits_reported is None for row in usage_rows)
    assert all(row.credits_estimated == 1 for row in usage_rows)
    assert all(row.credit_status == "estimated" for row in usage_rows)
    latest_run = db.scalar(
        select(PeopleDiscoveryRun)
        .where(
            PeopleDiscoveryRun.job_id == job.id,
            PeopleDiscoveryRun.provider == "pdl",
        )
        .order_by(PeopleDiscoveryRun.id.desc())
    )
    assert latest_run is not None
    assert latest_run.category_diagnostics is not None
    for category in (
        "likely_recruiter",
        "potential_hiring_manager",
        "potential_referrer",
    ):
        category_diagnostics = latest_run.category_diagnostics[category]
        assert category_diagnostics["query_executed"] is True
        assert category_diagnostics["provider_call_count"] == 1
        assert category_diagnostics["raw_search_result_count"] == 1
        assert category_diagnostics["normalized_profile_count"] == 1
        assert category_diagnostics["exact_company_current_profiles"] == 1
        assert category_diagnostics["accepted"] == 1
    rendered_logs = caplog.text
    assert all(value not in rendered_logs for value in private_ids.values())
    assert "Synthetic Technical Recruiter" not in rendered_logs


def test_pdl_budget_blocks_before_another_provider_operation(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    job = _job()
    user = User(
        email="pdl-budget-test@example.com",
        hashed_password=hash_password("password123"),
    )
    db.add_all([job, user])
    db.flush()
    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="complete",
        provider="pdl",
        query_fingerprint="pdl-budget-history",
    )
    db.add(run)
    db.commit()
    context = ProviderUsageContext(
        user_id=user.id,
        job_id=job.id,
        discovery_run_id=run.id,
        adapter_version=providers.PDL_DISCOVERY_STRATEGY_VERSION,
    )
    reconcile_unknown_operations(
        context=context,
        provider="pdl",
        operation_counts={"people_search": 2},
        safe_http_outcomes={"people_search": "http_200"},
        session_factory=sessionmaker(
            bind=db.get_bind(),
            autoflush=False,
        ),
    )
    monkeypatch.setattr(settings, "people_primary_provider", "pdl")
    monkeypatch.setattr(settings, "people_pdl_daily_credit_budget", 2)
    monkeypatch.setattr(settings, "people_pdl_per_user_daily_limit", 10)

    with pytest.raises(HTTPException) as blocked:
        service._budget_check(db, user.id)

    assert blocked.value.status_code == 429
    assert blocked.value.detail["availability_reason"] == (
        "provider_budget_exceeded"
    )


def test_uncertain_pdl_failure_records_unknown_credits_not_zero(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    user = User(
        email="pdl-unknown-credit@example.com",
        hashed_password=hash_password("password123"),
    )
    db.add_all([job, user])
    db.flush()
    run = PeopleDiscoveryRun(
        job_id=job.id,
        user_id=user.id,
        status="running",
        provider="pdl",
        query_fingerprint="pdl-unknown-credit",
    )
    db.add(run)
    db.commit()

    class ForbiddenClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method: str, url: str, **_kwargs):
            return httpx.Response(
                403,
                request=httpx.Request(method, url),
                json={"status": 403},
            )

    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **_kwargs: ForbiddenClient(),
    )
    provider = providers.PDLPeopleProvider(api_key="synthetic-test-key")
    provider.configure_usage(
        ProviderUsageContext(
            user_id=user.id,
            job_id=job.id,
            discovery_run_id=run.id,
            adapter_version=providers.PDL_DISCOVERY_STRATEGY_VERSION,
        ),
        session_factory=sessionmaker(
            bind=db.get_bind(),
            autoflush=False,
        ),
    )
    query = PeopleSearchQuery(
        category="likely_recruiter",
        company_name="Acme AI",
        company_domain="acme.example",
        titles=["Technical Recruiter"],
        limit=1,
    )

    with pytest.raises(ProviderUnavailable) as unavailable:
        asyncio.run(provider.search_people(query))

    assert unavailable.value.reason == "provider_forbidden"
    usage = db.scalar(
        select(PeopleProviderOperationUsage).where(
            PeopleProviderOperationUsage.discovery_run_id == run.id
        )
    )
    assert usage.credit_status == "unknown"
    assert usage.credits_reported is None
    assert usage.credits_estimated is None
    assert usage.budget_units == 1
