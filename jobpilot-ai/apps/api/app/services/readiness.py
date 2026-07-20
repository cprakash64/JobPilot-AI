"""Liveness and readiness checks for container orchestration.

Two distinct questions, deliberately kept separate:

* **Liveness** (`/healthz`) — "is this process alive?" It must not touch
  PostgreSQL, Redis, OpenAI or the filesystem. A liveness probe that depends on
  a database restarts healthy API pods during a database blip, turning a partial
  outage into a total one.
* **Readiness** (`/readyz`) — "should this replica receive traffic?" It verifies
  the dependencies the API genuinely cannot serve without, with short timeouts.

Nothing here calls a paid or external API.

SECURITY: check output is returned to unauthenticated callers, so it carries
only booleans, revision identifiers and exception *type* names. Driver
exception text is never included — a SQLAlchemy connection error stringifies to
the full DSN, including the password.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

logger = logging.getLogger("jobpilot.readiness")

# Readiness must fail fast: a probe that hangs is indistinguishable from a dead
# replica to most orchestrators, and blocks the worker handling it.
DB_PROBE_TIMEOUT_SECONDS = 2.0
REDIS_PROBE_TIMEOUT_SECONDS = 1.0


def check_database_readiness(db: Session) -> tuple[bool, dict]:
    """Verify the database is connected AND migrated to the current head.

    A replica whose schema is behind head is not ready: it will fail on any
    query touching a column the running code expects.
    """
    checks: dict[str, object] = {
        "database_connected": False,
        "alembic_version_exists": False,
        "current_revision": None,
        "head_revision": None,
        "critical_tables_exist": False,
        "schema_up_to_date": False,
    }
    try:
        connection = db.connection()
        connection.execute(text("select 1"))
        checks["database_connected"] = True

        inspector = inspect(connection)
        table_names = set(inspector.get_table_names())
        checks["alembic_version_exists"] = "alembic_version" in table_names
        checks["critical_tables_exist"] = {"users", "user_profiles"}.issubset(table_names)

        context = MigrationContext.configure(connection)
        current_revision = context.get_current_revision()
        checks["current_revision"] = current_revision

        api_root = Path(__file__).resolve().parents[2]
        alembic_config = Config(str(api_root / "alembic.ini"))
        script = ScriptDirectory.from_config(alembic_config)
        head_revision = script.get_current_head()
        checks["head_revision"] = head_revision
        checks["schema_up_to_date"] = current_revision == head_revision

        ready = all(
            [
                checks["database_connected"],
                checks["alembic_version_exists"],
                checks["critical_tables_exist"],
                checks["schema_up_to_date"],
            ]
        )
        return ready, checks
    except SQLAlchemyError as exc:
        # Only the exception TYPE. str(exc) on a connection failure embeds the
        # DSN — including credentials — and this body is returned to callers.
        checks["error"] = type(exc).__name__
        logger.warning("readiness: database probe failed (%s)", type(exc).__name__, exc_info=True)
        return False, checks


def check_redis_readiness() -> tuple[bool, dict]:
    """Probe the Celery/Redis broker with a short-timeout PING.

    Reported but NOT gating (see ``check_readiness``): the ingestion pipeline
    degrades to inline scoring when the broker is unreachable, so the API can
    still serve every request without it.
    """
    checks: dict[str, object] = {"redis_connected": False}
    try:
        import redis

        from app.core.config import settings

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=REDIS_PROBE_TIMEOUT_SECONDS,
            socket_timeout=REDIS_PROBE_TIMEOUT_SECONDS,
        )
        try:
            client.ping()
            checks["redis_connected"] = True
            return True, checks
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        # Type name only: a Redis URL with a password stringifies into the error.
        checks["error"] = type(exc).__name__
        logger.warning("readiness: redis probe failed (%s)", type(exc).__name__)
        return False, checks


def check_readiness(db: Session) -> tuple[bool, dict]:
    """Aggregate readiness. Only the database gates traffic.

    Redis status is surfaced for operators (a red broker means background
    scoring is running inline and will be slower) but does not remove a replica
    from the load balancer, because the API serves correctly without it.
    """
    db_ready, checks = check_database_readiness(db)
    _, redis_checks = check_redis_readiness()
    checks.update(redis_checks)
    checks["redis_required"] = False
    return db_ready, checks
