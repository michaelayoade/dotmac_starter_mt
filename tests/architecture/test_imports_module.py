"""Structural guarantees of the import run ledger (ADR-0025).

The behavioural contract lives in `tests/unit/test_imports.py`. What is checked
here is the shape the behaviour depends on: that a dry run holds nothing it
could mutate with, that the ledger never learns a domain's identity, and that
the module stays importable without a configured database.
"""

from __future__ import annotations

import ast
import pathlib
import tomllib

import dotmac_imports
import pytest
from dotmac_imports.manifest import module
from dotmac_imports.models import (
    TENANT_TABLES,
    ImportPartition,
    ImportRun,
    ImportRunRow,
)
from dotmac_kernel.namespaces import (
    IMPORTS_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    NamespaceRegistry,
    revision_id_pattern,
)
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

PACKAGE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "packages/dotmac-imports/src/dotmac_imports"
)
SERVICE = PACKAGE / "service.py"
PARTITIONING = PACKAGE / "partitioning.py"
MIGRATION = PACKAGE / "migrations/versions/im_0001_import_runs.py"


def _function(source: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _parameter_names(function: ast.FunctionDef) -> set[str]:
    args = function.args
    return {
        argument.arg
        for argument in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        if argument.arg
    }


def _mentions_an_applier(function: ast.FunctionDef) -> bool:
    """Whether anything in this function could reach a domain mutation.

    Deliberately broad — parameter names, bare names, attribute access and
    annotations. A narrow check (say, only `.apply(` calls) would pass a
    function that received an applier and handed it to a helper, which is
    exactly how the property would be lost in a later refactor.
    """
    if any("applier" in name.lower() for name in _parameter_names(function)):
        return True
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and "applier" in node.id.lower():
            return True
        if isinstance(node, ast.Attribute) and node.attr in {"apply", "applier"}:
            return True
        # A keyword argument name is neither a Name nor an Attribute node. The
        # sensitivity proof below caught this omission: without it, a function
        # that forwarded `applier=x` to a helper read as clean.
        if isinstance(node, ast.keyword) and "applier" in (node.arg or "").lower():
            return True
    return False


# ── the property ADR-0025 § 2 exists for ────────────────────────────────────


def test_the_dry_run_entry_point_holds_nothing_that_could_mutate() -> None:
    """ERP proved a dry run never wrote with an AST guard over `if dry_run:`
    shapes, and its own docstring concedes the guard does not check polarity —
    a backwards guard passes. Sub relies on the same discipline while committing
    every 200 rows. Here the applier is not in scope at all, so there is no
    branch to invert and no call site to guard."""
    assert not _mentions_an_applier(
        _function(SERVICE.read_text(encoding="utf-8"), "validate_next_chunk")
    )
    assert not _mentions_an_applier(
        _function(
            PARTITIONING.read_text(encoding="utf-8"),
            "validate_claimed_partition",
        )
    )


def test_the_detector_fires_on_a_function_that_does_hold_one() -> None:
    """Sensitivity proof, in the three shapes the property could be lost:
    taking the applier, naming it, or calling through it."""
    for source in (
        "def validate_next_chunk(db, *, rows, validator, applier):\n    pass\n",
        "def validate_next_chunk(db, *, rows, validator, x):\n"
        "    return _process(db, applier=x)\n",
        "def validate_next_chunk(db, *, rows, validator, x):\n"
        "    return x.apply(rows)\n",
    ):
        assert _mentions_an_applier(_function(source, "validate_next_chunk"))


def test_the_detector_does_fire_on_the_apply_entry_point() -> None:
    """Specificity: `apply_next_chunk` is supposed to hold one. A detector that
    did not flag it would be measuring nothing."""
    assert _mentions_an_applier(
        _function(SERVICE.read_text(encoding="utf-8"), "apply_next_chunk")
    )


def test_an_apply_run_can_only_be_created_by_promotion() -> None:
    """`create_dry_run` takes no `dry_run` flag: an apply run exists only as the
    promotion of a validated one, so it cannot be requested directly."""
    parameters = _parameter_names(
        _function(SERVICE.read_text(encoding="utf-8"), "create_dry_run")
    )
    assert "dry_run" not in parameters


def test_processing_entry_points_take_verified_bytes_not_free_form_rows() -> None:
    """The digest check must guard the content actually decoded and processed.
    A `rows` parameter would reopen the gap where promotion verifies one file
    and apply mutates from an unrelated caller sequence."""
    source = SERVICE.read_text(encoding="utf-8")
    for name in ("validate_next_chunk", "apply_next_chunk"):
        parameters = _parameter_names(_function(source, name))
        assert "data" in parameters
        assert "rows" not in parameters


def test_partition_io_and_database_settlement_are_separate_entry_points() -> None:
    """A provider read may block or fail and must not run while a claim row is
    locked. The read phase therefore cannot accept a session, and the two
    settlement phases cannot accept an opener."""
    source = PARTITIONING.read_text(encoding="utf-8")
    read_parameters = _parameter_names(_function(source, "read_claimed_partition"))
    assert "db" not in read_parameters
    assert "open_partition" in read_parameters

    for name in ("validate_claimed_partition", "apply_claimed_partition"):
        parameters = _parameter_names(_function(source, name))
        assert "db" in parameters
        assert "prepared" in parameters
        assert "open_partition" not in parameters


def test_the_partition_phase_guard_rejects_the_old_lock_then_read_shape() -> None:
    old_shape = """
def validate_claimed_partition(db, claim, *, open_partition, validator):
    return settle(db, open_partition(claim.file_id), validator)
"""
    function = _function(old_shape, "validate_claimed_partition")
    parameters = _parameter_names(function)
    assert "db" in parameters and "open_partition" in parameters


# ── the ledger carries no domain identity ───────────────────────────────────


def test_no_ledger_column_is_a_foreign_key_into_a_domain_table() -> None:
    """ADR-0025 § 3. Sub's shared `import_run_rows` carries `payment_id` and
    `record_created`, welding one domain's money concerns into the table every
    other domain would share. The reference runs the other way here."""
    # `fullname` omits the schema for the kernel's own `public` tables.
    permitted = {"tenants", "mod_imports.import_runs"}
    for table in (
        ImportRun.__table__,
        ImportRunRow.__table__,
        ImportPartition.__table__,
    ):
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            targets = {element.column.table.fullname for element in constraint.elements}
            assert targets <= permitted, (
                f"{table.fullname}.{constraint.name} references {targets} — the "
                "ledger records an opaque result, never a domain row"
            )


def test_no_column_name_suggests_a_domain() -> None:
    """A weaker but independent guard on the same rule: a column named for one
    product's noun is the shape the defect took in the source."""
    forbidden = ("payment", "invoice", "customer", "subscriber", "employee", "asset")
    for table in (
        ImportRun.__table__,
        ImportRunRow.__table__,
        ImportPartition.__table__,
    ):
        for column in table.columns:
            assert not any(word in column.name for word in forbidden), column.name


def test_the_row_ledger_minimises_imported_content() -> None:
    """The source bytes stay in dotmac-files. The run ledger records a stable
    fingerprint for repair/idempotency without becoming a second PII store."""
    columns = ImportRunRow.__table__.c
    assert "row_values" not in columns
    assert not columns.row_fingerprint_sha256.nullable
    assert columns.row_fingerprint_sha256.type.length == 64
    assert "error_code" in columns

    migration = MIGRATION.read_text(encoding="utf-8")
    fingerprint_column = (
        'sa.Column("row_fingerprint_sha256", sa.String(64), nullable=False)'
    )
    assert fingerprint_column in migration
    assert 'sa.Column("row_values"' not in migration


# ── tenancy ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "table",
    (ImportRun.__table__, ImportRunRow.__table__, ImportPartition.__table__),
)
def test_every_ledger_table_is_tenant_scoped(table) -> None:  # type: ignore[no-untyped-def]
    """Hard rule 11. Neither source product has a tenant column at all, so this
    is added by the extraction rather than ported by it."""
    assert not table.c.tenant_id.nullable
    composites = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("tenant_id", "id") in composites


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_the_migration_forces_rls_and_a_tenant_policy(table: str) -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert f"ALTER TABLE mod_imports.{table} ENABLE ROW LEVEL SECURITY;" in sql
    assert f"ALTER TABLE mod_imports.{table} FORCE ROW LEVEL SECURITY;" in sql
    assert f"CREATE POLICY {table}_tenant_isolation" in sql
    assert "tenant_id = public.app_current_tenant_id()" in sql


def test_the_migration_names_its_schema_in_every_raw_statement() -> None:
    """D1: module SQL is fully qualified, never dependent on `search_path`.

    Also the reason the RLS block is written out per table instead of looped —
    a schema name assembled at runtime is one the composed gate cannot read.
    """
    for node in ast.walk(ast.parse(MIGRATION.read_text(encoding="utf-8"))):
        is_execute = (
            isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "execute"
        )
        if not is_execute:
            continue
        statement = node.args[0]
        assert isinstance(statement, ast.Constant), (
            f"line {node.lineno}: op.execute() must take a literal string so the "
            "composed migration gate can read the schema it names"
        )
        assert "mod_imports" in statement.value


# ── allocation and composition ──────────────────────────────────────────────


def test_the_manifest_matches_its_immutable_ledger_row() -> None:
    assert module.migration_owner() == IMPORTS_MIGRATION_OWNER
    assert IMPORTS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert IMPORTS_MIGRATION_OWNER.db_schema == "mod_imports"
    assert IMPORTS_MIGRATION_OWNER.prefix == "im"


def test_the_manifest_declares_exactly_the_tables_the_models_define() -> None:
    registry = NamespaceRegistry.from_manifests([module])
    assert registry.declared_tables("mod_imports") == frozenset(TENANT_TABLES)
    assert {
        ImportRun.__tablename__,
        ImportRunRow.__tablename__,
        ImportPartition.__tablename__,
    } == set(TENANT_TABLES)


def test_no_platform_plane_is_declared() -> None:
    """ADR-0023 requires both planes to be DECLARED where both exist. The audit
    found no control-plane import capability anywhere in the fleet, and a plane
    no product uses would be declared rather than discovered."""
    registry = NamespaceRegistry.from_manifests([module])
    assert registry.declared_platform_tables("mod_imports") == frozenset()
    assert module.platform_tables == ()


def test_the_revision_id_matches_this_lineage() -> None:
    revision = next(
        node.value.value
        for node in ast.parse(MIGRATION.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", "") == "revision"
    )
    assert revision_id_pattern("im").match(revision)


def test_the_module_does_not_import_the_kernels_db_module() -> None:
    """`dotmac_kernel.db` builds an engine at import time, so importing it would
    make a configured database URL a prerequisite for merely importing this
    package — and a module that cannot be imported cannot be inspected by any
    of the gates above."""
    for path in PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "dotmac_kernel.db", path
            elif isinstance(node, ast.Import):
                assert all(alias.name != "dotmac_kernel.db" for alias in node.names)


def test_the_release_floor_is_the_kernel_that_allocated_the_schema() -> None:
    manifest = tomllib.loads(
        (PACKAGE.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert manifest["tool"]["poetry"]["dependencies"]["dotmac-kernel"] == ">=0.1.0a56"


def test_every_exported_name_resolves() -> None:
    for name in dotmac_imports.__all__:
        assert hasattr(dotmac_imports, name), name
    assert len(set(dotmac_imports.__all__)) == len(dotmac_imports.__all__)
