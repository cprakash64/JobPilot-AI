.PHONY: dev dev-hot down reset-db migrate logs-api test test-api test-web test-extension verify-auth

# Prefer the project-local backend environment when it exists. Developers can
# still override this explicitly, for example: make test-api API_PYTHON=python3.12
API_PYTHON ?= $(if $(wildcard $(CURDIR)/apps/api/.venv/bin/python),$(CURDIR)/apps/api/.venv/bin/python,python3)
# This repository commonly lives in a macOS File Provider folder. Buildx's
# automatic Git provenance scan can block there before Docker reads any build
# context, so local builds omit that optional metadata. Production CI can build
# normally and retain its own source/SBOM provenance.
LOCAL_DOCKER_BUILD ?= env BUILDX_GIT_INFO=false BUILDX_GIT_CHECK_DIRTY=false docker compose

dev:
	$(LOCAL_DOCKER_BUILD) up --build -d

dev-hot:
	$(LOCAL_DOCKER_BUILD) -f docker-compose.yml -f docker-compose.dev.yml up --build

down:
	docker compose down

reset-db:
	@echo "WARNING: this destroys local Docker volumes, including the local Postgres database."
	@echo "Use only for local development. Never run this against production data."
	docker compose down -v
	$(LOCAL_DOCKER_BUILD) up --build -d --force-recreate

migrate:
	docker compose exec api alembic upgrade head

logs-api:
	docker compose logs api -f

test: test-api test-web test-extension

test-api:
	@$(API_PYTHON) -c "import pytest" 2>/dev/null || \
		(echo "Backend test dependencies are missing. Run: cd apps/api && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1)
	$(API_PYTHON) -m compileall apps/api/app scripts
	cd apps/api && env APP_ENV=test DEBUG=false $(API_PYTHON) -m pytest

test-web:
	cd apps/web && npm run lint
	cd apps/web && npm run typecheck
	cd apps/web && npm run build
	cd apps/web && npm test

test-extension:
	cd apps/extension && npm run typecheck
	cd apps/extension && npm test
	cd apps/extension && npm run build

verify-auth:
	curl -i -sS http://localhost:8000/health
	curl -i -sS http://localhost:8000/readyz
	@EMAIL="make-auth-$$(date +%s)@example.com"; \
	curl -i -sS -X POST http://localhost:8000/auth/signup -H "Content-Type: application/json" -d "{\"email\":\"$$EMAIL\",\"password\":\"password123\"}"; \
	curl -i -sS -X POST http://localhost:8000/auth/login -H "Content-Type: application/json" -d "{\"email\":\"$$EMAIL\",\"password\":\"password123\"}"
