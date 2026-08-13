"""The application-directory module's structural contract.

What this file protects is the property the module exists to keep true:
**a directory is inventory, not an access control list.** ADR-0021 §3 makes
directory visibility distinct from authorization, and a table is where that
distinction would be lost first — one `granted_role_codes` column and the
Workspace has quietly become an identity provider for its siblings.

Everything else here guards the mechanics that keep the module shareable: the
ledger allocation, schema qualification, tenancy, and the absence of a surface
it has not earned.

Behaviour — digests, transitions, reconciliation outcomes — lives in
`tests/unit/test_application_directory.py`, in keeping with the repo's split.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from dotmac_application_directory import descriptor, lifecycle, models
from dotmac_application_directory.manifest import module
from dotmac_kernel.namespaces import (
    APPLICATION_DIRECTORY_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    module_schema,
)

MODULE_ROOT = Path(inspect.getfile(lifecycle)).parent
MIGRATIONS = MODULE_ROOT / "migrations" / "versions"
REPO_ROOT = Path(__file__).resolve().parents[2]

# The vocabulary a column name must not contain. Substrings rather than exact
# names, because the failure this guards against arrives as `granted_role_codes`
# or `member_id` rather than as a column called `authorization`.
_AUTHORIZATION_VOCABULARY = (
    "grant",
    "role",
    "permission",
    "member",
    "person",
    "party",
    "subject",
    "principal",
    "user",
    "actor",
)

# No exemptions. 0.1.0a1 had one — `role_catalogue_digest`, a content address of
# the menu an application published — and deferring the whole role-catalogue
# surface (nothing consumed it) removed the need for it. An empty exemption set
# is the strongest form of this rule, and re-introducing one requires arguing
# for it here.
_ROLE_COLUMN_EXEMPTIONS: frozenset[str] = frozenset()


# ── The property the module exists to protect ────────────────────────────────


def test_the_directory_holds_no_authorization_column() -> None:
    """No column may name a person, a role, or a grant.

    This is the executable half of ADR-0021 §3. The directory records that a
    tenant HAS an application; it must never record that someone MAY USE one.
    Desired allocation is `dotmac-application-access`'s domain, and effective
    grants belong to the target application and to nothing else.

    If this test fails, the fix is almost never to widen the exemption set.
    """
    offenders: list[str] = []
    for column in models.ApplicationBinding.__table__.columns:
        if column.name in _ROLE_COLUMN_EXEMPTIONS:
            continue
        lowered = column.name.lower()
        for term in _AUTHORIZATION_VOCABULARY:
            if term in lowered:
                offenders.append(f"{column.name} (matches {term!r})")
    assert not offenders, (
        "the application directory has acquired authorization columns: "
        f"{offenders}. Directory visibility is not authorization (ADR-0021 §3) "
        "— desired allocation belongs to dotmac-application-access, and "
        "effective grants belong to the target application."
    )


def test_the_exemption_set_is_empty_or_load_bearing() -> None:
    """Every exemption must name a column that actually exists.

    ADR-0018's sensitivity requirement: an exemption for a column nobody
    declares is an exemption nobody reviews, and it would silently pre-authorise
    whatever later claimed that name. Empty at 0.1.0a1, which is the strongest
    form of the rule.
    """
    declared = {column.name for column in models.ApplicationBinding.__table__.columns}
    assert (
        _ROLE_COLUMN_EXEMPTIONS <= declared
    ), f"exemption names no real column: {_ROLE_COLUMN_EXEMPTIONS - declared}"


def test_the_detector_catches_a_planted_column() -> None:
    """Prove the sweep above can fail (ADR-0018's sensitivity proof).

    A guard that has never been shown to fire is a guard nobody knows is wired.
    """
    planted = ("granted_role_codes", "member_id", "principal_ref")
    for name in planted:
        assert any(
            term in name.lower() for term in _AUTHORIZATION_VOCABULARY
        ), f"the vocabulary would not catch {name!r}"


def test_no_launchability_predicate_is_named_for_authorization() -> None:
    """`is_launchable`, never `is_allowed`.

    A predicate named for authorization is a predicate someone will eventually
    use for authorization, from a call site that never read this ADR.
    """
    forbidden = ("allowed", "permitted", "granted", "authorized", "authorised")
    # `allowed_transitions` describes STATE-MACHINE legality — which moves this
    # row may make — and says nothing about a person. Exempted by exact name
    # rather than by dropping "allowed" from the sweep, so a future
    # `allowed_members` still fails.
    exempt = {"allowed_transitions"}
    assert exempt <= set(lifecycle.__all__), "exemption names nothing real"
    offenders = [
        name
        for name in lifecycle.__all__
        if name not in exempt and any(term in name.lower() for term in forbidden)
    ]
    assert not offenders, f"lifecycle exports authorization-shaped names: {offenders}"
    assert any(
        term in "allowed_members" for term in forbidden
    ), "the sweep would not catch a genuinely authorization-shaped export"


# ── The ledger allocation ────────────────────────────────────────────────────


def test_the_manifest_matches_its_ledger_row() -> None:
    """Declaration and allocation agree, or the composition is refused at boot."""
    assert APPLICATION_DIRECTORY_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.short_code == "appdir"
    assert module.migration_prefix == APPLICATION_DIRECTORY_MIGRATION_OWNER.prefix
    assert module.migration_branch == APPLICATION_DIRECTORY_MIGRATION_OWNER.branch_label
    assert module.db_schema == APPLICATION_DIRECTORY_MIGRATION_OWNER.db_schema
    assert module.db_schema == module_schema("appdir")


def test_the_module_declares_exactly_the_tables_it_owns() -> None:
    assert tuple(module.tables) == ("application_bindings",)
    assert models.ApplicationBinding.__tablename__ == "application_bindings"


def test_the_module_is_not_core() -> None:
    """Most deployments have no portfolio; only a Workspace does."""
    assert module.core is False


def test_the_module_declares_no_surface_it_does_not_ship() -> None:
    """No routers means no capabilities, permissions or audit actions.

    Every such declaration exists to gate or annotate a route. Declaring one
    with no consumer is dead vocabulary that reads like a working gate — the
    failure ADR-0008's registries exist to prevent.
    """
    assert not module.api_routers
    assert not module.web_routers
    assert not module.nav
    assert not module.capabilities
    assert not module.permissions
    assert not module.audit_actions
    assert not module.setting_domains


# ── Schema qualification and tenancy ─────────────────────────────────────────


def test_every_table_is_bound_to_the_module_schema() -> None:
    """Fully qualified, never resolved through `search_path` — which is
    connection state a pooler or another module can change."""
    assert models.SCHEMA == "mod_appdir"
    assert models.ApplicationBinding.__table__.schema == "mod_appdir"


def test_the_binding_is_tenant_scoped_with_a_composite_reference_target() -> None:
    """Hard rule 11 at the model layer; the migration carries the RLS half."""
    table = models.ApplicationBinding.__table__
    assert table.c.tenant_id.nullable is False
    unique_columns = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("application_code", "instance_ref", "tenant_id") in unique_columns
    # The composite-FK target. A single-column reference would let one tenant's
    # row point at another tenant's binding the moment an id leaked.
    assert ("id", "tenant_id") in unique_columns


# ── The migration ────────────────────────────────────────────────────────────


def _migration_source() -> str:
    path = MIGRATIONS / "ad_0001_application_bindings.py"
    assert path.is_file(), f"lineage root missing: {path}"
    return path.read_text(encoding="utf-8")


def test_the_lineage_root_is_a_root_and_orders_by_declared_effects() -> None:
    """`down_revision` never crosses owners — that would splice two
    independently released lineages into one chain and make either
    un-releasable.

    AMENDED (ADR-0006 D1 amendment): this used to assert
    `depends_on == ("0001_initial_tenant_schema",)`. A module may not name a
    foreign revision, because that edge is true only in an assembly that runs
    the named lineage — ERP hosts `public.tenants` itself and can never run
    kernel 0001. The module declares the EFFECTS it needs and the assembly binds
    them to revisions it actually runs.
    """
    source = _migration_source()
    tree = ast.parse(source)
    assigned = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "ad_0001_application_bindings"
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("application_directory",)
    assert ast.literal_eval(assigned["REQUIRES"]) == (
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
    )
    # Asserted on the AST, not the text: the migration's comment names the old
    # revision to explain why it is no longer depended on, so a substring check
    # would match the explanation. The shape of the assignment is what matters.
    assert isinstance(assigned["depends_on"], ast.Call)
    assert getattr(assigned["depends_on"].func, "id", None) == "resolve_depends_on"


def test_the_revision_id_fits_alembics_column() -> None:
    """Alembic declares `alembic_version.version_num` as `String(32)`; an
    over-long id fails at `alembic upgrade` against a real database rather than
    at authoring time."""
    assert len("ad_0001_application_bindings") <= 32


@pytest.mark.parametrize(
    "statement",
    [
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "CREATE POLICY application_bindings_tenant_isolation",
        "app_current_tenant_id()",
    ],
)
def test_the_migration_installs_rls(statement: str) -> None:
    """FORCE matters: without it the table owner — which migrations run as —
    bypasses its own policy."""
    assert statement in _migration_source()


def test_the_migration_never_relies_on_search_path() -> None:
    """`search_path` is connection state a pooler, a psql session or another
    module can change, so every object is named schema-first.

    Reads the `op.execute` arguments through `ast.literal_eval` rather than
    grepping the file. The formatter splits long DDL into implicitly
    concatenated literals — `"ALTER TABLE mod_appdir.x " "ENABLE RLS;"` — which
    is ONE correct string to Python and two fragments to a text search.
    Evaluating the literal is what the database will see; anything else tests
    the formatter.
    """
    source = _migration_source()
    assert "schema=_SCHEMA" in source
    assert '_SCHEMA = "mod_appdir"' in source
    assert "search_path" not in source

    executed: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
        ):
            try:
                value = ast.literal_eval(node.args[0])
            except ValueError:  # a computed statement — see the next assertion
                executed.append("<computed>")
                continue
            executed.append(" ".join(str(value).split()))

    assert executed, "no op.execute statements found — did the file move?"
    # A computed statement is uninspectable by the composed gate, which reads
    # this file WITHOUT importing it. Fail closed on one.
    assert "<computed>" not in executed, "migration builds SQL dynamically"

    for statement in (
        "ALTER TABLE mod_appdir.application_bindings ENABLE ROW LEVEL SECURITY;",
        "ALTER TABLE mod_appdir.application_bindings FORCE ROW LEVEL SECURITY;",
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_appdir.application_bindings TO app_user;",
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_appdir.application_bindings TO platform_api;",
    ):
        assert statement in executed, f"missing or unqualified DDL: {statement}"

    # Every object named in every statement carries its schema.
    for statement in executed:
        if "application_bindings" in statement:
            assert (
                "mod_appdir.application_bindings" in statement
            ), f"unqualified table reference: {statement}"


# ── Independence ─────────────────────────────────────────────────────────────


def test_the_module_imports_no_assembly_and_no_other_module() -> None:
    """The import-linter contracts cover this repo-wide; this asserts it at the
    package so the module stays releasable on its own."""
    forbidden_roots = {"app", "dotmac_template_studio", "dotmac_ticketing"}
    for path in sorted(MODULE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden_roots, f"{path.name} imports {node.module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert (
                        root not in forbidden_roots
                    ), f"{path.name} imports {alias.name}"


def test_the_lineage_passes_the_composed_migration_gate() -> None:
    """`make migration-gate` reads the shipped `alembic.ini`, which deliberately
    omits this module — so the gate would never see `ad_0001` otherwise.

    Composed as a WORKSPACE composes it — the kernel's lineage plus this
    module's — rather than as the starter composes it. Passing the starter's
    other modules here would demand their version locations too, and would be
    testing a composition no deployment actually runs.
    """
    from dotmac_kernel.migrations.gate import run_gate

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    report = run_gate(
        [module],
        [
            REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions",
            REPO_ROOT / "alembic/versions",
            MIGRATIONS,
        ],
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
    )
    assert report.ok, f"composed gate violations: {report.violations}"


def test_the_descriptor_contract_is_owned_here_not_in_the_kernel() -> None:
    """ADR-0021 §4: the three application contracts are permanent module code,
    not temporary code awaiting kernel promotion. Only the generic
    signed-envelope mechanism is a promotion candidate."""
    assert descriptor.ApplicationDescriptor.__module__.startswith(
        "dotmac_application_directory"
    )
    with pytest.raises(ImportError):
        __import__("dotmac_kernel.applications")
