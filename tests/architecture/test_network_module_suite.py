"""One structural conformance kit for all nine network-suite modules."""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import tomllib
from pathlib import Path
from typing import get_type_hints

from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, module_schema

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_LEDGER = ROOT / "docs/inventories/network-module-contracts.toml"

MODULES = {
    "dotmac-ipam": ("dotmac_ipam", "ipam", "ipam", "ip"),
    "dotmac-network-inventory": (
        "dotmac_network_inventory",
        "network_inventory",
        "netinv",
        "ni",
    ),
    "dotmac-network-observability": (
        "dotmac_network_observability",
        "network_observability",
        "netobs",
        "no",
    ),
    "dotmac-network-topology": (
        "dotmac_network_topology",
        "network_topology",
        "nettop",
        "nt",
    ),
    "dotmac-network-assurance": (
        "dotmac_network_assurance",
        "network_assurance",
        "netassure",
        "na",
    ),
    "dotmac-network-control": (
        "dotmac_network_control",
        "network_control",
        "netctrl",
        "nc",
    ),
    "dotmac-fiber-plant": ("dotmac_fiber_plant", "fiber_plant", "fiber", "fp"),
    "dotmac-network-access": (
        "dotmac_network_access",
        "network_access",
        "netaccess",
        "nac",
    ),
    "dotmac-pon-access": ("dotmac_pon_access", "pon_access", "pon", "pn"),
}


def _contracts() -> dict[str, dict[str, object]]:
    with CONTRACT_LEDGER.open("rb") as stream:
        ledger = tomllib.load(stream)
    return {row["name"]: row for row in ledger["modules"]}


def test_every_public_contract_member_is_immutable_and_exported() -> None:
    contracts = _contracts()
    assert set(contracts) == set(MODULES)
    for distribution, (root, _, _, _) in MODULES.items():
        module = importlib.import_module(f"{root}.contracts")
        row = contracts[distribution]
        for field in ("commands", "queries", "snapshots", "events"):
            for name in row[field]:
                value = getattr(module, name)
                assert name in module.__all__
                assert dataclasses.is_dataclass(value)
                assert value.__dataclass_params__.frozen


def test_every_module_matches_its_permanent_namespace_and_tenant_plane() -> None:
    owners = {owner.owner: owner for owner in MIGRATION_OWNER_LEDGER}
    for _, (root, owner_name, short_code, prefix) in MODULES.items():
        manifest = importlib.import_module(f"{root}.manifest").module
        models = importlib.import_module(f"{root}.models")
        owner = owners[owner_name]
        assert manifest.code == owner.owner == owner_name
        assert manifest.short_code == short_code
        assert manifest.migration_prefix == owner.prefix == prefix
        assert manifest.migration_branch == owner.branch_label == owner_name
        assert manifest.db_schema == module_schema(short_code)
        assert manifest.platform_tables == ()
        assert set(manifest.tables) == set(models.TENANT_TABLES)
        for model in models.ALL_MODELS:
            assert model.__table__.schema == manifest.db_schema
            tenant_id = model.__table__.columns.get("tenant_id")
            assert tenant_id is not None and not tenant_id.nullable


def test_suite_has_no_product_foreign_keys_or_sibling_provider_imports() -> None:
    forbidden_columns = {
        "subscriber_id",
        "subscription_id",
        "service_id",
        "customer_id",
        "ticket_id",
        "work_order_id",
        "connector_id",
        "provider_id",
    }
    forbidden_roots = {
        *[root for root, _, _, _ in MODULES.values()],
        "app",
        "httpx",
        "requests",
        "boto3",
        "routeros_api",
        "pysnmp",
    }
    for _, (root, _, _, _) in MODULES.items():
        models = importlib.import_module(f"{root}.models")
        for model in models.ALL_MODELS:
            assert not (set(model.__table__.columns.keys()) & forbidden_columns)
            for constraint in model.__table__.foreign_key_constraints:
                local_tables = {item.column.table.name for item in constraint.elements}
                if local_tables & set(models.TENANT_TABLES):
                    assert {column.name for column in constraint.columns} >= {
                        "tenant_id"
                    }

        package_root = Path(inspect.getfile(models)).parent
        for path in package_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imported: str | None = None
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported = node.module.split(".", 1)[0]
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        candidate = alias.name.split(".", 1)[0]
                        if candidate in forbidden_roots and candidate != root:
                            raise AssertionError(f"{path} imports {candidate}")
                if imported in forbidden_roots and imported != root:
                    raise AssertionError(f"{path} imports {imported}")


def test_every_lineage_exists_and_declares_forced_rls() -> None:
    for _, (root, owner_name, short_code, prefix) in MODULES.items():
        package_root = ROOT / "packages" / root.replace("_", "-")
        if not package_root.exists():
            package_root = next(
                path
                for path in (ROOT / "packages").iterdir()
                if (path / "src" / root).exists()
            )
        versions = package_root / "src" / root / "migrations" / "versions"
        migrations = tuple(versions.glob(f"{prefix}_*.py"))
        assert len(migrations) == 1, f"{root} needs one lineage root"
        text = migrations[0].read_text(encoding="utf-8")
        assert f'branch_labels = ("{owner_name}",)' in text
        schema = module_schema(short_code)
        assert f"ALTER TABLE {schema}." in text
        assert "ENABLE ROW LEVEL SECURITY" in text
        assert "FORCE ROW LEVEL SECURITY" in text


def test_services_are_flush_only_and_return_no_orm_annotations() -> None:
    for _, (root, _, _, _) in MODULES.items():
        service = importlib.import_module(f"{root}.service")
        source = inspect.getsource(service)
        assert ".commit(" not in source
        assert ".rollback(" not in source
        assert "SessionLocal(" not in source
        assert "sessionmaker(" not in source
        exported = set(service.__all__)
        for name in exported:
            value = getattr(service, name)
            if not inspect.isfunction(value):
                continue
            annotation = inspect.signature(value).return_annotation
            assert "models." not in str(annotation)


def test_stock_to_asset_to_network_handoff_is_typed_and_opaque() -> None:
    from dotmac_assets import AssetSnapshot, create_asset_snapshot
    from dotmac_inventory import StockIssueEvidence, issue_stock_evidence

    assert dataclasses.is_dataclass(StockIssueEvidence)
    assert StockIssueEvidence.__dataclass_params__.frozen
    assert dataclasses.is_dataclass(AssetSnapshot)
    assert AssetSnapshot.__dataclass_params__.frozen
    assert get_type_hints(issue_stock_evidence)["return"] is StockIssueEvidence
    assert get_type_hints(create_asset_snapshot)["return"] is AssetSnapshot

    network_inventory = importlib.import_module("dotmac_network_inventory.models")
    node_columns = set(network_inventory.Node.__table__.columns.keys())
    assert "asset_ref" in node_columns
    assert "asset_id" not in node_columns
