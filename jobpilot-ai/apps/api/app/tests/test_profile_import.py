from collections.abc import Generator
from io import BytesIO

import pytest
from docx import Document
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.models import entities  # noqa: F401
from app.services.document_parser import MAX_UPLOAD_BYTES


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/auth/signup",
        json={"email": "import@example.com", "password": "password123"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def sample_profile_text() -> str:
    return "\n".join(
        [
            "Demo Student",
            "demo@example.com",
            "555-123-4567",
            "https://www.linkedin.com/in/demo-student",
            "https://github.com/demo",
            "Skills: Python, React, FastAPI, PostgreSQL",
            "Education",
            "Arizona State University",
            "Experience",
            "Backend Engineer Intern",
            "Built APIs for job search tools.",
            "Projects",
            "JobPilot AI",
        ]
    )


def make_pdf(text: str) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    y = 760
    for line in text.splitlines():
        pdf.drawString(72, y, line)
        y -= 18
    pdf.save()
    return buffer.getvalue()


def make_docx(text: str) -> bytes:
    buffer = BytesIO()
    document = Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    document.save(buffer)
    return buffer.getvalue()


def test_pdf_resume_upload_returns_import_draft(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.post(
        "/profile/import/file",
        headers=headers,
        data={"source_type": "resume"},
        files={"file": ("resume.pdf", make_pdf(sample_profile_text()), "application/pdf")},
    )

    assert response.status_code == 200
    draft = response.json()["draft"]
    assert draft["source_type"] == "resume"
    assert draft["basic_info"]["email"] == "demo@example.com"
    assert "Python" in draft["skills"]


def test_docx_resume_upload_returns_import_draft(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.post(
        "/profile/import/file",
        headers=headers,
        data={"source_type": "resume"},
        files={
            "file": (
                "resume.docx",
                make_docx(sample_profile_text()),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["draft"]["basic_info"]["full_name"] == "Demo Student"


def test_linkedin_pdf_source_type_is_preserved(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.post(
        "/profile/import/file",
        headers=headers,
        data={"source_type": "linkedin_pdf"},
        files={"file": ("linkedin.pdf", make_pdf(sample_profile_text()), "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["draft"]["source_type"] == "linkedin_pdf"


def test_invalid_file_extension_is_rejected(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.post(
        "/profile/import/file",
        headers=headers,
        data={"source_type": "resume"},
        files={"file": ("resume.exe", b"not a resume", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert "Unsupported file extension" in response.json()["detail"]


def test_oversized_file_is_rejected(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.post(
        "/profile/import/file",
        headers=headers,
        data={"source_type": "resume"},
        files={"file": ("resume.pdf", b"x" * (MAX_UPLOAD_BYTES + 1), "application/pdf")},
    )

    assert response.status_code == 400
    assert "too large" in response.json()["detail"]


def test_empty_pdf_returns_clear_error(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.post(
        "/profile/import/file",
        headers=headers,
        data={"source_type": "resume"},
        files={"file": ("resume.pdf", make_pdf(""), "application/pdf")},
    )

    assert response.status_code == 400
    assert "could not extract text" in response.json()["detail"]


def test_pasted_text_import_uses_fallback_without_sensitive_fields(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.post(
        "/profile/import/text",
        headers=headers,
        json={"source_type": "resume_text", "text": sample_profile_text()},
    )

    assert response.status_code == 200
    draft = response.json()["draft"]
    assert draft["source_type"] == "resume_text"
    assert draft["basic_info"]["full_name"] == "Demo Student"
    assert "AI parsing is unavailable" in " ".join(draft["confidence_warnings"])
    serialized = str(draft).lower()
    assert "gender" not in serialized
    assert "ethnicity" not in serialized
    assert "veteran_status" not in serialized


def sample_apply_draft() -> dict:
    return {
        "basic_info": {
            "full_name": "Imported Candidate",
            "email": "candidate@example.com",
            "phone": "555-111-2222",
            "location_city": "Phoenix",
            "location_state": "AZ",
            "location_country": "United States",
            "linkedin_url": "https://www.linkedin.com/in/imported",
            "github_url": "https://github.com/imported",
            "portfolio_url": "https://example.com",
            "work_authorization_status": "authorized_us",
            "requires_sponsorship": False,
            "gender": "should not persist",
        },
        "job_targets": {
            "target_roles": ["Backend Engineer"],
            "target_levels": ["New Grad"],
            "preferred_locations": ["Remote"],
            "work_preference": "remote",
        },
        "education": [
            {
                "school": "Arizona State University",
                "degree": "MS",
                "major": "Computer Science",
                "start_date": "2024-08-01",
                "end_date": "2026-05-01",
                "honors": ["Dean's List"],
                "coursework": ["Distributed Systems"],
            }
        ],
        "experience": [
            {
                "company": "Acme",
                "title": "Backend Engineer Intern",
                "location": "Phoenix, AZ",
                "start_date": "2025-05-01",
                "end_date": "2025-08-01",
                "currently_working": False,
                "bullets": ["Built FastAPI services"],
                "technologies": ["Python", "FastAPI"],
                "measurable_impact": ["Reduced latency"],
            }
        ],
        "projects": [
            {
                "name": "JobPilot AI",
                "description": "Job search assistant",
                "bullets": ["Built profile import"],
                "technologies": ["React"],
                "links": ["https://example.com/jobpilot"],
                "start_date": "2025-01-01",
                "end_date": "2025-04-01",
            }
        ],
        "skills": ["Python", "FastAPI"],
        "certifications": [
            {
                "name": "AWS Cloud Practitioner",
                "issuer": "AWS",
                "issue_date": "2025-01-01",
                "expiration_date": "",
                "credential_url": "https://example.com/cert",
            }
        ],
        "awards": [
            {
                "name": "Hackathon Winner",
                "issuer": "ASU",
                "date": "2025-02-01",
                "description": "First place",
            }
        ],
        "links": {
            "linkedin_url": "https://www.linkedin.com/in/imported",
            "github_url": "https://github.com/imported",
            "portfolio_url": "https://example.com",
        },
        "source_type": "resume_text",
        "confidence_warnings": [],
        "missing_fields": [],
        "low_confidence_fields": [],
        "raw_text_preview": "Imported Candidate",
        "ethnicity": "should not persist",
    }


def test_apply_import_all_sections_persists_profile_and_career(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.post(
        "/profile/import/apply",
        headers=headers,
        json={
            "sections": [
                "basic_info",
                "job_targets",
                "education",
                "experience",
                "projects",
                "skills",
                "certifications",
                "awards",
                "links",
            ],
            "draft": sample_apply_draft(),
            "overwrite": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["full_name"] == "Imported Candidate"
    assert body["profile"]["work_preference"] == "remote"
    assert "FastAPI" in body["profile"]["skills"]
    assert body["career"]["education"][0]["school"] == "Arizona State University"
    assert body["career"]["experience"][0]["title"] == "Backend Engineer Intern"
    assert body["career"]["projects"][0]["name"] == "JobPilot AI"
    assert body["career"]["certifications"][0]["name"] == "AWS Cloud Practitioner"
    assert body["career"]["awards"][0]["name"] == "Hackathon Winner"

    persisted = client.get("/profile", headers=headers)
    assert persisted.json()["profile"]["full_name"] == "Imported Candidate"
    career = client.get("/profile/career", headers=headers).json()
    assert career["projects"][0]["name"] == "JobPilot AI"


def test_apply_import_selected_sections_preserves_unchecked_profile_data(client: TestClient) -> None:
    headers = auth_headers(client)
    client.put(
        "/profile",
        headers=headers,
        json={
            "full_name": "Existing Candidate",
            "skills": ["SQL"],
            "remote_preference": "everything",
        },
    )

    response = client.post(
        "/profile/import/apply",
        headers=headers,
        json={"sections": ["skills"], "draft": sample_apply_draft(), "overwrite": False},
    )

    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["full_name"] == "Existing Candidate"
    assert profile["skills"] == ["SQL", "Python", "FastAPI"]
    assert profile["target_roles"] == []
    assert response.json()["career"]["education"] == []


def test_apply_import_ignores_sensitive_demographics(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.post(
        "/profile/import/apply",
        headers=headers,
        json={"sections": ["basic_info"], "draft": sample_apply_draft(), "overwrite": True},
    )

    assert response.status_code == 200
    serialized = str(response.json()).lower()
    assert "gender" not in serialized
    assert "ethnicity" not in serialized
    assert "veteran_status" not in serialized


def test_apply_import_rejects_invalid_draft(client: TestClient) -> None:
    headers = auth_headers(client)
    draft = sample_apply_draft()
    draft["source_type"] = "invalid_source"

    response = client.post(
        "/profile/import/apply",
        headers=headers,
        json={"sections": ["basic_info"], "draft": draft, "overwrite": False},
    )

    assert response.status_code == 422


def test_apply_import_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/profile/import/apply",
        json={"sections": ["basic_info"], "draft": sample_apply_draft(), "overwrite": False},
    )

    assert response.status_code == 401
