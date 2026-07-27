from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import CompanyBranding, JobPosting, PeopleDiscoveryRun, User
from app.people import providers
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
    PeopleSearchQuery,
    PersonEnrichmentRequest,
    ProviderPerson,
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
