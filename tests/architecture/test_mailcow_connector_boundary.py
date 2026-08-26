"""The Mailcow plugin owns wire translation and no execution engine."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "dotmac-connector-mailcow"
SOURCE = PACKAGE / "src" / "dotmac_connector_mailcow"


def _violations(source: Path) -> tuple[str, ...]:
    forbidden_imports = {
        "alembic",
        "asyncpg",
        "boto3",
        "django",
        "dotmac_kernel",
        "paramiko",
        "psycopg",
        "requests",
        "sqlalchemy",
    }
    product_roots = {
        "app",
        "dotmac_erp",
        "dotmac_sub",
        "dotmac_workspace",
        "vendor_cp",
    }
    engine_stems = {"backoff", "checkpoint", "dead_letter", "retry"}
    violations: list[str] = []
    for file in sorted(source.rglob("*.py")):
        if file.stem in engine_stems:
            violations.append(file.stem)
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                violations.extend(sorted(roots & (forbidden_imports | product_roots)))
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
                if root in forbidden_imports | product_roots:
                    violations.append(root)
            elif isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
                if name in {"create_engine", "run", "sessionmaker"}:
                    violations.append(name)
    return tuple(sorted(set(violations)))


def test_distribution_is_one_stateless_provision_entry_point() -> None:
    document = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    poetry = document["tool"]["poetry"]
    assert poetry["name"] == "dotmac-connector-mailcow"
    assert poetry["version"] == "0.1.0a1"
    assert poetry["dependencies"] == {
        "python": ">=3.11,<3.14",
        "dotmac-integration": ">=0.1.0a6",
        "dotmac-managed-email-contracts": "0.1.0a1",
        "httpx": ">=0.27,<0.29",
    }
    assert poetry["plugins"] == {
        "dotmac_integration.connectors": {"mailcow": "dotmac_connector_mailcow:PLUGIN"}
    }
    assert not (SOURCE / "migrations").exists()


def test_connector_has_no_persistence_product_import_or_private_engine() -> None:
    assert _violations(SOURCE) == ()


def test_boundary_guard_bites(tmp_path: Path) -> None:
    planted = tmp_path / "retry.py"
    planted.write_text(
        "import requests\n"
        "from dotmac_erp import models\n"
        "from dotmac_kernel import db\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == (
        "dotmac_erp",
        "dotmac_kernel",
        "requests",
        "retry",
    )


def test_provider_specific_names_stay_inside_the_connector() -> None:
    outside = [
        path
        for path in ROOT.glob("packages/*/src/**/*.py")
        if "dotmac-connector-mailcow" not in path.as_posix()
        and "mailcow" in path.read_text(encoding="utf-8").casefold()
        and "managed-service-connector-sources" not in path.as_posix()
    ]
    # Historical product inventory may name a provider, but shared engines and
    # owner catalogues do not grow provider branches for this connector.
    assert not [path for path in outside if path.name in {"provisioning.py", "spi.py"}]
