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
UI_SRC ?= packages/dotmac-ui/src/dotmac_ui
MODULE_SRC ?= packages/dotmac-template-studio/src/dotmac_template_studio
TICKETING_SRC ?= packages/dotmac-ticketing/src/dotmac_ticketing
RELEASE_CATALOG_SRC ?= packages/dotmac-release-catalog/src/dotmac_release_catalog
ENTITLEMENT_ALLOCATION_SRC ?= packages/dotmac-entitlement-allocation/src/dotmac_entitlement_allocation
APPDIR_SRC ?= packages/dotmac-application-directory/src/dotmac_application_directory
FILES_SRC ?= packages/dotmac-files/src/dotmac_files
IMPORTS_SRC ?= packages/dotmac-imports/src/dotmac_imports
APPROVALS_SRC ?= packages/dotmac-approvals/src/dotmac_approvals
NUMBERING_SRC ?= packages/dotmac-numbering/src/dotmac_numbering
PEOPLE_SRC ?= packages/dotmac-people/src/dotmac_people
DURABLE_TIMERS_SRC ?= packages/dotmac-durable-timers/src/dotmac_durable_timers
COMMERCIAL_AGREEMENTS_SRC ?= packages/dotmac-commercial-agreements/src/dotmac_commercial_agreements
LICENSING_SRC ?= packages/dotmac-licensing/src/dotmac_licensing
DEPLOYMENT_CONTROL_SRC ?= packages/dotmac-deployment-control/src/dotmac_deployment_control
BRAND_PROFILES_SRC ?= packages/dotmac-brand-profiles/src/dotmac_brand_profiles
INTEGRATION_SRC ?= packages/dotmac-integration/src/dotmac_integration
MEDIA_OBSERVATIONS_SRC ?= packages/dotmac-media-observations/src/dotmac_media_observations
OIDC_SRC ?= packages/dotmac-auth-oidc/src/dotmac_auth_oidc
CAMPAIGNS_SRC ?= packages/dotmac-campaigns/src/dotmac_campaigns
CONTENT_SRC ?= packages/dotmac-content/src/dotmac_content
PUBLISHING_SRC ?= packages/dotmac-publishing/src/dotmac_publishing
SITES_SRC ?= packages/dotmac-sites/src/dotmac_sites
INVENTORY_SRC ?= packages/dotmac-inventory/src/dotmac_inventory
ASSETS_SRC ?= packages/dotmac-assets/src/dotmac_assets
IPAM_SRC ?= packages/dotmac-ipam/src/dotmac_ipam
NETWORK_INVENTORY_SRC ?= packages/dotmac-network-inventory/src/dotmac_network_inventory
NETWORK_OBSERVABILITY_SRC ?= packages/dotmac-network-observability/src/dotmac_network_observability
NETWORK_TOPOLOGY_SRC ?= packages/dotmac-network-topology/src/dotmac_network_topology
NETWORK_ASSURANCE_SRC ?= packages/dotmac-network-assurance/src/dotmac_network_assurance
NETWORK_CONTROL_SRC ?= packages/dotmac-network-control/src/dotmac_network_control
FIBER_PLANT_SRC ?= packages/dotmac-fiber-plant/src/dotmac_fiber_plant
NETWORK_ACCESS_SRC ?= packages/dotmac-network-access/src/dotmac_network_access
PON_ACCESS_SRC ?= packages/dotmac-pon-access/src/dotmac_pon_access
REFERRALS_SRC ?= packages/dotmac-referrals/src/dotmac_referrals
RESELLER_MANAGEMENT_SRC ?= packages/dotmac-reseller-management/src/dotmac_reseller_management
POSITIONING_SRC ?= packages/dotmac-positioning/src/dotmac_positioning
ACCOUNTING_SRC ?= packages/dotmac-accounting/src/dotmac_accounting
ANALYTICS_SRC ?= packages/dotmac-analytics/src/dotmac_analytics
BANKING_SRC ?= packages/dotmac-banking/src/dotmac_banking
DOCUMENTS_SRC ?= packages/dotmac-documents/src/dotmac_documents
EXPENSES_SRC ?= packages/dotmac-expenses/src/dotmac_expenses
FINANCE_SRC ?= packages/dotmac-finance/src/dotmac_finance
INBOX_SRC ?= packages/dotmac-inbox/src/dotmac_inbox
PARTY_SRC ?= packages/dotmac-party/src/dotmac_party
PAYABLES_SRC ?= packages/dotmac-payables/src/dotmac_payables
PAYROLL_SRC ?= packages/dotmac-payroll/src/dotmac_payroll
PROCUREMENT_SRC ?= packages/dotmac-procurement/src/dotmac_procurement
PROJECTS_SRC ?= packages/dotmac-projects/src/dotmac_projects
RECORDS_SRC ?= packages/dotmac-records/src/dotmac_records
SURVEYS_SRC ?= packages/dotmac-surveys/src/dotmac_surveys
TAX_SRC ?= packages/dotmac-tax/src/dotmac_tax
WORK_ORDERS_SRC ?= packages/dotmac-work-orders/src/dotmac_work_orders
WEB_ANALYTICS_SRC ?= packages/dotmac-web-analytics/src/dotmac_web_analytics
# Connector plugins are an open package family. A new provider distribution
# enrolls in both quality gates by existing under the release-lane prefix; no
# provider name belongs in this Makefile.
CONNECTOR_SOURCES := $(sort $(filter-out %/__pycache__,$(wildcard packages/dotmac-connector-*/src/*)))
type-check: ## mypy (assembly + kernel + UI + module packages)
	poetry run mypy app $(KERNEL_SRC) $(UI_SRC) $(MODULE_SRC) $(TICKETING_SRC) $(RELEASE_CATALOG_SRC) $(ENTITLEMENT_ALLOCATION_SRC) $(APPDIR_SRC) $(FILES_SRC) $(IMPORTS_SRC) $(APPROVALS_SRC) $(NUMBERING_SRC) $(PEOPLE_SRC) $(DURABLE_TIMERS_SRC) $(COMMERCIAL_AGREEMENTS_SRC) $(LICENSING_SRC) $(DEPLOYMENT_CONTROL_SRC) $(BRAND_PROFILES_SRC) $(INTEGRATION_SRC) $(MEDIA_OBSERVATIONS_SRC) $(OIDC_SRC) $(CAMPAIGNS_SRC) $(CONTENT_SRC) $(PUBLISHING_SRC) $(SITES_SRC) $(INVENTORY_SRC) $(ASSETS_SRC) $(IPAM_SRC) $(NETWORK_INVENTORY_SRC) $(NETWORK_OBSERVABILITY_SRC) $(NETWORK_TOPOLOGY_SRC) $(NETWORK_ASSURANCE_SRC) $(NETWORK_CONTROL_SRC) $(FIBER_PLANT_SRC) $(NETWORK_ACCESS_SRC) $(PON_ACCESS_SRC) $(POSITIONING_SRC) $(REFERRALS_SRC) $(RESELLER_MANAGEMENT_SRC) $(ACCOUNTING_SRC) $(ANALYTICS_SRC) $(BANKING_SRC) $(DOCUMENTS_SRC) $(EXPENSES_SRC) $(FINANCE_SRC) $(INBOX_SRC) $(PARTY_SRC) $(PAYABLES_SRC) $(PAYROLL_SRC) $(PROCUREMENT_SRC) $(PROJECTS_SRC) $(RECORDS_SRC) $(SURVEYS_SRC) $(TAX_SRC) $(WORK_ORDERS_SRC) $(WEB_ANALYTICS_SRC) $(CONNECTOR_SOURCES)
security: ## Bandit security scan (assembly + kernel + UI + module packages)
	poetry run bandit -c pyproject.toml -r app $(KERNEL_SRC) $(UI_SRC) $(MODULE_SRC) $(TICKETING_SRC) $(RELEASE_CATALOG_SRC) $(ENTITLEMENT_ALLOCATION_SRC) $(APPDIR_SRC) $(FILES_SRC) $(IMPORTS_SRC) $(APPROVALS_SRC) $(NUMBERING_SRC) $(PEOPLE_SRC) $(DURABLE_TIMERS_SRC) $(COMMERCIAL_AGREEMENTS_SRC) $(LICENSING_SRC) $(DEPLOYMENT_CONTROL_SRC) $(BRAND_PROFILES_SRC) $(INTEGRATION_SRC) $(MEDIA_OBSERVATIONS_SRC) $(OIDC_SRC) $(CAMPAIGNS_SRC) $(CONTENT_SRC) $(PUBLISHING_SRC) $(SITES_SRC) $(INVENTORY_SRC) $(ASSETS_SRC) $(IPAM_SRC) $(NETWORK_INVENTORY_SRC) $(NETWORK_OBSERVABILITY_SRC) $(NETWORK_TOPOLOGY_SRC) $(NETWORK_ASSURANCE_SRC) $(NETWORK_CONTROL_SRC) $(FIBER_PLANT_SRC) $(NETWORK_ACCESS_SRC) $(PON_ACCESS_SRC) $(POSITIONING_SRC) $(REFERRALS_SRC) $(RESELLER_MANAGEMENT_SRC) $(ACCOUNTING_SRC) $(ANALYTICS_SRC) $(BANKING_SRC) $(DOCUMENTS_SRC) $(EXPENSES_SRC) $(FINANCE_SRC) $(INBOX_SRC) $(PARTY_SRC) $(PAYABLES_SRC) $(PAYROLL_SRC) $(PROCUREMENT_SRC) $(PROJECTS_SRC) $(RECORDS_SRC) $(SURVEYS_SRC) $(TAX_SRC) $(WORK_ORDERS_SRC) $(WEB_ANALYTICS_SRC) $(CONNECTOR_SOURCES)
ALEMBIC_INI ?= alembic.ini
migration-gate: ## Composed migration gate (ADR-0006 D1): revisions/prefixes/branches/schemas/table ownership
	ALEMBIC_INI=$(ALEMBIC_INI) poetry run python scripts/migration_gate.py
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
publication-check: ## Report every distribution declaring a version nobody can install
	poetry run python scripts/declared_publication_sweep.py --check
publication-baseline: ## Regenerate the declared-but-unpublished ledger (state the reason; commit the diff in the same change)
	poetry run python scripts/declared_publication_sweep.py --write-baseline
module-catalog: ## Regenerate the composable-module discovery catalogue
	poetry run python scripts/module_catalog.py
module-catalog-check: ## Fail if the committed module catalogue is stale
	poetry run python scripts/module_catalog.py --check
poetry-lock-check: ## Exact Poetry pin + committed root lock (never regenerates)
	python3 scripts/check_poetry_toolchain.py --active
	poetry check --lock
format-check: ## Formatting is a gate, not a recipe line — CI runs it as its own job
	poetry run ruff format --check .
check: poetry-lock-check lint lint-imports type-check security migration-gate ui-check module-catalog-check format-check ## Lock + lint + types + security + migration composition + generated catalogues + design-system assets

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
	publication-check publication-baseline module-catalog module-catalog-check \
	docker-build docker-dev \
	bump-version deploy
