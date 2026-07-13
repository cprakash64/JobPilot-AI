"""Posted-within date filtering (list/discover/refresh) and import replace/merge dedup."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.job_sources.base import JobSourceAdapter, NormalizedJob
from app.jobs import job_ingestion_service
from app.main import app
from app.models import entities as E
from app.models import entities  # noqa: F401


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


def auth(client: TestClient, email: str = "pw@example.com") -> dict[str, str]:
    token = client.post("/auth/signup", json={"email": email, "password": "password123"}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def complete_profile(client: TestClient, headers: dict[str, str]) -> None:
    client.put("/profile", headers=headers, json={
        "full_name": "Chandra Pandey", "target_roles": ["Backend Engineer"], "target_levels": ["Junior"],
        "preferred_locations": ["United States"], "remote_preference": "everything",
        "skills": ["Python", "FastAPI", "PostgreSQL"],
    })


def seed_job(client: TestClient, *, external: str, days: int, company="Acme") -> int:
    db = next(app.dependency_overrides[get_db]())
    src = db.scalar(select(E.JobSource).where(E.JobSource.name == company))
    if src is None:
        src = E.JobSource(name=company, type="greenhouse", base_url="x", enabled=True, supports_api=True)
        db.add(src)
        db.flush()
    job = E.JobPosting(
        source_id=src.id, external_id=external, title="Backend Engineer", company=company,
        location="Remote, United States", remote_type="remote",
        posted_at=datetime.now(UTC) - timedelta(days=days), discovered_at=datetime.now(UTC),
        application_url=f"https://boards.greenhouse.io/{company.lower()}/{external}",
        source_url=f"https://boards.greenhouse.io/{company.lower()}/{external}",
        description_raw="", description_clean="Backend Engineer. Requirements: Python, FastAPI.",
        required_skills=["Python", "FastAPI"], hash_for_deduplication=f"h-{external}",
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()
    return job_id


def _titles_ids(body: dict) -> set[str]:
    return {j["application_url"] for j in body["jobs"]}


# --------------------------------------------------------------------------- #
# GET /jobs posted_within_days
# --------------------------------------------------------------------------- #
def test_list_7_days_excludes_10_day_old(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    seed_job(client, external="recent", days=2)
    seed_job(client, external="old", days=10, company="Bcorp")

    body = client.get("/jobs?posted_within_days=7", headers=headers).json()
    urls = _titles_ids(body)
    assert any("recent" in u for u in urls)
    assert all("/bcorp/old" not in u for u in urls)


def test_list_15_days_includes_10_day_old(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    seed_job(client, external="recent", days=2)
    seed_job(client, external="old", days=10, company="Bcorp")

    body = client.get("/jobs?posted_within_days=15", headers=headers).json()
    urls = _titles_ids(body)
    assert any("/bcorp/old" in u for u in urls)


def test_list_30_days_includes_20_day_old(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    seed_job(client, external="mid", days=20, company="Ccorp")

    within_15 = client.get("/jobs?posted_within_days=15", headers=headers).json()
    within_30 = client.get("/jobs?posted_within_days=30", headers=headers).json()
    assert all("/ccorp/mid" not in u for u in _titles_ids(within_15))
    assert any("/ccorp/mid" in u for u in _titles_ids(within_30))


# --------------------------------------------------------------------------- #
# refresh-matches posted_within_days
# --------------------------------------------------------------------------- #
def test_refresh_respects_posted_within_days(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    seed_job(client, external="recent", days=2)
    seed_job(client, external="old", days=10, company="Bcorp")

    within_7 = client.post("/jobs/refresh-matches?posted_within_days=7", headers=headers).json()
    within_15 = client.post("/jobs/refresh-matches?posted_within_days=15", headers=headers).json()
    assert all("/bcorp/old" not in u for u in _titles_ids(within_7))
    assert any("/bcorp/old" in u for u in _titles_ids(within_15))


# --------------------------------------------------------------------------- #
# discover posted_within_days
# --------------------------------------------------------------------------- #
def _normalized(external: str, days: int, company: str = "Acme") -> NormalizedJob:
    url = f"https://job-boards.greenhouse.io/{company.lower()}/{external}"
    return NormalizedJob(
        external_id=external, title="Backend Engineer", company=company, location="Remote, United States",
        remote_type="remote", employment_type="full-time", seniority_level=None,
        posted_at=datetime.now(UTC) - timedelta(days=days), application_url=url, source_url=url,
        description_raw="", description_clean="Backend Engineer. Requirements: Python, FastAPI.",
        source="greenhouse", required_skills=["Python", "FastAPI"],
    )


class _FakeSource(JobSourceAdapter):
    source_type = "greenhouse"

    def __init__(self, jobs: list[NormalizedJob]) -> None:
        super().__init__("fake", "Acme")
        self._jobs = jobs

    async def fetch_recent_jobs(self, days: int = 7) -> list[NormalizedJob]:
        return list(self._jobs)


def test_discover_respects_posted_within_days(client: TestClient, monkeypatch) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    jobs = [_normalized("recent", 2), _normalized("old", 10, company="Bcorp")]
    monkeypatch.setattr(job_ingestion_service, "build_adapters", lambda *a, **k: [_FakeSource(jobs)])

    within_7 = client.post("/jobs/discover", headers=headers, json={"posted_within_days": 7}).json()
    assert all("/bcorp/old" not in u for u in _titles_ids(within_7))

    within_15 = client.post("/jobs/discover", headers=headers, json={"posted_within_days": 15}).json()
    assert any("/bcorp/old" in u for u in _titles_ids(within_15))


# --------------------------------------------------------------------------- #
# Import replace / merge de-duplication
# --------------------------------------------------------------------------- #
def _career(client: TestClient, headers: dict[str, str]) -> dict:
    return client.get("/profile/career", headers=headers).json()


def _apply(client, headers, *, sections, overwrite, draft):
    return client.post(
        "/profile/import/apply",
        headers=headers,
        json={"sections": sections, "overwrite": overwrite, "draft": draft},
    )


def test_import_replace_clears_old_records(client: TestClient) -> None:
    headers = auth(client)
    client.put("/profile/career", headers=headers, json={
        "education": [], "certifications": [], "awards": [], "projects": [],
        "experience": [{"company": "OldCorp", "title": "Intern"}],
    })
    resp = _apply(client, headers, sections=["experience"], overwrite=True, draft={
        "experience": [{"company": "NewCorp", "title": "Engineer"}],
    })
    assert resp.status_code == 200
    companies = [e["company"] for e in _career(client, headers)["experience"]]
    assert companies == ["NewCorp"]


def test_import_merge_dedupes_old_and_new(client: TestClient) -> None:
    headers = auth(client)
    client.put("/profile/career", headers=headers, json={
        "education": [], "certifications": [], "awards": [], "projects": [],
        "experience": [{"company": "SharedCorp", "title": "Engineer"}],
    })
    resp = _apply(client, headers, sections=["experience"], overwrite=False, draft={
        "experience": [
            {"company": "SharedCorp", "title": "Engineer"},   # duplicate of existing
            {"company": "FreshCorp", "title": "Engineer"},    # new
        ],
    })
    assert resp.status_code == 200
    companies = sorted(e["company"] for e in _career(client, headers)["experience"])
    assert companies == ["FreshCorp", "SharedCorp"]


def test_reimport_same_resume_twice_does_not_double(client: TestClient) -> None:
    headers = auth(client)
    draft = {
        "experience": [{"company": "Repeat Inc", "title": "Engineer"}],
        "projects": [{"name": "Luna AI"}],
        "awards": [{"name": "Dean's List"}],
    }
    for _ in range(2):
        assert _apply(client, headers, sections=["experience", "projects", "awards"], overwrite=False, draft=draft).status_code == 200
    career = _career(client, headers)
    assert len(career["experience"]) == 1
    assert len(career["projects"]) == 1
    assert len(career["awards"]) == 1


def test_import_drops_project_name_fragments(client: TestClient) -> None:
    headers = auth(client)
    resp = _apply(client, headers, sections=["projects"], overwrite=True, draft={
        "projects": [
            {"name": "Luna AI"},
            {"name": "validation states."},          # bullet fragment
            {"name": "timestamped video results."},  # bullet fragment
        ],
    })
    assert resp.status_code == 200
    names = [p["name"] for p in _career(client, headers)["projects"]]
    assert names == ["Luna AI"]
