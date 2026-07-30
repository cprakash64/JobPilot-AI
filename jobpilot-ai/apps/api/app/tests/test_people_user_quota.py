"""A user's people-search limit counts deliberate actions, not provider calls.

The limit was previously charged against ``PeopleProviderOperationUsage.
budget_units`` — provider credit units, where one PDL search costs one unit per
*record returned*. Measured on the live database, 14 user actions consumed 85 of
100 units, so the UI announced "You have reached today's people-search limit"
after a handful of companies.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.models.entities import (
    JobPosting,
    PeopleDiscoveryRun,
    PeopleProviderOperationUsage,
    User,
)
from app.people import circuit, pdl_company, providers
from app.people.quota import (
    quota_snapshot,
    user_discoveries_used,
)
from app.people.service import discover, recommendations_payload

AECOM_DOMAIN = "aecom.example"
BOSCH_DOMAIN = "bosch.example"

PDL_NOT_FOUND = {
    "status": 404,
    "error": {"type": "not_found", "message": "No records were found"},
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
    monkeypatch.setattr(settings, "people_user_daily_discovery_limit", 5)
    monkeypatch.setattr(settings, "people_discovery_rate_limit_per_hour", 50)
    monkeypatch.setattr(settings, "people_pdl_daily_credit_budget", 5000)
    monkeypatch.setattr(settings, "people_pdl_per_user_daily_limit", 5000)
    monkeypatch.setattr(settings, "people_min_relevance_score", 0)
    monkeypatch.setattr(settings, "people_min_recruiter_relevance", 0)
    monkeypatch.setattr(settings, "people_min_manager_relevance", 0)
    monkeypatch.setattr(settings, "people_min_referrer_relevance", 0)
    monkeypatch.setattr(settings, "people_min_data_confidence", 0)
    circuit.clear_local_circuits()
    pdl_company.clear_local_pdl_companies()


def _user(db: Session, email: str = "quota@example.com") -> User:
    user = User(email=email, hashed_password=hash_password("password123"))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _job(db: Session, *, company: str, domain: str, external_id: str) -> JobPosting:
    job = JobPosting(
        external_id=external_id,
        title="Software Engineer",
        company=company,
        company_domain=domain,
        location="Remote",
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


def _person(domain: str, title: str, role: str, sub_role: str, levels: list[str]) -> dict:
    return {
        "id": f"pdl-{domain}-{title}".replace(" ", "-"),
        "full_name": f"Sample {title}",
        "job_title": title,
        "job_company_name": domain.split(".")[0].title(),
        "job_company_website": domain,
        "job_company_id": f"pdl-{domain}",
        "job_title_role": role,
        "job_title_sub_role": sub_role,
        "job_title_levels": levels,
        "job_last_changed": datetime.now(UTC).isoformat(),
        "location_name": "Remote",
    }


class _Transport:
    """Counts every provider call and returns a full page of records."""

    def __init__(self, *, records_per_search: int = 20, no_match: bool = False) -> None:
        self.calls: list[str] = []
        self.records_per_search = records_per_search
        self.no_match = no_match

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, method: str, url: str, **kwargs):
        request = httpx.Request(method, url)
        if url.endswith("/company/enrich"):
            website = str((kwargs.get("params") or {}).get("website") or "")
            self.calls.append("company_enrich")
            if not website:
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
        self.calls.append("person_search")
        if self.no_match:
            return httpx.Response(404, request=request, json=PDL_NOT_FOUND)
        domain = AECOM_DOMAIN if AECOM_DOMAIN in sql else BOSCH_DOMAIN
        if "recruiting" in sql or "human_resources" in sql:
            template = ("Technical Recruiter", "human_resources", "recruiting", ["senior"])
        elif "'manager'" in sql:
            template = ("Engineering Manager", "engineering", "software", ["manager"])
        else:
            template = ("Software Engineer", "engineering", "software", ["senior"])
        rows = [
            {
                **_person(domain, template[0], template[1], template[2], template[3]),
                "id": f"pdl-{domain}-{template[0]}-{index}".replace(" ", "-"),
                "full_name": f"Sample {template[0]} {index}",
            }
            for index in range(self.records_per_search)
        ]
        return httpx.Response(
            200, request=request, json={"data": rows, "total": len(rows)}
        )

    @property
    def search_calls(self) -> int:
        return self.calls.count("person_search")


def _install(monkeypatch: pytest.MonkeyPatch, transport: _Transport) -> _Transport:
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_kw: transport)
    return transport


def _provider_units(db: Session, user: User) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(PeopleProviderOperationUsage.budget_units), 0)).where(
                PeopleProviderOperationUsage.user_id == user.id
            )
        )
        or 0
    )


# --------------------------------------------------------------------------- #
# One action, one unit
# --------------------------------------------------------------------------- #


def test_one_discovery_with_many_provider_calls_costs_one_user_unit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headline guarantee: provider fan-out never multiplies the user cost."""

    transport = _install(monkeypatch, _Transport(records_per_search=3))
    user = _user(db)
    job = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-1")

    payload = asyncio.run(discover(db, user, job.id))

    assert payload["status"] in {"complete", "partial"}
    # Company enrichment plus one search per category.
    assert len(transport.calls) >= 4
    assert user_discoveries_used(db, user.id) == 1
    # The provider ledger still records every call, in credit units.
    assert _provider_units(db, user) > 1


def test_eight_provider_calls_still_cost_exactly_one_unit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _install(monkeypatch, _Transport(no_match=True))
    monkeypatch.setattr(settings, "people_pdl_max_provider_calls_per_discovery", 8)
    monkeypatch.setattr(settings, "people_pdl_max_query_strategies", 4)
    user = _user(db)
    job = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-8")

    asyncio.run(discover(db, user, job.id))

    assert len(transport.calls) == 8, transport.calls
    assert user_discoveries_used(db, user.id) == 1


def test_truthful_no_match_still_costs_one_unit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, _Transport(no_match=True))
    user = _user(db)
    job = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-none")

    payload = asyncio.run(discover(db, user, job.id))

    assert payload["status"] == "no_reliable_matches"
    assert user_discoveries_used(db, user.id) == 1


def test_company_enrichment_does_not_add_a_user_unit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _install(monkeypatch, _Transport(no_match=True))
    user = _user(db)
    job = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-enrich")

    asyncio.run(discover(db, user, job.id))

    assert transport.calls.count("company_enrich") >= 1
    assert user_discoveries_used(db, user.id) == 1


def test_three_category_searches_are_one_unit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _install(monkeypatch, _Transport(records_per_search=3))
    user = _user(db)
    job = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-cat")

    asyncio.run(discover(db, user, job.id))

    assert transport.search_calls >= 3
    assert user_discoveries_used(db, user.id) == 1


# --------------------------------------------------------------------------- #
# Cache and coalescing
# --------------------------------------------------------------------------- #


def test_cache_hit_costs_nothing(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _install(monkeypatch, _Transport(records_per_search=5))
    user = _user(db)
    job = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-cache")

    asyncio.run(discover(db, user, job.id))
    assert user_discoveries_used(db, user.id) == 1
    calls_after_first = len(transport.calls)

    # Re-running the same discovery is served from stored results.
    asyncio.run(discover(db, user, job.id))

    assert user_discoveries_used(db, user.id) == 1
    assert len(transport.calls) == calls_after_first


def test_negative_cache_hit_costs_nothing(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _install(monkeypatch, _Transport(no_match=True))
    user = _user(db)
    job = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-neg")

    asyncio.run(discover(db, user, job.id))
    calls_after_first = len(transport.calls)
    assert user_discoveries_used(db, user.id) == 1

    asyncio.run(discover(db, user, job.id))

    assert user_discoveries_used(db, user.id) == 1
    assert len(transport.calls) == calls_after_first


def test_reading_recommendations_never_costs_quota(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rendering, reopening, sorting, and scrolling all use this read path."""

    _install(monkeypatch, _Transport(records_per_search=5))
    user = _user(db)
    jobs = [
        _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id=f"aecom-read-{index}")
        for index in range(20)
    ]

    for _ in range(3):
        for job in jobs:
            recommendations_payload(db, user, job.id)

    assert user_discoveries_used(db, user.id) == 0


# --------------------------------------------------------------------------- #
# Refunds
# --------------------------------------------------------------------------- #


def test_unresolved_company_is_not_charged(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JobPilot's own missing data must not cost the user a search."""

    _install(monkeypatch, _Transport(no_match=True))
    user = _user(db)
    job = _job(db, company="Unknown Co", domain=None, external_id="unknown-1")

    payload = asyncio.run(discover(db, user, job.id))

    assert payload["status"] == "domain_unresolved"
    assert user_discoveries_used(db, user.id) == 0


def test_invalid_job_is_not_charged(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    _install(monkeypatch, _Transport())
    user = _user(db)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(discover(db, user, 999_999))

    assert raised.value.status_code == 404
    assert user_discoveries_used(db, user.id) == 0


def test_provider_configuration_failure_is_refunded(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Unauthorized(_Transport):
        async def request(self, method: str, url: str, **kwargs):
            self.calls.append("unauthorized")
            return httpx.Response(
                401, request=httpx.Request(method, url), json={"status": 401}
            )

    _install(monkeypatch, Unauthorized())
    user = _user(db)
    job = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-401")

    payload = asyncio.run(discover(db, user, job.id))

    assert payload["status"] == "provider_configuration_error"
    # A broken credential is JobPilot's problem, not the user's.
    assert user_discoveries_used(db, user.id) == 0


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #


def test_daily_limit_rejects_only_after_the_configured_actions(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    _install(monkeypatch, _Transport(no_match=True))
    user = _user(db)
    jobs = [
        _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id=f"aecom-lim-{index}")
        for index in range(6)
    ]

    for job in jobs[:5]:
        asyncio.run(discover(db, user, job.id))
    assert user_discoveries_used(db, user.id) == 5

    with pytest.raises(HTTPException) as raised:
        asyncio.run(discover(db, user, jobs[5].id))

    assert raised.value.status_code == 429
    assert raised.value.detail["code"] == "PEOPLE_USER_DAILY_LIMIT_REACHED"
    # A rejection is not itself a charge.
    assert user_discoveries_used(db, user.id) == 5


def test_cached_results_remain_available_at_zero_remaining(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, _Transport(records_per_search=4))
    user = _user(db)
    job = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-zero")

    asyncio.run(discover(db, user, job.id))
    monkeypatch.setattr(settings, "people_user_daily_discovery_limit", 1)
    snapshot = quota_snapshot(db, user)
    assert snapshot.remaining == 0

    payload = recommendations_payload(db, user, job.id)
    assert any(payload["categories"].values())
    assert payload["quota"]["daily_remaining"] == 0


def test_hourly_rejection_does_not_consume_daily_quota(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    from app.people.service import _RATE_BUCKETS

    _RATE_BUCKETS.clear()
    _install(monkeypatch, _Transport(no_match=True))
    monkeypatch.setattr(settings, "people_discovery_rate_limit_per_hour", 1)
    user = _user(db)
    jobs = [
        _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id=f"aecom-burst-{index}")
        for index in range(2)
    ]

    asyncio.run(discover(db, user, jobs[0].id))
    assert user_discoveries_used(db, user.id) == 1

    with pytest.raises(HTTPException) as raised:
        asyncio.run(discover(db, user, jobs[1].id))

    assert raised.value.status_code == 429
    assert raised.value.detail["code"] == "PEOPLE_RATE_LIMITED"
    assert user_discoveries_used(db, user.id) == 1
    _RATE_BUCKETS.clear()


def test_internal_users_get_their_own_limit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "people_user_daily_discovery_limit", 20)
    monkeypatch.setattr(settings, "people_internal_user_daily_discovery_limit", 100)
    monkeypatch.setattr(settings, "people_internal_emails", ["insider@example.com"])

    standard = _user(db, "outsider@example.com")
    internal = _user(db, "insider@example.com")

    assert quota_snapshot(db, standard).daily_limit == 20
    assert quota_snapshot(db, internal).daily_limit == 100


def test_provider_budget_exhaustion_is_distinct_from_the_user_limit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    _install(monkeypatch, _Transport(records_per_search=5))
    user = _user(db)
    job = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-budget")
    monkeypatch.setattr(settings, "people_pdl_daily_credit_budget", 1)
    db.add(
        PeopleProviderOperationUsage(
            idempotency_key="budget-guard",
            user_id=user.id,
            job_id=job.id,
            provider="pdl",
            operation_type="people_search",
            http_outcome="http_200",
            adapter_version="pdl-category-search-v2",
            budget_units=50,
            occurred_at=datetime.now(UTC),
        )
    )
    db.commit()

    with pytest.raises(HTTPException) as raised:
        asyncio.run(discover(db, user, job.id))

    assert raised.value.detail["code"] == "PEOPLE_PROVIDER_BUDGET_EXCEEDED"
    assert raised.value.detail["availability_reason"] == "provider_budget_exceeded"
    # A provider-cost stop must never be reported as the user's search limit.
    assert "your daily" not in raised.value.detail["message"].lower()
    assert user_discoveries_used(db, user.id) == 0


# --------------------------------------------------------------------------- #
# Snapshot and reset
# --------------------------------------------------------------------------- #


def test_snapshot_reports_limit_used_remaining_and_reset(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, _Transport(no_match=True))
    user = _user(db)
    job = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-snap")

    before = quota_snapshot(db, user)
    assert before.daily_limit == 5
    assert before.used == 0
    assert before.remaining == 5
    assert before.resets_at > datetime.now(UTC)

    asyncio.run(discover(db, user, job.id))

    after = quota_snapshot(db, user)
    assert after.used == 1
    assert after.remaining == 4


def test_reset_clears_only_the_named_users_quota(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.people.quota import reset_user_quota

    _install(monkeypatch, _Transport(no_match=True))
    first = _user(db, "first@example.com")
    second = _user(db, "second@example.com")
    job_a = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-r1")
    job_b = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-r2")
    asyncio.run(discover(db, first, job_a.id))
    asyncio.run(discover(db, second, job_b.id))
    assert user_discoveries_used(db, first.id) == 1
    assert user_discoveries_used(db, second.id) == 1

    cleared = reset_user_quota(db, first.id)
    db.commit()

    assert cleared == 1
    assert user_discoveries_used(db, first.id) == 0
    assert user_discoveries_used(db, second.id) == 1
    # Results and runs survive a quota reset.
    assert db.query(PeopleDiscoveryRun).count() >= 2
    assert _provider_units(db, second) >= 0


# --------------------------------------------------------------------------- #
# End-to-end: five searches, many provider calls, three user actions
# --------------------------------------------------------------------------- #


def test_full_session_charges_actions_not_provider_calls(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scenario from the bug report, start to finish.

    Render many jobs, search two companies, reopen one, search a third job at a
    company already searched, then broaden. User usage tracks deliberate
    actions; the provider ledger tracks the far larger call count.
    """

    class _MixedTransport(_Transport):
        """AECOM matches; Bosch does not, which is what makes broaden eligible."""

        async def request(self, method: str, url: str, **kwargs):
            sql = str((kwargs.get("json") or {}).get("sql", ""))
            if BOSCH_DOMAIN in sql or f"pdl-{BOSCH_DOMAIN}" in sql:
                self.calls.append("person_search")
                return httpx.Response(
                    404, request=httpx.Request(method, url), json=PDL_NOT_FOUND
                )
            return await super().request(method, url, **kwargs)

    transport = _install(monkeypatch, _MixedTransport(records_per_search=3))
    monkeypatch.setattr(settings, "people_user_daily_discovery_limit", 5)
    user = _user(db)
    aecom = _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-e2e")
    aecom_second = _job(
        db, company="AECOM", domain=AECOM_DOMAIN, external_id="aecom-e2e-2"
    )
    bosch = _job(db, company="Bosch", domain=BOSCH_DOMAIN, external_id="bosch-e2e")
    filler = [
        _job(db, company="AECOM", domain=AECOM_DOMAIN, external_id=f"filler-{index}")
        for index in range(20)
    ]

    # 1. Rendering the jobs page: reads only, no provider calls, no quota.
    for job in [aecom, aecom_second, bosch, *filler]:
        recommendations_payload(db, user, job.id)
    assert user_discoveries_used(db, user.id) == 0
    assert transport.calls == []
    assert quota_snapshot(db, user).remaining == 5

    # 2. Search AECOM.
    asyncio.run(discover(db, user, aecom.id))
    assert user_discoveries_used(db, user.id) == 1
    aecom_calls = len(transport.calls)
    assert aecom_calls >= 2

    # 3. Search Bosch.
    asyncio.run(discover(db, user, bosch.id))
    assert user_discoveries_used(db, user.id) == 2
    assert len(transport.calls) > aecom_calls

    # 4. Reopen AECOM — a read, so nothing is charged and nothing is called.
    calls_before_reopen = len(transport.calls)
    for _ in range(5):
        recommendations_payload(db, user, aecom.id)
    assert user_discoveries_used(db, user.id) == 2
    assert len(transport.calls) == calls_before_reopen

    # 5. Re-running discovery on the already-searched job is a cache hit.
    asyncio.run(discover(db, user, aecom.id))
    assert user_discoveries_used(db, user.id) == 2
    assert len(transport.calls) == calls_before_reopen

    # 6. Broaden Bosch, which found nobody: a second deliberate action on that
    #    job, so exactly one more unit.
    asyncio.run(discover(db, user, bosch.id, strategy="broadened"))
    assert user_discoveries_used(db, user.id) == 3

    snapshot = quota_snapshot(db, user)
    assert snapshot.used == 3
    assert snapshot.remaining == 2
    # The provider did far more work than the user was charged for.
    assert len(transport.calls) >= 6
    assert _provider_units(db, user) > snapshot.used
