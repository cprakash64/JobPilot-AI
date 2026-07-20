"""Section C — liveness and readiness.

Liveness must stay dependency-free: if /healthz touched PostgreSQL, a database
blip would make every orchestrator restart every healthy API replica and turn a
degraded system into a dead one.

Readiness must fail closed AND leak nothing: its body is unauthenticated, and a
SQLAlchemy connection error stringifies to the full DSN including the password.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import app
from app.services import readiness


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# Liveness
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/healthz", "/health"])
def test_liveness_returns_200_with_a_minimal_body(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_does_not_touch_the_database(client: TestClient, monkeypatch) -> None:
    """The property that matters: a dead database must not fail liveness."""

    def explode(*args, **kwargs):
        raise AssertionError("liveness must not open a database session")

    monkeypatch.setattr("app.main.SessionLocal", explode)
    assert client.get("/healthz").status_code == 200


def test_liveness_still_passes_when_readiness_would_fail(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        readiness, "check_readiness", lambda db: (False, {"database_connected": False})
    )
    assert client.get("/healthz").status_code == 200


def test_liveness_exposes_no_configuration_or_version(client: TestClient) -> None:
    body = client.get("/healthz").json()
    assert set(body.keys()) == {"status"}


# --------------------------------------------------------------------------- #
# Readiness
# --------------------------------------------------------------------------- #
def test_readiness_returns_200_when_dependencies_are_healthy(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.main.check_readiness",
        lambda db: (
            True,
            {"database_connected": True, "schema_up_to_date": True, "redis_connected": True},
        ),
    )
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_returns_503_when_the_database_is_down(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.check_readiness",
        lambda db: (False, {"database_connected": False, "error": "OperationalError"}),
    )
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["database_connected"] is False


def test_readiness_fails_when_the_schema_is_behind_head(client: TestClient, monkeypatch) -> None:
    """A replica running new code against an un-migrated database must not take
    traffic — it would fail on the first query touching a new column."""
    monkeypatch.setattr(
        "app.main.check_readiness",
        lambda db: (
            False,
            {
                "database_connected": True,
                "current_revision": "0012_company_branding",
                "head_revision": "0013_name_parts_phone",
                "schema_up_to_date": False,
            },
        ),
    )
    assert client.get("/readyz").status_code == 503


# --------------------------------------------------------------------------- #
# Leak-free probe output
# --------------------------------------------------------------------------- #
def test_database_probe_never_returns_driver_text(monkeypatch) -> None:
    """The regression this pins: `checks["error"] = str(exc)` returned the DSN,
    password included, to an unauthenticated caller."""
    dsn_with_secret = "postgresql://jobpilot:sup3r-s3cret@db.internal:5432/jobpilot"

    class FakeSession:
        def connection(self):
            raise OperationalError(
                f"could not connect to {dsn_with_secret}", None, Exception("boom")
            )

    ready, checks = readiness.check_database_readiness(FakeSession())

    assert ready is False
    serialized = repr(checks)
    assert "sup3r-s3cret" not in serialized
    assert "db.internal" not in serialized
    assert checks["error"] == "OperationalError"


def test_redis_probe_never_returns_driver_text(monkeypatch) -> None:
    import redis

    def explode(*args, **kwargs):
        raise redis.ConnectionError("auth failed for redis://user:t0psecret@cache.internal:6379/0")

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(explode))

    ready, checks = readiness.check_redis_readiness()

    assert ready is False
    assert "t0psecret" not in repr(checks)
    assert checks["error"] == "ConnectionError"


def test_an_unreachable_redis_does_not_make_the_api_unready(monkeypatch) -> None:
    """Ingestion degrades to inline scoring without a broker, so the API still
    serves correctly — pulling the replica out of the load balancer would be a
    self-inflicted outage."""
    monkeypatch.setattr(
        readiness, "check_database_readiness", lambda db: (True, {"database_connected": True})
    )
    monkeypatch.setattr(
        readiness, "check_redis_readiness", lambda: (False, {"redis_connected": False})
    )

    ready, checks = readiness.check_readiness(db=None)

    assert ready is True, "redis is reported, not gating"
    assert checks["redis_connected"] is False
    assert checks["redis_required"] is False
