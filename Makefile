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

DEPLOY_DESCRIPTOR ?= deploy/product.toml
DEPLOY_RENDERED ?= deploy/rendered
DEPLOY_THRESHOLDS ?= deploy/alerts/thresholds.json

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
CONNECTOR_SOURCES := $(sort $(filter-out %/__pycache__,$(wildcard packages/dotmac-connector-*/src/*)))
MODULE_SOURCES := $(sort $(filter-out %/__pycache__ $(CONNECTOR_SOURCES),$(wildcard packages/dotmac-*/src/*)))
# Module and connector packages are open families. A new distribution enrolls
# in both quality gates by existing under packages/dotmac-*/src/*; no package
# name belongs in this Makefile.
type-check: ## mypy (assembly + kernel + UI + module packages)
	poetry run mypy app $(MODULE_SOURCES) $(CONNECTOR_SOURCES)
security: ## Bandit security scan (assembly + kernel + UI + module packages)
	poetry run bandit -c pyproject.toml -r app $(MODULE_SOURCES) $(CONNECTOR_SOURCES)
ALEMBIC_INI ?= alembic.ini
migration-gate: ## Composed migration gate (ADR-0006 D1): revisions/prefixes/branches/schemas/table ownership
	ALEMBIC_INI=$(ALEMBIC_INI) poetry run python scripts/migration_gate.py
ALLOCATION_BASE ?= origin/main
allocation-gate: ## Serialized allocation (ADR-0006 D1): a module's ledger row must be merged BEFORE its source
	poetry run python scripts/check_allocation_serialized.py --base $(ALLOCATION_BASE)
FLEET_ROOT ?= ..
fleet-matrix: ## Re-measure the ERP/CRM/Sub duplication baseline (needs the fleet beside this checkout; not in `check`)
	poetry run python scripts/fleet_decomposition_sweep.py --fleet-root $(FLEET_ROOT) --check
fleet-facts: ## Re-measure fact-level ownership coverage across ERP/CRM/Sub (same prerequisites)
	poetry run python scripts/fleet_fact_registry.py --fleet-root $(FLEET_ROOT) --check
palette-baseline: ## Regenerate the hardcoded-palette debt baseline (commit the diff in the same change)
	poetry run python scripts/palette_debt_baseline.py
connector-baseline: ## Regenerate the external-connector baseline after a verified Integrator cutover (commit the diff in the same change)
	poetry run python scripts/external_connector_sweep.py --write-baseline
connector-ratchet: ## Run the external-connector ratchet with full coverage disclosure (needs the fleet beside this checkout; not in `check`)
	poetry run python scripts/external_connector_sweep.py --fleet-root $(FLEET_ROOT) --check --strict-coverage
credential-baseline: ## Regenerate the credential-lifecycle debt baseline after a verified retirement (commit the diff in the same change)
	poetry run python scripts/credential_lifecycle_sweep.py --fleet-root $(FLEET_ROOT) --write-baseline
credential-ratchet: ## Run the credential-lifecycle ratchet with full coverage disclosure (siblings need the fleet beside this checkout; the Starter half is enforced by the architecture test)
	poetry run python scripts/credential_lifecycle_sweep.py --fleet-root $(FLEET_ROOT) --check
publication-check: ## Report every distribution declaring a version nobody can install
	poetry run python scripts/declared_publication_sweep.py --check
publication-baseline: ## Regenerate the declared-but-unpublished ledger (state the reason; commit the diff in the same change)
	poetry run python scripts/declared_publication_sweep.py --write-baseline
# A published version's manifest is its contract, and a contract does not move.
# The `--check` half is deliberately offline and tag-free — it reads the ledger
# and the working tree, nothing else — which is what lets it sit in `check` and
# in the cheap CI matrix, where `actions/checkout` fetches no tags. The tag
# cross-check that stops the ledger being brought into line with a bad edit is
# `tests/architecture/test_released_manifest_digests.py`, in the `unit` job that
# already has `fetch-depth: 0`. Same split, same reason, as the publication
# sweep.
manifest-digest-check: ## Fail if a PUBLISHED connector version's manifest digest would change
	poetry run python scripts/released_manifest_sweep.py --check
manifest-digest-verify: ## Cross-check every recorded digest against its tag (needs full history + tags; not in `check`)
	poetry run python scripts/released_manifest_sweep.py --verify-tags
RELEASE_RUN ?=
manifest-digest-record: ## Record one published tag's manifest digest: make manifest-digest-record TAG=... [RELEASE_RUN=...]
	poetry run python scripts/released_manifest_sweep.py --record --tag "$(TAG)" --release-run "$(RELEASE_RUN)"
deployment-check: ## Validate deploy/product.toml and prove the rendered assets match it
	poetry run dotmac-deploy -f $(DEPLOY_DESCRIPTOR) validate
	poetry run dotmac-deploy -f $(DEPLOY_DESCRIPTOR) render --check -o $(DEPLOY_RENDERED) --thresholds $(DEPLOY_THRESHOLDS)

deployment-render: ## Re-render every deployment asset (commit the diff in the same change)
	poetry run dotmac-deploy -f $(DEPLOY_DESCRIPTOR) render -o $(DEPLOY_RENDERED) --thresholds $(DEPLOY_THRESHOLDS)

deployment-plan: ## Print the ordered deployment plan, gates marked (never deploys)
	poetry run dotmac-deploy -f $(DEPLOY_DESCRIPTOR) plan

product-writer-check: ## Require one typed, exact-pinned writer claim for every inventoried product
	poetry run python scripts/product_writer_check.py --check
rehearsal-status-check: ## Fail if the GENERATED Lane 3 status document drifted
	poetry run python scripts/generate_rehearsal_status.py --check
module-catalog: ## Regenerate the composable-module discovery catalogue
	poetry run python scripts/module_catalog.py
module-catalog-check: ## Fail if the committed module catalogue is stale
	poetry run python scripts/module_catalog.py --check
poetry-lock-check: ## Exact Poetry pin + committed root lock (never regenerates)
	python3 scripts/check_poetry_toolchain.py --active
	poetry check --lock
format-check: ## Formatting is a gate, not a recipe line — CI runs it as its own job
	poetry run ruff format --check .
check: poetry-lock-check lint lint-imports type-check security migration-gate ui-check module-catalog-check manifest-digest-check product-writer-check rehearsal-status-check deployment-check format-check ## Lock + lint + types + security + migration composition + generated catalogues + published manifest digests + design-system assets + deployment descriptor

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
# Graph commands never run `env.py`, so they cannot see the bindings it
# installs. Exporting the pointer here keeps `make migrate-graph` truthful about
# cross-lineage edges; `upgrade` does not need it (env.py installs directly).
MIGRATION_BINDINGS ?= app.migration_bindings:ASSEMBLY_PREREQUISITE_BINDINGS

migrate: ## Apply migrations (uses MIGRATION_DATABASE_URL from env)
	poetry run alembic upgrade heads
migrate-new: ## Create migration: make migrate-new msg="..."
	poetry run alembic revision --autogenerate -m "$(msg)"
migrate-graph: ## Show composed lineage heads + history (no database needed)
	DOTMAC_MIGRATION_BINDINGS=$(MIGRATION_BINDINGS) poetry run alembic heads
	DOTMAC_MIGRATION_BINDINGS=$(MIGRATION_BINDINGS) poetry run alembic history

##@ Dev
dev: ## Run dev server (run `make css-build` at least once first — templates reference static/css/main.css, which is gitignored/build-only)
	poetry run uvicorn app.main:app --reload --port 8000
css-build: ## Build Tailwind CSS once (static/css/src/main.css -> static/css/main.css)
	npm install
	npm run css:build
css-watch: ## Rebuild Tailwind CSS on file change (dev loop)
	npm install
	npm run css:watch

##@ Design system (dotmac-ui)
# NOTE: no npm. The design system's published CSS is generated from its own
# token source by a pure-Python, deterministic build (ADR-0006 D3 — the
# package's toolchain is its business, not a consumer's). Its output is
# COMMITTED, so a checkout has working assets with no build step at all; the
# `ui-check` gate (wired into `make check`) fails if the committed copy drifts
# from its source.
ui-build: ## Regenerate the dotmac-ui compiled assets from the token source (commit the result)
	poetry run python -m dotmac_ui.build
ui-check: ## Fail if the committed dotmac-ui assets are stale
	poetry run python -m dotmac_ui.build --check

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

.PHONY: help lint lint-imports format type-check security migration-gate fleet-matrix fleet-facts poetry-lock-check check test test-unit \
	test-integration test-cov test-db-up test-db-down migrate migrate-new dev \
	css-build css-watch ui-build ui-check palette-baseline connector-baseline connector-ratchet \
	credential-baseline credential-ratchet \
	publication-check publication-baseline module-catalog module-catalog-check \
	manifest-digest-check manifest-digest-verify manifest-digest-record \
	product-writer-check \
	docker-build docker-dev \
	bump-version deploy
