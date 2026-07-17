.DEFAULT_GOAL := help

TEST_DB_PORT ?= 5433

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

##@ Quality
lint: ## Ruff lint
	poetry run ruff check .
lint-imports: ## Import boundary contracts
	poetry run lint-imports
format: ## Ruff format
	poetry run ruff format .
type-check: ## mypy
	poetry run mypy app
security: ## Bandit security scan
	poetry run bandit -c pyproject.toml -r app
check: lint type-check security ## Lint + types + security
	poetry run ruff format --check .

##@ Testing
test-unit: ## Fast SQLite unit + architecture tests
	poetry run pytest tests/unit tests/architecture -q
test-integration: ## Postgres RLS tests (needs test-db-up)
	TEST_DATABASE_URL=postgresql+psycopg://app_user:app_user@localhost:$(TEST_DB_PORT)/starter_test \
	TEST_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:$(TEST_DB_PORT)/starter_test \
	poetry run pytest tests -q --ignore=tests/unit --ignore=tests/architecture
test: test-unit ## Default test suite
test-cov: ## Unit tests with coverage
	poetry run pytest tests/unit tests/architecture --cov=app --cov-report=term-missing

##@ Database
test-db-up: ## Start disposable test Postgres (port $(TEST_DB_PORT)) and migrate
	TEST_DB_PORT=$(TEST_DB_PORT) docker compose -f docker-compose.test.yml up -d --wait
	MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:$(TEST_DB_PORT)/starter_test \
	DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:$(TEST_DB_PORT)/starter_test \
	poetry run alembic upgrade head
test-db-down: ## Stop test Postgres
	TEST_DB_PORT=$(TEST_DB_PORT) docker compose -f docker-compose.test.yml down -v
migrate: ## Apply migrations (uses MIGRATION_DATABASE_URL from env)
	poetry run alembic upgrade head
migrate-new: ## Create migration: make migrate-new msg="..."
	poetry run alembic revision --autogenerate -m "$(msg)"

##@ Dev
dev: ## Run dev server
	poetry run uvicorn app.main:app --reload --port 8000

.PHONY: help lint lint-imports format type-check security check test test-unit \
	test-integration test-cov test-db-up test-db-down migrate migrate-new dev
