# Phase 1: Infrastructure Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `dotmac_starter_mt` into the consolidated DotMac starter's foundation: `app/core/` + `app/features/` layout with a feature-manifest registry, dotmac_sub's infrastructure patterns (CRUD base, UnitOfWork, JSON logging, error envelope, architecture tests, import-linter, Makefile, CI, Docker/deploy), and a fast SQLite unit suite alongside the existing Postgres RLS canaries.

**Architecture:** Cross-cutting infrastructure moves to `app/core/`; each domain (tenants, auth, persons, rbac) becomes a self-contained package under `app/features/` with a `feature.py` manifest; `app/main.py` mounts features from a registry. Business logic is extracted from routers into per-feature `service.py` so architecture tests can enforce thin wrappers. Ports come from exact files in `/home/dotmac/projects/dotmac_sub` (infra) — features stay tenant-scoped per MT's RLS design.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Postgres 16 (RLS), Poetry, ruff, mypy, bandit, import-linter, pre-commit, pytest, GitHub Actions, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-07-17-starter-consolidation-design.md`

## Global Constraints

- Repo: `/home/dotmac/projects/dotmac_starter_mt` (branch `main`; create working branch `phase1-infra`).
- Port sources referenced as `SUB:<path>` mean `/home/dotmac/projects/dotmac_sub/<path>`; `ST:<path>` means `/home/dotmac/projects/dotmac_starter/<path>`. Copy then adapt — never import across repos.
- ruff line-length **88**, target py312, rules `E,W,F,I,B,C4,UP,S,RUF` (keep MT's existing ignores `B008`, `S101`-in-tests).
- Tenancy invariants must never regress: `get_db` sets `SET LOCAL app.current_tenant`; tenant-scoped models keep `tenant_id` + composite uniques; migrations run as `app_admin` role.
- Route prefixes stay as-is (`/auth`, `/people`, `/rbac`, `/platform/tenants`) — no `/api/v1` rename in this phase.
- Existing Postgres tests in `tests/*.py` must keep passing (they skip without `TEST_DATABASE_URL`; run them whenever a local test DB is up — see Task 5's `make test-db-up`).
- Every task ends with `make check` green (after Task 1 introduces it) and a commit.
- Python env: `poetry install` once at start; all commands via `poetry run` or the Makefile.
- Deferred out of this phase (recorded here so nobody "helpfully" adds them): auto version-bump-PR workflow, HTML error templates, Celery, web UI, detect-secrets baseline refresh automation.

---

### Task 1: Tooling baseline (ruff/mypy/bandit config, dev deps, pre-commit, Makefile)

**Files:**
- Modify: `pyproject.toml`
- Create: `.pre-commit-config.yaml`
- Create: `Makefile`
- Create: `mypy.ini` section inside `pyproject.toml` (no separate file)

**Interfaces:**
- Produces: `make help|lint|format|type-check|security|check|test|test-unit|test-integration` targets used by every later task; ruff config all later code must satisfy.

- [ ] **Step 1: Branch and install**

```bash
cd /home/dotmac/projects/dotmac_starter_mt
git checkout -b phase1-infra
poetry install
```

- [ ] **Step 2: Update pyproject tooling config**

Replace the existing `[tool.ruff]`/`[tool.ruff.lint]` blocks and append mypy/bandit/pytest config:

```toml
[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "C4", "UP", "S", "RUF"]
ignore = [
    "B008",  # FastAPI dependency injection uses calls in defaults.
    "S101",  # asserts (tests; also narrowed per-file below)
]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S105", "S106"]
"alembic/*" = ["E501"]

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
disallow_untyped_defs = false
check_untyped_defs = true
warn_unused_ignores = true

[tool.bandit]
exclude_dirs = ["tests", "alembic"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
pythonpath = ["."]
```

Add dev dependencies:

```bash
poetry add --group dev pytest-cov import-linter bandit pre-commit
```

- [ ] **Step 3: Create `.pre-commit-config.yaml`**

Base on `SUB:.pre-commit-config.yaml`; trimmed to what exists here (no detect-secrets baseline yet):

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: check-merge-conflict
      - id: debug-statements
      - id: detect-private-key
```

- [ ] **Step 4: Create `Makefile`**

Port the self-documenting pattern from `SUB:Makefile` (the `## ` help convention). Full content:

```makefile
.DEFAULT_GOAL := help

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
	TEST_DATABASE_URL=postgresql+psycopg://app_user:app_user@localhost:5433/starter_test \
	TEST_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5433/starter_test \
	poetry run pytest tests -q --ignore=tests/unit --ignore=tests/architecture
test: test-unit ## Default test suite
test-cov: ## Unit tests with coverage
	poetry run pytest tests/unit tests/architecture --cov=app --cov-report=term-missing

##@ Database
test-db-up: ## Start disposable test Postgres (port 5433) and migrate
	docker compose -f docker-compose.test.yml up -d --wait
	MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5433/starter_test \
	DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5433/starter_test \
	poetry run alembic upgrade head
test-db-down: ## Stop test Postgres
	docker compose -f docker-compose.test.yml down -v
migrate: ## Apply migrations (uses MIGRATION_DATABASE_URL from env)
	poetry run alembic upgrade head
migrate-new: ## Create migration: make migrate-new msg="..."
	poetry run alembic revision --autogenerate -m "$(msg)"

##@ Dev
dev: ## Run dev server
	poetry run uvicorn app.main:app --reload --port 8000

.PHONY: help lint lint-imports format type-check security check test test-unit \
	test-integration test-cov test-db-up test-db-down migrate migrate-new dev
```

Note: `lint-imports` and `tests/unit`/`tests/architecture` don't exist yet — later tasks create them. Until Task 9, run `make lint type-check security` instead of full `check` if `lint-imports` is wired into `check` (it is not — keep `check` as defined above, which excludes `lint-imports` until Task 9 adds it).

- [ ] **Step 5: Reformat repo to line-length 88 and verify**

```bash
poetry run ruff format .
poetry run ruff check . --fix
make lint type-check security
```
Expected: all pass (fix any new findings mechanically; `S` findings in app code get targeted `# noqa` only with justification comments).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: tooling baseline — ruff 88, mypy/bandit config, pre-commit, Makefile"
```

---

### Task 2: Restructure cross-cutting modules into `app/core/`

**Files:**
- Create: `app/core/__init__.py` (empty)
- Move: `app/config.py → app/core/config.py`; `app/db.py → app/core/db.py`; `app/models/base.py → app/core/models.py`; `app/services/exceptions.py → app/core/exceptions.py`; `app/services/security.py → app/core/security.py`; `app/api/deps.py → app/core/deps.py`; `app/middleware/{csrf,observability,rate_limit,tenant}.py → app/core/middleware/`
- Modify: every importer (`app/main.py`, `app/api/*.py`, `app/models/*.py`, `app/services/audit.py`, `alembic/env.py`, `tests/*.py`)

**Interfaces:**
- Produces: canonical import paths used by all later tasks — `app.core.config.settings`, `app.core.db.get_db/get_platform_db/SessionLocal`, `app.core.models.Base/TimestampMixin/uuid_pk`, `app.core.exceptions.{DomainError,NotFoundError,BadRequestError,ConflictError}`, `app.core.security.*`, `app.core.deps.{require_tenant,require_platform,require_user_auth}`, `app.core.middleware.*`.

- [ ] **Step 1: Move files with git mv**

```bash
mkdir -p app/core/middleware
git mv app/config.py app/core/config.py
git mv app/db.py app/core/db.py
git mv app/models/base.py app/core/models.py
git mv app/services/exceptions.py app/core/exceptions.py
git mv app/services/security.py app/core/security.py
git mv app/api/deps.py app/core/deps.py
git mv app/middleware/csrf.py app/core/middleware/csrf.py
git mv app/middleware/observability.py app/core/middleware/observability.py
git mv app/middleware/rate_limit.py app/core/middleware/rate_limit.py
git mv app/middleware/tenant.py app/core/middleware/tenant.py
rmdir app/middleware 2>/dev/null || true
touch app/core/__init__.py app/core/middleware/__init__.py
```

- [ ] **Step 2: Rewrite imports across the repo**

Update every occurrence (grep-driven, exact mapping):

| old | new |
|---|---|
| `app.config` | `app.core.config` |
| `app.db` | `app.core.db` |
| `app.models.base` | `app.core.models` |
| `app.services.exceptions` | `app.core.exceptions` |
| `app.services.security` | `app.core.security` |
| `app.api.deps` | `app.core.deps` |
| `app.middleware.` | `app.core.middleware.` |

```bash
grep -rl -E "app\.(config|db|models\.base|services\.(exceptions|security)|api\.deps|middleware\.)" app tests alembic \
  | xargs sed -i -E 's/app\.config/app.core.config/g; s/app\.db/app.core.db/g; s/app\.models\.base/app.core.models/g; s/app\.services\.exceptions/app.core.exceptions/g; s/app\.services\.security/app.core.security/g; s/app\.api\.deps/app.core.deps/g; s/app\.middleware\./app.core.middleware./g'
```
Then hand-review `git diff` — sed must not have touched strings/docs incorrectly.

- [ ] **Step 3: Verify app imports and tests still collect**

```bash
poetry run python -c "import app.main; print('ok')"
poetry run pytest --collect-only -q | tail -3
make lint type-check
```
Expected: `ok`; tests collected with no import errors. If a local test DB is up (`make test-db-up`), also run `make test-integration` — expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "refactor: move cross-cutting modules into app/core"
```

---

### Task 3: JSON structured logging (`app/core/logging.py`)

**Files:**
- Create: `app/core/logging.py`
- Modify: `app/core/middleware/observability.py` (set request-id contextvar), `app/main.py` (call `setup_logging()`)
- Test: `tests/unit/test_logging.py` (also creates `tests/unit/__init__.py`, empty `tests/unit/conftest.py` placeholder is NOT needed — Task 5 adds the real one; this test is pure-stdlib)

**Interfaces:**
- Consumes: nothing new.
- Produces: `setup_logging(level: str = "INFO") -> None`, `request_id_var: contextvars.ContextVar[str | None]`, `JsonLogFormatter(logging.Formatter)`. Later tasks (errors.py, CI) rely on `request_id_var`.

Port model: `SUB:app/logging.py` (JsonLogFormatter fields, lazy stderr handler idea) simplified — no actor extraction in phase 1.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_logging.py`:
```python
import json
import logging

from app.core.logging import JsonLogFormatter, request_id_var


def test_json_formatter_emits_request_id_and_fields():
    token = request_id_var.set("req-123")
    try:
        record = logging.LogRecord(
            name="app.test", level=logging.INFO, pathname=__file__,
            lineno=1, msg="hello %s", args=("world",), exc_info=None,
        )
        payload = json.loads(JsonLogFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["request_id"] == "req-123"
    assert "timestamp" in payload


def test_json_formatter_without_request_id():
    record = logging.LogRecord(
        name="app.test", level=logging.WARNING, pathname=__file__,
        lineno=1, msg="plain", args=(), exc_info=None,
    )
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload["request_id"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.logging`

- [ ] **Step 3: Implement `app/core/logging.py`**

```python
"""JSON structured logging (ported pattern from dotmac_sub app/logging.py)."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key in ("method", "path", "status_code", "duration_ms", "tenant_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _StderrStreamHandler(logging.StreamHandler):
    """Resolve sys.stderr at emit time so pytest's stream teardown can't break us."""

    def __init__(self) -> None:
        super().__init__()

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, value) -> None:
        pass


def setup_logging(level: str = "INFO") -> None:
    handler = _StderrStreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/test_logging.py -v`
Expected: 2 PASS

- [ ] **Step 5: Wire into middleware and app**

In `app/core/middleware/observability.py`, where the request id is generated/assigned per request, set and reset the contextvar around the downstream call:

```python
from app.core.logging import request_id_var
# inside dispatch/__call__, once request_id is known:
token = request_id_var.set(request_id)
try:
    ...existing downstream call...
finally:
    request_id_var.reset(token)
```

In `app/main.py`, before creating the app:

```python
from app.core.logging import setup_logging

setup_logging()
```

- [ ] **Step 6: Verify and commit**

```bash
make lint type-check && poetry run pytest tests/unit -q
git add -A && git commit -m "feat: JSON structured logging with request-id contextvar"
```

---

### Task 4: Content-negotiated error envelope (`app/core/errors.py`)

**Files:**
- Create: `app/core/errors.py`
- Modify: `app/main.py` (replace the four inline `@app.exception_handler` functions with `register_error_handlers(app)`)
- Test: `tests/unit/test_errors.py`

**Interfaces:**
- Consumes: `app.core.exceptions.{DomainError,NotFoundError,BadRequestError,ConflictError}`, `app.core.logging.request_id_var`.
- Produces: `register_error_handlers(app: FastAPI) -> None`; JSON envelope shape `{"code": str, "message": str, "details": dict | None, "request_id": str | None}`. Phase 3 will add HTML negotiation to this same module — keep the envelope builder separate (`_envelope()`).

Port model: `SUB:app/errors.py`, JSON branch only.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_errors.py`:
```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import register_error_handlers
from app.core.exceptions import ConflictError, NotFoundError


def _make_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/missing")
    def missing():
        raise NotFoundError("Widget not found")

    @app.get("/conflict")
    def conflict():
        raise ConflictError("Duplicate slug")

    @app.get("/boom")
    def boom():
        raise RuntimeError("nope")

    return app


def test_not_found_envelope():
    client = TestClient(_make_app())
    resp = client.get("/missing")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "not_found"
    assert body["message"] == "Widget not found"
    assert "request_id" in body


def test_conflict_envelope():
    resp = TestClient(_make_app()).get("/conflict")
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"


def test_unhandled_exception_is_opaque():
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["code"] == "internal_error"
    assert "nope" not in resp.text


def test_validation_error_envelope():
    app = _make_app()

    @app.get("/typed/{n}")
    def typed(n: int):
        return {"n": n}

    resp = TestClient(app).get("/typed/abc")
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"
    assert isinstance(resp.json()["details"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.errors`

- [ ] **Step 3: Implement `app/core/errors.py`**

```python
"""Structured JSON error handlers (ported pattern from dotmac_sub app/errors.py).

Phase 3 (web UI) adds HTML content negotiation here; keep `_envelope` reusable.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    DomainError,
    NotFoundError,
)
from app.core.logging import request_id_var

logger = logging.getLogger(__name__)


def _envelope(
    code: str, message: str, details: Any = None
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "details": details,
        "request_id": request_id_var.get(),
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content=_envelope("not_found", str(exc)))

    @app.exception_handler(BadRequestError)
    async def _bad_request(_: Request, exc: BadRequestError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content=_envelope("bad_request", str(exc))
        )

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content=_envelope("conflict", str(exc)))

    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        logger.exception("Unhandled DomainError")
        return JSONResponse(
            status_code=500, content=_envelope("internal_error", "Internal error")
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        safe = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": str(e.get("msg", ""))}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_envelope("validation_error", "Validation failed", safe),
        )

    @app.exception_handler(Exception)
    async def _catch_all(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception")
        return JSONResponse(
            status_code=500, content=_envelope("internal_error", "Internal error")
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/test_errors.py -v`
Expected: 4 PASS

- [ ] **Step 5: Replace inline handlers in `app/main.py`**

Delete the four `@app.exception_handler(...)` functions and their imports of `JSONResponse`; add:

```python
from app.core.errors import register_error_handlers

register_error_handlers(app)
```

CHECK existing Postgres tests for assertions on `{"detail": ...}` error bodies (`grep -rn '"detail"' tests/`) and update them to the new envelope (`body["message"]`).

- [ ] **Step 6: Verify and commit**

```bash
make lint type-check && poetry run pytest tests/unit -q
git add -A && git commit -m "feat: structured error envelope via register_error_handlers"
```

---

### Task 5: SQLite unit-test harness + dialect-portable model types

**Files:**
- Modify: `app/core/models.py` (use `sa.Uuid` instead of `postgresql.UUID`), every model file using `PG_UUID`/`JSONB` (`app/models/{tenant,person,auth,rbac}.py`)
- Create: `tests/unit/conftest.py`
- Create: `docker-compose.test.yml`
- Test: `tests/unit/test_harness.py`

**Interfaces:**
- Consumes: `app.core.models.Base`.
- Produces: fixtures `unit_engine` (session-scoped in-memory SQLite), `db` (function-scoped Session with outer-transaction rollback), and factory fixtures `tenant_row(db) -> Tenant`. Later unit tests (Task 6) depend on `db` and `tenant_row`.

- [ ] **Step 1: Make model column types dialect-portable**

In `app/core/models.py`, replace `from sqlalchemy.dialects.postgresql import UUID as PG_UUID` and `PG_UUID(as_uuid=True)` with:

```python
from sqlalchemy import Uuid
# uuid_pk:
return mapped_column(Uuid(), primary_key=True, default=uuid4)
```

Repo-wide sweep: `grep -rn "PG_UUID\|postgresql.UUID\|JSONB" app/` — replace every `PG_UUID(as_uuid=True)` with `Uuid()`, and any `JSONB` column with `sa.JSON().with_variant(postgresql.JSONB(), "postgresql")`. `sa.Uuid` renders native `uuid` on Postgres (no migration needed) and `CHAR(32)` on SQLite.

Run: `poetry run python -c "import app.main; print('ok')"` — Expected: `ok`.

- [ ] **Step 2: Write `tests/unit/conftest.py`**

```python
"""Fast unit-test fixtures on in-memory SQLite.

RLS does not exist on SQLite — tenancy enforcement is covered by the Postgres
canaries in tests/test_cross_tenant_isolation.py. Unit tests exercise service
logic only and must scope queries explicitly where they care about tenancy.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.models import Base
# Import model modules so Base.metadata is fully populated.
from app.models import auth, person, rbac, tenant  # noqa: F401
from app.models.tenant import Tenant


@pytest.fixture(scope="session")
def unit_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(unit_engine) -> Generator[Session, None, None]:
    connection = unit_engine.connect()
    outer = connection.begin()
    factory = sessionmaker(bind=connection, autocommit=False, autoflush=False)
    session = factory()
    # Restart a savepoint whenever service code commits, so the outer
    # rollback still isolates the test.
    connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture()
def tenant_row(db: Session) -> Tenant:
    row = Tenant(slug="acme", name="Acme")
    db.add(row)
    db.flush()
    return row
```

NOTE: check `app/models/tenant.py` for `Tenant`'s actual required columns and adjust the factory to satisfy NOT NULLs (e.g. if it has `status` or `subdomain` fields, set them).

- [ ] **Step 3: Write the harness self-test**

`tests/unit/test_harness.py`:
```python
from sqlalchemy import select

from app.models.tenant import Tenant


def test_tenant_row_visible_in_session(db, tenant_row):
    found = db.scalar(select(Tenant).where(Tenant.id == tenant_row.id))
    assert found is not None
    assert found.slug == "acme"


def test_rollback_isolation(db):
    assert db.scalar(select(Tenant)) is None
```

Run: `poetry run pytest tests/unit/test_harness.py -v`
Expected: 2 PASS (order-independent — the second test proves the first test's row rolled back).

- [ ] **Step 4: Create `docker-compose.test.yml`**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: starter_test
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 2s
      timeout: 2s
      retries: 15
```

The initial Alembic migration creates the `app_user`/`platform_api`/`app_admin` roles itself when run as superuser, so no init SQL is needed. NOTE: `make test-integration` (Task 1) connects as `app_user`; the migration must have given it LOGIN with a password or trust auth applies inside the container network — verify with `make test-db-up && make test-integration`; if `app_user` has no password, set one in the Make target via `PGPASSWORD`-less trust (host `localhost` + docker trust default) or adjust the migration/README accordingly and record what you did in the commit message.

- [ ] **Step 5: Full-suite verify and commit**

```bash
make test-db-up && make test-integration && make test-db-down
poetry run pytest tests/unit -q && make lint type-check
git add -A && git commit -m "test: SQLite unit harness, portable Uuid columns, disposable test Postgres"
```

---

### Task 6: CRUD base, query helpers, UnitOfWork (`app/core/{crud,query,unit_of_work}.py`)

**Files:**
- Create: `app/core/crud.py`, `app/core/query.py`, `app/core/unit_of_work.py`
- Test: `tests/unit/test_crud.py`, `tests/unit/test_unit_of_work.py`

**Interfaces:**
- Consumes: `db`/`tenant_row` fixtures (Task 5), `app.core.exceptions.NotFoundError`.
- Produces:
  - `CRUDManager[TModel]` classmethods: `create(db, payload, *, commit=True)`, `get(db, entity_id)`, `update(db, entity_id, payload, *, commit=True)`, `delete(db, entity_id, *, commit=True)`, class attrs `model`, `not_found_detail`, `soft_delete_field`, `soft_delete_value`.
  - `apply_pagination(stmt, *, limit: int, offset: int)`, `apply_ordering(stmt, model, order_by: str | None, allowed: set[str])` in `app/core/query.py`.
  - `UnitOfWork` context manager + `get_uow` dependency + `ConcurrencyConflict` exception in `app/core/unit_of_work.py`.
  - Task 7's services use `CRUDManager` and the helpers.

Port sources: `SUB:app/services/crud.py` (139 lines), `SUB:app/services/common.py` (only the ordering/pagination helpers), `SUB:app/services/unit_of_work.py` (169 lines).

- [ ] **Step 1: Write failing CRUD tests**

`tests/unit/test_crud.py`:
```python
import pytest

from app.core.crud import CRUDManager
from app.core.exceptions import NotFoundError
from app.models.person import Person


class People(CRUDManager[Person]):
    model = Person
    not_found_detail = "Person not found"


def _payload(tenant_row, **over):
    base = {
        "tenant_id": tenant_row.id,
        "email": "a@example.com",
        "display_name": "Ada",
    }
    base.update(over)
    return base


def test_create_and_get(db, tenant_row):
    row = People.create(db, _payload(tenant_row), commit=False)
    assert People.get(db, str(row.id)).email == "a@example.com"


def test_update_partial(db, tenant_row):
    row = People.create(db, _payload(tenant_row), commit=False)
    updated = People.update(db, str(row.id), {"display_name": "Grace"}, commit=False)
    assert updated.display_name == "Grace"
    assert updated.email == "a@example.com"


def test_get_missing_raises_not_found(db):
    with pytest.raises(NotFoundError):
        People.get(db, "00000000-0000-0000-0000-000000000000")


def test_delete_hard(db, tenant_row):
    row = People.create(db, _payload(tenant_row), commit=False)
    People.delete(db, str(row.id), commit=False)
    with pytest.raises(NotFoundError):
        People.get(db, str(row.id))
```

NOTE: check `app/models/person.py` for `Person`'s actual NOT NULL columns and adjust `_payload` (e.g. field may be `full_name` not `display_name`). Use the real column names.

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/unit/test_crud.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.crud`

- [ ] **Step 3: Port `app/core/crud.py`**

Copy `SUB:app/services/crud.py` with these adaptations (everything else verbatim):
1. Drop `from app.services.response import ListResponseMixin` and the mixin from the class bases — `class CRUDManager(Generic[TModel])`.
2. Replace `from fastapi import HTTPException` + `raise HTTPException(status_code=404, detail=cls.not_found_detail)` (both sites in `_get_or_404`) with `from app.core.exceptions import NotFoundError` + `raise NotFoundError(cls.not_found_detail)` — services must not speak HTTP; the Task 4 handler maps it to 404.
3. `db.get(model, entity_id)` accepts str UUIDs on Postgres but SQLite CHAR(32) needs a real UUID — coerce at the top of `_get_or_404`:
```python
from uuid import UUID
try:
    entity_id = UUID(str(entity_id))
except ValueError:
    raise NotFoundError(cls.not_found_detail) from None
```
4. Module docstring: note the port source and that tenancy scoping is enforced by RLS, not by this class.

- [ ] **Step 4: Run CRUD tests**

Run: `poetry run pytest tests/unit/test_crud.py -v`
Expected: 4 PASS

- [ ] **Step 5: Port `app/core/query.py`**

From `SUB:app/services/common.py` take only `apply_pagination` and `apply_ordering` (copy their bodies verbatim, adjusting imports); skip `validate_enum` unless a Task 7 service needs it (YAGNI).

- [ ] **Step 6: Write failing UoW test, port, pass**

`tests/unit/test_unit_of_work.py`:
```python
import pytest
from sqlalchemy import select

from app.core.unit_of_work import UnitOfWork
from app.models.tenant import Tenant


def test_uow_commits_on_clean_exit(db):
    with UnitOfWork(db) as uow:
        uow.session.add(Tenant(slug="t1", name="T1"))
    assert db.scalar(select(Tenant).where(Tenant.slug == "t1")) is not None


def test_uow_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        with UnitOfWork(db):
            db.add(Tenant(slug="t2", name="T2"))
            raise RuntimeError("boom")
    assert db.scalar(select(Tenant).where(Tenant.slug == "t2")) is None
```
(Adjust `Tenant` kwargs to real NOT NULL columns, same as Task 5.)

Run to see FAIL, then copy `SUB:app/services/unit_of_work.py` → `app/core/unit_of_work.py`, adapting: imports to `app.core.db`, keep `UnitOfWork`, `get_uow`, `ConcurrencyConflict`; drop any sub-domain-specific helpers if present. Match the constructor the test uses (`UnitOfWork(session)`); if sub's signature differs (e.g. takes a session factory), adapt the port so `UnitOfWork(db).__enter__` exposes `.session` and commits/rolls back as tested — the test is the contract.

Run: `poetry run pytest tests/unit/test_unit_of_work.py -v`
Expected: 2 PASS

- [ ] **Step 7: Verify and commit**

```bash
make lint type-check && poetry run pytest tests/unit -q
git add -A && git commit -m "feat: port CRUDManager, query helpers, UnitOfWork from dotmac_sub"
```

---

### Task 7: Extract business logic from routers into per-domain services

**Files:**
- Create: `app/services/tenants.py`, `app/services/persons.py`, `app/services/rbac.py`, `app/services/auth_flows.py`
- Modify: `app/api/tenants.py`, `app/api/persons.py`, `app/api/rbac.py`, `app/api/auth.py` (routers become thin: parse request → call service → return schema)

**Interfaces:**
- Consumes: `CRUDManager`, `apply_pagination` (Task 6), existing models/schemas.
- Produces (exact signatures Task 8/9 rely on):
  - `app.services.tenants: list_tenants(db) -> list[Tenant]`, `create_tenant(db, payload) -> Tenant` (move every `select()` from `app/api/tenants.py` here)
  - `app.services.persons: Persons(CRUDManager[Person])` + `list_persons(db) -> list[Person]`
  - `app.services.rbac: assign_role(db, tenant, payload) -> PersonRole`, `list_roles(db) -> list[Role]`, `list_audit_events(db, ...) -> list[AuditEvent]` — move the four `select()` calls out of `app/api/rbac.py`
  - `app.services.auth_flows: login(db, tenant, payload) -> TokenResponse-shaped result` — move the credential/role `select()`s out of `app/api/auth.py`

- [ ] **Step 1: Inventory the violations (this list is the definition of done)**

```bash
grep -rn "select(\|db.execute\|db.query" app/api/
```
Every hit must move to `app/services/`. Current hits: `tenants.py:60`, `auth.py:86,123,129`, `persons.py:73`, `rbac.py:90,93,123`.

- [ ] **Step 2: Extract, one router at a time, running Postgres tests after each**

Mechanical recipe per router: create the service module; move the query + surrounding business logic into a function taking `(db, ...domain args)`; router keeps only dependency resolution, service call, and response shaping. Where the logic is plain CRUD, subclass `CRUDManager` instead of writing bespoke functions. Existing behavior must not change — the Postgres tests are the safety net:

```bash
make test-db-up
make test-integration   # after each router extraction
```
Expected after each: same pass count as baseline (record baseline before starting).

- [ ] **Step 3: Confirm zero direct queries remain in routers**

Run: `grep -rn "select(\|db.execute\|db.query" app/api/ | wc -l`
Expected: `0`

- [ ] **Step 4: Verify and commit**

```bash
make lint type-check && poetry run pytest tests/unit -q && make test-integration
git add -A && git commit -m "refactor: thin routers — extract business logic into services"
```

---

### Task 8: Feature packages + manifest registry

**Files:**
- Create: `app/core/features.py`
- Create: `app/features/__init__.py` (holds `FEATURE_MODULES` list)
- Create: `app/features/{tenants,auth,persons,rbac}/` packages — each gets `__init__.py`, `feature.py`, and takes over the domain's `models.py`, `schemas.py` (if separate), `service.py`, `router.py`
- Move: `app/api/tenants.py → app/features/tenants/router.py`; `app/services/tenants.py → app/features/tenants/service.py`; `app/models/tenant.py → app/features/tenants/models.py` — same pattern for `auth` (incl. `app/services/auth_flows.py → service.py`, `app/models/auth.py → models.py`), `persons`, `rbac`. `app/services/audit.py → app/core/audit.py` (cross-cutting write-side; the audit *read* endpoint stays in `features/rbac`).
- Modify: `app/main.py` (mount via registry), `app/core/config.py` (add `disabled_features`), `alembic/env.py` + `tests/*` (import path updates)
- Test: `tests/unit/test_feature_registry.py`

**Interfaces:**
- Consumes: everything from Tasks 2–7.
- Produces:
  - `app.core.features.FeatureManifest(name: str, routers: Sequence[APIRouter], core: bool = True, enabled_by_default: bool = True)`
  - `app.core.features.load_manifests(module_names: Sequence[str]) -> list[FeatureManifest]` — imports each module, reads its `feature` attribute
  - `app.core.features.mount_features(app: FastAPI, *, disabled: set[str]) -> None` — mounts enabled manifests; `core=True` failures raise, `core=False` failures log and continue
  - `app.features.FEATURE_MODULES: list[str]`
  - Each feature package exports `feature: FeatureManifest` from `feature.py`.

- [ ] **Step 1: Write the failing registry test**

`tests/unit/test_feature_registry.py`:
```python
from fastapi import APIRouter, FastAPI

from app.core.features import FeatureManifest, load_manifests, mount_features


def test_load_manifests_reads_feature_attribute():
    manifests = load_manifests(["app.features.persons"])
    assert manifests[0].name == "persons"
    assert manifests[0].routers


def test_mount_features_skips_disabled():
    r = APIRouter()

    @r.get("/x")
    def x():
        return {}

    app = FastAPI()
    manifest = FeatureManifest(name="demo", routers=[r], core=False)
    mount_features_from = [manifest]
    mount_features(app, manifests=mount_features_from, disabled={"demo"})
    assert all(getattr(route, "path", "") != "/x" for route in app.routes)


def test_mount_features_mounts_enabled():
    r = APIRouter()

    @r.get("/y")
    def y():
        return {}

    app = FastAPI()
    mount_features(
        app, manifests=[FeatureManifest(name="demo", routers=[r])], disabled=set()
    )
    assert any(getattr(route, "path", "") == "/y" for route in app.routes)
```

Run: `poetry run pytest tests/unit/test_feature_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.features`

- [ ] **Step 2: Implement `app/core/features.py`**

```python
"""Feature-manifest registry.

Each package under app/features/ exports `feature: FeatureManifest` from its
feature.py. Core features fail hard at startup; non-core features are fault-
isolated (a broken optional feature logs and is skipped). Loading uses
importlib so app.core never statically imports app.features (import-linter
enforces this).
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureManifest:
    name: str
    routers: Sequence[APIRouter] = field(default_factory=tuple)
    core: bool = True
    enabled_by_default: bool = True


def load_manifests(module_names: Sequence[str]) -> list[FeatureManifest]:
    manifests: list[FeatureManifest] = []
    for module_name in module_names:
        module = importlib.import_module(f"{module_name}.feature")
        manifest = module.feature
        if not isinstance(manifest, FeatureManifest):
            raise TypeError(f"{module_name}.feature.feature must be a FeatureManifest")
        manifests.append(manifest)
    return manifests


def mount_features(
    app: FastAPI,
    *,
    manifests: Sequence[FeatureManifest],
    disabled: set[str],
) -> None:
    for manifest in manifests:
        if manifest.name in disabled or not manifest.enabled_by_default:
            logger.info("Feature %s disabled — skipping", manifest.name)
            continue
        try:
            for router in manifest.routers:
                app.include_router(router)
        except Exception:
            if manifest.core:
                raise
            logger.exception("Optional feature %s failed to mount", manifest.name)
```

Note the test in Step 1 calls `load_manifests(["app.features.persons"])` — that import target exists only after Step 4; run the two `mount_features` tests first (`-k mount_features`), and the full file after Step 4.

- [ ] **Step 3: Add feature toggles to settings**

In `app/core/config.py` `Settings`, add:

```python
disabled_features: str = ""  # comma-separated feature names

@property
def disabled_feature_set(self) -> set[str]:
    return {f.strip() for f in self.disabled_features.split(",") if f.strip()}
```

- [ ] **Step 4: Create the four feature packages**

For each domain, `git mv` per the Files list above, then create `feature.py`. Example — `app/features/persons/feature.py`:

```python
from app.core.features import FeatureManifest
from app.features.persons.router import router

feature = FeatureManifest(name="persons", routers=[router])
```

Same shape for `tenants`, `auth`, `rbac` (all `core=True` in this phase). `app/features/__init__.py`:

```python
FEATURE_MODULES = [
    "app.features.tenants",
    "app.features.auth",
    "app.features.persons",
    "app.features.rbac",
]
```

Update all imports repo-wide (grep-driven, same technique as Task 2): `app.models.tenant → app.features.tenants.models`, `app.api.persons → app.features.persons.router`, `app.services.audit → app.core.audit`, etc. Update `alembic/env.py` model imports and `tests/unit/conftest.py`'s model imports. Delete now-empty `app/api/`, `app/models/`, `app/services/` directories.

- [ ] **Step 5: Rewrite `app/main.py` mounting**

Replace the four `app.include_router(...)` lines and their imports with:

```python
from app.core.features import load_manifests, mount_features
from app.features import FEATURE_MODULES

mount_features(
    app,
    manifests=load_manifests(FEATURE_MODULES),
    disabled=settings.disabled_feature_set,
)
```

- [ ] **Step 6: Verify everything**

```bash
poetry run pytest tests/unit -q          # registry + all unit tests PASS
poetry run python -c "import app.main; print(len(app.main.app.routes))"  # routes > 4
make test-db-up && make test-integration # Postgres canaries still green
make lint type-check
```

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: feature packages with manifest registry (tenants/auth/persons/rbac)"
```

---

### Task 9: Architecture governance — import-linter contracts + architecture tests

**Files:**
- Modify: `pyproject.toml` (import-linter contracts), `Makefile` (`check` gains `lint-imports`)
- Create: `tests/architecture/__init__.py`, `tests/architecture/test_thin_wrappers.py`, `tests/architecture/test_route_guards.py`, `tests/architecture/test_feature_manifests.py`

**Interfaces:**
- Consumes: Task 8 layout (`app/core`, `app/features/*`), `app.core.deps.require_*` naming convention.
- Produces: build-failing governance that phases 2–4 inherit.

- [ ] **Step 1: Add import-linter contracts to `pyproject.toml`**

```toml
[tool.importlinter]
root_package = "app"

[[tool.importlinter.contracts]]
name = "Features are independent of each other"
type = "independence"
modules = [
    "app.features.tenants",
    "app.features.auth",
    "app.features.persons",
    "app.features.rbac",
]

[[tool.importlinter.contracts]]
name = "Core must not import features"
type = "forbidden"
source_modules = ["app.core"]
forbidden_modules = ["app.features"]
```

Run: `poetry run lint-imports`
Expected: both contracts KEPT. If `auth`/`rbac` import `persons.models` (likely — credentials/roles reference Person), that is a real finding: resolve it by moving the `Person` model into `app/core/` ONLY if truly cross-cutting, or by having features reference `person_id` UUID columns without importing the other feature's model class (preferred — use `ForeignKey("persons.id")` string form, which needs no import). Fix, re-run, expected KEPT.

- [ ] **Step 2: Thin-wrapper architecture test**

`tests/architecture/test_thin_wrappers.py` — adapt `SUB:tests/architecture/test_thin_wrappers.py`:

```python
"""Routers must not issue direct DB queries (logic lives in service.py)."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DISALLOWED = [
    re.compile(r"\bdb\.query\("),
    re.compile(r"\bdb\.execute\("),
    re.compile(r"\bselect\("),
]


def _router_files() -> list[Path]:
    features = PROJECT_ROOT / "app" / "features"
    return sorted(
        p
        for p in features.rglob("*.py")
        if p.name in {"router.py", "web.py"}
    )


def test_routers_do_not_issue_direct_queries() -> None:
    violations: list[str] = []
    for path in _router_files():
        text = path.read_text(encoding="utf-8")
        for pattern in DISALLOWED:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{line} -> {pattern.pattern}"
                )
    assert not violations, "\n".join(violations)
```

Run: `poetry run pytest tests/architecture/test_thin_wrappers.py -v`
Expected: PASS (Task 7 already cleaned the routers).

- [ ] **Step 3: Route-guard coverage test**

`tests/architecture/test_route_guards.py`:

```python
"""Every mounted route must carry an auth/tenancy guard dependency."""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import app

# Routes that are intentionally unauthenticated.
ALLOWLIST = {
    ("GET", "/health"),
    ("POST", "/auth/login"),
}


def _guard_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            names.add(getattr(dep.call, "__name__", ""))
        stack.extend(dep.dependencies)
    return names


def test_every_route_has_a_guard() -> None:
    missing: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if (method, route.path) in ALLOWLIST:
                continue
            if not any(n.startswith("require_") for n in _guard_names(route)):
                missing.append(f"{method} {route.path}")
    assert not missing, "Unguarded routes:\n" + "\n".join(sorted(missing))
```

Run: `poetry run pytest tests/architecture/test_route_guards.py -v`
Expected: PASS, or a real finding — an unguarded route. If a route legitimately needs no guard, add it to `ALLOWLIST` with a comment; otherwise add the missing `Depends(require_*)`. Check `/auth` sub-routes: login must be in ALLOWLIST since the router-level `require_tenant` IS a guard — confirm whether router-level dependencies appear in `route.dependant` (they do); if login only has `require_tenant` that counts as guarded, so remove it from ALLOWLIST if the test passes without it.

- [ ] **Step 4: Manifest completeness test**

`tests/architecture/test_feature_manifests.py`:

```python
"""Every app/features/* package must export a valid manifest and be registered."""

from __future__ import annotations

from pathlib import Path

from app.core.features import FeatureManifest, load_manifests
from app.features import FEATURE_MODULES

FEATURES_DIR = Path(__file__).resolve().parents[2] / "app" / "features"


def test_every_feature_package_is_registered() -> None:
    on_disk = {
        p.name for p in FEATURES_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"
    }
    registered = {m.rsplit(".", 1)[-1] for m in FEATURE_MODULES}
    assert on_disk == registered


def test_manifests_load_and_are_named_after_package() -> None:
    for module_name, manifest in zip(
        FEATURE_MODULES, load_manifests(FEATURE_MODULES), strict=True
    ):
        assert isinstance(manifest, FeatureManifest)
        assert module_name.endswith(manifest.name)
```

Run: `poetry run pytest tests/architecture -v`
Expected: all PASS.

- [ ] **Step 5: Wire into `make check` and commit**

In `Makefile`, change `check` to: `check: lint lint-imports type-check security`.

```bash
make check && poetry run pytest tests/unit tests/architecture -q
git add -A && git commit -m "feat: architecture governance — import-linter contracts + architecture tests"
```

---

### Task 10: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Makefile targets, `docker-compose.test.yml` role bootstrap behavior, Dockerfile (Task 11 — the docker job is added there; this task ships the pure-Python jobs).

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

Modeled on `SUB:.github/workflows/ci.yml` (poetry cache keyed on lock hash, parallel jobs):

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  quality:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        target: [lint, lint-imports, type-check, security]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: snok/install-poetry@v1
      - uses: actions/cache@v4
        with:
          path: .venv
          key: venv-${{ runner.os }}-${{ hashFiles('poetry.lock') }}
      - run: poetry install
      - run: make ${{ matrix.target }}

  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: snok/install-poetry@v1
      - uses: actions/cache@v4
        with:
          path: .venv
          key: venv-${{ runner.os }}-${{ hashFiles('poetry.lock') }}
      - run: poetry install
      - run: poetry run pytest tests/unit tests/architecture -q --cov=app --cov-report=term

  integration:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: starter_test
        ports: ["5433:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres" --health-interval 2s
          --health-timeout 2s --health-retries 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: snok/install-poetry@v1
      - uses: actions/cache@v4
        with:
          path: .venv
          key: venv-${{ runner.os }}-${{ hashFiles('poetry.lock') }}
      - run: poetry install
      - name: Migrate (creates RLS roles as superuser)
        run: poetry run alembic upgrade head
        env:
          MIGRATION_DATABASE_URL: postgresql+psycopg://postgres:postgres@localhost:5433/starter_test
          DATABASE_URL: postgresql+psycopg://postgres:postgres@localhost:5433/starter_test
      - name: RLS canaries
        run: >-
          poetry run pytest tests -q --ignore=tests/unit --ignore=tests/architecture
        env:
          TEST_DATABASE_URL: postgresql+psycopg://app_user:app_user@localhost:5433/starter_test
          TEST_MIGRATION_DATABASE_URL: postgresql+psycopg://postgres:postgres@localhost:5433/starter_test
```

NOTE: mirror whatever `app_user` credential decision Task 5 Step 4 landed on in the `TEST_DATABASE_URL` here; alembic env var name — check `alembic/env.py` for which env var it reads (`MIGRATION_DATABASE_URL` vs settings) and match it.

- [ ] **Step 2: Validate and commit**

```bash
poetry run python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"
git add -A && git commit -m "ci: quality matrix, unit, and Postgres RLS integration jobs"
```
If the repo has a GitHub remote, push the branch and confirm the run is green before proceeding; if not (MT currently has no remote), note that in the commit and continue.

---

### Task 11: Docker image + compose split (immutable prod vs dev overlay)

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docker-compose.dev.yml`
- Modify: `Makefile` (docker targets), `.github/workflows/ci.yml` (docker-build job)

**Interfaces:**
- Consumes: Task 10 CI file.
- Produces: image entrypoint `uvicorn app.main:app --host 0.0.0.0 --port 8000`; compose contract `APP_IMAGE` env var (required in prod compose); Task 12's deploy script drives these files.

- [ ] **Step 1: Create `Dockerfile`** (pattern from `SUB:Dockerfile`, minus WeasyPrint/snmp system deps this app doesn't need)

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 POETRY_VIRTUALENVS_CREATE=false
WORKDIR /srv/app

RUN pip install --no-cache-dir poetry==1.8.3
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-interaction

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini VERSION ./

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`.dockerignore`: `.git`, `.venv`, `tests`, `docs`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `*.md`. (VERSION file is created in Task 12 — create it here as `0.4.0` if doing tasks in order, or reorder the COPY once Task 12 lands.)

- [ ] **Step 2: Prod compose — immutable image, no build key**

`docker-compose.yml`:
```yaml
# Production compose: pulls a baked image only. Migrations are applied as a
# separate pre-deploy step (scripts/deploy.sh) — never on container boot.
services:
  app:
    image: ${APP_IMAGE:?Set APP_IMAGE to a published image tag}
    env_file: .env
    ports:
      - "127.0.0.1:8000:8000"
    restart: unless-stopped
    mem_limit: 512m
    pids_limit: 256
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://localhost:8000/health')\""]
      interval: 10s
      timeout: 3s
      retries: 5
```

- [ ] **Step 3: Dev overlay with local build + Postgres**

`docker-compose.dev.yml`:
```yaml
services:
  app:
    build: .
    image: dotmac_starter_mt:dev
    volumes:
      - ./app:/srv/app/app
    environment:
      DATABASE_URL: postgresql+psycopg://postgres:postgres@postgres:5432/starter
      MIGRATION_DATABASE_URL: postgresql+psycopg://postgres:postgres@postgres:5432/starter
    depends_on:
      postgres:
        condition: service_healthy
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: starter
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 2s
      timeout: 2s
      retries: 15
```

Makefile additions:
```makefile
##@ Docker
docker-build: ## Build local dev image
	docker build -t dotmac_starter_mt:dev .
docker-dev: ## Run app+postgres locally (dev overlay)
	APP_IMAGE=dotmac_starter_mt:dev docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

- [ ] **Step 4: Verify the image boots and health-gates**

```bash
make docker-build
docker run -d --rm --name starter_smoke -p 8001:8000 -e DATABASE_URL=postgresql+psycopg://x:x@localhost/x dotmac_starter_mt:dev
sleep 3 && curl -sf http://localhost:8001/health
docker stop starter_smoke
```
Expected: `{"status":"ok"}` (health endpoint does not touch the DB by design).

- [ ] **Step 5: Add docker-build job to CI**

Append to `ci.yml`:
```yaml
  docker-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t starter:ci .
      - run: |
          docker run -d --name smoke -p 8000:8000 \
            -e DATABASE_URL=postgresql+psycopg://x:x@localhost/x starter:ci
          for i in $(seq 1 15); do
            curl -sf http://localhost:8000/health && exit 0; sleep 2;
          done
          docker logs smoke; exit 1
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: Dockerfile + immutable prod compose with dev overlay; CI docker health gate"
```

---

### Task 12: Versioning + hardened deploy script

**Files:**
- Create: `VERSION` (content: `0.4.0`), `scripts/bump_version.py`, `scripts/deploy.sh`, `CHANGELOG.md`
- Modify: `pyproject.toml` (version = "0.4.0"), `Makefile` (bump-version, deploy targets)

**Interfaces:**
- Consumes: compose contract from Task 11 (`APP_IMAGE` pin in `.env`).
- Produces: `make bump-version part=minor`, `make deploy TAG=<tag>`.

- [ ] **Step 1: VERSION + bump script**

Create `VERSION` containing `0.4.0` and set `version = "0.4.0"` in `pyproject.toml`. Port `SUB:scripts/bump_version.py` adapting: it must read/write this repo's `VERSION` + `pyproject.toml` only (drop sub's `package.json` handling). Start `CHANGELOG.md`:

```markdown
# Changelog

## 0.4.0 — 2026-07-17
- Phase 1 infrastructure foundation: app/core + feature registry, sub-derived
  CRUD/UoW/logging/errors, architecture governance, CI, Docker/deploy.
```

Test: `poetry run python scripts/bump_version.py --dry-run patch`
Expected: prints `0.4.0 -> 0.4.1` without writing (add `--dry-run` if sub's script lacks it).

- [ ] **Step 2: Port `scripts/deploy.sh`**

Adapt `SUB:scripts/deploy.sh` (158 lines) with this flow, adjusting service names/URLs to this repo:
1. `TAG` argument required → resolve `APP_IMAGE`.
2. Verify image exists (`docker manifest inspect`).
3. `pg_dump` backup using `MIGRATION_DATABASE_URL` from `.env` → `backups/<timestamp>.sql.gz`.
4. Pin `APP_IMAGE` in `.env` (sed in place, keep previous value in a variable).
5. `docker compose pull app`.
6. Run migrations as one-off container: `docker compose run --rm -e DATABASE_URL=$MIGRATION_DATABASE_URL app alembic upgrade head`.
7. `docker compose up -d app`.
8. Health gate: curl `http://127.0.0.1:8000/health` retry 15×2s.
9. On any failure after step 4: restore previous `APP_IMAGE`, `docker compose up -d app`, exit 1.

Test (no prod here — dry mechanics only): `bash -n scripts/deploy.sh` → syntax OK; `./scripts/deploy.sh` with no TAG → usage message, exit 1.

- [ ] **Step 3: Makefile targets + commit**

```makefile
##@ Release
bump-version: ## Bump semver: make bump-version part=patch|minor|major
	poetry run python scripts/bump_version.py $(part)
deploy: ## Deploy tag: make deploy TAG=sha-abc123
	./scripts/deploy.sh $(TAG)
```

```bash
make check && git add -A && git commit -m "feat: VERSION/bump script and hardened deploy.sh (backup, migrate, health-gate, rollback)"
```

---

### Task 13: Docs — CLAUDE.md, .env.example, ADR-0002, README refresh

**Files:**
- Create: `CLAUDE.md`, `.env.example`, `docs/adr/0002-starter-consolidation.md`, `docs/ARCHITECTURE.md`
- Modify: `README.md`

**Interfaces:** none consumed by code; this is the contract for humans and agents in phases 2–4.

- [ ] **Step 1: Write `CLAUDE.md`**

```markdown
# dotmac_starter_mt

The consolidated DotMac starter (spec: docs/superpowers/specs/2026-07-17-starter-consolidation-design.md).
Multi-tenant always; a single-tenant app = a deployment with one tenant.

## Layout
- `app/core/` — config, db, models base, security, deps, middleware, logging,
  errors, crud, unit_of_work, features registry, audit write-side. Core never
  imports `app/features` (import-linter enforced).
- `app/features/<name>/` — self-contained: models.py, schemas.py, service.py,
  router.py, feature.py (exports `feature: FeatureManifest`). Features never
  import each other; cross-feature references use FK strings / UUID columns.

## Hard rules (enforced by tests/architecture and import-linter)
- Routers (`router.py`, `web.py`) never issue direct DB queries — logic in service.py.
- Every route carries a `require_*` guard or is in the test ALLOWLIST with a comment.
- Every tenant-scoped model: `tenant_id` FK + composite unique; RLS policy in migration.
- Migrations run as `app_admin` (MIGRATION_DATABASE_URL), never on container boot.
- New feature: create package + feature.py, register in `app/features/__init__.py`
  (`FEATURE_MODULES`), add to the import-linter independence contract, write the
  cross-tenant isolation test FIRST.

## Commands
- `make help` — everything. `make check` before any commit.
- `make test-unit` (SQLite, fast) / `make test-db-up && make test-integration` (RLS canaries).

## Testing model
Unit tests: in-memory SQLite (no RLS — do not test tenancy there).
Tenancy correctness: Postgres canaries in tests/ (require real DB).
```

- [ ] **Step 2: Write `.env.example`** (sub style — every var commented, change-me placeholders)

```bash
# Environment: dev | staging | prod
ENVIRONMENT=dev
# Tenant request role (app_user) — RLS enforced.
DATABASE_URL=postgresql+psycopg://app_user:change-me@localhost:5432/starter
# Platform routes role (platform_api) — explicit grants, no RLS bypass. Required in prod.
PLATFORM_DATABASE_URL=
# Migrations/maintenance role (app_admin, BYPASSRLS). Used by alembic + deploy.sh only.
MIGRATION_DATABASE_URL=
# Root domain for subdomain→tenant resolution, e.g. app.example.com
PLATFORM_ROOT_DOMAIN=localhost
# Comma-separated allowed Host headers. Required in prod.
TRUSTED_HOSTS=
# Secrets — generate with: python -c "import secrets;print(secrets.token_urlsafe(48))"
JWT_SECRET=dev-insecure-change-me
SESSION_HASH_SECRET=dev-insecure-change-me
JWT_TTL_SECONDS=3600
CSRF_ENABLED=true
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=120
RATE_LIMIT_WINDOW_SECONDS=60
TRUST_INBOUND_REQUEST_ID=false
# Comma-separated feature names to disable (see app/features/__init__.py)
DISABLED_FEATURES=
# Deploy: image pin managed by scripts/deploy.sh
APP_IMAGE=
```

- [ ] **Step 3: ADR + ARCHITECTURE + README**

`docs/adr/0002-starter-consolidation.md` — short: context (two starter repos), decision (this repo is the one starter; sub = infra SoT; starter = feature source; feature-package registry), consequences (dotmac_starter frozen → archived after phase 2), link to the spec.

`docs/ARCHITECTURE.md` — the Layout + Hard rules sections from CLAUDE.md expanded with the request flow (middleware order from `app/main.py` docstring), the three-role DB model (from README), and the feature-mount sequence.

`README.md` — update: remove "What's NOT here yet" items now delivered (CI, Dockerfile/compose); add "Starting a new app from this template" (clone → rename → disable/delete features → set .env → `make test-db-up && make migrate && make dev`); state that this repo supersedes `dotmac_starter` per ADR-0002.

- [ ] **Step 4: Final verification and commit**

```bash
make check
poetry run pytest tests/unit tests/architecture -q
make test-db-up && make test-integration && make test-db-down
git add -A && git commit -m "docs: CLAUDE.md, .env.example, ADR-0002, architecture docs, README refresh"
```

---

## Completion criteria (phase gate)

- `make check` green (ruff, import-linter, mypy, bandit, format).
- `poetry run pytest tests/unit tests/architecture -q` green on SQLite with no DB.
- `make test-integration` green against the disposable Postgres (RLS canaries).
- `curl /health` returns ok from the built Docker image.
- CI file present with quality/unit/integration/docker jobs.
- Merge `phase1-infra` → `main` per superpowers:finishing-a-development-branch.

Phase 2 (auth hardening: MFA/TOTP, refresh rotation, lockout, API keys; settings-as-data; branding) gets its own plan once this lands.
