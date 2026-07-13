.PHONY: dev dev-hot down reset-db migrate logs-api test verify-auth

dev:
	docker compose up --build -d

dev-hot:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

down:
	docker compose down

reset-db:
	@echo "WARNING: this destroys local Docker volumes, including the local Postgres database."
	@echo "Use only for local development. Never run this against production data."
	docker compose down -v
	docker compose up --build -d --force-recreate

migrate:
	docker compose exec api alembic upgrade head

logs-api:
	docker compose logs api -f

test:
	python3 -m compileall apps/api/app scripts
	cd apps/api && pytest
	cd apps/web && npm run lint
	cd apps/web && npm run typecheck
	cd apps/web && npm run build
	cd apps/web && npm test

verify-auth:
	curl -i -sS http://localhost:8000/health
	curl -i -sS http://localhost:8000/readyz
	@EMAIL="make-auth-$$(date +%s)@example.com"; \
	curl -i -sS -X POST http://localhost:8000/auth/signup -H "Content-Type: application/json" -d "{\"email\":\"$$EMAIL\",\"password\":\"password123\"}"; \
	curl -i -sS -X POST http://localhost:8000/auth/login -H "Content-Type: application/json" -d "{\"email\":\"$$EMAIL\",\"password\":\"password123\"}"
