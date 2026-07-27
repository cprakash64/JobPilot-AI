from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
    ProfessionalPerson,
    User,
    UserJobPeopleRecommendation,
    UserProfile,
)
from app.people import providers
from app.people.employment_validation import (
    EMPLOYMENT_VALIDATION_VERSION,
    validate_current_employment,
)
from app.people.feature_flags import configuration_summary
from app.people.intelligence import (
    expand_titles,
    extract_job_people_profile,
    resolve_company_identity,
    validate_company_domain,
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
    job.title = "Agentic Software Engineer Intern"
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


def test_secondary_verification_flag_invalidates_only_unresolved_run_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.people import service

    job = _job()
    monkeypatch.setattr(
        settings, "people_employment_secondary_verification_enabled", False
    )
    without_secondary = service.query_fingerprint(job)
    monkeypatch.setattr(
        settings, "people_employment_secondary_verification_enabled", True
    )
    with_secondary = service.query_fingerprint(job)

    assert without_secondary != with_secondary


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
                        "id": "apollo-person-1",
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
    assert rows[0].employment_verified_at is None
    assert rows[0].employment_source == "provider_current_listing"


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
                    "id": "apollo-person-1",
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
        PersonEnrichmentRequest(provider_person_id="apollo-person-1")
    ]))
    assert len(enriched) == 1
    assert calls[0][0:2] == (
        "POST", "https://api.apollo.io/api/v1/people/bulk_match"
    )
    assert calls[0][2]["json"] == {"details": [{"id": "apollo-person-1"}]}

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


def test_provider_circuit_open_response_includes_retry_eligibility(
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
    assert payload["retry_eligible"] is True
    assert payload["retry_after_seconds"] == 60


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
    assert service._fresh_candidates(db, job.id) == []

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
    monkeypatch.setattr(settings, "people_primary_provider", "apollo")
    monkeypatch.setattr(settings, "apollo_api_key", "do-not-log-apollo-key")
    monkeypatch.setattr(settings, "people_data_encryption_key", "do-not-log-encryption-key")
    monkeypatch.setattr(settings, "people_daily_credit_budget", 100)
    monkeypatch.setattr(settings, "people_per_user_daily_limit", 5)
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
