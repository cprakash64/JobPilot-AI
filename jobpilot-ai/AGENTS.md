# JobPilot AI repository guidance

- `apps/api` is the FastAPI/SQLAlchemy backend; add reversible Alembic migrations under
  `apps/api/alembic/versions` and never edit an already-applied migration.
- `apps/web` is the Next.js client, `apps/extension` is the user-controlled MV3 autofill
  extension, and `packages/shared` contains shared TypeScript code.
- Substantial features must keep a living plan in `docs/plans/` and record architecture,
  decisions, progress, validation, rollout, and rollback there.
- Preserve unrelated worktree changes. Never commit credentials, provider payloads, or personal
  data. Provider secrets are backend-only.
- Validate with `make test-api`, `make test-web`, and `make test-extension`; also run
  `docker compose config`, the relevant Alembic upgrade/downgrade checks, and any feature-specific
  evaluation command documented in its plan.
