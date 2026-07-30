"""End-to-end orchestration isolation for People Who Can Help.

Reproduces the production scenario across three companies at once: one fails
with a request-scoped provider rejection, one returns a genuine empty result,
and one returns people. The third must succeed, and the provider circuit must
stay closed throughout.

Everything runs against a fake HTTP transport and an in-memory database. No
provider account is contacted and no credits are spent.
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
from app.models.entities import JobPosting, User
from app.people import circuit, providers
from app.people.service import PROVIDER_ERROR_STATUSES, discover, recommendations_payload

CISCO_DOMAIN = "cisco.example"
HII_DOMAIN = "hii.example"
L3HARRIS_DOMAIN = "l3harris.example"


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
def _pdl_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "people_primary_provider", "pdl")
    monkeypatch.setattr(settings, "people_pdl_discovery_enabled", True)
    monkeypatch.setattr(settings, "pdl_api_key", "test-key-not-a-real-credential")
    monkeypatch.setattr(settings, "people_recommendations_enabled", True)
    monkeypatch.setattr(settings, "people_pdl_daily_credit_budget", 500)
    monkeypatch.setattr(settings, "people_pdl_per_user_daily_limit", 100)
    circuit.clear_local_circuits()


def _job(db: Session, *, company: str, domain: str, external_id: str) -> JobPosting:
    job = JobPosting(
        external_id=external_id,
        title="Machine Learning Engineer",
        company=company,
        company_domain=domain,
        location="Remote",
        employment_type="full-time",
        seniority_level="mid",
        # Sourced through an aggregator, exactly like the production jobs.
        application_url="https://simplify.jobs/p/" + external_id,
        source_url="https://simplify.jobs/p/" + external_id,
        description_raw="Build machine learning systems.",
        description_clean="Build machine learning systems with Python.",
        required_skills=["Python", "Machine Learning"],
        hash_for_deduplication=external_id.ljust(64, "0"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _user(db: Session, email: str = "isolation@example.com") -> User:
    user = User(email=email, hashed_password=hash_password("password123"))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _person_row(domain: str, identifier: str) -> dict:
    return {
        "id": identifier,
        "full_name": "Alex Example",
        "job_title": "Technical Recruiter",
        "job_company_name": "L3Harris Technologies",
        "job_company_website": domain,
        "job_title_levels": ["manager"],
        "job_last_verified": datetime.now(UTC).isoformat(),
        "location_name": "Remote",
        "linkedin_url": "https://www.linkedin.com/in/alex-example",
    }


# PDL company ids, keyed by the domain each fixture company resolves from.
_PDL_COMPANY_IDS = {
    CISCO_DOMAIN: "pdl-cisco",
    HII_DOMAIN: "pdl-hii",
    L3HARRIS_DOMAIN: "pdl-l3harris",
}

# PDL's documented no-match envelope. Per
# https://docs.peopledatalabs.com/docs/errors a 404 "simply means there were no
# profiles found matching your request" — it is not a rejected request.
_PDL_NOT_FOUND = {
    "status": 404,
    "error": {
        "type": "not_found",
        "message": "No records were found matching your request",
    },
}
# A genuinely malformed query, which PDL answers with 400.
_PDL_BAD_REQUEST = {
    "status": 400,
    "error": {"type": "invalid_request", "message": "Malformed SQL"},
}


class _ThreeCompanyTransport:
    """Cisco is a real 400, Huntington Ingalls 404s (no match), L3Harris matches."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.concurrent = 0
        self.peak_concurrent = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, method: str, url: str, **kwargs):
        request = httpx.Request(method, url)
        if url.endswith("/company/enrich"):
            website = (kwargs.get("params") or {}).get("website")
            company_id = _PDL_COMPANY_IDS.get(str(website))
            self.calls.append(f"enrich:{website}")
            if not company_id:
                return httpx.Response(404, request=request, json=_PDL_NOT_FOUND)
            return httpx.Response(
                200,
                request=request,
                json={
                    "status": 200,
                    "likelihood": 9,
                    "id": company_id,
                    "name": str(website).split(".")[0].title(),
                    "website": website,
                },
            )
        sql = str((kwargs.get("json") or {}).get("sql", ""))
        domain = next(
            (
                candidate
                for candidate, company_id in _PDL_COMPANY_IDS.items()
                if company_id in sql or candidate in sql
            ),
            "unknown",
        )
        self.calls.append(domain)
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        try:
            await asyncio.sleep(0)
            if domain == CISCO_DOMAIN:
                return httpx.Response(400, request=request, json=_PDL_BAD_REQUEST)
            if domain == HII_DOMAIN:
                return httpx.Response(404, request=request, json=_PDL_NOT_FOUND)
            return httpx.Response(
                200,
                request=request,
                json={"data": [_person_row(L3HARRIS_DOMAIN, "pdl-l3harris-1")]},
            )
        finally:
            self.concurrent -= 1


def _install(monkeypatch: pytest.MonkeyPatch) -> _ThreeCompanyTransport:
    transport = _ThreeCompanyTransport()
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **_kwargs: transport
    )
    return transport


def _pdl_fingerprint() -> str:
    return providers.account_fingerprint(settings.pdl_api_key)


def _transient_state() -> str:
    return circuit.circuit_state(
        provider="pdl",
        account_fingerprint=_pdl_fingerprint(),
        operation="people_search",
    ).transient


def test_one_failing_company_does_not_block_the_others(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _install(monkeypatch)
    user = _user(db)
    cisco = _job(db, company="Cisco Systems, Inc.", domain=CISCO_DOMAIN, external_id="cisco-1")
    hii = _job(
        db,
        company="Huntington Ingalls Industries",
        domain=HII_DOMAIN,
        external_id="hii-1",
    )
    l3harris = _job(
        db, company="L3Harris Technologies", domain=L3HARRIS_DOMAIN, external_id="l3-1"
    )

    cisco_payload = asyncio.run(discover(db, user, cisco.id))
    hii_payload = asyncio.run(discover(db, user, hii.id))
    l3_payload = asyncio.run(discover(db, user, l3harris.id))

    # Cisco failed for its own request-scoped reason, and never as an outage.
    assert cisco_payload["status"] in PROVIDER_ERROR_STATUSES
    assert cisco_payload["availability_reason"] == "provider_request_invalid"

    # Huntington Ingalls got PDL's documented 404 no-match envelope. That is a
    # successful answer with nobody in it, not a rejected request.
    assert hii_payload["status"] == "no_reliable_matches"
    assert hii_payload["availability_reason"] == "available"

    # L3Harris still ran and returned its people.
    assert l3_payload["categories"]["likely_recruiters"], l3_payload
    assert l3_payload["status"] == "complete"

    # The provider circuit stayed closed the whole time.
    assert _transient_state() == "closed"
    assert L3HARRIS_DOMAIN in transport.calls


def test_no_more_than_the_configured_concurrency_is_observed(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _install(monkeypatch)
    user = _user(db)
    jobs = [
        _job(
            db,
            company=f"Company {index}",
            domain=L3HARRIS_DOMAIN,
            external_id=f"conc-{index}",
        )
        for index in range(6)
    ]

    async def run_all() -> None:
        await asyncio.gather(*(discover(db, user, job.id) for job in jobs))

    asyncio.run(run_all())
    assert transport.peak_concurrent <= settings.people_provider_max_concurrent_calls


def test_cached_results_survive_an_open_provider_circuit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch)
    user = _user(db)
    l3harris = _job(
        db, company="L3Harris Technologies", domain=L3HARRIS_DOMAIN, external_id="l3-2"
    )
    first = asyncio.run(discover(db, user, l3harris.id))
    assert first["categories"]["likely_recruiters"]

    # Force the provider circuit open; stored results must still be served.
    for _ in range(settings.people_circuit_failure_threshold):
        circuit.record_failure(
            provider="pdl",
            account_fingerprint=_pdl_fingerprint(),
            operation="people_search",
            code=providers.PeopleErrorCode.PROVIDER_SERVER_ERROR,
        )
    assert _transient_state() == "open"

    payload = recommendations_payload(db, user, l3harris.id)
    assert payload["categories"]["likely_recruiters"]
    assert payload["result_freshness"] == "fresh"
    assert payload["provider_circuit"] == "transient_open"


def test_stale_results_are_served_only_inside_the_configured_window(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.entities import JobPeopleCandidate

    _install(monkeypatch)
    user = _user(db)
    l3harris = _job(
        db, company="L3Harris Technologies", domain=L3HARRIS_DOMAIN, external_id="l3-3"
    )
    asyncio.run(discover(db, user, l3harris.id))

    for _ in range(settings.people_circuit_failure_threshold):
        circuit.record_failure(
            provider="pdl",
            account_fingerprint=_pdl_fingerprint(),
            operation="people_search",
            code=providers.PeopleErrorCode.PROVIDER_SERVER_ERROR,
        )

    candidates = db.query(JobPeopleCandidate).all()
    assert candidates

    # Just inside the stale window: served, and explicitly labelled as cached.
    for candidate in candidates:
        candidate.expires_at = datetime.now(UTC) - timedelta(days=1)
    db.commit()
    inside = recommendations_payload(db, user, l3harris.id)
    assert inside["categories"]["likely_recruiters"]
    assert inside["result_freshness"] == "stale"

    # Beyond the window: not served.
    beyond = settings.people_stale_result_window_days + 1
    for candidate in candidates:
        candidate.expires_at = datetime.now(UTC) - timedelta(days=beyond)
    db.commit()
    outside = recommendations_payload(db, user, l3harris.id)
    assert not any(outside["categories"].values())


def test_provider_budget_exhaustion_does_not_touch_the_provider_circuit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    _install(monkeypatch)
    user = _user(db)
    l3harris = _job(
        db, company="L3Harris Technologies", domain=L3HARRIS_DOMAIN, external_id="l3-4"
    )
    monkeypatch.setattr(settings, "people_pdl_per_user_daily_limit", 1)

    from app.models.entities import PeopleProviderOperationUsage

    db.add(
        PeopleProviderOperationUsage(
            idempotency_key="budget-test-1",
            user_id=user.id,
            job_id=l3harris.id,
            provider="pdl",
            operation_type="people_search",
            http_outcome="http_200",
            adapter_version="pdl-category-search-v2",
            budget_units=5,
            occurred_at=datetime.now(UTC),
        )
    )
    db.commit()

    with pytest.raises(HTTPException) as raised:
        asyncio.run(discover(db, user, l3harris.id))

    assert raised.value.detail["code"] == "PEOPLE_PROVIDER_BUDGET_EXCEEDED"
    assert _transient_state() == "closed"
