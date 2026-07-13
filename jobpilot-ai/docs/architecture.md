# Architecture

JobPilot AI is a monorepo with a Next.js frontend, FastAPI backend, shared schema package, Docker Compose infrastructure, and GitHub Actions CI.

## Backend

The API owns authentication, profile data, job ingestion, matching, document generation, exports, and deletion. SQLAlchemy models define all persistent tables. Sensitive demographic values live in `sensitive_demographics`, separate from `user_profiles`.

Job adapters implement a common `JobSourceAdapter` interface and normalize allowed public structured endpoints into `JobPosting` records. Demo data is available for local testing.

AI work is routed through `app/ai/provider.py`. If `OPENAI_API_KEY` is absent, deterministic local outputs keep the MVP usable without sending data to a provider.

## Frontend

The frontend uses App Router pages for public routes and authenticated product workflows. The UI is workflow-first: profile setup, job discovery, generation, tracking, EEO settings, and data controls.

## Data Boundaries

Sensitive demographics are excluded from matching, ranking, and resume generation. Application-answer generation may use EEO data only when a future request explicitly passes consent for that specific use.

