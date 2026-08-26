"""The Keycloak connector stays a stateless, realm-scoped SPI plugin."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "dotmac-connector-keycloak-admin"
SOURCE = PACKAGE / "src" / "dotmac_connector_keycloak_admin"


def _source_violations(source: Path) -> tuple[str, ...]:
    forbidden_imports = {
        "alembic",
        "asyncpg",
        "boto3",
        "django",
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
                if name in {"sessionmaker", "create_engine"}:
                    violations.append(name)
    return tuple(sorted(set(violations)))


def test_package_is_one_stateless_provision_entry_point() -> None:
    pyproject = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    poetry = pyproject["tool"]["poetry"]
    assert poetry["name"] == "dotmac-connector-keycloak-admin"
    assert poetry["version"] == "0.1.0a1"
    assert poetry["dependencies"] == {
        "python": ">=3.11,<3.14",
        "dotmac-integration": ">=0.1.0a6",
        "dotmac-managed-identity-contracts": "0.1.0a1",
        "httpx": ">=0.27,<1.0",
    }
    assert poetry["plugins"] == {
        "dotmac_integration.connectors": {
            "keycloak_admin": "dotmac_connector_keycloak_admin:PLUGIN"
        }
    }
    assert not (SOURCE / "migrations").exists()


def test_connector_has_no_persistence_product_import_or_private_engine() -> None:
    assert _source_violations(SOURCE) == ()


def test_boundary_guard_bites(tmp_path: Path) -> None:
    planted = tmp_path / "retry.py"
    planted.write_text(
        "import requests\nfrom dotmac_erp import models\n",
        encoding="utf-8",
    )
    assert _source_violations(tmp_path) == (
        "dotmac_erp",
        "requests",
        "retry",
    )


def test_no_master_or_generated_secret_endpoint_is_constructed() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    )
    assert "/realms/master" not in source
    assert "/client-secret" not in source
    assert "generateNewSecret" not in source


def test_bandit_exemptions_are_schema_typed_public_observations() -> None:
    """The two secret-shaped field names never excuse credential material."""

    import sys

    sys.path.insert(0, str(ROOT / "packages" / "dotmac-kernel" / "src"))
    sys.path.insert(
        0, str(ROOT / "packages" / "dotmac-managed-identity-contracts" / "src")
    )
    import dotmac_managed_identity_contracts as identity

    operation = identity.OIDC_CLIENT_LIFECYCLE.require_operation("apply")
    output = next(
        schema
        for schema in identity.CAPABILITY_SCHEMAS
        if schema.schema_ref == operation.output_schema_ref
    ).to_mapping()
    properties = output["properties"]
    assert isinstance(properties, dict)
    assert properties["client_secret_configured"] == {
        "type": "boolean",
        "x-dotmac-data-classification": "public_non_secret",
    }
    algorithm = properties["id_token_signing_algorithm"]
    assert isinstance(algorithm, dict)
    assert algorithm["const"] == "RS256"
    assert algorithm["x-dotmac-data-classification"] == "public_non_secret"

    source = (SOURCE / "plugin.py").read_text(encoding="utf-8")
    assert source.count("# nosec B105 -- typed public evidence") == 2


def test_real_transport_policy_is_structural_and_bounded() -> None:
    source = (SOURCE / "transport.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    clients = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Client"
    ]
    assert len(clients) == 1
    keywords = {keyword.arg: keyword.value for keyword in clients[0].keywords}
    assert isinstance(keywords["follow_redirects"], ast.Constant)
    assert keywords["follow_redirects"].value is False
    assert isinstance(keywords["trust_env"], ast.Constant)
    assert keywords["trust_env"].value is False
    assignments = {
        target.id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        for target in (node.target,)
    }
    response_limit = assignments["_MAX_RESPONSE_BYTES"]
    assert isinstance(response_limit, ast.Constant)
    assert response_limit.value == 1_048_576
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Timeout"
        for node in ast.walk(tree)
    )


def test_public_surface_is_curated() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "packages" / "dotmac-kernel" / "src"))
    sys.path.insert(0, str(ROOT / "packages" / "dotmac-integration" / "src"))
    sys.path.insert(
        0, str(ROOT / "packages" / "dotmac-managed-identity-contracts" / "src")
    )
    sys.path.insert(0, str(PACKAGE / "src"))
    import dotmac_connector_keycloak_admin as connector

    assert set(connector.__all__) == {
        "MANIFEST",
        "PLUGIN",
        "HttpxKeycloakTransport",
        "KeycloakAdminConnector",
        "KeycloakAdminRequest",
        "KeycloakAdminResponse",
        "KeycloakAdminTransport",
        "KeycloakTransportError",
        "__version__",
    }
    with pytest.raises(AttributeError):
        connector.PLUGIN.modes.add("poll")  # type: ignore[attr-defined]
