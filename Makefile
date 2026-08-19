.PHONY: setup format lint typecheck test build schemas schemas-check scenarios scenarios-check docs db-up db-down migrate verify dashboard-setup dashboard-verify

setup:
	python3.12 -m venv .venv
	.venv/bin/python -m pip install -e '.[dev]'

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

verify: lint typecheck schemas-check scenarios-check test docs build

dashboard-setup:
	cd apps/dashboard && npm install

dashboard-verify:
	cd apps/dashboard && npm run lint && npm run build
