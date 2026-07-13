"""Tests for real ATS connectors, placeholder rejection, and demo cleanup."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.job_sources.base import NormalizedJob
from app.job_sources.greenhouse import GreenhouseAdapter
from app.job_sources.lever import LeverAdapter
from app.jobs.job_normalization_service import normalize_jobs, validate_job
from app.jobs.sources.ashby import AshbyAdapter
from app.main import app
from app.maintenance.cleanup_demo_jobs import cleanup_demo_jobs
from app.models import entities  # noqa: F401
from app.models.entities import ApplicationTracker, JobMatch, JobPosting, JobSource


def recent_iso(days: int = 2) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


# --------------------------------------------------------------------------- #
# httpx patching helper
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

    async def get(self, url: str) -> _FakeResponse:
        return _FakeResponse(self._payload)


def patch_httpx(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(payload))


# --------------------------------------------------------------------------- #
# Connector parsing
# --------------------------------------------------------------------------- #
def test_greenhouse_connector_parses_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "jobs": [
            {
                "id": 123,
                "title": "Machine Learning Engineer",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123",
                "first_published": recent_iso(2),
                "updated_at": recent_iso(0),
                "location": {"name": "Remote, US"},
                "content": "&lt;p&gt;Build models with Python and PyTorch&lt;/p&gt;",
            }
        ]
    }
    patch_httpx(monkeypatch, payload)
    jobs = asyncio.run(GreenhouseAdapter("acme", "Acme").fetch_recent_jobs(7))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Machine Learning Engineer"
    assert job.company == "Acme"
    assert job.source == "greenhouse"
    assert job.application_url == "https://job-boards.greenhouse.io/acme/jobs/123"
    assert job.posted_at is not None and job.posted_at.date() == (datetime.now(UTC) - timedelta(days=2)).date()
    # HTML entities are unescaped before stripping.
    assert "Python" in job.description_clean and "<p>" not in job.description_clean


def test_greenhouse_uses_first_published_not_updated_at(monkeypatch: pytest.MonkeyPatch) -> None:
    # A job first published 30 days ago but "updated" today must be excluded from
    # the 7-day window (we must not treat updated_at as the posting date).
    payload = {
        "jobs": [
            {
                "id": 9,
                "title": "Old Role",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/9",
                "first_published": recent_iso(30),
                "updated_at": recent_iso(0),
                "location": {"name": "Remote"},
                "content": "old",
            }
        ]
    }
    patch_httpx(monkeypatch, payload)
    jobs = asyncio.run(GreenhouseAdapter("acme", "Acme").fetch_recent_jobs(7))
    assert jobs == []


def test_greenhouse_unknown_date_kept_at_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "jobs": [
            {
                "id": 5,
                "title": "No Date Role",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/5",
                "location": {"name": "Remote"},
                "content": "python",
            }
        ]
    }
    patch_httpx(monkeypatch, payload)
    jobs = asyncio.run(GreenhouseAdapter("acme", "Acme").fetch_recent_jobs(7))
    assert len(jobs) == 1
    assert jobs[0].posted_at is None  # unknown, never faked


def test_lever_connector_parses_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    created_ms = int((datetime.now(UTC) - timedelta(days=1)).timestamp() * 1000)
    payload = [
        {
            "id": "abc-123",
            "text": "Backend Engineer",
            "hostedUrl": "https://jobs.lever.co/acme/abc-123",
            "createdAt": created_ms,
            "categories": {"location": "Remote", "commitment": "Full-time"},
            "description": "Build APIs with Python and FastAPI",
            "lists": [],
        }
    ]
    patch_httpx(monkeypatch, payload)
    jobs = asyncio.run(LeverAdapter("acme", "Acme").fetch_recent_jobs(7))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Backend Engineer"
    assert job.source == "lever"
    assert job.application_url == "https://jobs.lever.co/acme/abc-123"
    assert job.posted_at is not None


def test_ashby_connector_parses_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "jobs": [
            {
                "id": "z-1",
                "title": "AI Engineer",
                "applyUrl": "https://jobs.ashbyhq.com/acme/z-1/application",
                "jobUrl": "https://jobs.ashbyhq.com/acme/z-1",
                "publishedAt": recent_iso(3),
                "location": "Remote",
                "employmentType": "FullTime",
                "isRemote": True,
                "descriptionPlain": "Work with LLMs and RAG",
            }
        ]
    }
    patch_httpx(monkeypatch, payload)
    jobs = asyncio.run(AshbyAdapter("acme", "Acme").fetch_recent_jobs(7))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "AI Engineer"
    assert job.source == "ashby"
    assert job.application_url == "https://jobs.ashbyhq.com/acme/z-1/application"
    assert job.workplace_type == "remote"
    assert job.posted_at is not None


# --------------------------------------------------------------------------- #
# Validation / placeholder rejection
# --------------------------------------------------------------------------- #
def _job(**kw: object) -> NormalizedJob:
    base = dict(
        external_id="1", title="Engineer", company="Acme", location="Remote", remote_type="remote",
        employment_type="ft", seniority_level=None, posted_at=None,
        application_url="https://jobs.lever.co/acme/1", source_url="https://jobs.lever.co/acme/1",
        description_raw="", description_clean="", source="lever",
    )
    base.update(kw)
    return NormalizedJob(**base)  # type: ignore[arg-type]


def test_placeholder_and_demo_urls_are_rejected() -> None:
    assert validate_job(_job()) is True
    assert validate_job(_job(application_url="https://example.com/apply")) is False
    assert validate_job(_job(source_url="http://localhost:3000/x")) is False
    assert validate_job(_job(application_url="https://test.com/apply")) is False
    assert validate_job(_job(company="DemoCo")) is False
    assert validate_job(_job(source="demo")) is False
    assert validate_job(_job(title="")) is False
    assert validate_job(_job(application_url="")) is False


def test_normalize_jobs_drops_placeholder_jobs() -> None:
    jobs = [
        _job(external_id="1", application_url="https://jobs.lever.co/acme/1"),
        _job(external_id="2", company="DemoCo", application_url="https://example.com/x"),
    ]
    normalized = normalize_jobs(jobs)
    assert len(normalized) == 1
    assert normalized[0].company == "Acme"


# --------------------------------------------------------------------------- #
# Demo cleanup + endpoint exclusion
# --------------------------------------------------------------------------- #
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


def _session(client: TestClient) -> Session:
    return next(app.dependency_overrides[get_db]())


def signup(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/auth/signup", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed_demo_and_real(db: Session) -> None:
    demo_source = JobSource(name="DemoCo", type="demo", base_url="https://example.com/careers", enabled=True, supports_api=True)
    real_source = JobSource(name="Acme", type="greenhouse", base_url="https://job-boards.greenhouse.io/acme", enabled=True, supports_api=True)
    db.add_all([demo_source, real_source])
    db.flush()
    db.add_all(
        [
            JobPosting(
                source_id=demo_source.id, external_id="d1", title="Demo Engineer", company="DemoCo",
                application_url="https://example.com/careers/d1", source_url="https://example.com/careers/d1",
                posted_at=datetime.now(UTC) - timedelta(days=1), discovered_at=datetime.now(UTC),
                description_raw="", description_clean="", hash_for_deduplication="demohash",
            ),
            JobPosting(
                source_id=real_source.id, external_id="r1", title="Real ML Engineer", company="Acme",
                application_url="https://job-boards.greenhouse.io/acme/jobs/r1",
                source_url="https://job-boards.greenhouse.io/acme/jobs/r1",
                posted_at=datetime.now(UTC) - timedelta(days=1), discovered_at=datetime.now(UTC),
                description_raw="", description_clean="Python role", hash_for_deduplication="realhash",
            ),
        ]
    )
    db.commit()


def test_get_jobs_never_returns_demo_records(client: TestClient) -> None:
    headers = signup(client, "demoexcl@example.com")
    _seed_demo_and_real(_session(client))

    body = client.get("/jobs", headers=headers, params={"include_unknown_dates": True}).json()
    companies = {job["company"] for job in body["jobs"]}
    titles = {job["title"] for job in body["jobs"]}
    assert "DemoCo" not in companies
    assert "Demo Engineer" not in titles
    assert not any("example.com" in job["application_url"] for job in body["jobs"])
    # The real posting is still present.
    assert "Real ML Engineer" in titles


def test_cleanup_removes_demo_records(client: TestClient) -> None:
    db = _session(client)
    _seed_demo_and_real(db)
    # Attach a match + tracker to the demo posting to prove cascading cleanup.
    demo = db.scalar(select(JobPosting).where(JobPosting.company == "DemoCo"))
    db.add(JobMatch(user_id=1, job_id=demo.id, fit_score=50, fit_summary="x"))
    db.add(ApplicationTracker(user_id=1, job_id=demo.id))
    db.commit()

    result = cleanup_demo_jobs(db)
    assert result["postings"] == 1
    assert result["sources"] == 1

    remaining = db.scalars(select(JobPosting)).all()
    assert [job.company for job in remaining] == ["Acme"]
    assert db.scalar(select(JobSource).where(JobSource.type == "demo")) is None
    assert db.scalars(select(JobMatch)).all() == []
    assert db.scalars(select(ApplicationTracker)).all() == []
