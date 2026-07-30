.DEFAULT_GOAL := help

TEST_DB_PORT ?= 5433
TEST_DB_USER ?= app_user
TEST_DB_PASSWORD ?= app_user
TEST_DB_ADMIN_USER ?= postgres
TEST_DB_ADMIN_PASSWORD ?= postgres
# platform_api role for the app's PLATFORM_DATABASE_URL in integration runs —
# the test compose uses trust auth, so the password value is irrelevant there.
TEST_DB_PLATFORM_USER ?= platform_api
TEST_DB_PLATFORM_PASSWORD ?= platform_api
TEST_DB_HOST ?= localhost
TEST_DB_NAME ?= starter_test

IMAGE_NAME ?= dotmac_starter_mt
IMAGE_TAG ?= dev
APP_PORT ?= 8000

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

##@ Quality
lint: ## Ruff lint
	poetry run ruff check .
lint-imports: ## Import boundary contracts
	poetry run lint-imports
format: ## Ruff format
	poetry run ruff format .
KERNEL_SRC ?= packages/dotmac-kernel/src/dotmac_kernel
type-check: ## mypy (assembly + kernel package)
	poetry run mypy app $(KERNEL_SRC)
security: ## Bandit security scan (assembly + kernel package)
	poetry run bandit -c pyproject.toml -r app $(KERNEL_SRC)
check: lint lint-imports type-check security ## Lint + types + security
	poetry run ruff format --check .

##@ Testing
test-unit: ## Fast SQLite unit + architecture tests
	poetry run pytest tests/unit tests/architecture -q
test-integration: ## Postgres RLS tests (needs test-db-up)
	TEST_DATABASE_URL=postgresql+psycopg://$(TEST_DB_USER):$(TEST_DB_PASSWORD)@$(TEST_DB_HOST):$(TEST_DB_PORT)/$(TEST_DB_NAME) \
	TEST_PLATFORM_DATABASE_URL=postgresql+psycopg://$(TEST_DB_PLATFORM_USER):$(TEST_DB_PLATFORM_PASSWORD)@$(TEST_DB_HOST):$(TEST_DB_PORT)/$(TEST_DB_NAME) \
	TEST_MIGRATION_DATABASE_URL=postgresql+psycopg://$(TEST_DB_ADMIN_USER):$(TEST_DB_ADMIN_PASSWORD)@$(TEST_DB_HOST):$(TEST_DB_PORT)/$(TEST_DB_NAME) \
	poetry run pytest tests -q --ignore=tests/unit --ignore=tests/architecture
test: test-unit ## Default test suite
test-cov: ## Unit tests with coverage
	poetry run pytest tests/unit tests/architecture --cov=app --cov-report=term-missing

##@ Database
test-db-up: ## Start disposable test Postgres (TEST_DB_PORT, default 5433) and migrate
	TEST_DB_PORT=$(TEST_DB_PORT) TEST_DB_ADMIN_USER=$(TEST_DB_ADMIN_USER) TEST_DB_ADMIN_PASSWORD=$(TEST_DB_ADMIN_PASSWORD) TEST_DB_NAME=$(TEST_DB_NAME) \
	docker compose -f docker-compose.test.yml up -d --wait
	MIGRATION_DATABASE_URL=postgresql+psycopg://$(TEST_DB_ADMIN_USER):$(TEST_DB_ADMIN_PASSWORD)@$(TEST_DB_HOST):$(TEST_DB_PORT)/$(TEST_DB_NAME) \
	DATABASE_URL=postgresql+psycopg://$(TEST_DB_ADMIN_USER):$(TEST_DB_ADMIN_PASSWORD)@$(TEST_DB_HOST):$(TEST_DB_PORT)/$(TEST_DB_NAME) \
	poetry run alembic upgrade heads
test-db-down: ## Stop test Postgres
	TEST_DB_PORT=$(TEST_DB_PORT) TEST_DB_ADMIN_USER=$(TEST_DB_ADMIN_USER) TEST_DB_ADMIN_PASSWORD=$(TEST_DB_ADMIN_PASSWORD) TEST_DB_NAME=$(TEST_DB_NAME) \
	docker compose -f docker-compose.test.yml down -v
migrate: ## Apply migrations (uses MIGRATION_DATABASE_URL from env)
	poetry run alembic upgrade heads
migrate-new: ## Create migration: make migrate-new msg="..."
	poetry run alembic revision --autogenerate -m "$(msg)"

##@ Dev
dev: ## Run dev server (run `make css-build` at least once first — templates reference static/css/main.css, which is gitignored/build-only)
	poetry run uvicorn app.main:app --reload --port 8000
css-build: ## Build Tailwind CSS once (static/css/src/main.css -> static/css/main.css)
	npm install
	npm run css:build
css-watch: ## Rebuild Tailwind CSS on file change (dev loop)
	npm install
	npm run css:watch

##@ Docker
docker-build: ## Build local dev image (IMAGE_NAME, IMAGE_TAG, APP_PORT overridable)
	docker build --build-arg APP_PORT=$(APP_PORT) -t $(IMAGE_NAME):$(IMAGE_TAG) .
docker-dev: ## Run app+postgres locally (dev overlay)
	APP_IMAGE=$(IMAGE_NAME):$(IMAGE_TAG) APP_PORT=$(APP_PORT) IMAGE_NAME=$(IMAGE_NAME) IMAGE_TAG=$(IMAGE_TAG) \
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

##@ Release
bump-version: ## Bump semver: make bump-version part=patch|minor|major
	poetry run python scripts/bump_version.py $(part)
deploy: ## Deploy tag: make deploy TAG=sha-abc123
	IMAGE_NAME=$(IMAGE_NAME) APP_PORT=$(APP_PORT) ./scripts/deploy.sh $(TAG)

.PHONY: help lint lint-imports format type-check security check test test-unit \
	test-integration test-cov test-db-up test-db-down migrate migrate-new dev \
	css-build css-watch docker-build docker-dev bump-version deploy
