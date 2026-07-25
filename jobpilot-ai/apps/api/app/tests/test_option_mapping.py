"""Section E safety rules for AI dropdown-option mapping.

The model may only ever TRANSLATE an already-confirmed answer into an employer's
exact option wording. It may never originate an answer, and it may never return
a label we did not supply. Both rules are enforced in code, so a
misbehaving/prompt-injected model cannot put a wrong answer on a real
application.
"""

import asyncio
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import entities  # noqa: F401

from app.ai.provider import AIResult
from app.applications import option_mapping_service as svc

OPTIONS = ["Yes, I will require sponsorship", "No, I will not require sponsorship"]


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


def _session(client):
    """Create an authenticated application session; returns (headers, session_id)."""
    from app.tests.test_applications import auth, complete_profile, create_session, seed_job  # noqa: PLC0415

    headers = auth(client, "mapopt@example.com")
    complete_profile(client, headers)
    job_id = seed_job(client)
    return headers, create_session(client, headers, job_id)["session_id"]


def run(coro):
    """This repo has no async pytest plugin; drive coroutines directly."""
    return asyncio.run(coro)


@pytest.fixture()
def fake_ai(monkeypatch):
    """Install a stub AI response; records whether the model was called at all."""
    calls: list[dict] = []

    def install(data: dict, ai_used: bool = True):
        async def fake_json_task(prompt_name, payload, smart=True):
            calls.append(payload)
            return AIResult(data=data, model_used="stub", ai_used=ai_used)

        monkeypatch.setattr(svc.ai_provider, "json_task", fake_json_task)
        return calls

    return install


def test_maps_a_confirmed_answer_to_the_exact_employer_label(fake_ai):
    fake_ai({
        "selected_option_label": "No, I will not require sponsorship",
        "confidence": 0.97,
        "requires_user_confirmation": False,
        "reason": "Candidate confirmed they do not need sponsorship.",
    })
    result = run(svc.map_option(
        question_label="Will you now or in the future require sponsorship?",
        options=OPTIONS,
        canonical_key="sponsorship_required_future",
        confirmed_answer="No",
    ))
    assert result.selected_option_label == "No, I will not require sponsorship"
    assert result.usable is True


def test_a_label_the_model_invented_is_discarded(fake_ai):
    fake_ai({
        "selected_option_label": "No (I do not need sponsorship)",  # not in OPTIONS
        "confidence": 0.99,
        "requires_user_confirmation": False,
        "reason": "paraphrased",
    })
    result = run(svc.map_option(
        question_label="Sponsorship?", options=OPTIONS,
        canonical_key="sponsorship_required_future", confirmed_answer="No",
    ))
    assert result.selected_option_label is None
    assert result.requires_user_confirmation is True
    assert result.usable is False


@pytest.mark.parametrize(
    "canonical_key",
    ["sponsorship_required_future", "work_authorization_us", "gender", "race",
     "disability_status", "veteran_status", "criminal_history", "consent_processing",
     "policy_acknowledgement"],
)
def test_consequential_questions_never_reach_the_model_without_a_confirmed_answer(fake_ai, canonical_key):
    calls = fake_ai({"selected_option_label": OPTIONS[0], "confidence": 1.0,
                     "requires_user_confirmation": False, "reason": "x"})
    result = run(svc.map_option(
        question_label="A consequential question",
        options=OPTIONS,
        canonical_key=canonical_key,
        confirmed_answer=None,
    ))
    # The model was never even asked — it cannot invent a legal/EEO answer.
    assert calls == []
    assert result.selected_option_label is None
    assert result.requires_user_confirmation is True


def test_low_confidence_always_requires_user_confirmation(fake_ai):
    fake_ai({
        "selected_option_label": OPTIONS[0],
        "confidence": 0.55,
        "requires_user_confirmation": False,  # model claims otherwise
        "reason": "unsure",
    })
    result = run(svc.map_option(
        question_label="Sponsorship?", options=OPTIONS,
        canonical_key="sponsorship_required_future", confirmed_answer="Yes",
    ))
    assert result.requires_user_confirmation is True
    assert result.usable is False


def test_null_selection_is_ask_the_user_not_a_guess(fake_ai):
    fake_ai({"selected_option_label": None, "confidence": 0.0,
             "requires_user_confirmation": True, "reason": "no match"})
    result = run(svc.map_option(
        question_label="How did you hear about us?",
        options=["LinkedIn", "Job board"],
        canonical_key="referral_source",
        confirmed_answer="A recruiter emailed me",
    ))
    assert result.selected_option_label is None
    assert result.usable is False


def test_provider_failure_falls_back_to_asking_the_user(fake_ai):
    fake_ai({}, ai_used=False)
    result = run(svc.map_option(
        question_label="Country", options=["United States", "Canada"],
        canonical_key="country", confirmed_answer="USA",
    ))
    assert result.selected_option_label is None
    assert result.requires_user_confirmation is True


def test_no_options_means_nothing_to_map(fake_ai):
    calls = fake_ai({"selected_option_label": "x", "confidence": 1.0,
                     "requires_user_confirmation": False, "reason": ""})
    result = run(svc.map_option(
        question_label="Country", options=[], canonical_key="country", confirmed_answer="USA",
    ))
    assert calls == []
    assert result.selected_option_label is None


def test_only_the_confirmed_answer_and_options_are_sent_to_the_model(fake_ai):
    calls = fake_ai({"selected_option_label": "United States", "confidence": 0.99,
                     "requires_user_confirmation": False, "reason": "ok"})
    run(svc.map_option(
        question_label="Country", options=["United States", "Canada"],
        canonical_key="country", confirmed_answer="USA", help_text="Select your country",
    ))
    payload = calls[0]
    assert set(payload) == {"question_label", "help_text", "canonical_key", "confirmed_answer", "options"}
    # No profile, name, email, or session data is ever included.
    assert payload["confirmed_answer"] == "USA"


# --------------------------------------------------------------------------- #
# Section G — the LIVE route path (extension → background → API → service)
# --------------------------------------------------------------------------- #
def test_route_returns_a_usable_mapping(client, monkeypatch):
    """A confirmed answer maps to an exact employer label over the real route."""
    from app.applications import option_mapping_service as service

    async def fake(**kwargs):
        assert kwargs["confirmed_answer"] == "No"
        return service.OptionMapping("No, I will not require sponsorship", 0.97, False, "ok")

    monkeypatch.setattr("app.routes.applications.option_mapping_service.map_option", fake)
    token, session_id = _session(client)
    response = client.post(
        f"/application-sessions/{session_id}/map-option",
        json={
            "question_label": "Will you require sponsorship?",
            "options": OPTIONS,
            "canonical_key": "sponsorship_required_future",
            "confirmed_answer": "No",
        },
        headers=token,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["selected_option_label"] == "No, I will not require sponsorship"
    assert body["usable"] is True


def test_route_falls_back_to_the_widget_when_the_model_is_unusable(client, monkeypatch):
    from app.applications import option_mapping_service as service

    async def fake(**kwargs):
        return service.OptionMapping(None, 0.0, True, "AI mapping was unavailable")

    monkeypatch.setattr("app.routes.applications.option_mapping_service.map_option", fake)
    token, session_id = _session(client)
    response = client.post(
        f"/application-sessions/{session_id}/map-option",
        json={"question_label": "Country", "options": ["United States"], "canonical_key": "country"},
        headers=token,
    )
    assert response.status_code == 200
    assert response.json()["usable"] is False
    assert response.json()["requires_user_confirmation"] is True


def test_route_requires_authentication(client):
    response = client.post(
        "/application-sessions/1/map-option",
        json={"question_label": "Country", "options": ["United States"], "canonical_key": "country"},
    )
    assert response.status_code in (401, 403)
