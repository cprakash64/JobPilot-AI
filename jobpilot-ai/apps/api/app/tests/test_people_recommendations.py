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
from app.models.entities import JobPosting, User
from app.people.feature_flags import configuration_summary
from app.people.intelligence import (
    expand_titles,
    extract_job_people_profile,
    validate_company_domain,
)
from app.people import providers
from app.people.providers import ApolloPeopleProvider, MockPeopleProvider, ProviderUnavailable
from app.people.schemas import (
    EmailVerificationResult,
    PeopleSearchQuery,
    PersonEnrichmentRequest,
    ProviderPerson,
    WorkEmailResult,
)
from app.people.scoring import confidence, explanations, score_candidate
from app.people.security import is_professional_email, safe_profile_url


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
        limit=1,
    )))

    assert len(rows) == 1
    method, url, kwargs = calls[0]
    assert method == "POST"
    assert url == "https://api.apollo.io/api/v1/mixed_people/api_search"
    assert kwargs["json"]["q_organization_domains_list"] == ["acme.example"]
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
