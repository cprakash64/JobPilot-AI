from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.models import entities  # noqa: F401


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
        json={"email": "profile@example.com", "password": "password123"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_update_profile_with_structured_targets(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.put(
        "/profile",
        headers=headers,
        json={
            "full_name": "Test Candidate",
            "work_authorization_status": "authorized_us",
            "requires_sponsorship": False,
            "target_roles": ["Software Engineer", "Backend Engineer"],
            "target_levels": ["New Grad", "Junior"],
            "preferred_locations": ["Remote", "Phoenix, AZ"],
            "work_preference": "hybrid",
            "skills": ["Python", "FastAPI"],
        },
    )

    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["work_authorization_status"] == "authorized_us"
    assert profile["target_roles"] == ["Software Engineer", "Backend Engineer"]
    assert profile["target_levels"] == ["New Grad", "Junior"]
    assert profile["preferred_locations"] == ["Remote", "Phoenix, AZ"]
    assert profile["work_preference"] == "hybrid"


def test_career_profile_replace_supports_education_experience_projects(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.put(
        "/profile/career",
        headers=headers,
        json={
            "education": [
                {
                    "school": "Arizona State University",
                    "degree": "BS",
                    "major": "Computer Science",
                    "honors": ["Dean's List"],
                    "coursework": ["Algorithms"],
                }
            ],
            "experience": [
                {
                    "company": "DemoCo",
                    "title": "Backend Engineer Intern",
                    "bullets": ["Built APIs"],
                    "technologies": ["Python"],
                    "measurable_impact": ["Reduced manual work"],
                }
            ],
            "projects": [
                {
                    "name": "JobPilot",
                    "description": "Career platform",
                    "bullets": ["Built profile wizard"],
                    "technologies": ["React"],
                    "links": ["https://example.com"],
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["education"][0]["school"] == "Arizona State University"
    assert body["experience"][0]["title"] == "Backend Engineer Intern"
    assert body["projects"][0]["name"] == "JobPilot"


def test_profile_import_from_pasted_text_returns_structured_draft(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.post(
        "/profile/import",
        headers=headers,
        json={
            "text": "\n".join(
                [
                    "Test Candidate",
                    "https://www.linkedin.com/in/test-candidate",
                    "Skills: Python, FastAPI, React",
                    "Arizona State University",
                    "Backend Engineer Intern",
                ]
            )
        },
    )

    assert response.status_code == 200
    draft = response.json()["draft"]
    assert draft["basic_info"]["full_name"] == "Test Candidate"
    assert draft["skills"] == ["Python", "FastAPI", "React"]
    assert draft["links"]["linkedin_url"] == "https://www.linkedin.com/in/test-candidate"
    assert "salary" not in draft["basic_info"]


def test_profile_import_requires_user_supplied_text(client: TestClient) -> None:
    headers = auth_headers(client)

    response = client.post("/profile/import", headers=headers, json={"text": "short"})

    assert response.status_code == 422
