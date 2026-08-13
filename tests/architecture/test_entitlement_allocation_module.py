"""The entitlement-allocation module's structural contract.

Two properties, and they are not the same property:

1. **The module validates, and cannot be persuaded not to.** No trusted-caller
   escape, no optional catalogue, no default that silently validates against
   nothing.
2. **It carries no dependency on whoever owns contracts.** No import, no foreign
   key, no `depends_on`. That is what makes the extraction A2-neutral, and it is
   the thing most likely to be reintroduced by someone "fixing" the missing
   referential integrity.

Behaviour lives in `tests/unit/test_entitlement_allocation_service.py` and
`tests/test_entitlement_allocation_canaries.py`; this file is static structure.
"""

from __future__ import annotations

import ast
import inspect
import tomllib
from pathlib import Path

import pytest
from dotmac_entitlement_allocation import models, ports, versions_dir
from dotmac_entitlement_allocation.manifest import module
from dotmac_kernel.namespaces import (
    ENTITLEMENT_ALLOCATION_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    module_schema,
)

MODULE_ROOT = Path(inspect.getfile(ports)).parent
PACKAGE_ROOT = MODULE_ROOT.parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
LINEAGE = MODULE_ROOT / "migrations" / "versions" / "ea_0001_allocations.py"


def _migration_source() -> str:
    return LINEAGE.read_text(encoding="utf-8")


def test_the_public_migration_locator_resolves_the_installed_lineage() -> None:
    location = versions_dir()
    assert location.is_dir()
    assert location == MODULE_ROOT / "migrations" / "versions"
    assert (location / "ea_0001_allocations.py").is_file()


# ── D1: the ledger allocation ────────────────────────────────────────────────


def test_the_manifest_matches_its_immutable_ledger_row() -> None:
    owner = ENTITLEMENT_ALLOCATION_MIGRATION_OWNER
    assert owner in MIGRATION_OWNER_LEDGER
    assert module.code == owner.owner == "entitlement_allocation"
    assert module.short_code == "ealloc"
    assert module.migration_prefix == owner.prefix == "ea"
    assert module.migration_branch == owner.branch_label == "entitlement_allocation"
    assert module.db_schema == owner.db_schema == module_schema("ealloc")


def test_the_module_is_not_core() -> None:
    """Ruling C4 splits allocating from granting. A data plane installing this
    would be acquiring the wrong half."""
    assert module.core is False


def test_declared_tables_are_exactly_what_the_migration_creates() -> None:
    calls = [
        node
        for node in ast.walk(ast.parse(_migration_source()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_table"
    ]
    assert calls, "no create_table calls found"
    for call in calls:
        assert isinstance(call.args[0], ast.Constant), ast.unparse(call.func)
    assert {call.args[0].value for call in calls} == set(module.tables)


def test_the_revision_id_fits_the_alembic_version_column() -> None:
    assert len("ea_0001_allocations") <= 32


# ── No dependency on whoever owns contracts ──────────────────────────────────


def test_the_lineage_declares_no_cross_module_dependency() -> None:
    """`depends_on` here would order this behind a table another module owns and
    make either un-releasable without the other."""
    tree = ast.parse(_migration_source())
    assigned = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert assigned["revision"].value == "ea_0001_allocations"
    assert assigned["down_revision"].value is None
    assert assigned["depends_on"].value is None


def test_contract_ref_carries_no_foreign_key() -> None:
    """The regression this test exists for: someone restoring the "missing"
    referential integrity the source implementation had.

    An allocation is an immutable projection. It must stay readable after the
    contract row is archived, corrected, or moved into whichever module ends up
    owning commercial contracts — and it must not become deletable by deleting
    that row.
    """
    column = models.Allocation.__table__.c["contract_ref"]
    assert column.foreign_keys == set()
    assert "contract" not in _migration_source().split("Revises")[1].lower() or (
        "ForeignKeyConstraint" not in _migration_source().split("contract_ref")[1][:400]
    )


def test_the_only_foreign_key_is_within_this_module() -> None:
    for table in (models.Allocation.__table__, models.AllocationEntry.__table__):
        for fk in table.foreign_keys:
            assert fk.column.table.schema == module_schema("ealloc"), fk


def test_the_module_imports_no_assembly_and_no_sibling_module() -> None:
    forbidden = (
        "app.",
        "vendor_cp",
        "dotmac_ticketing",
        "dotmac_template_studio",
        "dotmac_release_catalog",
    )
    for path in MODULE_ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(forbidden), f"{path.name}: {name}"


def test_the_package_depends_on_the_kernel_and_nothing_else_dotmac() -> None:
    manifest = tomllib.loads(
        (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    deps = manifest["tool"]["poetry"]["dependencies"]
    assert {name for name in deps if name.startswith("dotmac")} == {"dotmac-kernel"}


# ── Validation cannot be switched off ────────────────────────────────────────


def test_the_catalogue_port_is_owned_by_this_module_not_the_kernel() -> None:
    """ADR-0017's moratorium holds until the kernel lineage runs in production.
    A module-owned port needs no kernel facility and no exception."""
    assert ports.CapabilityCatalogueReader.__module__.startswith(
        "dotmac_entitlement_allocation"
    )


def test_the_reader_protocol_raises_rather_than_returning_a_verdict() -> None:
    """A boolean invites `if not declared: log_and_continue`. The whole point is
    that an undeclared code stops the write."""
    signature = inspect.signature(ports.CapabilityCatalogueReader.require_declared)
    assert signature.return_annotation in (None, "None")
    assert set(signature.parameters) == {"self", "product_code", "capability_code"}


def test_no_public_function_offers_a_trusted_caller_escape() -> None:
    import dotmac_entitlement_allocation as package

    escapes = {"validated", "skip_validation", "trusted", "unchecked", "force"}
    for name in package.__all__:
        member = getattr(package, name)
        if not callable(member) or isinstance(member, type):
            continue
        parameters = set(inspect.signature(member).parameters)
        assert parameters & escapes == set(), f"{name} accepts {parameters & escapes}"


def test_the_module_never_reaches_for_the_process_capability_catalogue() -> None:
    """`active_capabilities()` describes the modules installed in the process
    doing the asking, not the ones declared by the TARGET application. Using it
    would validate against the wrong product's manifest — and it would look
    correct in every vendor-CP test, because there the two sets overlap."""
    for path in MODULE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            referenced = (
                node.id
                if isinstance(node, ast.Name)
                else node.attr
                if isinstance(node, ast.Attribute)
                else None
            )
            assert referenced != "active_capabilities", path.name
            if isinstance(node, ast.ImportFrom):
                imported = {alias.name for alias in node.names}
                assert "active_capabilities" not in imported, path.name


# ── Typed contracts (the fleet-wide standard) ────────────────────────────────


def test_every_public_value_object_is_frozen_and_slotted() -> None:
    """Deeply immutable, not merely annotated.

    A mutable contract type lets a consumer change what it was handed and pass
    it on, so two owners disagree about a value neither of them wrote. Frozen
    plus slots also refuses an attribute nobody declared, which is how a typo
    becomes a silent second field.
    """
    import dataclasses

    import dotmac_entitlement_allocation as package

    # Only types this package DEFINES. `module` is the kernel's ModuleManifest
    # — re-asserting its shape here would test the kernel's contract from the
    # wrong repository, and fail for reasons that are not this module's.
    value_objects = [
        getattr(package, name)
        for name in package.__all__
        if dataclasses.is_dataclass(getattr(package, name))
        and getattr(package, name).__module__.startswith(
            "dotmac_entitlement_allocation"
        )
    ]
    assert value_objects, "no public dataclasses found — the surface moved"
    for cls in value_objects:
        params = cls.__dataclass_params__
        assert params.frozen, f"{cls.__name__} is not frozen"
        assert getattr(cls, "__slots__", None) is not None, f"{cls.__name__} slotless"


def test_no_public_contract_carries_a_mutable_collection() -> None:
    """A `list` or `dict` field is a hole in immutability: the dataclass is
    frozen, the list inside it is not."""
    import dataclasses
    import typing

    import dotmac_entitlement_allocation as package

    for name in package.__all__:
        member = getattr(package, name)
        if not dataclasses.is_dataclass(member) or not member.__module__.startswith(
            "dotmac_entitlement_allocation"
        ):
            continue
        hints = typing.get_type_hints(member)
        for field in dataclasses.fields(member):
            annotation = hints[field.name]
            origin = typing.get_origin(annotation) or annotation
            assert origin not in (list, dict, set), (
                f"{member.__name__}.{field.name} is {annotation!r}; "
                "use a tuple or a frozen value object"
            )


def test_every_public_callable_is_fully_annotated() -> None:
    """Including the return. An unannotated public function is a contract the
    consumer's type-checker cannot see, which is what `py.typed` promises it
    can."""
    import inspect

    import dotmac_entitlement_allocation as package

    for name in package.__all__:
        member = getattr(package, name)
        if not inspect.isfunction(member):
            continue
        signature = inspect.signature(member)
        assert signature.return_annotation is not inspect.Signature.empty, name
        for parameter in signature.parameters.values():
            assert (
                parameter.annotation is not inspect.Parameter.empty
            ), f"{name}({parameter.name})"


def test_the_status_vocabulary_has_exactly_one_definition() -> None:
    """Shared by persistence and by every owner reading the contract. Two
    definitions drift, and the drift shows up as a row nobody can classify."""
    from dotmac_entitlement_allocation import AllocationStatus

    assert issubclass(AllocationStatus, str)
    assert [member.value for member in AllocationStatus] == ["staged"]
    # The model's default is the SAME object, not a parallel literal.
    default = models.Allocation.__table__.c["status"].default.arg
    assert default == AllocationStatus.STAGED.value


def test_the_package_ships_a_pep561_marker() -> None:
    """Without it a consuming assembly's type-checker treats every one of these
    contracts as `Any`."""
    assert (MODULE_ROOT / "py.typed").is_file()


# ── Platform catalog, not tenant-scoped ──────────────────────────────────────


@pytest.mark.parametrize("model", [models.Allocation, models.AllocationEntry])
def test_no_tenant_column(model: type) -> None:
    assert "tenant_id" not in {c.name for c in model.__table__.columns}


@pytest.mark.parametrize("model", [models.Allocation, models.AllocationEntry])
def test_every_table_is_bound_to_the_module_schema(model: type) -> None:
    assert model.__table__.schema == module_schema("ealloc")


def test_the_online_role_cannot_rewrite_a_staged_allocation() -> None:
    source = _migration_source()
    for table in module.tables:
        assert f"GRANT SELECT, INSERT ON mod_ealloc.{table} TO platform_api;" in source
    for verb in ("UPDATE", "DELETE"):
        for table in module.tables:
            assert f"{verb} ON mod_ealloc.{table} TO platform_api" not in source


def test_a_staged_allocation_cannot_be_appended_to() -> None:
    """An INSERT-only grant makes the PARENT immutable and leaves the CHILD
    appendable — staging needs INSERT. The trigger is what closes it."""
    source = _migration_source()
    assert "CREATE TRIGGER refuse_late_entry" in source
    assert "BEFORE INSERT ON mod_ealloc.allocation_entries" in source
    # The seal is explicit. Transaction-identity inference cannot work here: the
    # kernel's at-most-once owner runs inside `conflict_savepoint`, and a
    # SAVEPOINT is a subtransaction whose writes carry a subtransaction xid.
    assert "sealed" in source
    assert "CREATE TRIGGER seal_is_one_way" in source
    assert (
        "GRANT UPDATE (sealed, updated_at) ON mod_ealloc.allocations TO platform_api;"
        in source
    )
    # No BUSINESS column is grantable to the online role.
    for column in ("product_code", "customer_ref", "content_hash", "contract_ref"):
        assert f"GRANT UPDATE ({column}" not in source


def test_the_quantity_floor_is_a_database_constraint() -> None:
    """The service checks it; the service cannot police a path that never calls
    it."""
    assert "ck_allocation_entries_quantity" in _migration_source()
    assert "quantity > 0" in _migration_source()


def test_the_claim_fingerprint_is_stored_on_the_allocation() -> None:
    """Not only in the idempotency record: those have a retention policy and
    allocations do not, so a purge must not turn a conflict back into a silent
    replay."""
    assert "snapshot_fingerprint" in {
        c.name for c in models.Allocation.__table__.columns
    }
    assert "snapshot_fingerprint" in _migration_source()


def test_no_path_returns_before_the_at_most_once_owner_is_consulted() -> None:
    """Every call must present its delivery key, including an activation replay.

    The defect this guards: a short-circuit that returned an existing allocation
    before reaching `execute_once_platform`, letting a delivery key be spent
    without being recorded. The resolution of an existing activation therefore
    belongs INSIDE the operation, not ahead of it.
    """
    tree = ast.parse((MODULE_ROOT / "service.py").read_text(encoding="utf-8"))
    stage = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "stage_allocation"
    )
    # `_existing` may be called only from within the nested operation, never
    # from `stage_allocation`'s own body.
    top_level_calls = {
        child.func.id
        for statement in stage.body
        if not isinstance(statement, ast.FunctionDef)
        for child in ast.walk(statement)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    assert "_existing" not in top_level_calls, (
        "stage_allocation resolves an existing activation before entering the "
        "at-most-once owner; the delivery key would go unrecorded"
    )
    assert "_replay_or_conflict" not in top_level_calls


def test_both_consumers_of_an_allocation_check_the_seal() -> None:
    """An unsealed row is an incomplete write, not a fact. Replay resolution and
    `allocation_product` both fail closed on one."""
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert source.count("IncompleteAllocationError") >= 3  # import + two raises
    assert "not row.sealed" in source
    assert "not sealed" in source


def test_the_module_uses_the_kernel_at_most_once_owner() -> None:
    """ADR-0014 gives at-most-once ONE owner. A module rolling its own would be
    the second implementation the ADR exists to prevent — and the source's
    idempotency behaviour would have been silently dropped in extraction."""
    service_source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert "execute_once_platform" in service_source
    assert "write_platform_audit_event" in service_source


def test_the_module_stays_importable_without_a_database() -> None:
    """`dotmac_kernel.idempotency` reaches `dotmac_kernel.db`, which builds the
    engine at import. A module-scope import would make this package
    unimportable without a DATABASE_URL and break the import-safety a migration
    gate and an offline type-check rely on."""
    tree = ast.parse((MODULE_ROOT / "service.py").read_text(encoding="utf-8"))
    module_level = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "dotmac_kernel.idempotency" not in module_level


def test_the_checked_in_docs_do_not_contradict_the_migration() -> None:
    """Documentation drift is a defect, not a cosmetic problem.

    The CHANGELOG described a mechanism the code no longer has (`age(xmin)`) and
    a grant that is no longer accurate ("SELECT/INSERT only"). A reader trusting
    either would reason about immutability from a model that does not exist —
    and the second would hide the column-level UPDATE entirely.
    """
    changelog = (PACKAGE_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
    models_doc = (MODULE_ROOT / "models.py").read_text(encoding="utf-8")
    migration = _migration_source()

    # The abandoned mechanism may be named as HISTORY, never as behaviour.
    for text_body, label in ((changelog, "CHANGELOG"), (readme, "README")):
        if "age(xmin)" in text_body:
            assert "earlier revision" in text_body or "rejected legitimate" in (
                text_body
            ), f"{label} presents age(xmin) as current behaviour"

    # Anywhere the grant is described, the column-level UPDATE is part of it.
    for text_body, label in (
        (changelog, "CHANGELOG"),
        (models_doc, "models.py"),
        (migration, "migration"),
    ):
        assert "SELECT and INSERT only" not in text_body, (
            f"{label} still claims SELECT/INSERT only; the online role also "
            "holds a column-level UPDATE on the seal"
        )
        assert "sealed" in text_body, f"{label} omits the seal"


def test_the_data_plane_role_is_revoked_from_every_table() -> None:
    """Ruling C4: the data plane is the only writer of its own grants, and
    learns what it may write from a signed envelope — never by reading here."""
    source = _migration_source()
    for table in module.tables:
        assert f"REVOKE ALL ON mod_ealloc.{table} FROM app_user;" in source


def test_the_product_a_capability_was_validated_against_is_persisted() -> None:
    """Without it, an allocation validated against product A can be issued as a
    licence for product B and every code still resolves — in a different
    catalogue, with nothing recording the swap."""
    assert "product_code" in {c.name for c in models.Allocation.__table__.columns}
    assert "product_code" in _migration_source()


# ── The assembly may not install it ──────────────────────────────────────────


def test_the_assembly_cannot_install_this_vendor_only_module() -> None:
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wanted = "The assembly must not install vendor-only modules"
    contracts = manifest["tool"]["importlinter"]["contracts"]
    vendor_only = [c for c in contracts if c["name"] == wanted]
    assert vendor_only, "the vendor-only installation contract is missing"
    assert "dotmac_entitlement_allocation" in vendor_only[0]["forbidden_modules"]


def test_the_extraction_dossier_records_the_single_qualifying_source() -> None:
    dossier = tomllib.loads(
        (PACKAGE_ROOT / "EXTRACTION.toml").read_text(encoding="utf-8")
    )
    assert dossier["source_mode"] == "product-first"
    assert dossier["status"] == "audit-complete"
    assert dossier["contract_consumers"] == []
    assert "dotmac_vendor_control_plane" in dossier["candidate_consumers"]
    assert {"dotmac_erp", "dotmac_sub"} <= set(dossier["source_repositories"])
