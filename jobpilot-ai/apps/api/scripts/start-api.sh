#!/bin/sh
set -eu

echo "Waiting for Postgres..."

python - <<'PY'
import os
import sys
import time
from sqlalchemy import create_engine, text

database_url = os.environ["DATABASE_URL"]

for attempt in range(60):
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("Postgres is ready.")
        sys.exit(0)
    except Exception as exc:
        # Only the exception TYPE. SQLAlchemy/psycopg connection errors
        # stringify to the full DSN, so printing `exc` writes the database
        # password into container logs on every retry.
        print(f"Postgres not ready yet ({attempt + 1}/60): {type(exc).__name__}")
        time.sleep(1)

print("Postgres did not become ready in time.")
sys.exit(1)
PY

# Schema migration is a RELEASE step, not a per-replica startup step.
#
# With N API replicas, startup migration means N concurrent `alembic upgrade
# head` runs racing on the same database. Alembic takes a lock so they do not
# corrupt each other, but the losers block on boot, and a migration failure
# surfaces as a crash-looping replica rather than a halted deployment.
#
# Local development keeps it on for convenience (compose sets it true). In
# production leave RUN_MIGRATIONS_ON_STARTUP unset/false and run
# `alembic upgrade head` as one explicit step before rolling the replicas —
# see docs/DEPLOYMENT.md.
if [ "${RUN_MIGRATIONS_ON_STARTUP:-false}" = "true" ]; then
  if [ "${APP_ENV:-development}" = "production" ] || [ "${APP_ENV:-development}" = "prod" ]; then
    echo "REFUSING to run migrations on startup with APP_ENV=${APP_ENV}." >&2
    echo "Run 'alembic upgrade head' as an explicit release step instead (docs/DEPLOYMENT.md)." >&2
    exit 1
  fi
  echo "Running Alembic migrations (development convenience)..."
  alembic upgrade head
else
  echo "Skipping Alembic migrations because RUN_MIGRATIONS_ON_STARTUP is not true."
fi

echo "Starting JobPilot AI API..."
if [ "${API_RELOAD:-false}" = "true" ]; then
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
else
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000
fi
