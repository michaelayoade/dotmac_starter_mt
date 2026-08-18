"""Structural canaries for the product-neutral positioning module."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/dotmac-positioning"
SOURCE = PACKAGE / "src/dotmac_positioning"
MIGRATION = SOURCE / "migrations/versions/po_0001_positioning.py"
MAKEFILE = ROOT / "Makefile"


def _python_sources() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def _public_service_signatures(source: str) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(source)
    exported: set[str] = set()
    functions: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            functions[node.name] = node
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            )
            and isinstance(node.value, ast.List)
        ):
            exported = {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    return {name: functions[name] for name in exported}


def _defaulted_keyword_only_arguments(
    source: str,
    required: dict[str, set[str]],
) -> set[str]:
    functions = _public_service_signatures(source)
    violations: set[str] = set()
    for function_name, argument_names in required.items():
        function = functions[function_name]
        defaults = dict(
            zip(function.args.kwonlyargs, function.args.kw_defaults, strict=True)
        )
        for argument, default in defaults.items():
            if argument.arg in argument_names and default is not None:
                violations.add(f"{function_name}.{argument.arg}")
    return violations


def _tenant_scope_violations(source: str) -> set[str]:
    violations: set[str] = set()
    for name, function in _public_service_signatures(source).items():
        arguments = {
            argument.arg: (argument, default)
            for argument, default in zip(
                function.args.kwonlyargs,
                function.args.kw_defaults,
                strict=True,
            )
        }
        scope = arguments.get("scope")
        if scope is None or scope[1] is not None:
            violations.add(name)
            continue
        if ast.unparse(scope[0].annotation) != "TenantScope":
            violations.add(name)
    return violations


def test_positioning_package_has_one_tenant_lineage_and_extraction_dossier() -> None:
    assert (PACKAGE / "EXTRACTION.toml").is_file()
    assert (PACKAGE / "pyproject.toml").is_file()
    assert (SOURCE / "manifest.py").is_file()
    assert (SOURCE / "models.py").is_file()
    assert (SOURCE / "contracts.py").is_file()
    assert (SOURCE / "service.py").is_file()
    assert MIGRATION.is_file()


def test_canonical_check_type_and_security_scan_positioning() -> None:
    source = MAKEFILE.read_text(encoding="utf-8")
    assert (
        "POSITIONING_SRC ?= packages/dotmac-positioning/src/dotmac_positioning"
        in source
    )

    type_target = re.search(
        r"^type-check:.*?(?=^[A-Za-z][A-Za-z0-9_-]*:|\Z)",
        source,
        re.MULTILINE | re.DOTALL,
    )
    security_target = re.search(
        r"^security:.*?(?=^[A-Za-z][A-Za-z0-9_-]*:|\Z)",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert type_target is not None
    assert security_target is not None
    assert "$(POSITIONING_SRC)" in type_target.group(0)
    assert "$(POSITIONING_SRC)" in security_target.group(0)


def test_positioning_python_has_no_product_provider_or_presentation_branch() -> None:
    forbidden_terms = {
        "technician",
        "work_order",
        "vehicle",
        "attendance",
        "shift",
        "subscriber",
        "dispatch",
        "field_operations",
        "dotmac_sub",
        "dotmac_erp",
    }
    offenders: list[str] = []
    for path in _python_sources():
        source = path.read_text(encoding="utf-8")
        lowered = source.lower()
        for term in forbidden_terms:
            if term in lowered:
                offenders.append(f"{path.relative_to(PACKAGE)}: {term}")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports = [node.module]
            else:
                continue
            for imported in imports:
                if imported == "app" or imported.startswith("app."):
                    offenders.append(f"{path.relative_to(PACKAGE)}: {imported}")
                if imported in {"fastapi", "jinja2"} or imported.startswith(
                    ("fastapi.", "jinja2.", "folium", "leaflet")
                ):
                    offenders.append(f"{path.relative_to(PACKAGE)}: {imported}")
    assert not offenders


def test_positioning_service_is_transaction_neutral() -> None:
    tree = ast.parse((SOURCE / "service.py").read_text(encoding="utf-8"))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not ({"commit", "rollback"} & calls)


def test_every_public_positioning_operation_requires_explicit_tenant_scope() -> None:
    source = (SOURCE / "service.py").read_text(encoding="utf-8")
    assert not _tenant_scope_violations(source)

    # Sensitivity proof: the detector rejects a default tenant bucket.
    altered = source.replace(
        "scope: TenantScope,",
        "scope: TenantScope = DEFAULT_SCOPE,",
        1,
    )
    assert len(_tenant_scope_violations(altered)) == 1


def test_product_operational_choices_have_no_module_defaults() -> None:
    source = (SOURCE / "service.py").read_text(encoding="utf-8")
    required = {
        "evaluate_geofences": {"evaluation"},
        "get_trail": {"limit"},
        "prune_observations": {"received_before"},
        "record_observations": {
            "observations",
            "policy",
            "purpose",
            "received_at",
        },
    }
    assert not _defaulted_keyword_only_arguments(source, required)

    # Sensitivity proof: a package-selected policy is reported as a violation.
    altered = source.replace(
        "policy: ObservationPolicy,",
        "policy: ObservationPolicy = DEFAULT_POLICY,",
    )
    assert _defaulted_keyword_only_arguments(altered, required) == {
        "record_observations.policy"
    }


def test_position_ingest_never_chooses_which_geofences_apply() -> None:
    tree = ast.parse((SOURCE / "service.py").read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for function_name in ("record_observations", "_record_one"):
        called_names = {
            node.func.id
            for node in ast.walk(functions[function_name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "evaluate_geofences" not in called_names
        assert "_evaluate_selected_geofences" not in called_names

    assert "evaluate_geofences" in functions


def test_positioning_manifest_and_models_declare_only_tenant_state() -> None:
    from dotmac_kernel.namespaces import (
        MIGRATION_OWNER_LEDGER,
        POSITIONING_MIGRATION_OWNER,
    )
    from dotmac_positioning.manifest import module
    from dotmac_positioning.models import TENANT_TABLES

    assert POSITIONING_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == "positioning"
    assert module.short_code == "pos"
    assert module.migration_prefix == "po"
    assert module.migration_branch == "positioning"
    assert module.db_schema == "mod_pos"
    assert tuple(module.tables) == TENANT_TABLES
    assert tuple(module.platform_tables) == ()


def test_positioning_tenant_plane_is_atomic_not_selectable() -> None:
    """A tenant-only module has one installation shape, so selecting it is an error.

    ADR-0028 requires an assembly choice only when a lineage declares multiple
    supported plane sets. Turning the sole tenant plane into a fake choice would
    add assembly configuration without expressing any real product decision.
    """
    from dotmac_kernel.planes import (
        ModulePlane,
        ModulePlaneSelection,
        ModulePlaneSelectionError,
        supported_plane_sets,
        validate_module_plane_selections,
    )
    from dotmac_positioning.manifest import module

    assert tuple(module.supported_plane_sets) == ()
    assert supported_plane_sets(module) == ((ModulePlane.TENANT,),)
    with pytest.raises(ModulePlaneSelectionError, match="atomic plane contract"):
        validate_module_plane_selections(
            (module,),
            (ModulePlaneSelection("positioning", (ModulePlane.TENANT,)),),
        )

    migration_source = MIGRATION.read_text(encoding="utf-8")
    assert "selected_module_planes" not in migration_source


def test_every_positioning_table_is_explicitly_tenant_scoped() -> None:
    from dotmac_positioning.models import TENANT_MODELS

    for model in TENANT_MODELS:
        table = model.__table__
        assert table.schema == "mod_pos"
        assert table.c.tenant_id.nullable is False
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert any(columns[0] == "tenant_id" for columns in unique_columns)


def test_shared_schema_has_no_product_subject_or_consequence_columns() -> None:
    from dotmac_positioning.models import TENANT_MODELS

    columns = {
        column.name for model in TENANT_MODELS for column in model.__table__.columns
    }
    assert (
        not {
            "technician_id",
            "vehicle_id",
            "work_order_id",
            "subscriber_id",
            "attendance_status",
            "shift_status",
            "business_status",
        }
        & columns
    )
    assert {
        "tracked_unit_id",
        "source_identity_id",
        "source_unit_ref",
        "context_ref",
        "purpose",
    } <= columns


def test_migration_creates_rls_and_grants_for_every_declared_table() -> None:
    from dotmac_positioning.models import TENANT_TABLES

    source = MIGRATION.read_text(encoding="utf-8")
    statements = re.sub(r"\s+", " ", re.sub(r'"\s*\n\s*"', "", source))
    assert 'revision = "po_0001_positioning"' in source
    assert "down_revision = None" in source
    assert 'branch_labels = ("positioning",)' in source
    assert (
        'REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")' in source
    )
    assert "search_path" not in source
    for table in TENANT_TABLES:
        qualified = f"mod_pos.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in source
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in source
        assert f"ON {qualified}" in source
        assert f"ON {qualified} TO app_user" in statements


def test_positioning_lineage_passes_the_composed_migration_gate() -> None:
    from dotmac_kernel.migrations.gate import run_gate
    from dotmac_positioning.manifest import module

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    locations = (
        ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions",
        ROOT / "alembic/versions",
        SOURCE / "migrations/versions",
    )
    report = run_gate(
        (module,),
        locations,
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
    )
    assert report.ok, report.render()
    assert report.attribution["po_0001_positioning"]["owner"] == "positioning"
