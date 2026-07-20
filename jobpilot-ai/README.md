# JobPilot AI

JobPilot AI is an open-source, user-controlled job-search and job-application copilot for students, fresh graduates, and professionals.

It helps users maintain one truthful career profile, discover recent jobs from allowed structured sources, generate ATS-friendly resumes and cover letters, prepare editable application answers, and track application status.

## Compliance Philosophy

JobPilot AI assists the user. It does not pretend to be the user.

- No scraping of restricted platforms such as LinkedIn, Indeed, or portals that prohibit scraping or automation.
- No automated login to third-party job portals.
- No automatic mass submission.
- No fake typing, captcha bypass, proxy rotation, stealth browser automation, or anti-bot evasion.
- No invented resume claims.
- Sensitive demographic data is optional, stored separately, and excluded from matching, ranking, and resume generation.

## Tech Stack

- Frontend: Next.js App Router, TypeScript, Tailwind CSS, React forms.
- Backend: FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL.
- Jobs: Greenhouse and Lever public API adapters plus demo source.
- AI: OpenAI provider abstraction with deterministic local fallback.
- Documents: DOCX and PDF export services.
- Infrastructure: Docker Compose, Redis, Celery `worker` + `scheduler` (beat) services, GitHub Actions CI.

## Automated Ingestion & Fit Scoring

Discovery, ingestion and fit scoring run automatically — no browser session required.

- **Daily ingestion**: the `scheduler` (Celery beat) service triggers a system-wide
  run on `JOB_INGESTION_SCHEDULE` (default `0 6 * * *`). The run is guarded by a Redis
  lock (+ Postgres advisory lock) so multiple replicas never ingest concurrently.
  Per-source failures are isolated; each run is recorded in `ingestion_runs`.
- **Automatic scoring**: whenever a job is inserted or its score-relevant content
  changes, scoring is enqueued to the `worker` for every active user. Each
  `(user, job)` fit score carries an explicit `score_state`
  (`pending`/`scoring`/`scored`/`failed`/`profile_incomplete`), a `score_version`,
  and content/profile hashes so work is idempotent and never redundant.
- **Frontend**: the Jobs page shows "Calculating fit…" while a score settles, prompts
  to complete the profile when needed, and polls only while pending scores are visible.

Operational commands (run inside the `api` container):

```bash
# One-off backfill of missing scores (dry-run first)
python -m app.jobs.backfill_scores --posted-within-days 7 --only-missing --dry-run
python -m app.jobs.backfill_scores --posted-within-days 7 --only-missing

# Admin ingestion controls
python -m app.jobs.manage run-all                 # ingest every enabled source now
python -m app.jobs.manage run-ats greenhouse      # one ATS provider
python -m app.jobs.manage run-company stripe       # one company slug
python -m app.jobs.manage validate-registry        # verify sources against live ATS
python -m app.jobs.manage runs                      # recent ingestion-run history
```

Recent runs and the scoring backlog are also exposed at `/debug/ingestion-runs` and
`/debug/scoring-status`.

## Stable Local Start

The default Docker Compose file is the stable local and production-like path. It runs API and web code from the built images and does not bind-mount source code into the containers.

```bash
cd /Users/cprakash/Documents/Jobs/Job_Apply/jobpilot-ai
cp .env.example .env
docker compose down -v
docker compose up --build -d --force-recreate
docker compose logs api -f
```

`docker compose down -v` deletes local Docker database volumes. Use it only for local development and never for production data.

Open:

- Web: http://localhost:3000
- API docs: http://localhost:8000/docs

## Test Backend

```bash
curl -i http://localhost:8000/health
curl -i http://localhost:8000/readyz
curl -i http://localhost:8000/docs
```

## Test Auth

```bash
curl -i -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test1@example.com","password":"password123"}'

curl -i -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test1@example.com","password":"password123"}'
```

## Profile Import

The Profile manager can import a draft profile from user-supplied content:

- Resume PDF
- Resume DOCX
- LinkedIn PDF exported by the user with LinkedIn's Save to PDF action
- Pasted resume/profile text

LinkedIn support means user-uploaded PDF parsing only. JobPilot AI does not scrape LinkedIn, automate login, use cookies, use sessions, or run browser automation for this feature.

Uploaded files are parsed in memory and discarded. The MVP file size limit is 5MB. Scanned image PDFs may not work because OCR is not enabled; upload a text-based PDF/DOCX or paste the resume text instead.

Text import:

```bash
curl -i -X POST http://localhost:8000/profile/import/text \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_type":"resume_text","text":"Demo Student\nSkills: Python, React, FastAPI\nEducation: Arizona State University"}'
```

File import:

```bash
curl -i -X POST http://localhost:8000/profile/import/file \
  -H "Authorization: Bearer $TOKEN" \
  -F "source_type=resume" \
  -F "file=@/path/to/resume.pdf"
```

LinkedIn PDF import:

```bash
curl -i -X POST http://localhost:8000/profile/import/file \
  -H "Authorization: Bearer $TOKEN" \
  -F "source_type=linkedin_pdf" \
  -F "file=@/path/to/linkedin-profile.pdf"
```

## Hot Reload Dev Mode

Use the optional development override when you want source bind mounts and reload:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

The override bind-mounts `./apps/api:/app` and `./apps/web:/app`, preserves `/app/node_modules` for the web container, and sets `API_RELOAD=true` for Uvicorn reload.

## Dirty Local DB Reset

A failed local migration can leave PostgreSQL enum types behind without creating tables. Symptoms include `relation "users" does not exist` or `DuplicateObject: type "documenttype" already exists`.

For local development only, reset Docker volumes:

```bash
docker compose down -v
docker compose up --build -d --force-recreate
```

Warning: `docker compose down -v` deletes local Docker database volumes and must never be used for production data.

## Migration Policy

Local Docker testing enables startup migrations with `RUN_MIGRATIONS_ON_STARTUP=true` in `.env.example` and the default compose environment. Startup fails immediately if migrations fail.

Production should run migrations as a separate release or predeploy command before starting API containers:

```bash
alembic upgrade head
```

The API should start only after that migration command succeeds. Do not add destructive reset behavior to production startup, and do not auto-drop tables or volumes.

Useful migration checks:

```bash
docker compose exec api alembic current
docker compose exec api alembic heads
docker compose exec api alembic upgrade head
```

## Makefile Commands

```bash
make dev
make dev-hot
make down
make reset-db
make logs-api
make migrate
make test
make verify-auth
```

`make reset-db` warns before deleting local Docker volumes. It is only for disposable local databases.

## Development Without Docker

Backend:

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend:

```bash
cd apps/web
npm install
npm run dev
```

## Environment Variables

See `.env.example`. `OPENAI_API_KEY` is optional for local development because the API includes deterministic fallbacks. Set production-grade `SECRET_KEY`, database credentials, CORS origins, and demographic encryption strategy before deployment.

## Testing

Backend:

```bash
cd apps/api
python3 -m compileall app
pytest
```

Frontend:

```bash
cd apps/web
npm run lint
npm run typecheck
npm run build
npm test
```

## Roadmap

- Complete first-class UI editors for education, experience, projects, certifications, and awards.
- Add production encryption for sensitive demographic values.
- Add official ATS integrations where allowed.
- Add deployment templates for managed PostgreSQL, Redis, object storage, and malware scanning.
- Expand end-to-end tests around generated document editing and export.

## Contributing

Pull requests should preserve the compliance boundaries in `docs/compliance.md`, add tests for data isolation and generation guardrails, and avoid unsupported automation against third-party job portals.

## License

MIT
