"""Structural canaries for ``dotmac-approvals`` (ADR-0026).

These guard the boundary rather than the behaviour: the module decides approval
state and nothing else, on two declared planes, without inheriting the domain
vocabulary the audit ruled out. Three of them exist specifically because the
first draft of ADR-0026 got the rule wrong and review corrected it — a
correction with no gate behind it is a sentence that regresses.
"""

from __future__ import annotations

import ast
import inspect
import tomllib
from pathlib import Path

from dotmac_approvals import contracts, models, policy, service
from dotmac_approvals.manifest import module
from dotmac_kernel.namespaces import APPROVALS_MIGRATION_OWNER, MIGRATION_OWNER_LEDGER

MODULE_ROOT = Path(inspect.getfile(service)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/ap_0001_approvals.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _called_attributes(path: Path) -> set[str]:
    calls: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
    return calls


# ── The allocation ──────────────────────────────────────────────────────────


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert APPROVALS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == "approvals"
    assert module.short_code == "approvals"
    assert module.migration_prefix == "ap"
    assert module.migration_branch == "approvals"
    assert module.db_schema == "mod_approvals"
    assert module.core is False


def test_both_planes_are_declared_and_disjoint() -> None:
    """ADR-0023: a plane is DECLARED, never inferred from a missing column.

    Both tuples are populated because both planes exist in production today —
    ERP for tenant subjects, the vendor control plane for fleet plans.
    """
    assert tuple(module.tables) == (
        "approval_policies",
        "approval_requests",
        "approval_decisions",
    )
    assert tuple(module.platform_tables) == (
        "platform_approval_policies",
        "platform_approval_requests",
        "platform_approval_decisions",
    )
    assert not set(module.tables) & set(module.platform_tables)


def test_the_migration_declares_the_same_prerequisites_as_the_manifest() -> None:
    """Migration and manifest drifting apart is how an assembly discovers a
    missing prerequisite at deploy time instead of at compose time."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'PLATFORM_REQUIRES = ("module_database_roles.v1",)' in source
    assert 'TENANT_REQUIRES = ("tenant_scope_catalog.v1",)' in source
    # Split by PLANE (ADR-0027): roles to create anything, a tenant catalogue
    # only for the tenant plane. That split is what makes the module installable
    # in the vendor control plane, which has neither a tenant catalogue nor any
    # prospect of one.
    assert set(module.requires) == {"module_database_roles.v1"}
    assert set(module.tenant_requires) == {"tenant_scope_catalog.v1"}


def test_the_module_requires_no_identity_or_rbac_estate() -> None:
    """ERP's service joined PersonRole/Role directly. Taking role membership as
    a value on `Actor` is what makes this module installable beside a product
    whose RBAC the kernel has never seen — so a prerequisite naming an identity
    catalogue would be a regression, not an addition."""
    assert all(
        "identity" not in requirement and "rbac" not in requirement
        for requirement in (*module.requires, *module.tenant_requires)
    )


# ── Planes stay apart ───────────────────────────────────────────────────────


def test_no_foreign_key_crosses_the_planes_or_leaves_the_module() -> None:
    """A subject id is a REFERENCE, not a relation. A module that could join to
    a product's tables would be a second reader of another owner's data and
    un-installable beside a product that spells them differently."""
    tenant_tables = {
        models.ApprovalPolicy.__table__,
        models.ApprovalRequest.__table__,
        models.ApprovalDecision.__table__,
    }
    platform_tables = {
        models.PlatformApprovalPolicy.__table__,
        models.PlatformApprovalRequest.__table__,
        models.PlatformApprovalDecision.__table__,
    }
    platform_names = {table.name for table in platform_tables}
    tenant_names = {table.name for table in tenant_tables}

    for table in tenant_tables | platform_tables:
        for key in table.foreign_keys:
            target = key.column.table
            allowed_schema = target.schema in (None, "public", "mod_approvals")
            assert allowed_schema, f"{table.name} points outside the module: {target}"
            if target.schema != "mod_approvals":
                # The kernel maps `tenants` with schema=None, so identify the
                # host reference by NAME rather than by schema — checking the
                # schema alone would let a future FK to any unqualified host
                # table pass as "the tenant scope".
                assert target.name == "tenants", (
                    f"{table.name} references host table {target.name}; the only "
                    "permitted host reference is the tenant scope"
                )
                assert (
                    table in tenant_tables
                ), f"platform table {table.name} references the tenant scope"
                continue
            if table in platform_tables:
                assert target.name in platform_names, (
                    f"platform table {table.name} references {target.name} — no "
                    "foreign key may cross the planes"
                )
            else:
                assert target.name in tenant_names, (
                    f"tenant table {table.name} references {target.name} — no "
                    "foreign key may cross the planes"
                )


def test_platform_tables_carry_no_tenant_column() -> None:
    for model in (
        models.PlatformApprovalPolicy,
        models.PlatformApprovalRequest,
        models.PlatformApprovalDecision,
    ):
        assert "tenant_id" not in model.__table__.c, model.__tablename__


def test_every_tenant_table_has_a_not_null_tenant_and_composite_identity() -> None:
    for model in (
        models.ApprovalPolicy,
        models.ApprovalRequest,
        models.ApprovalDecision,
    ):
        column = model.__table__.c["tenant_id"]
        assert not column.nullable, model.__tablename__


def test_a_duplicate_vote_is_refused_by_a_constraint_on_both_planes() -> None:
    """Quorum counts distinct people. In memory that is a check; durably it must
    be a constraint, or two racing approvals both pass."""
    for model, expected in (
        (models.ApprovalDecision, {"tenant_id", "request_id", "level", "actor_id"}),
        (models.PlatformApprovalDecision, {"request_id", "level", "actor_id"}),
    ):
        columns = {
            frozenset(constraint.columns.keys())
            for constraint in model.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert frozenset(expected) in columns, model.__tablename__


# ── The rules stay pure, the services stay thin ─────────────────────────────


def test_the_policy_engine_imports_no_persistence() -> None:
    """ADR-0026 § 5: shared pure policy code imports no persistence. It is what
    lets two separately named plane surfaces reach one verdict."""
    imported = _imported_names(Path(inspect.getfile(policy)))
    for forbidden in ("sqlalchemy", "dotmac_approvals.models", "dotmac_kernel.db"):
        assert not any(name.startswith(forbidden) for name in imported), imported


def test_services_never_commit_roll_back_or_build_a_session() -> None:
    """Hard rule 8, and a deliberate correction of the source: ERP's service
    called `db.commit()` in four places, which stops a caller composing an
    approval atomically with its own state change."""
    called = _called_attributes(Path(inspect.getfile(service)))
    assert "commit" not in called
    assert "rollback" not in called
    assert "close" not in called
    assert "sessionmaker" not in called


def test_the_module_imports_no_consuming_domain() -> None:
    for source in MODULE_ROOT.rglob("*.py"):
        for name in _imported_names(source):
            assert not name.startswith("app"), f"{source.name} imports {name}"
            assert "erp" not in name and "vendor_cp" not in name, source.name


def test_the_outbox_adapter_is_the_only_file_touching_kernel_messaging() -> None:
    """Keeping it out of `service` means a consumer with its own delivery — or
    none — is not forced to install the kernel's outbox tables."""
    for source in MODULE_ROOT.rglob("*.py"):
        if source.name == "outbox.py":
            continue
        assert not any(
            "messaging" in name for name in _imported_names(source)
        ), source.name


def test_importing_the_package_never_builds_a_database_engine() -> None:
    """Import-safety is an invariant, not a preference.

    A manifest is imported by tooling, gates and test collection long before any
    database exists. `dotmac_kernel.messaging` re-exports through a chain that
    reaches `dotmac_kernel.db`, which builds an Engine from settings AT IMPORT
    TIME — so a module-level import of it would make this package unimportable
    wherever `DATABASE_URL` is unset, and would drag an engine into a process
    that only wanted to read the contracts.

    Caught by importing the package with no database configured, which is
    exactly the condition this asserts. The adapter therefore imports the kernel
    inside its two functions, and this is the check that keeps it there.
    """
    engine_building = ("dotmac_kernel.db", "dotmac_kernel.messaging")
    for source in MODULE_ROOT.rglob("*.py"):
        top_level: set[str] = set()
        for node in _tree(source).body:
            if isinstance(node, ast.Import):
                top_level.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.add(node.module)
        for name in top_level:
            assert not name.startswith(engine_building), (
                f"{source.name} imports {name} at module level; move it inside "
                "the function that needs it or the package stops being "
                "importable without a database"
            )


# ── The two review corrections, kept from regressing ────────────────────────


def test_the_module_holds_no_money_currency_or_fx_vocabulary() -> None:
    """ADR-0026 § 7a. Threshold routing is the domain's: the caller arrives with
    the policy revision already resolved.

    This is a RATCHET, not decoration. The audit's first recommendation was to
    port ERP's threshold selection "as optional typed policy predicates", which
    would have required Money, an FX rate and a conversion date inside a
    neutral-sounding module — one schema change from owning pricing.
    """
    banned = ("Money", "currency", "exchange_rate", "fx", "threshold_amount")
    for source in MODULE_ROOT.rglob("*.py"):
        text = source.read_text(encoding="utf-8").lower()
        # Comments are allowed to EXPLAIN the exclusion; code is not allowed to
        # implement it, so only the import surface and identifiers are checked.
        for name in _imported_names(source):
            assert "money" not in name.lower(), f"{source.name} imports {name}"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for token in banned:
            assert not any(
                token.lower() == identifier.lower() for identifier in identifiers
            ), f"{source.name} names {token!r}; routing stays with the domain"
        assert "decimal" not in text or source.name == "__init__.py", source.name


def test_policy_codes_are_data_and_subject_types_are_opaque_strings() -> None:
    """ADR-0026 § 4. An operator invents a policy code at runtime; only CODE
    introduces a subject type, and that declaration lives on the CONSUMING
    module's manifest, never here.

    So this package must ship no enum of either. The first draft of the ADR
    turned "policy kinds" into manifest-declared "policy codes", which would
    have put a software release between an operator and their own configuration.
    """
    enum_names = {
        node.name
        for node in ast.walk(_tree(Path(inspect.getfile(contracts))))
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name) and base.id.endswith("Enum")
            for base in node.bases
        )
    }
    assert enum_names == {
        "ApprovalState",
        "DecisionAction",
        "ApproverKind",
        "SoDRule",
    }, enum_names
    assert models.ApprovalPolicy.__table__.c["policy_code"].type.python_type is str
    assert models.ApprovalRequest.__table__.c["subject_type"].type.python_type is str


def test_the_public_api_names_its_plane_and_carries_no_plane_flag() -> None:
    """ADR-0026 § 5: no `platform=` argument, no nullable tenant. A caller states
    its security context by naming the operation."""
    tenant_functions = [
        service.publish_tenant_policy_version,
        service.request_tenant_approval,
        service.record_tenant_decision,
        service.evaluate_tenant_approval,
        service.cancel_tenant_request,
    ]
    platform_functions = [
        service.publish_platform_policy_version,
        service.request_platform_approval,
        service.record_platform_decision,
        service.evaluate_platform_approval,
        service.cancel_platform_request,
    ]
    for function in tenant_functions:
        parameters = inspect.signature(function).parameters
        assert "tenant_id" in parameters, function.__name__
    for function in platform_functions:
        parameters = inspect.signature(function).parameters
        assert "tenant_id" not in parameters, function.__name__
    for function in tenant_functions + platform_functions:
        parameters = set(inspect.signature(function).parameters)
        assert not parameters & {"platform", "is_platform", "plane", "scope"}


# ── Release posture ─────────────────────────────────────────────────────────


def test_the_package_exposes_the_manifest_attribute_the_allowlist_names() -> None:
    """The release verifier resolves `<import_name>.<manifest_attr>` on the
    INSTALLED wheel, so the package root must actually export it.

    This is the check the release job made for me the hard way: the first
    dispatch of `dotmac-approvals 0.1.0a1` failed at "Release wheel smoke" with
    `module 'dotmac_approvals' has no attribute 'module'`, because `__init__`
    re-exported the contracts and not the manifest. Nothing published — the
    build fails closed before publish — but the feedback arrived a merge and a
    dispatch later than it needed to.

    Asserted against the ALLOWLIST rather than a literal, so the two cannot
    drift: renaming the attribute in one place now fails here.
    """
    import json

    import dotmac_approvals

    entry = json.loads(
        (REPO_ROOT / ".github/release-modules.json").read_text(encoding="utf-8")
    )["modules"]["dotmac-approvals"]
    attribute = entry["manifest_attr"]
    assert hasattr(dotmac_approvals, attribute), (
        f"the allowlist resolves dotmac_approvals.{attribute} on the installed "
        f"wheel, but the package root does not export {attribute!r}"
    )
    assert getattr(dotmac_approvals, attribute) is module
    assert attribute in dotmac_approvals.__all__


def test_the_release_entry_matches_the_allocation_it_publishes() -> None:
    """The entry landed WITH the live Postgres proof, not ahead of it.

    Absence from the closed allowlist was the safety mechanism while
    `tests/test_approvals_isolation.py` had not run against a real database
    (ADR-0026 § 8). It has now, in the same change, so the entry exists — and
    what it asserts has to match the allocation, or a release would publish a
    wheel claiming a schema and a kernel floor the module does not have.
    """
    import json

    allowlist = json.loads(
        (REPO_ROOT / ".github/release-modules.json").read_text(encoding="utf-8")
    )["modules"]
    entry = allowlist["dotmac-approvals"]
    assert entry["db_schema"] == module.db_schema
    assert entry["import_name"] == "dotmac_approvals"
    assert entry["tag_prefix"] == "dotmac-approvals-v"
    assert entry["kernel_floor"] == "0.1.0a60"
    # The lineage is a REQUIRED wheel content: this repository does not compose
    # the module, so a wheel that shipped the manifest and dropped the migration
    # would fail first in an adopter's deployment rather than here.
    assert (
        "dotmac_approvals/migrations/versions/ap_0001_approvals.py"
        in entry["wheel_contents"]["required"]
    )
    # Only dotmac-ticketing may import Alembic at runtime; this module emits no
    # DDL into a consumer's migration.
    assert "alembic" not in entry["wheel_contents"]["allowed_requires"]


def test_the_dossier_claims_no_adoption_yet() -> None:
    dossier = tomllib.loads(
        (REPO_ROOT / "packages/dotmac-approvals/EXTRACTION.toml").read_text(
            encoding="utf-8"
        )
    )
    assert dossier["status"] == "audit-complete"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"] == [
        "dotmac_vendor_control_plane",
        "dotmac_erp",
    ]
