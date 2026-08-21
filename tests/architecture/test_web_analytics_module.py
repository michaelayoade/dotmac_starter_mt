"""Architecture canaries for the reusable web-analytics module.

Each source detector is factored as a pure function and driven against a planted
violation. That is ADR-0018's sensitivity proof: a green scan is evidence only
if the same scanner demonstrably fails on the forbidden shape.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, module_schema

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/dotmac-web-analytics"
SOURCE = PACKAGE / "src/dotmac_web_analytics"
INVENTORY = ROOT / "docs/inventories/web-analytics-sources.md"
ADR = ROOT / "docs/adr/0055-web-analytics-owns-first-party-observations.md"

FORBIDDEN_PERSISTED_NAMES = frozenset(
    {
        "name",
        "email",
        "email_address",
        "phone",
        "phone_number",
        "subscriber_id",
        "customer_id",
        "lead_id",
        "raw_ip",
        "ip_address",
        "user_agent",
        "fingerprint",
        "fingerprint_hash",
        "query_string",
        "request_body",
        "form_value",
        "metadata",
        "revenue",
        "revenue_amount",
    }
)
_HOST_LITERAL = re.compile(r"https?://(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/:]|$)", re.I)


def persisted_name_violations(source: str) -> set[str]:
    """Find ORM mapped-column assignments using forbidden privacy names."""
    tree = ast.parse(source)
    violations: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        function = value.func
        name = (
            function.id
            if isinstance(function, ast.Name)
            else getattr(function, "attr", "")
        )
        if name == "mapped_column" and node.target.id in FORBIDDEN_PERSISTED_NAMES:
            violations.add(node.target.id)
    return violations


def website_literal_violations(source: str) -> set[str]:
    """Find concrete website URLs in executable package source.

    Scheme validators such as ``startswith(("https://", "http://"))`` contain
    no host and are intentionally clean. A concrete host is adopter
    configuration and has no legitimate package-source use.
    """
    tree = ast.parse(source)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _HOST_LITERAL.search(node.value)
    }


def test_source_ruling_precedes_the_package() -> None:
    dossier = tomllib.loads((PACKAGE / "EXTRACTION.toml").read_text())
    assert dossier["source_mode"] == "greenfield-after-inventory"
    assert dossier["status"] == "audit-complete"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"] == ["dotmac_backoffice", "dotmac_sub"]
    assert "No audited implementation" in INVENTORY.read_text()
    assert "greenfield-after-inventory" in ADR.read_text()


def test_the_package_contains_no_adopter_website_literal() -> None:
    violations = {
        (path.relative_to(SOURCE), literal)
        for path in SOURCE.rglob("*.py")
        for literal in website_literal_violations(path.read_text())
    }
    assert not violations


def test_the_website_literal_detector_catches_a_planted_site() -> None:
    assert website_literal_violations('ORIGIN = "https://website.example/collect"\n')


def test_models_persist_no_direct_identity_raw_network_or_free_metadata() -> None:
    models = SOURCE / "models.py"
    assert models.is_file(), "the tenant-only persistence model has not landed"
    assert not persisted_name_violations(models.read_text())


def test_the_pii_detector_catches_a_planted_column() -> None:
    planted = """
class Event:
    email: Mapped[str] = mapped_column(String(255))
    raw_ip: Mapped[str] = mapped_column(String(64))
"""
    assert persisted_name_violations(planted) == {"email", "raw_ip"}


def test_manifest_is_tenant_only_and_owns_exactly_its_models() -> None:
    from dotmac_web_analytics.manifest import module
    from dotmac_web_analytics.models import TENANT_TABLES

    assert module.short_code == "webanalytics"
    assert module.migration_prefix == "wa"
    assert module.migration_branch == "web_analytics"
    assert module.db_schema == module_schema("webanalytics")
    assert module.platform_tables == ()
    assert set(module.tables) == set(TENANT_TABLES)


def test_namespace_allocation_is_unique_and_immutable() -> None:
    matching = [
        owner for owner in MIGRATION_OWNER_LEDGER if owner.owner == "web_analytics"
    ]
    assert len(matching) == 1
    owner = matching[0]
    assert owner.prefix == "wa"
    assert owner.branch_label == "web_analytics"
    assert owner.db_schema == "mod_webanalytics"


def test_every_model_is_tenant_scoped_and_composite_unique() -> None:
    from dotmac_web_analytics import models

    for model in models.TENANT_MODELS:
        tenant = model.__table__.columns.get("tenant_id")
        assert tenant is not None and not tenant.nullable, model.__name__
        uniques = [
            {column.name for column in constraint.columns}
            for constraint in model.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        ]
        assert any(
            "tenant_id" in unique for unique in uniques
        ), f"{model.__name__} has no tenant-scoped composite uniqueness"


def test_raw_observations_do_not_carry_aggregate_counters() -> None:
    from dotmac_web_analytics.models import EventObservation

    forbidden = {"views", "events", "visitors", "sessions", "count", "total"}
    assert forbidden.isdisjoint(EventObservation.__table__.columns.keys())


def test_the_migration_forces_rls_and_makes_observations_append_only() -> None:
    from dotmac_web_analytics.models import APPEND_ONLY_TABLES, TENANT_TABLES

    migration = next((SOURCE / "migrations/versions").glob("wa_0001_*.py"))
    sql = migration.read_text()
    for table in TENANT_TABLES:
        qualified = f"mod_webanalytics.{table}"
        assert f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY" in sql
        assert f"{table}_tenant_isolation" in sql
    for table in APPEND_ONLY_TABLES:
        assert f"REVOKE UPDATE, DELETE ON mod_webanalytics.{table} FROM app_user" in sql
        assert f"{table}_append_only" in sql


def test_public_package_imports_neither_assembly_nor_sibling_module() -> None:
    forbidden_roots = {
        "app",
        "dotmac_approvals",
        "dotmac_files",
        "dotmac_imports",
        "dotmac_integration",
        "dotmac_numbering",
        "dotmac_ticketing",
    }
    violations: list[str] = []
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {(node.module or "").split(".", 1)[0]}
            else:
                continue
            if roots & forbidden_roots:
                violations.append(f"{path.relative_to(SOURCE)}:{node.lineno}")
    assert not violations


def test_wheel_declares_migrations_and_typed_marker() -> None:
    pyproject = tomllib.loads((PACKAGE / "pyproject.toml").read_text())
    included = {entry["path"] for entry in pyproject["tool"]["poetry"]["include"]}
    assert "src/dotmac_web_analytics/py.typed" in included
    assert "src/dotmac_web_analytics/migrations/**/*" in included
    assert (SOURCE / "py.typed").is_file()


def test_clean_wheel_installation_is_on_the_governed_module_release_lane() -> None:
    allowlist = json.loads((ROOT / ".github/release-modules.json").read_text())
    assert "dotmac-web-analytics" in allowlist["modules"]
    workflow = (ROOT / ".github/workflows/release-module.yml").read_text()
    assert "- dotmac-web-analytics" in workflow
    assert "verify-wheel" in workflow
    assert "verify-registry" in workflow
    assert workflow.index("verify-wheel") < workflow.index("Publish to the Forgejo")


def test_no_provider_or_official_attribution_vocabulary_enters_the_engine() -> None:
    source = "\n".join(path.read_text().lower() for path in SOURCE.rglob("*.py"))
    forbidden = (
        "google_analytics",
        "ga4",
        "facebook_pixel",
        "official_attribution",
        "attributed_customer",
        "authoritative_revenue",
    )
    assert not [term for term in forbidden if term in source]
