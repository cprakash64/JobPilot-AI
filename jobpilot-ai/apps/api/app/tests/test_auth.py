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


def test_signup_succeeds(client: TestClient) -> None:
    response = client.post(
        "/auth/signup",
        json={"email": "signup@example.com", "password": "password123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


def test_duplicate_signup_returns_clean_error(client: TestClient) -> None:
    payload = {"email": "duplicate@example.com", "password": "password123"}

    first = client.post("/auth/signup", json=payload)
    second = client.post("/auth/signup", json=payload)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == "Email already registered"


def test_login_succeeds(client: TestClient) -> None:
    payload = {"email": "login@example.com", "password": "password123"}
    client.post("/auth/signup", json=payload)

    response = client.post("/auth/login", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


def test_bad_login_returns_401(client: TestClient) -> None:
    client.post(
        "/auth/signup",
        json={"email": "bad-login@example.com", "password": "password123"},
    )

    response = client.post(
        "/auth/login",
        json={"email": "bad-login@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_me_works_with_token(client: TestClient) -> None:
    signup = client.post(
        "/auth/signup",
        json={"email": "me@example.com", "password": "password123"},
    )
    token = signup.json()["access_token"]

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"


def test_me_without_token_returns_401(client: TestClient) -> None:
    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing token"
