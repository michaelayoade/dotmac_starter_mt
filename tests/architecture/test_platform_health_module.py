"""Static ownership and plane canaries for ``dotmac-platform-health``."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, PLATFORM_HEALTH_MIGRATION_OWNER
from dotmac_platform_health import models
from dotmac_platform_health.manifest import module

ROOT = Path(inspect.getfile(models)).parent
MIGRATION = ROOT / "migrations/versions/ph_0001_platform_health.py"


def test_platform_health_is_one_declared_platform_lineage() -> None:
    assert PLATFORM_HEALTH_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == PLATFORM_HEALTH_MIGRATION_OWNER.owner == "platform_health"
    assert module.short_code == "health"
    assert module.migration_prefix == "ph"
    assert module.migration_branch == "platform_health"
    assert module.db_schema == "mod_health"
    assert module.tables == ()
    assert set(module.platform_tables) == set(models.PLATFORM_TABLES)
    for model in models.PLATFORM_MODELS:
        assert model.__table__.schema == "mod_health"
        assert "tenant_id" not in model.__table__.columns


def test_platform_health_has_no_transport_deployment_or_transaction_owner() -> None:
    forbidden = {"app", "dotmac_deployment_control", "dotmac_integration", "httpx", "requests"}
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])
        assert not roots & forbidden, (path, roots & forbidden)
    source = (ROOT / "service.py").read_text(encoding="utf-8")
    for call in (".commit(", ".rollback(", "SessionLocal(", "sessionmaker("):
        assert call not in source


def test_platform_health_migration_states_both_halves_of_platform_isolation() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "GRANT USAGE ON SCHEMA mod_health TO platform_api, app_admin" in source
    for table in module.platform_tables:
        assert f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_health.{table} TO platform_api" in source
        assert f"REVOKE ALL ON mod_health.{table} FROM app_user" in source
    assert "ROW LEVEL SECURITY" not in source

