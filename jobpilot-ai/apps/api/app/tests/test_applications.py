"""Assisted auto-apply: session lifecycle, security, vault, and audit tests."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.applications.url_validation import is_valid_official_url, validate_official_url
from app.core.session_tokens import create_launch_token, hash_token
from app.db.base import Base
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


def auth(client: TestClient, email: str = "apply@mailbox.test-domain.co") -> dict[str, str]:
    token = client.post("/auth/signup", json={"email": email, "password": "password123"}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def complete_profile(client: TestClient, headers: dict[str, str]) -> None:
    resp = client.put("/profile", headers=headers, json={
        "full_name": "Chandra Pandey", "phone": "602-555-0100",
        "location_city": "Phoenix", "location_state": "AZ", "location_country": "United States",
        "linkedin_url": "https://linkedin.com/in/cp", "work_authorization": "authorized_us",
        "target_roles": ["Backend Engineer"], "target_levels": ["Junior"],
        "preferred_locations": ["United States"], "remote_preference": "remote",
        "skills": ["Python", "FastAPI", "PostgreSQL"],
    })
    assert resp.status_code == 200, resp.text
    client.put("/profile/career", headers=headers, json={
        "education": [{"school": "Arizona State University", "degree": "BS"}],
        "experience": [{"company": "Cardinal Health", "title": "ML Engineer Intern",
                        "bullets": ["Built Python services"], "technologies": ["Python"], "currently_working": True}],
        "projects": [{"name": "Luna AI", "bullets": ["Built RAG pipeline"], "technologies": ["Python"]}],
        "certifications": [], "awards": [],
    })


def seed_job(client: TestClient, *, company="Acme", url=None, days=1) -> int:
    db = next(app.dependency_overrides[get_db]())
    src = db.scalar(select(E.JobSource).where(E.JobSource.name == company))
    if src is None:
        src = E.JobSource(name=company, type="greenhouse", base_url="x", enabled=True, supports_api=True)
        db.add(src)
        db.flush()
    apply_url = url or f"https://boards.greenhouse.io/{company.lower()}/1"
    job = E.JobPosting(
        source_id=src.id, external_id=f"be-{company}", title="Backend Engineer", company=company,
        location="Remote, United States", remote_type="remote",
        posted_at=datetime.now(UTC) - timedelta(days=days), discovered_at=datetime.now(UTC),
        application_url=apply_url, source_url=apply_url,
        description_raw="", description_clean="Backend Engineer. Requirements: Python, FastAPI.",
        required_skills=["Python", "FastAPI"], hash_for_deduplication=f"h-{company}",
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()
    return job_id


def create_session(client, headers, job_id) -> dict:
    resp = client.post("/application-sessions", headers=headers, json={"job_id": job_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# URL validation
# --------------------------------------------------------------------------- #
def test_url_validation_rejects_unsafe_schemes():
    for bad in ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "ftp://x.com", None, ""]:
        assert not is_valid_official_url(bad)
    assert is_valid_official_url("https://boards.greenhouse.io/acme/1")
    with pytest.raises(Exception):
        validate_official_url("https://example.com/apply")  # placeholder host


# --------------------------------------------------------------------------- #
# Session creation, ownership, documents
# --------------------------------------------------------------------------- #
def test_create_session_prepares_documents_and_answers(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    body = create_session(client, headers, job_id)

    assert body["status"] == "ready"
    assert body["ats_type"] == "greenhouse"
    assert body["official_application_url"].startswith("https://")
    assert body["resume"]["status"] == "ready"
    assert body["cover_letter"]["status"] == "ready"
    assert body["answers_available"] >= 1
    assert "extension_launch_token" in body
    # Verified name/email are auto-fillable.
    session_id = body["session_id"]
    answers = client.get(f"/application-sessions/{session_id}/answers", headers=headers).json()["answers"]
    keys = {a["canonical_key"] for a in answers}
    assert "full_name" in keys and "email" in keys
    assert all(not a["sensitive"] for a in answers)


def test_profile_eeo_answers_are_included_in_the_application_package(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    saved = client.put(
        "/profile/demographics",
        headers=headers,
        json={
            "gender_identity": "man",
            "veteran_status": "not_protected_veteran",
            "disability_status": "no",
            "hispanic_or_latino": "no",
            "race_ethnicity": ["asian"],
            "consent_to_store": True,
        },
    )
    assert saved.status_code == 200

    job_id = seed_job(client)
    body = create_session(client, headers, job_id)
    answers = client.get(
        f"/application-sessions/{body['session_id']}/answers", headers=headers
    ).json()["answers"]
    by_key = {answer["canonical_key"]: answer for answer in answers}

    assert by_key["gender"]["value"] == "Man"
    assert by_key["race"]["value"] == "Asian"
    assert by_key["veteran_status"]["value"] == "I am not a protected veteran"
    assert by_key["disability_status"]["sensitive"] is True
    assert all(by_key[key]["verified"] for key in ("gender", "race", "veteran_status"))


def test_saving_profile_eeo_refreshes_an_existing_session(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    body = create_session(client, headers, job_id)

    client.put(
        "/profile/demographics",
        headers=headers,
        json={"gender_identity": "man", "race_ethnicity": ["asian"], "consent_to_store": True},
    )
    refreshed = client.get(
        f"/application-sessions/{body['session_id']}/answers", headers=headers
    ).json()

    assert refreshed["refreshed"] is True
    assert {answer["canonical_key"] for answer in refreshed["answers"]} >= {"gender", "race"}


def test_session_not_accessible_to_other_user(client: TestClient) -> None:
    owner = auth(client, "owner@mailbox.test-domain.co")
    complete_profile(client, owner)
    job_id = seed_job(client)
    session_id = create_session(client, owner, job_id)["session_id"]

    intruder = auth(client, "intruder@mailbox.test-domain.co")
    resp = client.get(f"/application-sessions/{session_id}", headers=intruder)
    assert resp.status_code == 403


def test_create_session_rejects_placeholder_url(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client, company="Democo", url="https://example.com/apply")
    resp = client.post("/application-sessions", headers=headers, json={"job_id": job_id})
    assert resp.status_code == 422


def test_resume_and_cover_letter_download(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    session_id = create_session(client, headers, job_id)["session_id"]

    r = client.get(f"/application-sessions/{session_id}/resume?fmt=pdf", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    c = client.get(f"/application-sessions/{session_id}/cover-letter?fmt=docx", headers=headers)
    assert c.status_code == 200


# --------------------------------------------------------------------------- #
# Token exchange (launch -> session)
# --------------------------------------------------------------------------- #
def test_launch_token_exchange_and_session_scope(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    body = create_session(client, headers, job_id)
    launch = body["extension_launch_token"]
    session_id = body["session_id"]

    exchanged = client.post("/application-sessions/token", json={"launch_token": launch})
    assert exchanged.status_code == 200
    session_token = exchanged.json()["session_token"]

    # The session token can read its own session...
    ext_headers = {"Authorization": f"Bearer {session_token}"}
    assert client.get(f"/application-sessions/{session_id}", headers=ext_headers).status_code == 200
    # ...and its answers/documents.
    assert client.get(f"/application-sessions/{session_id}/answers", headers=ext_headers).status_code == 200
    assert client.get(f"/application-sessions/{session_id}/resume", headers=ext_headers).status_code == 200


def test_launch_token_is_one_time_use(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    launch = create_session(client, headers, job_id)["extension_launch_token"]

    assert client.post("/application-sessions/token", json={"launch_token": launch}).status_code == 200
    # Second exchange must fail.
    assert client.post("/application-sessions/token", json={"launch_token": launch}).status_code == 401


def test_session_token_cannot_access_other_session(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_a = seed_job(client, company="Acme")
    job_b = seed_job(client, company="Bcorp")
    a = create_session(client, headers, job_a)
    b = create_session(client, headers, job_b)

    token_a = client.post("/application-sessions/token", json={"launch_token": a["extension_launch_token"]}).json()["session_token"]
    ext = {"Authorization": f"Bearer {token_a}"}
    # Token scoped to session A cannot read session B.
    assert client.get(f"/application-sessions/{b['session_id']}", headers=ext).status_code == 403


def test_bad_launch_token_rejected(client: TestClient) -> None:
    assert client.post("/application-sessions/token", json={"launch_token": "not-a-token"}).status_code == 401


# --------------------------------------------------------------------------- #
# Expiry
# --------------------------------------------------------------------------- #
def test_expired_session_cannot_be_exchanged(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    body = create_session(client, headers, job_id)

    db = next(app.dependency_overrides[get_db]())
    row = db.get(E.ApplicationSession, body["session_id"])
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    db.close()

    assert client.post("/application-sessions/token", json={"launch_token": body["extension_launch_token"]}).status_code == 401


# --------------------------------------------------------------------------- #
# Status transitions
# --------------------------------------------------------------------------- #
def test_status_patch_allows_filling_but_not_completed(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    session_id = create_session(client, headers, job_id)["session_id"]

    ok = client.patch(f"/application-sessions/{session_id}/status", headers=headers, json={"status": "filling"})
    assert ok.status_code == 200 and ok.json()["status"] == "filling"
    # Cannot jump to completed via PATCH (must confirm via /complete).
    bad = client.patch(f"/application-sessions/{session_id}/status", headers=headers, json={"status": "completed"})
    assert bad.status_code == 409


def test_complete_requires_confirmation_and_updates_tracker(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    session_id = create_session(client, headers, job_id)["session_id"]

    # Unconfirmed completion is rejected — JobPilot never assumes submission.
    assert client.post(f"/application-sessions/{session_id}/complete", headers=headers, json={"confirmed": False}).status_code == 422
    done = client.post(f"/application-sessions/{session_id}/complete", headers=headers, json={"confirmed": True})
    assert done.status_code == 200 and done.json()["status"] == "completed"

    tracker = client.get("/jobs/tracker/all", headers=headers).json()["applications"]
    assert any(t["job_id"] == job_id and t["status"] == "applied" for t in tracker)


def test_cancel_session(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    session_id = create_session(client, headers, job_id)["session_id"]
    resp = client.post(f"/application-sessions/{session_id}/cancel", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "cancelled"


# --------------------------------------------------------------------------- #
# Audit trail + events
# --------------------------------------------------------------------------- #
def test_events_are_audited(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    session_id = create_session(client, headers, job_id)["session_id"]

    client.post(f"/application-sessions/{session_id}/events", headers=headers,
                json={"action_type": "field_filled", "field_key": "email", "source": "extension", "confidence": 0.97})

    db = next(app.dependency_overrides[get_db]())
    logs = db.scalars(select(E.ApplicationAuditLog).where(E.ApplicationAuditLog.session_id == session_id)).all()
    action_types = {log.action_type for log in logs}
    assert "session_created" in action_types
    assert "resume_generated" in action_types
    assert "field_filled" in action_types
    db.close()


# --------------------------------------------------------------------------- #
# Answer vault + sensitive-field policy
# --------------------------------------------------------------------------- #
def test_answer_vault_crud(client: TestClient) -> None:
    headers = auth(client)
    put = client.put("/application-answers/salary_expectation", headers=headers, json={"value": "120000"})
    assert put.status_code == 200 and put.json()["answer"]["value"] == "120000"

    listed = client.get("/application-answers", headers=headers).json()["answers"]
    assert any(a["canonical_key"] == "salary_expectation" for a in listed)

    client.post("/application-answers/salary_expectation/verify", headers=headers)
    verified = client.get("/application-answers", headers=headers).json()["answers"]
    assert next(a for a in verified if a["canonical_key"] == "salary_expectation")["is_user_verified"] is True

    client.post("/application-answers/salary_expectation/disable-autofill", headers=headers)
    disabled = client.get("/application-answers", headers=headers).json()["answers"]
    assert next(a for a in disabled if a["canonical_key"] == "salary_expectation")["allow_auto_fill"] is False

    assert client.delete("/application-answers/salary_expectation", headers=headers).status_code == 204
    assert not client.get("/application-answers", headers=headers).json()["answers"]


def test_sensitive_answer_never_auto_filled_until_verified(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    # Save a sensitive answer but do NOT verify/enable it.
    client.put("/application-answers/gender", headers=headers, json={"value": "Prefer not to say"})
    job_id = seed_job(client)
    body = create_session(client, headers, job_id)
    session_id = body["session_id"]

    answers = client.get(f"/application-sessions/{session_id}/answers", headers=headers).json()
    # The sensitive value is NOT in the auto-fill set...
    assert all(a["canonical_key"] != "gender" for a in answers["answers"])
    # ...it is surfaced as an unresolved question the user must answer directly.
    assert any(q["canonical_key"] == "gender" for q in answers["unresolved_questions"])


def test_unknown_canonical_key_rejected(client: TestClient) -> None:
    headers = auth(client)
    assert client.put("/application-answers/totally_made_up", headers=headers, json={"value": "x"}).status_code == 422


# --------------------------------------------------------------------------- #
# Auth required everywhere
# --------------------------------------------------------------------------- #
def test_routes_require_auth(client: TestClient) -> None:
    assert client.post("/application-sessions", json={"job_id": 1}).status_code == 401
    assert client.get("/application-answers").status_code == 401
    assert client.get("/application-sessions/1").status_code == 401


# --------------------------------------------------------------------------- #
# "Failed to fetch" regression: errors must carry CORS headers so the browser
# receives a readable message instead of an opaque network failure.
# --------------------------------------------------------------------------- #
def _error_client() -> TestClient:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, raise_server_exceptions=False)


def test_preflight_succeeds_for_frontend_origin(client: TestClient) -> None:
    resp = client.options(
        "/application-sessions",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_unhandled_error_response_includes_cors_header(monkeypatch) -> None:
    """A 500 raised in the route must still carry Access-Control-Allow-Origin,
    otherwise a cross-origin browser blocks it and shows 'Failed to fetch'."""
    import app.routes.applications as approutes

    ec = _error_client()
    try:
        headers = auth(ec, "cors@mailbox.test-domain.co")
        complete_profile(ec, headers)
        job_id = seed_job(ec)

        async def boom(*_args, **_kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(approutes, "create_application_session", boom)
        resp = ec.post(
            "/application-sessions",
            headers={**headers, "Origin": "http://localhost:3000"},
            json={"job_id": job_id},
        )
        assert resp.status_code == 500
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert "detail" in resp.json()
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Graceful degradation: a document-generation failure is a warning, not a 500.
# --------------------------------------------------------------------------- #
def test_generation_failure_degrades_to_warning(client: TestClient, monkeypatch) -> None:
    import app.applications.session_service as svc

    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)

    async def failing_resume(*_args, **_kwargs):
        raise RuntimeError("resume model exploded")

    monkeypatch.setattr(svc, "generate_resume", failing_resume)
    resp = client.post("/application-sessions", headers=headers, json={"job_id": job_id})
    # Session is still created (not a network-style failure)...
    assert resp.status_code == 201
    body = resp.json()
    assert body["resume"]["status"] == "missing"
    assert body["cover_letter"]["status"] == "ready"
    # ...and the failure is surfaced as a warning the user can retry.
    assert any("resume" in w.lower() for w in body["warnings"])


def test_regenerate_resume_recovers_after_failure(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    session_id = create_session(client, headers, job_id)["session_id"]
    resp = client.post(f"/application-sessions/{session_id}/regenerate-resume", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["resume"]["status"] == "ready"


# --------------------------------------------------------------------------- #
# Migration presence (guards against the missing-table root cause).
# --------------------------------------------------------------------------- #
def test_application_tables_are_in_migrations() -> None:
    import pathlib

    versions = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions"
    text = "\n".join(p.read_text() for p in versions.glob("*.py"))
    for table in ["application_sessions", "application_audit_logs", "application_answers"]:
        assert table in text, f"{table} missing from alembic migrations"


def test_applicationstatus_applying_enum_is_added_by_migration() -> None:
    """Root-cause regression guard: the tracker is set to ``applying`` during
    assisted apply, so the Postgres ``applicationstatus`` enum must gain that
    label via a migration. SQLite tests can't catch this — assert on the SQL."""
    import pathlib

    versions = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions"
    text = "\n".join(p.read_text() for p in versions.glob("*.py"))
    assert "ALTER TYPE applicationstatus ADD VALUE" in text
    assert "'applying'" in text


# --------------------------------------------------------------------------- #
# Structured error envelope: preparation stages fail with actionable codes.
# --------------------------------------------------------------------------- #
def test_missing_profile_returns_structured_422(client: TestClient) -> None:
    headers = auth(client)  # signed up, but no profile completed
    job_id = seed_job(client)
    resp = client.post("/application-sessions", headers=headers, json={"job_id": job_id})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "PROFILE_INCOMPLETE"
    assert err["stage"] == "load_candidate_profile"
    assert err["retryable"] is False
    assert "basic_info" in err["details"]["missing_sections"]


def test_missing_job_returns_structured_404(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    resp = client.post("/application-sessions", headers=headers, json={"job_id": 999999})
    assert resp.status_code == 404
    err = resp.json()["error"]
    assert err["code"] == "JOB_NOT_FOUND"
    assert err["retryable"] is False


def test_unauthenticated_prepare_is_rejected(client: TestClient) -> None:
    assert client.post("/application-sessions", json={"job_id": 1}).status_code == 401


def test_response_carries_request_id_header(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    resp = client.post("/application-sessions", headers=headers, json={"job_id": job_id})
    assert resp.headers.get("x-request-id")


# --------------------------------------------------------------------------- #
# Idempotency: repeated preparation reuses the live session and its artifacts.
# --------------------------------------------------------------------------- #
def test_repeated_prepare_is_idempotent(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)

    first = create_session(client, headers, job_id)
    second = create_session(client, headers, job_id)
    # Same session, same tailored documents — no duplicates.
    assert first["session_id"] == second["session_id"]
    assert first["resume"]["document_id"] == second["resume"]["document_id"]
    assert first["cover_letter"]["document_id"] == second["cover_letter"]["document_id"]

    db = next(app.dependency_overrides[get_db]())
    sessions = db.scalars(select(E.ApplicationSession).where(E.ApplicationSession.job_id == job_id)).all()
    trackers = db.scalars(select(E.ApplicationTracker).where(E.ApplicationTracker.job_id == job_id)).all()
    assert len(sessions) == 1
    assert len(trackers) == 1
    db.close()


def test_repeated_prepare_repairs_missing_document_links(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    first = create_session(client, headers, job_id)

    db = next(app.dependency_overrides[get_db]())
    session = db.get(E.ApplicationSession, first["session_id"])
    assert session is not None
    session.tailored_resume_id = None
    session.tailored_cover_letter_id = None
    session.warnings = [
        "Tailored resume could not be prepared automatically (TimeoutError). You can retry it.",
        "Tailored cover letter could not be prepared automatically (TimeoutError). You can retry it.",
    ]
    db.commit()
    db.close()

    repaired = create_session(client, headers, job_id)
    assert repaired["session_id"] == first["session_id"]
    assert repaired["resume"]["document_id"] == first["resume"]["document_id"]
    assert repaired["cover_letter"]["document_id"] == first["cover_letter"]["document_id"]
    assert not any("could not be prepared" in warning for warning in repaired["warnings"])


def test_prepare_after_cancel_creates_fresh_session(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    first_id = create_session(client, headers, job_id)["session_id"]
    client.post(f"/application-sessions/{first_id}/cancel", headers=headers)
    # A cancelled session is terminal; the next prepare starts a new one.
    second_id = create_session(client, headers, job_id)["session_id"]
    assert second_id != first_id


# --------------------------------------------------------------------------- #
# Provider failures degrade to warnings; DB outage is a retryable 503.
# --------------------------------------------------------------------------- #
def test_provider_timeout_degrades_to_warning(client: TestClient, monkeypatch) -> None:
    import app.applications.session_service as svc

    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)

    async def timeout_cover(*_args, **_kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(svc, "generate_cover_letter", timeout_cover)
    resp = client.post("/application-sessions", headers=headers, json={"job_id": job_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["cover_letter"]["status"] == "missing"
    assert any("cover letter" in w.lower() for w in body["warnings"])


def test_provider_malformed_response_degrades_to_warning(client: TestClient, monkeypatch) -> None:
    import app.applications.session_service as svc

    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)

    async def malformed_resume(*_args, **_kwargs):
        raise ValueError("could not parse model JSON")

    monkeypatch.setattr(svc, "generate_resume", malformed_resume)
    resp = client.post("/application-sessions", headers=headers, json={"job_id": job_id})
    assert resp.status_code == 201
    assert resp.json()["resume"]["status"] == "missing"


def test_database_persistence_failure_returns_structured_503(client: TestClient, monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    import app.applications.session_service as svc

    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)

    def boom(*_args, **_kwargs):
        raise OperationalError("INSERT", {}, Exception("database is down"))

    monkeypatch.setattr(svc, "_link_tracker", boom)
    resp = client.post("/application-sessions", headers=headers, json={"job_id": job_id})
    assert resp.status_code == 503
    err = resp.json()["error"]
    assert err["code"] == "DATABASE_UNAVAILABLE"
    assert err["retryable"] is True


def test_autofill_results_recorded_and_sanitized(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    sid = create_session(client, headers, job_id)["session_id"]

    resp = client.post(f"/application-sessions/{sid}/autofill-results", headers=headers, json={
        "status": "completed_with_review", "ats": "ashby",
        "fields_discovered": 14, "fields_filled": 10,
        "documents_uploaded": ["resume", "cover_letter", "totally_bogus"],
        "review_items": 4,
        "failures": [{"field_key": "salary_expectation", "reason_code": "NO_VERIFIED_ANSWER"}],
    })
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]
    assert summary["status"] == "completed_with_review"
    # Unknown upload kinds are dropped.
    assert summary["documents_uploaded"] == ["resume", "cover_letter"]
    assert summary["fields_filled"] == 10
    assert summary["failures"] == [{"field_key": "salary_expectation", "reason_code": "NO_VERIFIED_ANSWER"}]

    # Recorded in the audit trail (counts + codes only).
    db = next(app.dependency_overrides[get_db]())
    logs = db.scalars(select(E.ApplicationAuditLog).where(E.ApplicationAuditLog.session_id == sid)).all()
    assert any(log.action_type == "autofill_summary" for log in logs)
    db.close()


def test_autofill_results_unknown_status_is_sanitized(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    sid = create_session(client, headers, job_id)["session_id"]
    resp = client.post(f"/application-sessions/{sid}/autofill-results", headers=headers,
                       json={"status": "hacked; drop table"})
    assert resp.status_code == 200
    assert resp.json()["summary"]["status"] == "unknown"


def test_autofill_results_accepts_session_scoped_token(client: TestClient) -> None:
    headers = auth(client)
    complete_profile(client, headers)
    job_id = seed_job(client)
    body = create_session(client, headers, job_id)
    sid = body["session_id"]
    session_token = client.post(
        "/application-sessions/token", json={"launch_token": body["extension_launch_token"]}
    ).json()["session_token"]

    # The extension uses the session-scoped token, not the user's main token.
    ext = {"Authorization": f"Bearer {session_token}"}
    resp = client.post(f"/application-sessions/{sid}/autofill-results", headers=ext,
                       json={"status": "completed", "ats": "ashby", "fields_filled": 5, "fields_discovered": 5})
    assert resp.status_code == 200, resp.text


def test_autofill_results_rejected_for_other_user(client: TestClient) -> None:
    owner = auth(client, "owner-af@example.com")
    complete_profile(client, owner)
    job_id = seed_job(client)
    sid = create_session(client, owner, job_id)["session_id"]

    intruder = auth(client, "intruder-af@example.com")
    resp = client.post(f"/application-sessions/{sid}/autofill-results", headers=intruder,
                       json={"status": "completed"})
    assert resp.status_code == 403


def test_autofill_results_requires_auth(client: TestClient) -> None:
    assert client.post("/application-sessions/1/autofill-results", json={"status": "completed"}).status_code == 401


def test_extension_origin_receives_cors_headers(client: TestClient) -> None:
    origin = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    resp = client.options(
        "/application-sessions/token",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == origin


def test_nullable_job_fields_do_not_break_preparation(client: TestClient) -> None:
    """A job with null description/location/salary/etc. must still prepare."""
    headers = auth(client)
    complete_profile(client, headers)

    db = next(app.dependency_overrides[get_db]())
    src = E.JobSource(name="Sparse", type="greenhouse", base_url="x", enabled=True, supports_api=True)
    db.add(src)
    db.flush()
    job = E.JobPosting(
        source_id=src.id, external_id="sparse-1", title="Engineer", company="Sparse",
        location=None, remote_type=None, posted_at=datetime.now(UTC), discovered_at=datetime.now(UTC),
        application_url="https://boards.greenhouse.io/sparse/1", source_url="https://boards.greenhouse.io/sparse/1",
        description_raw=None, description_clean=None, required_skills=[], hash_for_deduplication="h-sparse",
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    resp = client.post("/application-sessions", headers=headers, json={"job_id": job_id})
    assert resp.status_code == 201, resp.text
