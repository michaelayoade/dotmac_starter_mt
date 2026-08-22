"""Static ownership, tenancy and provider-boundary canaries for ADR-0040 ISP modules."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_ai_operations import models as ai_models
from dotmac_ai_operations.manifest import module as ai_module
from dotmac_compliance_reporting import models as compliance_models
from dotmac_compliance_reporting.manifest import module as compliance_module
from dotmac_kernel.namespaces import (
    AI_OPERATIONS_MIGRATION_OWNER,
    COMPLIANCE_REPORTING_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    REMOTE_ACCESS_MIGRATION_OWNER,
)
from dotmac_remote_access import models as remote_models
from dotmac_remote_access.manifest import module as remote_module

CASES = (
    (
        remote_models,
        remote_module,
        REMOTE_ACCESS_MIGRATION_OWNER,
        "remote_access",
        "remoteaccess",
        "ra",
        "remote_access",
        "mod_remoteaccess",
        "ra_0001_remote_access.py",
    ),
    (
        compliance_models,
        compliance_module,
        COMPLIANCE_REPORTING_MIGRATION_OWNER,
        "compliance_reporting",
        "compliance",
        "cr",
        "compliance_reporting",
        "mod_compliance",
        "cr_0001_compliance_reporting.py",
    ),
    (
        ai_models,
        ai_module,
        AI_OPERATIONS_MIGRATION_OWNER,
        "ai_operations",
        "aiops",
        "ao",
        "ai_operations",
        "mod_aiops",
        "ao_0001_ai_operations.py",
    ),
)


def test_each_module_owns_one_tenant_lineage_with_composite_identity() -> None:
    for (
        models,
        module,
        owner,
        code,
        short_code,
        prefix,
        branch,
        schema,
        migration_name,
    ) in CASES:
        assert owner in MIGRATION_OWNER_LEDGER
        assert module.code == owner.owner == code
        assert module.short_code == short_code
        assert module.migration_prefix == prefix
        assert module.migration_branch == branch
        assert module.db_schema == schema
        assert module.platform_tables == ()
        assert set(module.tables) == set(models.TENANT_TABLES)
        for model in models.TENANT_MODELS:
            assert not model.__table__.columns.tenant_id.nullable
            for constraint in model.__table__.foreign_key_constraints:
                if schema in {
                    element.column.table.schema for element in constraint.elements
                }:
                    assert "tenant_id" in constraint.columns
        source = (
            Path(inspect.getfile(models)).parent
            / "migrations/versions"
            / migration_name
        ).read_text(encoding="utf-8")
        assert "ENABLE ROW LEVEL SECURITY" in source
        assert "FORCE ROW LEVEL SECURITY" in source
        assert "UNIQUE (tenant_id, id)" in source


def test_modules_import_no_product_sibling_provider_or_transport() -> None:
    forbidden = {
        "app",
        "dotmac_sub",
        "dotmac_remote_access",
        "dotmac_compliance_reporting",
        "dotmac_ai_operations",
        "dotmac_integration",
        "httpx",
        "requests",
        "openai",
        "anthropic",
        "boto3",
    }
    roots_by_module = {
        "dotmac_remote_access": remote_models,
        "dotmac_compliance_reporting": compliance_models,
        "dotmac_ai_operations": ai_models,
    }
    for own_root, models in roots_by_module.items():
        root = Path(inspect.getfile(models)).parent
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".", 1)[0])
            assert not imports & (forbidden - {own_root}), (path, imports & forbidden)
        service = (root / "service.py").read_text(encoding="utf-8")
        for call in (".commit(", ".rollback(", "SessionLocal(", "sessionmaker("):
            assert call not in service


def test_remote_and_ai_tables_structurally_hold_no_secret_or_endpoint() -> None:
    forbidden = {
        "credential",
        "password",
        "secret",
        "api_key",
        "endpoint",
        "access_token",
        "private_key",
    }
    columns = {
        column.name
        for models in (remote_models, ai_models)
        for model in models.TENANT_MODELS
        for column in model.__table__.columns
    }
    assert not columns & forbidden
