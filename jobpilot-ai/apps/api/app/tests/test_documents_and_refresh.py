"""Tests for refresh-matches, resume/cover-letter generation, and guardrails."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.documents.document_guardrail_service import build_profile_facts, validate_resume
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


def auth(client: TestClient, email: str = "doc@example.com") -> dict[str, str]:
    token = client.post("/auth/signup", json={"email": email, "password": "password123"}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def complete_profile(client: TestClient, headers: dict[str, str]) -> None:
    client.put("/profile", headers=headers, json={
        "full_name": "Chandra Pandey", "target_roles": ["Backend Engineer"], "target_levels": ["Junior"],
        "preferred_locations": ["United States"], "remote_preference": "everything",
        "skills": ["Python", "FastAPI", "PostgreSQL"],
    })
    client.put("/profile/career", headers=headers, json={
        "education": [{"school": "Arizona State University", "degree": "BS"}],
        "experience": [{"company": "Cardinal Health", "title": "ML Engineer Intern",
                        "bullets": ["Built Python services", "Improved latency by 43%"],
                        "technologies": ["Python", "FastAPI"]}],
        "projects": [{"name": "Luna AI", "description": "Video platform", "bullets": ["Built with Python"],
                      "technologies": ["Python"]}],
        "certifications": [], "awards": [],
    })


def seed_job(client: TestClient, *, required=("Python", "FastAPI", "Kubernetes", "Go"), title="Backend Engineer",
             location="Remote, United States", company="Acme", days=1) -> int:
    db = next(app.dependency_overrides[get_db]())
    src = db.scalar(select(E.JobSource).where(E.JobSource.name == company))
    if src is None:
        src = E.JobSource(name=company, type="greenhouse", base_url="x", enabled=True, supports_api=True)
        db.add(src)
        db.flush()
    job = E.JobPosting(
        source_id=src.id, external_id=f"{title}-{company}", title=title, company=company, location=location,
        remote_type="remote", posted_at=datetime.now(UTC) - timedelta(days=days), discovered_at=datetime.now(UTC),
        application_url=f"https://boards.greenhouse.io/{company.lower()}/1",
        source_url=f"https://boards.greenhouse.io/{company.lower()}/1",
        description_raw="", description_clean=f"{title}. Requirements: {', '.join(required)}.",
        required_skills=list(required), hash_for_deduplication=f"h-{title}-{company}",
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()
    return job_id


# --------------------------------------------------------------------------- #
# Refresh matches
# --------------------------------------------------------------------------- #
def test_refresh_requires_auth(client: TestClient) -> None:
    assert client.post("/jobs/refresh-matches").status_code == 401


def test_refresh_incomplete_profile_returns_422(client: TestClient) -> None:
    headers = auth(client)
    response = client.post("/jobs/refresh-matches", headers=headers)
    assert response.status_code == 422
    assert "Complete your profile" in response.json()["detail"]


def test_refresh_rescore_existing_jobs_schema(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    seed_job(client)  # eligible
    seed_job(client, title="Backend Engineer", company="Bcorp", location="Toronto, Canada")  # excluded

    response = client.post("/jobs/refresh-matches", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert "jobs" in body and "summary" in body
    summary = body["summary"]
    for key in ["matched_count", "rescored_count", "raw_jobs_considered", "excluded_by_location",
                "excluded_by_seniority", "excluded_by_role", "excluded_by_date", "source_warnings"]:
        assert key in summary
    # Canada job excluded; only the US job shows.
    assert all("Canada" not in (j["location"] or "") for j in body["jobs"])
    assert summary["matched_count"] >= 1


def test_refresh_empty_index_is_clean(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    response = client.post("/jobs/refresh-matches", headers=headers)
    assert response.status_code == 200
    assert response.json()["jobs"] == []


def test_refresh_does_not_return_demo_jobs(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    seed_job(client, company="DemoCo", location="Remote, United States")  # demo company
    body = client.post("/jobs/refresh-matches", headers=headers).json()
    assert all(j["company"] != "DemoCo" for j in body["jobs"])


# --------------------------------------------------------------------------- #
# Resume generation
# --------------------------------------------------------------------------- #
def test_generate_resume_endpoint(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    response = client.post(f"/jobs/{job_id}/generate-resume", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["document_type"] == "resume"
    assert body["title"] == "Tailored Resume - Acme - Backend Engineer"
    # Skills are grouped; only supported skills appear, unsupported job skills are missing.
    resume_skills = {item for group in body["content"]["skills"] for item in group["items"]}
    assert resume_skills <= {"Python", "FastAPI", "PostgreSQL"}
    assert "Kubernetes" in body["content"]["missing_skills"]
    assert "Kubernetes" not in resume_skills
    assert any("Missing skills" in w for w in body["warnings"])
    assert body["quality"]["missing_job_skills_not_claimed"] == body["content"]["missing_skills"]
    # Real experience company preserved; no invented companies.
    companies = [e["company"] for e in body["content"]["experience"]]
    assert companies == ["Cardinal Health"]


def test_generate_resume_stores_document(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    body = client.post(f"/jobs/{job_id}/generate-resume", headers=headers).json()
    gen = app.dependency_overrides[get_db]()
    db = next(gen)
    record = db.get(E.GeneratedDocument, body["document_id"])
    assert record is not None
    assert record.type == E.DocumentType.resume
    assert record.content_markdown
    assert record.source_profile_snapshot  # snapshot stored for audit
    db.close()


def test_generate_resume_dedupes_education_and_keeps_projects(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    # Simulate a legacy bad import: Arizona State University saved twice.
    client.put("/profile/career", headers=headers, json={
        "education": [
            {"school": "Arizona State University", "degree": "B.S.", "major": "Computer Science"},
            {"school": "Arizona State University", "degree": "Bachelor of Science", "major": "Computer Science"},
        ],
        "experience": [{"company": "Cardinal Health", "title": "ML Engineer Intern",
                        "bullets": ["Built Python services"], "technologies": ["Python", "FastAPI"]}],
        "projects": [
            {"name": "Luna AI", "description": "AI video platform",
             "bullets": ["Built RAG pipeline with Python and FastAPI"], "technologies": ["Python", "FastAPI"]},
            {"name": "VeoTrex", "description": "CV pipeline", "bullets": ["Trained models"], "technologies": ["PyTorch"]},
        ],
        "certifications": [], "awards": [],
    })
    job_id = seed_job(client)
    content = client.post(f"/jobs/{job_id}/generate-resume", headers=headers).json()["content"]

    # Education appears exactly once (deduped before rendering/export).
    schools = [e["school"] for e in content["education"]]
    assert schools.count("Arizona State University") == 1
    # Selected Projects section is present and relevant.
    project_names = [p["name"] for p in content["projects"]]
    assert project_names, "resume must keep a Selected Projects section"
    assert "Luna AI" in project_names


def test_generate_resume_template_mode_without_openai(client: TestClient) -> None:
    from app.ai.provider import ai_provider
    assert ai_provider.client is None  # no OPENAI key in tests
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    body = client.post(f"/jobs/{job_id}/generate-resume", headers=headers).json()
    assert any("template mode" in w for w in body["warnings"])


def test_generate_resume_incomplete_profile_422(client: TestClient) -> None:
    headers = auth(client)
    job_id = seed_job(client)  # no profile saved
    assert client.post(f"/jobs/{job_id}/generate-resume", headers=headers).status_code == 422


# --------------------------------------------------------------------------- #
# Cover letter
# --------------------------------------------------------------------------- #
def test_generate_cover_letter(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    body = client.post(f"/jobs/{job_id}/generate-cover-letter", headers=headers).json()
    assert body["document_type"] == "cover_letter"
    letter = body["content"]
    assert letter["company"] == "Acme"
    assert letter["role"] == "Backend Engineer"
    assert isinstance(letter["paragraphs"], list) and len(letter["paragraphs"]) == 3
    joined = " ".join(letter["paragraphs"])
    assert "Acme" in joined and "Backend Engineer" in joined
    assert 150 <= len(joined.split()) <= 300
    assert any("template mode" in w for w in body["warnings"])


# --------------------------------------------------------------------------- #
# Guardrails
# --------------------------------------------------------------------------- #
def _facts():
    payload = {
        "profile": {"full_name": "A", "skills": ["Python", "FastAPI"], "work_authorization": ""},
        "experience": [{"company": "Cardinal Health", "title": "ML Engineer Intern",
                        "bullets": ["Improved latency by 43%"], "technologies": ["Python"]}],
        "projects": [{"name": "Luna AI", "technologies": ["Python"]}],
        "education": [{"school": "ASU"}],
        "certifications": [],
        "awards": [],
    }
    return build_profile_facts(payload)


def test_guardrail_removes_unsupported_skill() -> None:
    content = {"skills": ["Python", "Rust"], "experience": [], "projects": [], "education": [], "certifications": []}
    clean, removed = validate_resume(content, _facts())
    assert "Rust" not in clean["skills"]
    assert "skill: Rust" in removed


def test_guardrail_removes_fake_company() -> None:
    content = {"skills": [], "experience": [{"company": "Fake Corp", "title": "ML Engineer Intern", "bullets": []}],
               "projects": [], "education": [], "certifications": []}
    clean, removed = validate_resume(content, _facts())
    assert clean["experience"] == []
    assert any("company: Fake Corp" in r for r in removed)


def test_guardrail_removes_fake_metric() -> None:
    content = {"skills": [], "experience": [{"company": "Cardinal Health", "title": "ML Engineer Intern",
               "bullets": ["Improved latency by 43%", "Cut costs by 99%"]}], "projects": [], "education": [],
               "certifications": []}
    clean, removed = validate_resume(content, _facts())
    kept = clean["experience"][0]["bullets"]
    assert "Improved latency by 43%" in kept   # 43 exists in profile
    assert "Cut costs by 99%" not in kept       # 99 invented
    assert any("metric" in r for r in removed)


def test_guardrail_removes_invented_work_authorization() -> None:
    content = {"header": {"work_authorization": "US Citizen"}, "skills": [], "experience": [], "projects": [],
               "education": [], "certifications": []}
    clean, removed = validate_resume(content, _facts())
    assert clean["header"]["work_authorization"] == ""
    assert any("work authorization" in r for r in removed)
