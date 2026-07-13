"""Regression tests for the deterministic resume parser and import apply flow.

The fixture is a realistic plain-text extraction of an ML Engineer resume. These
tests lock in the behavior that a resume with 3 jobs, 2 projects and an awards
section parses into exactly those, and that projects/awards never leak into the
experience list.
"""

from collections.abc import Generator
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.models import entities  # noqa: F401
from app.services.profile_import import normalize_import_draft
from app.services.resume_parser_service import parse_resume_text

FIXTURE = Path(__file__).parent / "fixtures" / "ml_engineer_resume.txt"


def resume_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


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
        json={"email": "parser@example.com", "password": "password123"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --------------------------------------------------------------------------- #
# Pure parser unit tests
# --------------------------------------------------------------------------- #
def test_header_extraction_is_correct() -> None:
    data = parse_resume_text(resume_text())
    basic = data["basic_info"]
    assert basic["full_name"] == "CHANDRA PRAKASH PANDEY"
    assert "Machine Learning Engineer" in basic["headline"]
    assert basic["email"] == "cprakash.work@gmail.com"
    assert basic["phone"] == "602-816-1309"
    assert basic["location_city"] == "Phoenix"
    assert basic["location_state"] == "AZ"
    assert "linkedin.com/in/" in data["links"]["linkedin_url"]
    assert data["links"]["portfolio_url"] == "https://cpandey.com"


def test_exactly_three_professional_experiences() -> None:
    data = parse_resume_text(resume_text())
    experience = data["experience"]
    assert len(experience) == 3
    companies = [item["company"] for item in experience]
    assert companies == ["VeoTrex", "Cardinal Health", "Platinum Venture Labs"]
    assert experience[0]["currently_working"] is True
    assert experience[0]["start_date"] == "Apr 2026"
    assert all(item["bullets"] for item in experience)


def test_exactly_two_selected_projects() -> None:
    data = parse_resume_text(resume_text())
    projects = data["projects"]
    assert len(projects) == 2
    names = [item["name"] for item in projects]
    assert names == ["LunaiCAD / SourceCAD AI Part Studio", "Luna AI - Video Analysis Platform"]
    assert projects[0]["subtitle"] == "AI copilot for parametric CAD modeling"
    assert projects[0]["bullets"]


def test_education_is_parsed() -> None:
    data = parse_resume_text(resume_text())
    assert len(data["education"]) == 1
    education = data["education"][0]
    assert education["school"] == "Arizona State University"
    assert "B.S." in education["degree"]
    assert education["minor"] == "Data Science"
    assert education["gpa"] == "3.6/4.0"
    assert "Magna Cum Laude" in education["honors"]


def test_awards_are_not_experience() -> None:
    data = parse_resume_text(resume_text())
    assert len(data["awards"]) == 3
    award_names = " ".join(item["name"] for item in data["awards"])
    assert "Namu Scholarship" in award_names
    assert "Published:" in award_names
    # None of the award or project names leaked into experience.
    titles = " ".join(item["title"].lower() for item in data["experience"])
    assert "scholarship" not in titles
    assert "lunaicad" not in titles


def test_skills_are_grouped() -> None:
    data = parse_resume_text(resume_text())
    categories = {group["category"] for group in data["skill_groups"]}
    assert {"Machine Learning", "Languages"} <= categories
    ml_group = next(g for g in data["skill_groups"] if g["category"] == "Machine Learning")
    assert "PyTorch" in ml_group["items"]
    assert "Python" in data["skills"]
    assert "PyTorch" in data["skills"]


def test_no_empty_experience_records() -> None:
    data = parse_resume_text(resume_text())
    for item in data["experience"]:
        assert item["company"] or item["title"]


def test_fallback_pipeline_does_not_crash_without_openai() -> None:
    # model_used == "deterministic-local" simulates OpenAI being unavailable.
    draft = normalize_import_draft({"summary": "x"}, resume_text(), "resume", "deterministic-local")
    assert len(draft.experience) == 3
    assert len(draft.projects) == 2
    assert len(draft.awards) == 3
    warnings = " ".join(draft.confidence_warnings)
    assert "AI parsing is unavailable" in warnings
    # No fake/empty experience warnings should appear for a clean resume.
    assert "no company or title" not in warnings
    assert draft.confidence.overall > 0


# --------------------------------------------------------------------------- #
# Endpoint + apply tests
# --------------------------------------------------------------------------- #
def _make_pdf(text: str) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    y = 780
    for line in text.splitlines():
        pdf.drawString(50, y, line)
        y -= 14
        if y < 40:
            pdf.showPage()
            y = 780
    pdf.save()
    return buffer.getvalue()


def test_pdf_upload_parses_resume_cleanly(client: TestClient) -> None:
    headers = auth_headers(client)
    response = client.post(
        "/profile/import/file",
        headers=headers,
        data={"source_type": "resume"},
        files={"file": ("resume.pdf", _make_pdf(resume_text()), "application/pdf")},
    )
    assert response.status_code == 200
    draft = response.json()["draft"]
    assert draft["basic_info"]["full_name"] == "CHANDRA PRAKASH PANDEY"
    assert len(draft["experience"]) == 3
    assert len(draft["projects"]) == 2
    assert len(draft["awards"]) == 3


def test_import_text_endpoint_parses_resume(client: TestClient) -> None:
    headers = auth_headers(client)
    response = client.post(
        "/profile/import/text",
        headers=headers,
        json={"source_type": "resume_text", "text": resume_text()},
    )
    assert response.status_code == 200
    draft = response.json()["draft"]
    assert len(draft["experience"]) == 3
    assert len(draft["projects"]) == 2
    assert len(draft["awards"]) == 3
    assert draft["basic_info"]["full_name"] == "CHANDRA PRAKASH PANDEY"


def _apply(client: TestClient, headers: dict[str, str], sections: list[str], overwrite: bool = False):
    draft = parse_resume_text(resume_text())
    draft["source_type"] = "resume"
    return client.post(
        "/profile/import/apply",
        headers=headers,
        json={"sections": sections, "draft": draft, "overwrite": overwrite},
    )


def test_accept_all_saves_selected_sections(client: TestClient) -> None:
    headers = auth_headers(client)
    response = _apply(
        client,
        headers,
        ["basic_info", "experience", "projects", "education", "awards", "skills", "links"],
    )
    assert response.status_code == 200
    career = response.json()["career"]
    assert len(career["experience"]) == 3
    assert len(career["projects"]) == 2
    assert len(career["awards"]) == 3
    assert response.json()["profile"]["full_name"] == "CHANDRA PRAKASH PANDEY"
    # No placeholder/empty rows were created.
    assert all(item["company"] for item in career["experience"])
    assert all(item["name"] for item in career["projects"])


def test_apply_selected_only_saves_chosen_sections(client: TestClient) -> None:
    headers = auth_headers(client)
    response = _apply(client, headers, ["skills"])
    assert response.status_code == 200
    body = response.json()
    assert "PyTorch" in body["profile"]["skills"]
    assert body["career"]["experience"] == []
    assert body["career"]["projects"] == []


def test_conflict_replacement_replaces_instead_of_duplicating(client: TestClient) -> None:
    headers = auth_headers(client)
    first = _apply(client, headers, ["experience"], overwrite=False)
    assert len(first.json()["career"]["experience"]) == 3
    # Re-importing with overwrite replaces rather than appending duplicates.
    second = _apply(client, headers, ["experience"], overwrite=True)
    assert len(second.json()["career"]["experience"]) == 3


def test_empty_experience_records_are_not_saved(client: TestClient) -> None:
    headers = auth_headers(client)
    draft = parse_resume_text(resume_text())
    draft["source_type"] = "resume"
    # Inject a junk empty experience the user did not remove.
    draft["experience"].append({"company": "", "title": "", "bullets": []})
    response = client.post(
        "/profile/import/apply",
        headers=headers,
        json={"sections": ["experience"], "draft": draft, "overwrite": True},
    )
    assert response.status_code == 200
    # The empty record is dropped; only the 3 real jobs persist.
    assert len(response.json()["career"]["experience"]) == 3
