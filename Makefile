COMPOSE = docker compose -f infra/compose.yaml --env-file .env

.PHONY: install test test-rules fixture up down logs migrate backup web-install web-dev web-build web-test api-dev worker-once

install:            ## Python deps (uv) for the whole workspace
	uv sync

test:               ## All Python tests (embedded PostgreSQL via pgserver, or LK_TEST_DATABASE_URL)
	uv run pytest

test-rules:
	uv run pytest packages

fixture:            ## Regenerate the synthetic adventure PDF
	uv run python scripts/generate_fixture.py

up:                 ## Build and start the full stack
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

migrate:
	$(COMPOSE) run --rm migrate

backup:
	sh scripts/backup.sh

api-dev:            ## Run the API locally against LK_DATABASE_URL
	cd services/api && uv run uvicorn lk_api.main:create_app --factory --reload --port 8000

worker-once:
	cd services/api && uv run python -m lk_api.worker --once

web-install:
	cd apps/web && pnpm install

web-dev:
	cd apps/web && pnpm dev

web-build:
	cd apps/web && pnpm build

web-test:           ## Playwright end-to-end tests (needs the stack running)
	cd apps/web && pnpm exec playwright test
