.PHONY: setup format lint typecheck test build schemas schemas-check openapi openapi-check catalog catalog-check scenarios scenarios-check docs db-up db-down migrate verify api dashboard-setup dashboard-verify

setup: dashboard-setup
	python3.12 -m venv .venv
	.venv/bin/python -m pip install -e '.[dev,openai-agents,langgraph]'

dashboard-setup:
	cd apps/dashboard && npm ci
	cd apps/dashboard && npx playwright install --with-deps chromium

format:
	.venv/bin/ruff format .
	.venv/bin/ruff check --fix .

lint:
	.venv/bin/ruff format --check .
	.venv/bin/ruff check .

typecheck:
	.venv/bin/mypy src tests

test:
	.venv/bin/pytest

build:
	.venv/bin/python -m build

schemas:
	.venv/bin/worker-worlds-schema generate

schemas-check:
	.venv/bin/worker-worlds-schema check

openapi:
	PYTHONPATH=src .venv/bin/python -m worker_worlds.openapi_cli generate

openapi-check:
	PYTHONPATH=src .venv/bin/python -m worker_worlds.openapi_cli check

catalog:
	PYTHONPATH=src .venv/bin/python -m worker_worlds.catalog generate

catalog-check:
	PYTHONPATH=src .venv/bin/python -m worker_worlds.catalog check

scenarios:
	.venv/bin/worker-worlds scenario export scenarios/release --overwrite

scenarios-check:
	.venv/bin/worker-worlds scenario export scenarios/release --check
	.venv/bin/worker-worlds scenario validate scenarios/release

docs:
	.venv/bin/python scripts/build_docs.py

db-up:
	docker compose up -d --wait postgres

db-down:
	docker compose down

migrate:
	.venv/bin/worker-worlds migrate

api:
	.venv/bin/worker-worlds-api

verify: lint typecheck schemas-check openapi-check catalog-check scenarios-check test docs dashboard-verify build

dashboard-verify:
	test -s site/dashboard.html
	grep -q 'aria-live' site/dashboard.html
	cd apps/dashboard && npm run lint
	cd apps/dashboard && npx tsc --noEmit
	cd apps/dashboard && npm run test:e2e
	cd apps/dashboard && npm run build
