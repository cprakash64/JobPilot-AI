"""The two scenarios observed live, end to end against a mocked provider.

1. A Toshiba job carrying a legacy INVALID_INPUT run must not display that
   result, and a fresh search under the current contract must report the
   truthful no-match state.
2. An L3Harris job with manager contacts must produce an editable draft and
   carry the verified LinkedIn URL and email availability for handoff.

No provider account is contacted and no credits are spent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.models.entities import (
    JobPosting,
    PeopleDiscoveryRun,
    User,
    UserProfile,
)
from app.people import circuit, pdl_company, providers
from app.people.schemas import OutreachDraftRequest
from app.people.service import (
    CONTRACT_VERSION_KEY,
    PEOPLE_SEARCH_CONTRACT_VERSION,
    discover,
    outreach_draft,
    query_fingerprint,
    recommendations_payload,
)

TOSHIBA_DOMAIN = "toshibacommerce.example"
L3HARRIS_DOMAIN = "l3harris.example"
LINKEDIN_URL = "https://www.linkedin.com/in/morgan-manager"

PDL_NOT_FOUND = {
    "status": 404,
    "error": {
        "type": "not_found",
        "message": "No records were found matching your request",
    },
}


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


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "people_primary_provider", "pdl")
    monkeypatch.setattr(settings, "people_pdl_discovery_enabled", True)
    monkeypatch.setattr(settings, "pdl_api_key", "test-key-not-a-real-credential")
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_outreach_drafting_enabled", True)
    monkeypatch.setattr(settings, "people_pdl_daily_credit_budget", 500)
    monkeypatch.setattr(settings, "people_pdl_per_user_daily_limit", 200)
    monkeypatch.setattr(settings, "people_min_relevance_score", 0)
    monkeypatch.setattr(settings, "people_min_recruiter_relevance", 0)
    monkeypatch.setattr(settings, "people_min_manager_relevance", 0)
    monkeypatch.setattr(settings, "people_min_referrer_relevance", 0)
    monkeypatch.setattr(settings, "people_min_data_confidence", 0)
    circuit.clear_local_circuits()
    pdl_company.clear_local_pdl_companies()


def _user(db: Session) -> User:
    user = User(email="live@example.com", hashed_password=hash_password("password123"))
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserProfile(user_id=user.id, full_name="Sam Candidate", skills=["Python"]))
    db.commit()
    return user


def _job(db: Session, *, company: str, domain: str, external_id: str) -> JobPosting:
    job = JobPosting(
        external_id=external_id,
        title="Software Engineer",
        company=company,
        company_domain=domain,
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


class _Transport:
    """Toshiba matches nobody; L3Harris returns one engineering manager."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, method: str, url: str, **kwargs):
        request = httpx.Request(method, url)
        if url.endswith("/company/enrich"):
            website = str((kwargs.get("params") or {}).get("website") or "")
            self.calls.append(f"enrich:{website}")
            if website not in {TOSHIBA_DOMAIN, L3HARRIS_DOMAIN}:
                return httpx.Response(404, request=request, json=PDL_NOT_FOUND)
            return httpx.Response(
                200,
                request=request,
                json={
                    "status": 200,
                    "likelihood": 9,
                    "id": f"pdl-{website}",
                    "name": website.split(".")[0].title(),
                    "website": website,
                },
            )
        sql = str((kwargs.get("json") or {}).get("sql", ""))
        self.calls.append("search")
        if f"pdl-{L3HARRIS_DOMAIN}" not in sql:
            # Toshiba: PDL answers the query and nobody matches.
            return httpx.Response(404, request=request, json=PDL_NOT_FOUND)
        if "'manager'" not in sql:
            return httpx.Response(404, request=request, json=PDL_NOT_FOUND)
        return httpx.Response(
            200,
            request=request,
            json={
                "data": [
                    {
                        "id": "pdl-morgan",
                        "full_name": "Morgan Manager",
                        "job_title": "Engineering Manager",
                        "job_company_name": "L3Harris Technologies",
                        "job_company_website": L3HARRIS_DOMAIN,
                        "job_company_id": f"pdl-{L3HARRIS_DOMAIN}",
                        "job_title_role": "engineering",
                        "job_title_sub_role": "software",
                        "job_title_levels": ["manager"],
                        "job_last_changed": datetime.now(UTC).isoformat(),
                        "location_name": "Durham, North Carolina",
                        "linkedin_url": LINKEDIN_URL,
                    }
                ],
                "total": 1,
            },
        )


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> _Transport:
    instance = _Transport()
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kw: instance)
    return instance


def test_toshiba_legacy_invalid_run_is_replaced_by_a_truthful_no_match(
    db: Session, transport: _Transport
) -> None:
    user = _user(db)
    job = _job(
        db,
        company="Toshiba Global Commerce Solutions",
        domain=TOSHIBA_DOMAIN,
        external_id="toshiba-live",
    )
    # The Jul 28 run, recorded before PDL's 404 was understood.
    db.add(
        PeopleDiscoveryRun(
            job_id=job.id,
            user_id=user.id,
            status="provider_unavailable",
            provider="pdl",
            query_fingerprint=query_fingerprint(job),
            failure_code="provider_request_invalid",
            safe_failure_message=(
                "We could not complete this search because the provider request "
                "was invalid."
            ),
            company_context={"discovery_strategy": "exact"},
            started_at=datetime.now(UTC) - timedelta(days=1),
            completed_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db.commit()

    # Before any new search, the legacy failure must not be presented as current.
    stale = recommendations_payload(db, user, job.id)
    assert stale["status"] == "stale"
    assert stale["availability_reason"] != "provider_request_invalid"
    assert stale["search_scope"]["refresh_eligible"] is True
    assert transport.calls == []

    # An explicit fresh search runs under the current contract.
    refreshed = asyncio.run(discover(db, user, job.id))

    assert refreshed["status"] == "no_reliable_matches"
    assert refreshed["availability_reason"] == "available"
    assert not any(refreshed["categories"].values())
    assert any(call == "search" for call in transport.calls)

    latest = db.scalars(
        db.query(PeopleDiscoveryRun)
        .filter(PeopleDiscoveryRun.job_id == job.id)
        .order_by(PeopleDiscoveryRun.id.desc())
        .statement
    ).first()
    assert (latest.company_context or {}).get(
        CONTRACT_VERSION_KEY
    ) == PEOPLE_SEARCH_CONTRACT_VERSION


def test_two_toshiba_jobs_do_not_show_contradictory_states(
    db: Session, transport: _Transport
) -> None:
    user = _user(db)
    job_a = _job(
        db,
        company="Toshiba Global Commerce Solutions",
        domain=TOSHIBA_DOMAIN,
        external_id="toshiba-a-live",
    )
    job_b = _job(
        db,
        company="Toshiba Global Commerce Solutions",
        domain=TOSHIBA_DOMAIN,
        external_id="toshiba-b-live",
    )
    db.add(
        PeopleDiscoveryRun(
            job_id=job_a.id,
            user_id=user.id,
            status="provider_unavailable",
            provider="pdl",
            query_fingerprint=query_fingerprint(job_a),
            failure_code="provider_request_invalid",
            company_context={},
            started_at=datetime.now(UTC) - timedelta(days=1),
            completed_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db.commit()

    payload_a = asyncio.run(discover(db, user, job_a.id))
    payload_b = asyncio.run(discover(db, user, job_b.id))

    assert payload_a["status"] == payload_b["status"] == "no_reliable_matches"
    assert payload_a["availability_reason"] == payload_b["availability_reason"]


def test_l3harris_manager_produces_a_draft_with_handoff_evidence(
    db: Session, transport: _Transport
) -> None:
    user = _user(db)
    job = _job(
        db,
        company="L3Harris Technologies",
        domain=L3HARRIS_DOMAIN,
        external_id="l3harris-live",
    )

    discovered = asyncio.run(discover(db, user, job.id))
    managers = discovered["categories"]["potential_hiring_managers"]
    assert managers, discovered
    manager = managers[0]
    assert manager["full_name"] == "Morgan Manager"
    # The status every PDL-discovered person receives.
    assert (
        manager["employment_validation_status"]
        == "exact_company_current_but_unverified_freshness"
    )

    draft = outreach_draft(
        db,
        user,
        job.id,
        manager["recommendation_id"],
        OutreachDraftRequest(
            draft_type="potential_hiring_manager_introduction",
            message_type="linkedin_message",
        ),
    )

    assert draft["body"]
    assert draft["generation_path"] == "deterministic_template"
    assert draft["linkedin_url"] == LINKEDIN_URL
    assert draft["linkedin_available"] is True
    # No verified email was discovered, so the email handoff stays unavailable
    # rather than inventing an address.
    assert draft["email_available"] is False
    assert draft["professional_email"] is None
    assert draft["sent"] is False


def test_l3harris_email_draft_separates_subject_and_body(
    db: Session, transport: _Transport
) -> None:
    user = _user(db)
    job = _job(
        db,
        company="L3Harris Technologies",
        domain=L3HARRIS_DOMAIN,
        external_id="l3harris-email",
    )
    discovered = asyncio.run(discover(db, user, job.id))
    manager = discovered["categories"]["potential_hiring_managers"][0]

    draft = outreach_draft(
        db,
        user,
        job.id,
        manager["recommendation_id"],
        OutreachDraftRequest(
            draft_type="potential_hiring_manager_introduction",
            message_type="email",
        ),
    )
    assert draft["subject"]
    assert draft["subject"] != draft["body"]
    assert "Software Engineer" in draft["subject"]
