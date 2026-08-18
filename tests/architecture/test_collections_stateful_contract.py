"""RED-first stateful package and migration contract for Collections.

This scanner is deliberately static.  It can prove its own sensitivity before
the package exists and, once the active Billing allocation work settles, it
becomes the first gate on the real package diff.  PostgreSQL remains the final
authority for catalog shape and concurrency; this file prevents an obviously
wrong lineage from reaching that lane.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "packages/dotmac-collections"
SUB_PIN = "d1a1a913e287ffadaf21b7da7be448f2c28b5483"

TENANT_TABLES = (
    "collection_policies",
    "collection_policy_versions",
    "collection_policy_steps",
    "collection_cases",
    "collection_case_exposures",
    "collection_case_transitions",
    "collection_step_attempts",
    "payment_arrangements",
    "payment_arrangement_exposures",
    "payment_arrangement_installments",
    "payment_arrangement_settlement_receipts",
    "collection_grace_grants",
    "collection_notice_requests",
    "collection_notice_receipts",
    "collection_action_requests",
    "collection_action_receipts",
    "collection_reconciliations",
)

REQUIRED_PATHS = (
    "EXTRACTION.toml",
    "pyproject.toml",
    "src/dotmac_collections/__init__.py",
    "src/dotmac_collections/manifest.py",
    "src/dotmac_collections/models.py",
    "src/dotmac_collections/migrations/__init__.py",
    "src/dotmac_collections/migrations/versions",
)


def _assignment(tree: ast.Module, name: str) -> ast.expr | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
    return None


def _literal(node: ast.AST | None) -> object | None:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (TypeError, ValueError):
        return None


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _manifest_call(tree: ast.Module) -> ast.Call | None:
    value = _assignment(tree, "module")
    if isinstance(value, ast.Call) and _dotted_name(value.func).endswith(
        "ModuleManifest"
    ):
        return value
    return None


def _keywords(call: ast.Call) -> dict[str, ast.expr]:
    return {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}


def _referenced_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _string_sequence(node: ast.AST | None) -> tuple[str, ...] | None:
    value = _literal(node)
    if not isinstance(value, (tuple, list)) or not all(
        isinstance(item, str) for item in value
    ):
        return None
    return tuple(value)


def _call_string_sequence(node: ast.AST) -> tuple[str, ...] | None:
    value = _literal(node)
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        return None
    return tuple(value)


def _migration_table_problems(
    tree: ast.Module,
    *,
    schema: str,
) -> tuple[set[str], list[str]]:
    created: set[str] = set()
    problems: list[str] = []
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or _dotted_name(node.func) != "op.create_table"
        ):
            continue
        table = _literal(node.args[0]) if node.args else None
        if not isinstance(table, str):
            problems.append("migration:create-table-name-must-be-literal")
            continue
        created.add(table)
        keywords = {item.arg: item.value for item in node.keywords if item.arg}
        if _literal(keywords.get("schema")) != schema and not (
            isinstance(keywords.get("schema"), ast.Name)
            and keywords["schema"].id in {"SCHEMA", "_SCHEMA"}
        ):
            problems.append(f"migration:{table}:missing-explicit-schema")

        columns: dict[str, ast.Call] = {}
        uniques: list[tuple[str, ...]] = []
        foreign_keys: list[tuple[tuple[str, ...] | None, tuple[str, ...] | None]] = []
        direct_foreign_keys: list[tuple[str, str | None]] = []
        for argument in node.args[1:]:
            if not isinstance(argument, ast.Call):
                continue
            callee = _dotted_name(argument.func).rsplit(".", 1)[-1]
            if callee == "Column" and argument.args:
                column_name = _literal(argument.args[0])
                if isinstance(column_name, str):
                    columns[column_name] = argument
                    for child in argument.args[1:]:
                        if isinstance(child, ast.Call) and _dotted_name(
                            child.func
                        ).endswith("ForeignKey"):
                            target = _literal(child.args[0]) if child.args else None
                            direct_foreign_keys.append(
                                (
                                    column_name,
                                    target if isinstance(target, str) else None,
                                )
                            )
            elif callee == "UniqueConstraint":
                values = tuple(
                    item
                    for item in (_literal(value) for value in argument.args)
                    if isinstance(item, str)
                )
                uniques.append(values)
            elif callee == "ForeignKeyConstraint" and len(argument.args) >= 2:
                foreign_keys.append(
                    (
                        _call_string_sequence(argument.args[0]),
                        _call_string_sequence(argument.args[1]),
                    )
                )

        tenant = columns.get("tenant_id")
        if tenant is None:
            problems.append(f"migration:{table}:missing-tenant-id")
        else:
            tenant_keywords = {
                item.arg: item.value for item in tenant.keywords if item.arg
            }
            if _literal(tenant_keywords.get("nullable")) is not False:
                problems.append(f"migration:{table}:tenant-id-must-be-not-null")
        if "id" not in columns:
            problems.append(f"migration:{table}:missing-id")
        if not any(unique[:2] == ("tenant_id", "id") for unique in uniques):
            problems.append(f"migration:{table}:missing-tenant-composite-identity")

        for column, target in direct_foreign_keys:
            if column != "tenant_id" or target != "public.tenants.id":
                problems.append(f"migration:{table}:bare-column-foreign-key:{column}")
        for sources, targets in foreign_keys:
            if sources == ("tenant_id",) and targets == ("public.tenants.id",):
                continue
            if not sources or not targets:
                problems.append(f"migration:{table}:uninspectable-foreign-key")
                continue
            if sources[0] != "tenant_id" or not targets[0].endswith(".tenant_id"):
                problems.append(
                    f"migration:{table}:foreign-key-is-not-tenant-composite"
                )
                continue
            if any(not target.startswith(f"{schema}.") for target in targets):
                problems.append(f"migration:{table}:foreign-key-leaves-module-schema")
    return created, problems


def scan_collections_stateful_contract(package_root: Path) -> tuple[str, ...]:
    """Return stable violations for the stateful package's first revision."""

    if not package_root.is_dir():
        return ("package-missing:packages/dotmac-collections",)

    problems: list[str] = []
    for relative in REQUIRED_PATHS:
        if not (package_root / relative).exists():
            problems.append(f"missing:{relative}")
    if problems:
        return tuple(sorted(problems))

    extraction = tomllib.loads(
        (package_root / "EXTRACTION.toml").read_text(encoding="utf-8")
    )
    if extraction.get("package") != "dotmac-collections":
        problems.append("extraction:wrong-package")
    if extraction.get("status") != "audit-complete":
        problems.append("extraction:must-start-audit-complete")
    if extraction.get("source_mode") != "product-first":
        problems.append("extraction:source-mode-must-be-product-first")
    if "dotmac_sub" not in extraction.get("source_repositories", []):
        problems.append("extraction:missing-sub-source")
    if f"dotmac_sub:{SUB_PIN}" not in extraction.get("source_revisions", []):
        problems.append("extraction:missing-exact-sub-pin")
    if extraction.get("contract_consumers") != []:
        problems.append("extraction:consumer-claim-before-cutover")
    if "dotmac_sub" not in extraction.get("candidate_consumers", []):
        problems.append("extraction:sub-not-candidate-adopter")
    if "dotmac_sub" not in str(extraction.get("first_cutover", "")):
        problems.append("extraction:first-cutover-is-not-sub")
    retirement = str(extraction.get("local_copy_retirement", ""))
    for required in ("dunning_runner", "prepaid_balance_sweep", "sensitivity"):
        if required not in retirement:
            problems.append(f"extraction:retirement-missing:{required}")

    pyproject = tomllib.loads(
        (package_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    poetry = pyproject.get("tool", {}).get("poetry", {})
    if poetry.get("name") != "dotmac-collections":
        problems.append("pyproject:wrong-distribution-name")
    dependencies = poetry.get("dependencies", {})
    if "dotmac-kernel" not in dependencies or "sqlalchemy" not in dependencies:
        problems.append("pyproject:missing-kernel-or-sqlalchemy")
    sibling_dependencies = sorted(
        name
        for name in dependencies
        if name.startswith("dotmac-") and name != "dotmac-kernel"
    )
    if sibling_dependencies:
        problems.append(
            f"pyproject:sibling-dependencies:{','.join(sibling_dependencies)}"
        )

    source_root = package_root / "src/dotmac_collections"
    models_path = source_root / "models.py"
    manifest_path = source_root / "manifest.py"
    models_tree = ast.parse(models_path.read_text(encoding="utf-8"))
    manifest_tree = ast.parse(manifest_path.read_text(encoding="utf-8"))

    model_tables = _string_sequence(_assignment(models_tree, "TENANT_TABLES"))
    platform_tables = _string_sequence(_assignment(models_tree, "PLATFORM_TABLES"))
    if model_tables != TENANT_TABLES:
        problems.append("models:tenant-table-declaration-drift")
    if platform_tables != ():
        problems.append("models:platform-plane-must-be-empty")

    manifest = _manifest_call(manifest_tree)
    if manifest is None:
        problems.append("manifest:missing-module-manifest")
        return tuple(sorted(problems))
    manifest_keywords = _keywords(manifest)
    if _literal(manifest_keywords.get("code")) != "collections":
        problems.append("manifest:wrong-code")
    if _literal(manifest_keywords.get("core")) is not False:
        problems.append("manifest:must-be-optional")
    short_code = _literal(manifest_keywords.get("short_code"))
    prefix = _literal(manifest_keywords.get("migration_prefix"))
    branch = _literal(manifest_keywords.get("migration_branch"))
    if not isinstance(short_code, str) or not short_code:
        problems.append("manifest:missing-short-code")
    if not isinstance(prefix, str) or not prefix:
        problems.append("manifest:missing-migration-prefix")
    if not isinstance(branch, str) or not branch:
        problems.append("manifest:missing-migration-branch")
    if (
        _dotted_name(manifest_keywords.get("tables", ast.Constant(None)))
        != "TENANT_TABLES"
    ):
        problems.append("manifest:tenant-tables-not-model-declaration")
    if _literal(manifest_keywords.get("platform_tables")) != ():
        problems.append("manifest:platform-tables-must-be-explicit-empty")
    if _literal(manifest_keywords.get("supported_plane_sets")) != ():
        problems.append("manifest:tenant-plane-must-be-atomic")

    common_names = _referenced_names(manifest_keywords.get("requires"))
    if not {"MODULE_DATABASE_ROLES_V1", "OUTBOX_RELAY_V1"} <= common_names:
        problems.append("manifest:missing-common-prerequisite")
    tenant_names = _referenced_names(manifest_keywords.get("tenant_requires"))
    if "TENANT_SCOPE_CATALOG_V1" not in tenant_names:
        problems.append("manifest:missing-tenant-prerequisite")
    if _literal(manifest_keywords.get("platform_requires")) not in {None, ()}:
        problems.append("manifest:platform-prerequisites-without-platform")

    schema_assignment = _assignment(models_tree, "SCHEMA")
    if not (
        isinstance(schema_assignment, ast.Call)
        and _dotted_name(schema_assignment.func).endswith("module_schema")
        and schema_assignment.args
        and _literal(schema_assignment.args[0]) == short_code
    ):
        problems.append("models:schema-does-not-use-allocated-short-code")

    versions = source_root / "migrations/versions"
    migrations = sorted(
        path for path in versions.glob("*.py") if path.name != "__init__.py"
    )
    if len(migrations) != 1:
        problems.append(f"migration:expected-one-creation-revision:{len(migrations)}")
        return tuple(sorted(problems))
    migration = migrations[0]
    migration_source = migration.read_text(encoding="utf-8")
    migration_tree = ast.parse(migration_source)
    revision = _literal(_assignment(migration_tree, "revision"))
    down_revision = _literal(_assignment(migration_tree, "down_revision"))
    branch_labels = _string_sequence(_assignment(migration_tree, "branch_labels"))
    schema = _literal(_assignment(migration_tree, "_SCHEMA"))
    if schema is None:
        schema = _literal(_assignment(migration_tree, "SCHEMA"))
    expected_schema = f"mod_{short_code}" if isinstance(short_code, str) else None
    if schema != expected_schema:
        problems.append("migration:schema-does-not-match-allocation")
    if not isinstance(revision, str) or not isinstance(prefix, str):
        problems.append("migration:missing-literal-revision")
    else:
        if not revision.startswith(f"{prefix}_") or len(revision) > 32:
            problems.append("migration:revision-does-not-match-prefix")
        if migration.stem != revision:
            problems.append("migration:filename-does-not-match-revision")
    if down_revision is not None:
        problems.append("migration:lineage-root-must-not-name-foreign-revision")
    if branch_labels != (branch,):
        problems.append("migration:branch-label-drift")
    depends_on = _assignment(migration_tree, "depends_on")
    if not (
        isinstance(depends_on, ast.Call)
        and _dotted_name(depends_on.func).endswith("resolve_depends_on")
    ):
        problems.append("migration:depends-on-must-use-logical-binding")

    for name, expected in (
        ("COMMON_REQUIRES", ("module_database_roles.v1", "outbox_relay.v1")),
        ("TENANT_REQUIRES", ("tenant_scope_catalog.v1",)),
        ("PLATFORM_REQUIRES", ()),
        ("TENANT_TABLES", TENANT_TABLES),
    ):
        if _string_sequence(_assignment(migration_tree, name)) != expected:
            problems.append(f"migration:snapshot-drift:{name}")

    upgrade = next(
        (
            node
            for node in migration_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        ),
        None,
    )
    if upgrade is None:
        problems.append("migration:missing-upgrade")
    else:
        upgrade_source = ast.get_source_segment(migration_source, upgrade) or ""
        prerequisite_at = upgrade_source.find("require_prerequisites(")
        ddl_positions = [
            position
            for marker in ("op.execute(", "op.create_table(", "_upgrade_tenant_plane(")
            if (position := upgrade_source.find(marker)) >= 0
        ]
        if (
            prerequisite_at < 0
            or not ddl_positions
            or prerequisite_at > min(ddl_positions)
        ):
            problems.append("migration:prerequisites-not-checked-before-ddl")

    created, table_problems = _migration_table_problems(
        migration_tree,
        schema=str(schema),
    )
    problems.extend(table_problems)
    if created != set(TENANT_TABLES):
        problems.append("migration:created-table-set-drift")

    normalized = re.sub(r"\s+", " ", migration_source)
    for required_sql in (
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "CREATE POLICY",
        "tenant_id = public.app_current_tenant_id()",
        "GRANT USAGE ON SCHEMA",
        "GRANT SELECT, INSERT, UPDATE, DELETE",
        "TO app_user",
        "REVOKE ALL",
        "FROM PUBLIC",
    ):
        if required_sql not in normalized:
            problems.append(f"migration:missing-security-sql:{required_sql}")
    for forbidden in ("search_path", "CREATE TYPE", "postgresql.ENUM"):
        if forbidden in migration_source:
            problems.append(f"migration:forbidden-sql-shape:{forbidden}")
    if "TO platform_api" in migration_source:
        problems.append("migration:platform-grant-without-platform-adopter")
    if any(
        isinstance(node, ast.Call)
        and _dotted_name(node.func).rsplit(".", 1)[-1] in {"Enum", "ENUM"}
        for node in ast.walk(migration_tree)
    ):
        problems.append("migration:native-postgresql-enum")
    if not any(
        isinstance(node, ast.Call)
        and _dotted_name(node.func).endswith("CheckConstraint")
        for node in ast.walk(migration_tree)
    ):
        problems.append("migration:no-constrained-string-vocabulary")

    return tuple(sorted(set(problems)))


def _good_migration() -> str:
    tables = repr(TENANT_TABLES)
    create_blocks: list[str] = []
    for index, table in enumerate(TENANT_TABLES):
        parent = ""
        if index:
            parent = """
        sa.Column("parent_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            [
                "mod_coll.collection_policies.tenant_id",
                "mod_coll.collection_policies.id",
            ],
        ),"""
        create_blocks.append(
            f"""
    op.create_table(
        "{table}",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),{parent}
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"]),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.CheckConstraint("state <> ''"),
        schema=_SCHEMA,
    )
"""
        )
    return f"""
import sqlalchemy as sa
from alembic import op
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

revision = "cl_0001_collections"
down_revision = None
branch_labels = ("collections",)
MODULE_CODE = "collections"
COMMON_REQUIRES = ("module_database_roles.v1", "outbox_relay.v1")
TENANT_REQUIRES = ("tenant_scope_catalog.v1",)
PLATFORM_REQUIRES = ()
TENANT_TABLES = {tables}
depends_on = resolve_depends_on(
    COMMON_REQUIRES,
    module=MODULE_CODE,
    tenant=TENANT_REQUIRES,
    platform=PLATFORM_REQUIRES,
)
_SCHEMA = "mod_coll"

def upgrade():
    require_prerequisites(op.get_bind(), COMMON_REQUIRES)
    require_prerequisites(op.get_bind(), TENANT_REQUIRES)
    op.execute("CREATE SCHEMA mod_coll")
{"".join(create_blocks)}
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE mod_coll.{{table}} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE mod_coll.{{table}} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {{table}}_tenant_isolation ON mod_coll.{{table}} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id())"
        )
        op.execute(f"REVOKE ALL ON mod_coll.{{table}} FROM PUBLIC")
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_coll.{{table}} TO app_user"
        )
    op.execute("GRANT USAGE ON SCHEMA mod_coll TO app_user")
""".lstrip()


def _write_good_package(tmp_path: Path) -> Path:
    package = tmp_path / "dotmac-collections"
    source = package / "src/dotmac_collections"
    versions = source / "migrations/versions"
    versions.mkdir(parents=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    (source / "migrations/__init__.py").write_text("", encoding="utf-8")
    (source / "models.py").write_text(
        f"""
from dotmac_kernel.namespaces import module_schema

SCHEMA = module_schema("coll")
TENANT_TABLES = {TENANT_TABLES!r}
PLATFORM_TABLES = ()
""".lstrip(),
        encoding="utf-8",
    )
    (source / "manifest.py").write_text(
        """
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    OUTBOX_RELAY_V1,
    TENANT_SCOPE_CATALOG_V1,
)
from dotmac_collections.models import TENANT_TABLES

module = ModuleManifest(
    code="collections",
    version="0.1.0a1",
    core=False,
    short_code="coll",
    migration_prefix="cl",
    migration_branch="collections",
    tables=TENANT_TABLES,
    platform_tables=(),
    requires=(MODULE_DATABASE_ROLES_V1.name, OUTBOX_RELAY_V1.name),
    tenant_requires=(TENANT_SCOPE_CATALOG_V1.name,),
    platform_requires=(),
    supported_plane_sets=(),
)
""".lstrip(),
        encoding="utf-8",
    )
    (versions / "cl_0001_collections.py").write_text(
        _good_migration(), encoding="utf-8"
    )
    (package / "EXTRACTION.toml").write_text(
        f"""
schema_version = 1
package = "dotmac-collections"
classification = "optional-module"
status = "audit-complete"
source_mode = "product-first"
owner = "Collections decisions"
contract = "Cases and requests, not receivables or product effects"
source_repositories = ["dotmac_sub"]
source_revisions = ["dotmac_sub:{SUB_PIN}"]
source_paths = ["dotmac_sub:app/services/collections/_core.py"]
preserved_tests = ["dotmac_sub:tests/test_collections_target_lifecycle.py"]
contract_consumers = []
candidate_consumers = ["dotmac_sub"]
inventory_evidence = ["dotmac_starter_mt:docs/inventories/collections-sources.md"]
first_cutover = "dotmac_sub"
shadow_and_drift = "shadow exact decisions"
local_copy_retirement = '''
retire dunning_runner and prepaid_balance_sweep with sensitivity proof
'''
next_action = "turn canaries green"
""".lstrip(),
        encoding="utf-8",
    )
    (package / "pyproject.toml").write_text(
        """
[tool.poetry]
name = "dotmac-collections"
version = "0.1.0a1"
packages = [{ include = "dotmac_collections", from = "src" }]

[tool.poetry.dependencies]
python = ">=3.11,<3.14"
dotmac-kernel = ">=0.1.0a67"
sqlalchemy = "^2.0"
""".lstrip(),
        encoding="utf-8",
    )
    return package


def _replace(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    assert old in source
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def test_collections_stateful_package_matches_its_first_revision_contract() -> None:
    assert scan_collections_stateful_contract(PACKAGE_ROOT) == ()


def test_stateful_scanner_accepts_a_complete_tenant_only_fixture(
    tmp_path: Path,
) -> None:
    assert scan_collections_stateful_contract(_write_good_package(tmp_path)) == ()


@pytest.mark.parametrize(
    ("relative", "old", "new", "expected"),
    [
        (
            "EXTRACTION.toml",
            'source_mode = "product-first"',
            'source_mode = "greenfield-after-inventory"',
            "extraction:source-mode-must-be-product-first",
        ),
        (
            "src/dotmac_collections/models.py",
            "PLATFORM_TABLES = ()",
            'PLATFORM_TABLES = ("platform_cases",)',
            "models:platform-plane-must-be-empty",
        ),
        (
            "src/dotmac_collections/manifest.py",
            "platform_tables=()",
            'platform_tables=("platform_cases",)',
            "manifest:platform-tables-must-be-explicit-empty",
        ),
        (
            "src/dotmac_collections/manifest.py",
            "supported_plane_sets=()",
            'supported_plane_sets=(("tenant",),)',
            "manifest:tenant-plane-must-be-atomic",
        ),
        (
            "src/dotmac_collections/manifest.py",
            "MODULE_DATABASE_ROLES_V1.name, OUTBOX_RELAY_V1.name",
            "MODULE_DATABASE_ROLES_V1.name",
            "manifest:missing-common-prerequisite",
        ),
        (
            "src/dotmac_collections/migrations/versions/cl_0001_collections.py",
            'sa.Column("tenant_id", sa.Uuid(), nullable=False)',
            'sa.Column("tenant_id", sa.Uuid(), nullable=True)',
            "tenant-id-must-be-not-null",
        ),
        (
            "src/dotmac_collections/migrations/versions/cl_0001_collections.py",
            (
                '["tenant_id", "parent_id"],\n'
                "            [\n"
                '                "mod_coll.collection_policies.tenant_id",\n'
                '                "mod_coll.collection_policies.id",\n'
                "            ]"
            ),
            ('["parent_id"],\n            ["mod_coll.collection_policies.id"]'),
            "foreign-key-is-not-tenant-composite",
        ),
        (
            "src/dotmac_collections/migrations/versions/cl_0001_collections.py",
            " FORCE ROW LEVEL SECURITY",
            " ROW LEVEL SECURITY",
            "missing-security-sql:FORCE ROW LEVEL SECURITY",
        ),
        (
            "src/dotmac_collections/migrations/versions/cl_0001_collections.py",
            "sa.String(32)",
            'sa.Enum("active", "closed", name="bad_state")',
            "migration:native-postgresql-enum",
        ),
        (
            "src/dotmac_collections/migrations/versions/cl_0001_collections.py",
            'op.execute("CREATE SCHEMA mod_coll")',
            'op.execute("SET search_path TO mod_coll")',
            "migration:forbidden-sql-shape:search_path",
        ),
        (
            "src/dotmac_collections/migrations/versions/cl_0001_collections.py",
            "down_revision = None",
            'down_revision = "foreign_0001"',
            "migration:lineage-root-must-not-name-foreign-revision",
        ),
        (
            "src/dotmac_collections/migrations/versions/cl_0001_collections.py",
            'op.execute("GRANT USAGE ON SCHEMA mod_coll TO app_user")',
            'op.execute("GRANT USAGE ON SCHEMA mod_coll TO platform_api")',
            "migration:platform-grant-without-platform-adopter",
        ),
    ],
)
def test_stateful_scanner_detects_each_planted_violation(
    tmp_path: Path,
    relative: str,
    old: str,
    new: str,
    expected: str,
) -> None:
    package = _write_good_package(tmp_path)
    _replace(package / relative, old, new)
    problems = scan_collections_stateful_contract(package)
    assert any(expected in problem for problem in problems), problems
