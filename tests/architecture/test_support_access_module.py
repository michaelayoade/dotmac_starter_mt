"""Static ownership and least-privilege canaries for ``dotmac-support-access``."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, SUPPORT_ACCESS_MIGRATION_OWNER
from dotmac_support_access import models
from dotmac_support_access.manifest import module

ROOT = Path(inspect.getfile(models)).parent
MIGRATION = ROOT / "migrations/versions/sa_0001_support_access.py"


def test_support_access_is_one_declared_platform_lineage() -> None:
    assert SUPPORT_ACCESS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == SUPPORT_ACCESS_MIGRATION_OWNER.owner == "support_access"
    assert module.short_code == "supportaccess"
    assert module.migration_prefix == "sa"
    assert module.migration_branch == "support_access"
    assert module.db_schema == "mod_supportaccess"
    assert module.tables == ()
    assert set(module.platform_tables) == set(models.PLATFORM_TABLES)
    for model in models.PLATFORM_MODELS:
        assert "tenant_id" not in model.__table__.columns


def test_support_access_stores_no_credential_or_enforcement_secret() -> None:
    forbidden_names = {"token", "credential", "password", "secret", "session_cookie", "private_key"}
    columns = {column.name for model in models.PLATFORM_MODELS for column in model.__table__.columns}
    assert not columns & forbidden_names
    forbidden_imports = {"app", "dotmac_approvals", "dotmac_integration", "httpx", "requests"}
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])
        assert not roots & forbidden_imports, (path, roots & forbidden_imports)
    source = (ROOT / "service.py").read_text(encoding="utf-8")
    for call in (".commit(", ".rollback(", "SessionLocal(", "sessionmaker("):
        assert call not in source


def test_support_access_migration_states_both_halves_of_platform_isolation() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "GRANT USAGE ON SCHEMA mod_supportaccess TO platform_api, app_admin" in source
    for table in module.platform_tables:
        assert f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_supportaccess.{table} TO platform_api" in source
        assert f"REVOKE ALL ON mod_supportaccess.{table} FROM app_user" in source
    assert "ROW LEVEL SECURITY" not in source

