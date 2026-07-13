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
        print(f"Postgres not ready yet ({attempt + 1}/60): {exc}")
        time.sleep(1)

print("Postgres did not become ready in time.")
sys.exit(1)
PY

if [ "${RUN_MIGRATIONS_ON_STARTUP:-false}" = "true" ]; then
  echo "Running Alembic migrations..."
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
