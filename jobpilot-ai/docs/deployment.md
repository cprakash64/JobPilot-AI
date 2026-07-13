# Deployment

The MVP runs locally with Docker Compose. Production deployments should use managed PostgreSQL, managed Redis, TLS termination, secret management, structured logs, and object storage for generated files.

## Required Production Settings

- Strong `SECRET_KEY`.
- Restricted `CORS_ORIGINS`.
- Production database URL.
- Redis URL.
- OpenAI API key and approved model names if AI generation is enabled.
- Encryption strategy for sensitive demographic values.
- Upload malware scanning implementation.

Run Alembic migrations before starting the API:

```bash
cd apps/api
alembic upgrade head
```

## Migration Policy

Local Docker development may set `RUN_MIGRATIONS_ON_STARTUP=true` so the API container waits for PostgreSQL and runs `alembic upgrade head` before Uvicorn starts.

Production should run `alembic upgrade head` as a separate release or predeploy job. Start API containers only after the migration job succeeds. Do not delete production database volumes to recover from migration failures, and do not run destructive migrations automatically unless that behavior is explicitly designed, reviewed, and configured.
