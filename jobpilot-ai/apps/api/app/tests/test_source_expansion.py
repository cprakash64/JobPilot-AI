"""Tests for new ATS connectors, the verified catalog, packs, and scale."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.core.config import settings
from app.db.base import Base
from app.job_sources.base import JobSourceAdapter, NormalizedJob
from app.jobs import job_ingestion_service
from app.jobs.sources.breezy import BreezyAdapter
from app.jobs.sources.recruitee import RecruiteeAdapter
from app.jobs.sources.simplifyjobs import (
    SimplifyJobsAdapter,
    parse_verified_company_domain,
)
from app.jobs.sources.smartrecruiters import SmartRecruitersAdapter
from app.jobs.sources.teamtailor import TeamtailorAdapter
from app.jobs.sources.workable import WorkableAdapter
from app.jobs.source_packs import load_pack_file, packs_for_profile, tags_for_packs
from app.jobs.source_registry import build_adapters, load_registry
from app.main import app
from app.models import entities  # noqa: F401


def recent_iso(days: int = 2) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


# --------------------------------------------------------------------------- #
# httpx patch that supports headers/params (used by get_json-based connectors)
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, data: object) -> None:
        self._data = data
        self.status_code = 200

    def json(self) -> object:
        return self._data

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def get(self, url: str, headers=None, params=None) -> _FakeResponse:
        return _FakeResponse(self._payload)


def patch_httpx(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(payload))


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Connector fixtures
# --------------------------------------------------------------------------- #
def test_smartrecruiters_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "totalFound": 1,
        "content": [
            {
                "id": "744000123",
                "name": "Machine Learning Engineer",
                "releasedDate": recent_iso(2),
                "location": {"city": "Austin", "region": "TX", "country": "us", "remote": False, "fullLocation": "Austin, TX, United States"},
                "typeOfEmployment": {"label": "Full-time"},
            }
        ],
    }
    patch_httpx(monkeypatch, payload)
    jobs = run(SmartRecruitersAdapter("Acme", "Acme").fetch_recent_jobs(7))
    assert len(jobs) == 1
    assert jobs[0].title == "Machine Learning Engineer"
    assert jobs[0].source == "smartrecruiters"
    assert jobs[0].application_url == "https://jobs.smartrecruiters.com/Acme/744000123"
    assert "United States" in (jobs[0].location or "")
    assert jobs[0].posted_at is not None


def test_recruitee_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "offers": [
            {
                "id": 5,
                "title": "Backend Engineer",
                "careers_url": "https://acme.recruitee.com/o/backend-engineer",
                "city": "New York",
                "country": "United States",
                "published_at": recent_iso(1),
                "description": "<p>Build APIs with Python</p>",
            }
        ]
    }
    patch_httpx(monkeypatch, payload)
    jobs = run(RecruiteeAdapter("acme", "Acme").fetch_recent_jobs(7))
    assert len(jobs) == 1
    assert jobs[0].source == "recruitee"
    assert jobs[0].application_url == "https://acme.recruitee.com/o/backend-engineer"
    assert "Python" in jobs[0].description_clean


def test_workable_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "jobs": [
            {
                "id": "abc",
                "title": "Frontend Engineer",
                "url": "https://acme.workable.com/j/ABC123",
                "application_url": "https://apply.workable.com/acme/j/ABC123",
                "published_on": (datetime.now(UTC) - timedelta(days=2)).date().isoformat(),
                "country": "United States",
                "city": "Remote",
            }
        ]
    }
    patch_httpx(monkeypatch, payload)
    jobs = run(WorkableAdapter("acme", "Acme").fetch_recent_jobs(7))
    assert len(jobs) == 1
    assert jobs[0].source == "workable"
    assert jobs[0].application_url == "https://apply.workable.com/acme/j/ABC123"
    assert jobs[0].posted_at is not None


def test_teamtailor_requires_token_then_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without a token the adapter is skipped cleanly (never scrapes).
    assert run(TeamtailorAdapter("acme", "Acme").fetch_recent_jobs(7)) == []

    payload = {
        "data": [
            {
                "id": "55",
                "attributes": {
                    "title": "AI Engineer",
                    "apply-url": "https://acme.teamtailor.com/jobs/55",
                    "created-at": recent_iso(3),
                    "body": "Work with LLMs",
                    "remote-status": "fully",
                    "location": "Remote, US",
                },
            }
        ]
    }
    patch_httpx(monkeypatch, payload)
    jobs = run(TeamtailorAdapter("acme", "Acme", {"token": "secret"}).fetch_recent_jobs(7))
    assert len(jobs) == 1
    assert jobs[0].source == "teamtailor"
    assert jobs[0].application_url == "https://acme.teamtailor.com/jobs/55"


def test_breezy_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "id": "p1",
            "name": "NLP Engineer",
            "url": "https://acme.breezy.hr/p/p1",
            "published_date": recent_iso(2),
            "location": {"name": "Remote, US", "is_remote": True},
            "type": {"name": "Full-Time"},
        }
    ]
    patch_httpx(monkeypatch, payload)
    jobs = run(BreezyAdapter("acme", "Acme").fetch_recent_jobs(7))
    assert len(jobs) == 1
    assert jobs[0].source == "breezy"
    assert jobs[0].workplace_type == "remote"
    assert jobs[0].application_url == "https://acme.breezy.hr/p/p1"


def test_simplifyjobs_connector_uses_direct_apply_links(monkeypatch: pytest.MonkeyPatch) -> None:
    recent_timestamp = int((datetime.now(UTC) - timedelta(hours=2)).timestamp())
    payload = [
        {
            "id": "new-grad-1",
            "company_name": "Example Robotics",
            "title": "Software Engineer New Grad",
            "locations": ["Pittsburgh, PA", "Remote in USA"],
            "date_posted": recent_timestamp,
            "url": "https://jobs.ashbyhq.com/example/role/application",
            "company_url": (
                "https://simplify.jobs/c/"
                "f58a30c3-a04b-42bb-a8b9-b55894b3d405/Example-Robotics"
            ),
            "active": True,
            "is_visible": True,
            "sponsorship": "Offers Sponsorship",
            "degrees": ["Bachelor's"],
            "category": "Software",
            "source": "Simplify",
        },
        {
            "id": "closed-1",
            "company_name": "Closed Company",
            "title": "Closed Role",
            "locations": ["Remote"],
            "date_posted": recent_timestamp,
            "url": "https://example.jobs/closed",
            "active": False,
            "is_visible": True,
        },
    ]
    patch_httpx(monkeypatch, payload)

    async def verified_profile(*_args, **_kwargs) -> str:
        return (
            '<script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"company":{"name":"Example Robotics",'
            '"verified":true,"url":"https://www.example-robotics.com/"}}}}'
            "</script>"
        )

    monkeypatch.setattr("app.jobs.sources.simplifyjobs.get_text", verified_profile)
    jobs = run(
        SimplifyJobsAdapter("New-Grad-Positions", "SimplifyJobs New Grad").fetch_recent_jobs(7)
    )
    assert len(jobs) == 1
    assert jobs[0].source == "simplifyjobs"
    assert jobs[0].company == "Example Robotics"
    assert jobs[0].seniority_level == "entry"
    assert jobs[0].workplace_type == "remote"
    assert jobs[0].application_url == "https://jobs.ashbyhq.com/example/role/application"
    assert jobs[0].company_domain == "example-robotics.com"
    assert jobs[0].company_logo_url == ""
    assert jobs[0].work_authorization_notes == "Offers Sponsorship"
    assert jobs[0].degree_requirement == "Bachelor's"


def test_simplifyjobs_does_not_derive_logo_from_an_untrusted_company_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recent_timestamp = int((datetime.now(UTC) - timedelta(hours=2)).timestamp())
    payload = [
        {
            "id": "new-grad-2",
            "company_name": "Example Robotics",
            "title": "Software Engineer",
            "locations": ["Remote in USA"],
            "date_posted": recent_timestamp,
            "url": "https://jobs.ashbyhq.com/example/role/application",
            "company_url": (
                "https://attacker.example/c/"
                "f58a30c3-a04b-42bb-a8b9-b55894b3d405"
            ),
            "active": True,
            "is_visible": True,
        }
    ]
    patch_httpx(monkeypatch, payload)
    jobs = run(
        SimplifyJobsAdapter("New-Grad-Positions", "SimplifyJobs New Grad").fetch_recent_jobs(7)
    )
    assert jobs[0].company_logo_url == ""


def test_simplifyjobs_uses_only_matching_verified_official_website() -> None:
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"company":{"name":"Example Robotics, Inc.",'
        '"verified":true,"url":"https://www.example-robotics.com/about"}}}}'
        "</script>"
    )
    assert parse_verified_company_domain(
        html, expected_company_name="Example Robotics"
    ) == "example-robotics.com"
    assert parse_verified_company_domain(
        html, expected_company_name="Different Company"
    ) == ""


def test_simplifyjobs_rejects_unapproved_repository() -> None:
    jobs = run(SimplifyJobsAdapter("unknown-repository", "Unknown").fetch_recent_jobs(7))
    assert jobs == []


def test_connector_broken_source_raises_not_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    with pytest.raises(Exception):
        run(BreezyAdapter("acme", "Acme").fetch_recent_jobs(7))


# --------------------------------------------------------------------------- #
# Catalog + packs
# --------------------------------------------------------------------------- #
def test_catalog_has_100_plus_verified_sources() -> None:
    registry = load_registry()
    assert len(registry) >= 100
    providers = {c.provider for c in registry}
    assert {"greenhouse", "lever", "ashby"} <= providers
    smartrecruiters = [c for c in registry if c.provider == "smartrecruiters"]
    assert len(smartrecruiters) >= 17
    assert {"Visa", "ServiceNow", "Bosch", "Western Digital"} <= {
        c.name for c in smartrecruiters
    }
    simplify = [c for c in registry if c.provider == "simplifyjobs"]
    assert {c.slug for c in simplify} == {
        "New-Grad-Positions",
        "Summer2026-Internships",
    }
    # Sorted by priority (highest first).
    assert registry[0].priority >= registry[-1].priority


def test_disabled_sources_are_skipped(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = tmp_path / "cat.json"
    catalog.write_text(
        '{"greenhouse":[{"company":"On","board_token":"on","enabled":true},'
        '{"company":"Off","board_token":"off","enabled":false}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "job_sources_file", str(catalog))
    slugs = {c.slug for c in load_registry()}
    assert "on" in slugs
    assert "off" not in slugs


@pytest.mark.parametrize(
    "roles,expected",
    [
        (["Machine Learning Engineer", "AI Engineer"], "ai_ml_us"),
        (["Backend Engineer"], "devtools_us"),
        (["Software Engineer"], "software_ai_us"),
    ],
)
def test_packs_map_to_profile(roles, expected) -> None:
    assert expected in packs_for_profile(roles, ["Junior"])


def test_pack_files_load_and_have_tags() -> None:
    doc = load_pack_file("ai_ml_us")
    assert doc["sources"]
    assert set(doc["tags"]) == tags_for_packs(["ai_ml_us"])


def test_build_adapters_respects_limit_and_tags() -> None:
    assert len(build_adapters(limit=5)) == 5
    ai = build_adapters(tags={"ai", "ml"})
    assert ai and len(ai) < len(build_adapters())


def test_verify_rejects_placeholder_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.jobs import verify_sources
    from app.jobs.source_registry import SourceCompany

    class BadAdapter(JobSourceAdapter):
        source_type = "greenhouse"

        async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
            url = "https://example.com/apply"  # placeholder -> must be rejected
            return [NormalizedJob(external_id="1", title="Eng", company="X", location="US",
                                  remote_type="remote", employment_type="ft", seniority_level=None,
                                  posted_at=datetime.now(UTC), application_url=url, source_url=url,
                                  description_raw="", description_clean="", source="greenhouse")]

    monkeypatch.setitem(verify_sources.ADAPTERS, "greenhouse", BadAdapter)
    check = run(verify_sources._check(SourceCompany("greenhouse", "x", "X")))
    assert check.ok is False


# --------------------------------------------------------------------------- #
# Scale / discovery
# --------------------------------------------------------------------------- #
def _job(i, title, company="Acme", loc="Remote, United States", source="greenhouse"):
    url = f"https://job-boards.greenhouse.io/{company.lower()}/{i}"
    return NormalizedJob(
        external_id=str(i), title=title, company=company, location=loc,
        remote_type="remote", employment_type="ft", seniority_level=None,
        posted_at=datetime.now(UTC) - timedelta(days=2), application_url=url, source_url=url,
        description_raw="", description_clean=f"{title}. Requirements: Python.", source=source,
        required_skills=["Python"],
    )


class _Src(JobSourceAdapter):
    def __init__(self, source_type, name, jobs, delay=0.0):
        super().__init__(name.lower(), name)
        self.source_type = source_type
        self._jobs = jobs
        self._delay = delay

    async def fetch_recent_jobs(self, days: int = 7):
        if self._delay:
            await asyncio.sleep(self._delay)
        return list(self._jobs)


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _headers(client: TestClient) -> dict[str, str]:
    token = client.post("/auth/signup", json={"email": "scale@example.com", "password": "password123"}).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    client.put("/profile", headers=headers, json={
        "full_name": "T", "target_roles": ["Software Engineer", "Backend Engineer", "AI Engineer", "Machine Learning Engineer"],
        "target_levels": ["New Grad", "Junior"], "preferred_locations": ["Remote", "United States"],
        "remote_preference": "everything", "skills": ["Python"], "requires_sponsorship": False,
    })
    return headers


def test_discovery_dedupes_across_sources(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Two sources surface the same logical job (same company+title+location).
    src_a = _Src("greenhouse", "AcmeGH", [_job(1, "Backend Engineer"), _job(2, "AI Engineer")])
    src_b = _Src("lever", "AcmeLever", [_job(9, "Backend Engineer", source="lever")])  # dup of job 1
    monkeypatch.setattr(job_ingestion_service, "build_adapters", lambda *args, **kw: [src_a, src_b])

    body = client.post("/jobs/discover", headers=_headers(client), json={}).json()
    titles = [j["title"] for j in body["jobs"]]
    assert titles.count("Backend Engineer") == 1  # deduped across sources
    d = body["discovery"]
    assert d["sources_searched"] == 2
    assert d["sources_succeeded"] == 2


def test_discovery_reports_source_stats_and_warnings(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    good = _Src("greenhouse", "Good", [_job(1, "Backend Engineer")])

    class Broken(JobSourceAdapter):
        source_type = "ashby"

        def __init__(self):
            super().__init__("broken", "BrokenCo")

        async def fetch_recent_jobs(self, days: int = 7):
            raise RuntimeError("boom")

    monkeypatch.setattr(job_ingestion_service, "build_adapters", lambda *a, **k: [good, Broken()])
    body = client.post("/jobs/discover", headers=_headers(client), json={}).json()
    d = body["discovery"]
    assert d["sources_searched"] == 2
    assert d["sources_failed"] == 1
    assert any("BrokenCo" in w for w in d["source_warnings"])
    # A failed source becomes a warning, never a fake job.
    assert all(j["company"] != "BrokenCo" for j in body["jobs"])


def test_discovery_respects_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "job_discovery_timeout_seconds", 0.2)
    slow = _Src("greenhouse", "Slow", [_job(1, "Backend Engineer")], delay=1.0)
    fast = _Src("lever", "Fast", [_job(2, "AI Engineer")])
    monkeypatch.setattr(job_ingestion_service, "build_adapters", lambda *a, **k: [slow, fast])

    body = client.post("/jobs/discover", headers=_headers(client), json={}).json()
    titles = {j["title"] for j in body["jobs"]}
    assert "AI Engineer" in titles          # fast source returned
    assert "Backend Engineer" not in titles  # slow source timed out (no crash)
    assert body["discovery"]["sources_failed"] == 1


def test_timed_out_refresh_keeps_the_last_successful_source_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "job_discovery_cache_ttl_minutes", 1)
    monkeypatch.setattr(settings, "job_discovery_timeout_seconds", 0.01)
    source = _Src("greenhouse", "ReliableUntilSlow", [_job(1, "Backend Engineer")])

    first_jobs, first_warnings, _ = run(job_ingestion_service._fetch_all([source], 7))
    assert [job.title for job in first_jobs] == ["Backend Engineer"]
    assert first_warnings == []

    # Expire the normal cache, then make the next network refresh time out. The
    # stale snapshot is preferable to deleting the source's jobs from the UI.
    for key, (_, jobs) in list(job_ingestion_service._SOURCE_CACHE.items()):
        job_ingestion_service._SOURCE_CACHE[key] = (
            datetime.now(UTC) - timedelta(minutes=2), jobs
        )
    source._delay = 0.1
    stale_jobs, warnings, _ = run(job_ingestion_service._fetch_all([source], 7))
    assert [job.title for job in stale_jobs] == ["Backend Engineer"]
    assert warnings == []


def test_more_sources_yield_more_eligible_jobs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    small = [_Src("greenhouse", "One", [_job(1, "Backend Engineer")])]
    large = [
        _Src("greenhouse", "One", [_job(1, "Backend Engineer")]),
        _Src("ashby", "Two", [_job(2, "AI Engineer", company="Beta")]),
        _Src("lever", "Three", [_job(3, "Machine Learning Engineer", company="Gamma")]),
    ]
    headers = _headers(client)
    monkeypatch.setattr(job_ingestion_service, "build_adapters", lambda *a, **k: small)
    n_small = len(client.post("/jobs/discover", headers=headers, json={}).json()["jobs"])
    monkeypatch.setattr(job_ingestion_service, "build_adapters", lambda *a, **k: large)
    n_large = len(client.post("/jobs/discover", headers=headers, json={}).json()["jobs"])
    assert n_large > n_small


def test_eligibility_preserved_with_many_sources(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sources = [
        _Src("greenhouse", "A", [_job(1, "Backend Engineer", loc="Remote, United States")]),
        _Src("greenhouse", "B", [_job(2, "Backend Engineer", company="Bcorp", loc="Toronto, Canada")]),
        _Src("ashby", "C", [_job(3, "Senior Software Engineer", company="Ccorp", loc="Remote, United States")]),
        _Src("lever", "D", [_job(4, "Customer Success Engineer", company="Dcorp", loc="Remote, United States")]),
        _Src("ashby", "E", [_job(5, "Software Engineer", company="Ecorp", loc="Bengaluru, India")]),
    ]
    monkeypatch.setattr(job_ingestion_service, "build_adapters", lambda *a, **k: sources)
    body = client.post("/jobs/discover", headers=_headers(client), json={}).json()
    titles = {j["title"] for j in body["jobs"]}
    locations = {j["location"] for j in body["jobs"]}
    assert titles == {"Backend Engineer"}
    assert not any("Canada" in loc or "India" in loc for loc in locations)
