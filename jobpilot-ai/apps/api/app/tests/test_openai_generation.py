"""Tests for AI-backed resume/cover-letter generation and OpenAI diagnostics."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import openai
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai import provider as provider_module
from app.ai.provider import AIProvider, AIResult, ai_provider
from app.api.deps import get_db
from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.models import entities as E
from app.models import entities  # noqa: F401


def run(coro):
    return asyncio.run(coro)


def _openai_error(cls, status_code):
    response = type("R", (), {"status_code": status_code, "request": None, "headers": {}})()
    return cls("boom", response=response, body=None)


# --------------------------------------------------------------------------- #
# Settings + provider status
# --------------------------------------------------------------------------- #
def test_settings_load_openai_models() -> None:
    assert settings.openai_model_smart  # loaded from env or default
    assert settings.openai_model_fast


def test_provider_disabled_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_module.settings, "openai_api_key", None)
    provider = AIProvider()
    assert provider.provider_enabled is False
    assert provider.api_key_present is False
    result = run(provider.json_task("tailor_resume.md", {"profile": {}}, smart=True))
    assert result.ai_used is False
    assert result.error_reason == "no_key"


def test_provider_enabled_with_key_and_status_hides_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_module.settings, "openai_api_key", "sk-secret-value-123")
    provider = AIProvider()
    assert provider.provider_enabled is True
    assert provider.api_key_present is True
    status = provider.status()
    # The status dict never contains the actual key.
    assert "sk-secret-value-123" not in str(status)
    assert status["smart_model"] == settings.openai_model_smart
    assert set(status) == {"api_key_present", "smart_model", "fast_model", "provider_enabled", "last_error"}


def test_model_not_found_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_module.settings, "openai_api_key", "sk-test")
    provider = AIProvider()

    async def boom(prompt_name, payload, model):
        raise _openai_error(openai.NotFoundError, 404)

    monkeypatch.setattr(provider, "_call", boom)
    result = run(provider.json_task("tailor_resume.md", {}, smart=True))
    assert result.ai_used is False
    assert result.error_reason == "model_not_found"
    assert "model was not found" in result.error
    # Sanitized error recorded, no secret.
    assert "sk-test" not in (provider.last_error or "")


def test_auth_error_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_module.settings, "openai_api_key", "sk-test")
    provider = AIProvider()

    async def boom(prompt_name, payload, model):
        raise _openai_error(openai.AuthenticationError, 401)

    monkeypatch.setattr(provider, "_call", boom)
    result = run(provider.json_task("tailor_resume.md", {}, smart=True))
    assert result.error_reason == "auth"


def test_smart_model_falls_back_to_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_module.settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(provider_module.settings, "openai_model_smart", "smart-x")
    monkeypatch.setattr(provider_module.settings, "openai_model_fast", "fast-y")
    provider = AIProvider()
    calls: list[str] = []

    async def call(prompt_name, payload, model):
        calls.append(model)
        if model == "smart-x":
            raise _openai_error(openai.NotFoundError, 404)
        return {"summary": "ok"}

    monkeypatch.setattr(provider, "_call", call)
    result = run(provider.json_task("tailor_resume.md", {}, smart=True))
    assert result.ai_used is True
    assert result.model_used == "fast-y"
    assert calls == ["smart-x", "fast-y"]


# --------------------------------------------------------------------------- #
# Endpoint-level: AI used / fallback
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


def _setup(client: TestClient) -> tuple[dict, int]:
    token = client.post("/auth/signup", json={"email": "ai@example.com", "password": "password123"}).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    client.put("/profile", headers=headers, json={
        "full_name": "Chandra Pandey", "target_roles": ["Backend Engineer"], "target_levels": ["Junior"],
        "preferred_locations": ["United States"], "remote_preference": "everything",
        "skills": ["Python", "FastAPI", "PostgreSQL"],
    })
    client.put("/profile/career", headers=headers, json={
        "education": [{"school": "Arizona State University", "degree": "BS"}],
        "experience": [{"company": "Cardinal Health", "title": "ML Engineer Intern",
                        "bullets": ["Built Python services"], "technologies": ["Python", "FastAPI"]}],
        "projects": [], "certifications": [], "awards": [],
    })
    db = next(app.dependency_overrides[get_db]())
    src = E.JobSource(name="Acme", type="greenhouse", base_url="x", enabled=True, supports_api=True)
    db.add(src)
    db.flush()
    job = E.JobPosting(
        source_id=src.id, external_id="1", title="Backend Engineer", company="Acme",
        location="Remote, United States", remote_type="remote", posted_at=datetime.now(UTC) - timedelta(days=1),
        discovered_at=datetime.now(UTC), application_url="https://boards.greenhouse.io/acme/1",
        source_url="https://boards.greenhouse.io/acme/1", description_raw="",
        description_clean="Requirements: Python, FastAPI, Kubernetes.", required_skills=["Python", "FastAPI", "Kubernetes"],
        hash_for_deduplication="h1",
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()
    return headers, job_id


def _fake_json_task(data: dict, *, ai_used=True, model="gpt-5.5", error=None, error_reason=None):
    async def fake(prompt_name, payload, smart=True):
        return AIResult(data=data, model_used=model if ai_used else "deterministic-local",
                        ai_used=ai_used, error=error, error_reason=error_reason)
    return fake


def test_resume_uses_ai_and_has_no_template_warning(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers, job_id = _setup(client)
    ai_resume = {
        "summary": "Backend engineer with production Python and FastAPI experience.",
        "skills": ["FastAPI", "Python", "PostgreSQL"],
        "experience": [{"company": "Cardinal Health", "title": "ML Engineer Intern",
                        "bullets": ["Engineered Python services powering production workflows"]}],
        "projects": [],
    }
    monkeypatch.setattr(ai_provider, "json_task", _fake_json_task(ai_resume))
    body = client.post(f"/jobs/{job_id}/generate-resume", headers=headers).json()

    assert body["model_used"] == "gpt-5.5"
    assert not any("template mode" in w for w in body["warnings"])
    # AI summary + rephrased bullet applied; entity preserved.
    assert body["content"]["summary"].startswith("Backend engineer")
    assert body["content"]["experience"][0]["company"] == "Cardinal Health"
    assert "Engineered Python services powering production workflows" in body["content"]["experience"][0]["bullets"]


def test_resume_ai_failure_shows_specific_reason(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers, job_id = _setup(client)
    monkeypatch.setattr(
        ai_provider, "json_task",
        _fake_json_task({}, ai_used=False, error="the configured model was not found", error_reason="model_not_found"),
    )
    body = client.post(f"/jobs/{job_id}/generate-resume", headers=headers).json()
    assert any("template mode because the configured model was not found" in w for w in body["warnings"])


def test_resume_guardrail_removes_ai_invented_metric(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers, job_id = _setup(client)
    ai_resume = {
        "summary": "Solid engineer.",
        "skills": ["Python"],
        "experience": [{"company": "Cardinal Health", "title": "ML Engineer Intern",
                        "bullets": ["Cut infra costs by 99% single-handedly"]}],  # 99 not in profile
        "projects": [],
    }
    monkeypatch.setattr(ai_provider, "json_task", _fake_json_task(ai_resume))
    body = client.post(f"/jobs/{job_id}/generate-resume", headers=headers).json()
    bullets = body["content"]["experience"][0]["bullets"]
    assert not any("99%" in b for b in bullets)  # invented metric removed by guardrail
    assert any("metric" in r for r in body["unsupported_claims_removed"])


def test_cover_letter_uses_ai_and_no_template_warning(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers, job_id = _setup(client)
    body_text = "I am excited to apply for the Backend Engineer role at Acme. " * 12
    monkeypatch.setattr(
        ai_provider, "json_task",
        _fake_json_task({"paragraphs": [body_text, "Second paragraph about my experience at Acme.", "Closing paragraph."]}),
    )
    body = client.post(f"/jobs/{job_id}/generate-cover-letter", headers=headers).json()
    assert body["model_used"] == "gpt-5.5"
    assert not any("template mode" in w for w in body["warnings"])
    assert "Acme" in " ".join(body["content"]["paragraphs"])


def test_cover_letter_ai_failure_falls_back(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers, job_id = _setup(client)
    monkeypatch.setattr(
        ai_provider, "json_task",
        _fake_json_task({}, ai_used=False, error="the OpenAI API key was rejected", error_reason="auth"),
    )
    body = client.post(f"/jobs/{job_id}/generate-cover-letter", headers=headers).json()
    assert any("template mode because the OpenAI API key was rejected" in w for w in body["warnings"])
    assert "Acme" in " ".join(body["content"]["paragraphs"])  # template still names the company


# --------------------------------------------------------------------------- #
# Debug endpoint
# --------------------------------------------------------------------------- #
def test_openai_status_requires_auth(client: TestClient) -> None:
    assert client.get("/debug/openai-status").status_code == 401


def test_openai_status_returns_sanitized_status(client: TestClient) -> None:
    token = client.post("/auth/signup", json={"email": "dbg@example.com", "password": "password123"}).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    body = client.get("/debug/openai-status", headers=headers).json()
    assert set(body) == {"api_key_present", "smart_model", "fast_model", "provider_enabled", "last_error"}
    assert isinstance(body["api_key_present"], bool)
