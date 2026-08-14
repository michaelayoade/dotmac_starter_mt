"""The ticketing module's structural contract.

What this file protects is the property that makes the module shareable at all:
**the status vocabulary is closed, and a product extends only the reason layer.**
Everything else here guards the mechanics that keep that true — the ledger
allocation, the schema qualification, and the absence of product-domain columns.

A note on what is deliberately NOT tested here: the behaviour of transitions and
reason validation lives in `tests/unit/test_ticketing_lifecycle.py`. This file is
static structure, in keeping with the repo's split.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest
from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    TICKETING_MIGRATION_OWNER,
    module_schema,
)
from dotmac_ticketing import lifecycle, linking, models
from dotmac_ticketing.manifest import module

MODULE_ROOT = Path(inspect.getfile(lifecycle)).parent
MIGRATIONS = MODULE_ROOT / "migrations" / "versions"
MIGRATION = MIGRATIONS / "tk_0001_tickets.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_SQL = (MIGRATIONS / "tk_0001_tickets.py").read_text(encoding="utf-8")

#: The migration's SQL with Python's adjacent-string-literal concatenation
#: undone and whitespace collapsed, so an assertion can name a statement the way
#: PostgreSQL receives it rather than the way `ruff format` happened to wrap it.
MIGRATION_STATEMENTS = re.sub(r"\s+", " ", re.sub(r'"\s*\n\s*"', "", MIGRATION_SQL))

_TENANT_MODELS = (models.TenantTicket, models.TenantTicketComment)
_PLATFORM_MODELS = (models.PlatformTicket, models.PlatformTicketComment)


# ── The closed vocabulary ────────────────────────────────────────────────────


def test_the_standard_status_vocabulary_is_exactly_nine_terms() -> None:
    """Pin the vocabulary so widening it is a visible, argued diff.

    This is the whole contract. A tenth status added casually is a term every
    consuming product inherits and must handle, and the pressure to add one
    always arrives as a single product's local need — which is what the reason
    layer is for.
    """
    assert [status.value for status in lifecycle.STANDARD_STATUSES] == [
        "new",
        "open",
        "pending",
        "waiting_on_customer",
        "on_hold",
        "resolved",
        "closed",
        "cancelled",
        "merged",
    ]


def test_every_status_has_a_lifecycle_class() -> None:
    """No status may exist that consumers cannot reason about generically."""
    for status in lifecycle.Status:
        assert isinstance(status.lifecycle_class, lifecycle.LifecycleClass)


def test_lifecycle_classes_are_exactly_five() -> None:
    """The class layer is the fixed point everything else keys off.

    Adding a sixth class means every `is_open`-style predicate in every consumer
    is potentially wrong until reviewed, which is precisely the cost this test
    exists to make visible.
    """
    assert [cls.value for cls in lifecycle.LifecycleClass] == [
        "open",
        "waiting",
        "resolved",
        "closed",
        "cancelled",
    ]


def test_no_isp_or_product_specific_term_leaked_into_the_vocabulary() -> None:
    """The module must not carry any product's domain language.

    Named terms rather than a heuristic: this is a regression guard against the
    specific failure the source audit found, where the source product's terms
    would have become the fleet's standard.
    """
    forbidden = {
        "lastmile_rerun",
        "site_under_construction",
        "pending_confirmation",
        "replied",
        "lower",
        "normal",
    }
    declared = {status.value for status in lifecycle.Status}
    declared |= {priority.value for priority in lifecycle.Priority}
    declared |= {channel.value for channel in lifecycle.Channel}
    leaked = declared & forbidden
    assert not leaked, (
        f"product-specific terms leaked into the shared vocabulary: {sorted(leaked)}. "
        "These belong in the reason layer, declared by the product that needs "
        "them — see docs/inventories/ticket-sources.md."
    )


def test_sla_clock_runs_only_while_actively_workable() -> None:
    """Waiting on someone else pauses the clock.

    Encoded as a test rather than left to the docstring because it is the one
    place this module knowingly diverges from `dotmac_sub`, and a future port of
    Sub's logic would otherwise quietly reintroduce the behaviour.
    """
    running = {s for s in lifecycle.Status if lifecycle.sla_clock_runs(s)}
    assert running == {lifecycle.Status.NEW, lifecycle.Status.OPEN}


def test_is_open_covers_open_and_waiting() -> None:
    open_states = {s for s in lifecycle.Status if lifecycle.is_open(s)}
    assert open_states == {
        lifecycle.Status.NEW,
        lifecycle.Status.OPEN,
        lifecycle.Status.PENDING,
        lifecycle.Status.WAITING_ON_CUSTOMER,
        lifecycle.Status.ON_HOLD,
    }


# ── Namespace and manifest ───────────────────────────────────────────────────


def test_the_manifest_matches_its_ledger_allocation_exactly() -> None:
    """A module cannot re-point its own schema; the ledger is the authority."""
    assert TICKETING_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == TICKETING_MIGRATION_OWNER.owner
    assert module.short_code == "tkt"
    assert module.migration_prefix == TICKETING_MIGRATION_OWNER.prefix
    assert module.migration_branch == TICKETING_MIGRATION_OWNER.branch_label
    assert module.db_schema == TICKETING_MIGRATION_OWNER.db_schema
    assert module.db_schema == module_schema("tkt")


def test_the_manifest_declares_only_tables_this_module_creates() -> None:
    """Product link tables are NOT this module's, and must not be declared.

    They live in the product's schema and lineage. Declaring them here would
    make the live-catalog gate assert ownership of a table this module never
    creates — wrong in the direction that hides a real problem.
    """
    assert set(module.tables) == {"tickets", "ticket_comments"}
    assert set(module.platform_tables) == {
        "platform_tickets",
        "platform_ticket_comments",
    }


def test_the_manifest_planes_match_the_models_that_implement_them() -> None:
    """The declaration is what the kernel gate enforces, so a manifest that
    drifts from the models silently audits the wrong tables."""
    assert set(module.tables) == set(models.TENANT_TABLES)
    assert set(module.platform_tables) == set(models.PLATFORM_TABLES)


def test_models_are_bound_to_the_allocated_schema() -> None:
    assert models.SCHEMA == module_schema("tkt")
    for model in _TENANT_MODELS + _PLATFORM_MODELS:
        assert model.__table__.schema == models.SCHEMA


# ── No product domain in the shared tables ───────────────────────────────────


@pytest.mark.parametrize(
    "forbidden",
    ["subscriber_id", "project_id", "customer_id", "lead_id", "licence_id", "site_id"],
)
def test_no_subject_column_leaked_onto_the_shared_ticket(forbidden: str) -> None:
    """Subjects are product-owned link tables, never columns here.

    A ticket has many subjects — Sub's has six, ERP's five — so a column per
    product would be both wrong and unbounded. Checked on BOTH planes: the
    platform plane is the one a vendor is most tempted to give a `deployment_id`.
    """
    for model in _TENANT_MODELS + _PLATFORM_MODELS:
        assert forbidden not in model.__table__.columns, model.__name__


def test_every_tenant_plane_table_is_tenant_scoped() -> None:
    for model in _TENANT_MODELS:
        tenant_id = model.__table__.columns.get("tenant_id")
        assert tenant_id is not None, f"{model.__name__} has no tenant_id"
        assert not tenant_id.nullable, f"{model.__name__}.tenant_id must be NOT NULL"


def test_comments_reference_their_ticket_compositely() -> None:
    """A bare ticket_id FK would let a comment cross tenants when an id leaks."""
    composite = [
        fk
        for fk in models.TenantTicketComment.__table__.foreign_key_constraints
        if {"tenant_id", "ticket_id"} == {c.name for c in fk.columns}
    ]
    assert composite, (
        "ticket_comments must reference tickets on (tenant_id, ticket_id), not "
        "on ticket_id alone"
    )


# ── The two planes cannot cross (ADR-0023) ───────────────────────────────────


def test_no_platform_plane_table_carries_a_tenant_column() -> None:
    """The whole point of the plane. A `tenant_id` here would assert that a
    control-plane fact belongs to one tenant of the data plane, which is false —
    and a nullable one, or a sentinel tenant, is the dodge ADR-0023 rejects."""
    for model in _PLATFORM_MODELS:
        assert "tenant_id" not in model.__table__.columns, (
            f"{model.__name__} carries a tenant_id — it is declared a PLATFORM "
            "table and the kernel's live-catalog gate will reject it"
        )


def test_platform_ticket_numbers_are_unique_control_plane_wide() -> None:
    """Not per tenant, because there is no tenant. The vendor runs one series."""
    uniques = {
        tuple(sorted(c.name for c in constraint.columns))
        for constraint in models.PlatformTicket.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("number",) in uniques


def test_no_foreign_key_crosses_the_two_planes() -> None:
    """They share a lifecycle, never a row.

    An FK is the one crossing the database itself would enforce and therefore
    permit — a tenant-scoped delete cascading into control-plane data, or a
    platform row whose visibility depends on a tenant predicate it has no column
    to satisfy. The kernel gate refuses this live; this catches it at the model
    layer, where it is cheaper to see.
    """
    tenant_tables = set(models.TENANT_TABLES)
    platform_tables = set(models.PLATFORM_TABLES)
    for model in _TENANT_MODELS + _PLATFORM_MODELS:
        own_plane = (
            platform_tables if model.__tablename__ in platform_tables else tenant_tables
        )
        for fk in model.__table__.foreign_key_constraints:
            for element in fk.elements:
                target = element.column.table
                if target.schema != models.SCHEMA:
                    continue  # a kernel table (e.g. public.tenants) — not a plane
                assert target.name in own_plane, (
                    f"{model.__tablename__}.{fk.name} references "
                    f"{target.name}, crossing the tenant/platform plane boundary"
                )


def test_the_planes_do_not_share_a_mapped_ancestor() -> None:
    """Column reuse is a mixin, never a shared mapped base.

    A common mapped ancestor would make a polymorphic query span both planes,
    which turns the separation back into a naming convention.
    """
    from dotmac_kernel.models import Base

    for tenant_model in _TENANT_MODELS:
        for platform_model in _PLATFORM_MODELS:
            shared = set(tenant_model.__mro__) & set(platform_model.__mro__)
            mapped = {
                cls for cls in shared if cls is not Base and hasattr(cls, "__table__")
            }
            assert not mapped, (
                f"{tenant_model.__name__} and {platform_model.__name__} share "
                f"mapped ancestor(s) {sorted(c.__name__ for c in mapped)}"
            )


def test_the_shared_engine_imports_no_persistence() -> None:
    """One behaviour, two planes: the lifecycle and vocabulary must stay pure.

    If either imported `models`, the "shared engine" claim would be false — the
    plane would have leaked into the layer whose whole job is to not know about
    it, and a product could not reuse the transition guards on the other plane.
    """
    from dotmac_ticketing import vocabulary

    for shared in (lifecycle, vocabulary):
        source = Path(inspect.getfile(shared)).read_text(encoding="utf-8")
        assert "dotmac_ticketing.models" not in source, (
            f"{shared.__name__} imports persistence — the lifecycle engine is "
            "shared by both planes and must not know which one it is on"
        )


def test_the_migration_grants_the_platform_plane_and_revokes_the_tenant_role() -> None:
    """On the platform plane the REVOKE *is* the isolation.

    An un-revoked platform table is exactly as exposed as a tenant table with no
    RLS policy, and reads just as safe — so this is the platform-side equivalent
    of the FORCE-RLS assertion above, not a nice-to-have.
    """
    for table in models.PLATFORM_TABLES:
        assert f"REVOKE ALL ON mod_tkt.{table} FROM app_user;" in MIGRATION_STATEMENTS
        for role in ("platform_api", "app_admin"):
            assert (
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tkt.{table} "
                f"TO {role};" in MIGRATION_STATEMENTS
            ), f"{table} is not granted to {role}"


def test_the_migration_puts_no_rls_on_the_platform_plane() -> None:
    """A policy there could only deny everything or nothing — it has no tenant
    column to test — so its presence would mean the plane was misunderstood."""
    for table in models.PLATFORM_TABLES:
        assert f"ALTER TABLE mod_tkt.{table} ENABLE ROW LEVEL SECURITY" not in (
            MIGRATION_SQL
        )
        assert f"{table}_tenant_isolation" not in MIGRATION_SQL


# ── The migration ────────────────────────────────────────────────────────────


def test_the_lineage_has_exactly_one_root_and_it_is_ours() -> None:
    revisions = sorted(p.name for p in MIGRATIONS.glob("tk_*.py"))
    assert revisions == ["tk_0001_tickets.py"]


def test_the_migration_orders_after_the_kernel_by_depends_on() -> None:
    """Cross-lineage ordering is `depends_on`, never `down_revision`.

    A `down_revision` across owners splices two independently released lineages
    into one chain and makes either un-releasable.
    """
    tree = ast.parse((MIGRATIONS / "tk_0001_tickets.py").read_text(encoding="utf-8"))
    assigned = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert isinstance(assigned["down_revision"], ast.Constant)
    assert assigned["down_revision"].value is None
    assert "depends_on" in assigned


def test_the_migration_forces_row_level_security_on_every_table() -> None:
    """ENABLE alone is bypassed by the table owner, which migrations run as."""
    sql = (MIGRATIONS / "tk_0001_tickets.py").read_text(encoding="utf-8")
    for table in ("tickets", "ticket_comments"):
        assert f"ALTER TABLE mod_tkt.{table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE mod_tkt.{table} FORCE ROW LEVEL SECURITY" in sql
        assert f"{table}_tenant_isolation" in sql


def test_the_migration_never_relies_on_search_path() -> None:
    """Every statement names its schema; `search_path` is mutable connection state."""
    sql = (MIGRATIONS / "tk_0001_tickets.py").read_text(encoding="utf-8")
    for statement in ("ALTER TABLE ", "CREATE POLICY ", "GRANT SELECT"):
        for line in sql.splitlines():
            if statement in line and "mod_tkt" not in line and "ON " not in line:
                pytest.fail(f"statement does not name its schema: {line.strip()}")


# ── The linking helper ───────────────────────────────────────────────────────


_LINK_HELPERS = (linking.link_tenant_subject, linking.link_platform_subject)


@pytest.mark.parametrize("helper", _LINK_HELPERS, ids=lambda h: h.__name__)
def test_link_helpers_require_an_explicit_on_delete_for_the_subject(helper) -> None:
    """Cascade-vs-restrict is product policy; a default would decide it silently."""
    subject = inspect.signature(helper).parameters["on_delete_subject"]
    assert subject.default is inspect.Parameter.empty
    assert subject.kind is inspect.Parameter.KEYWORD_ONLY


def test_link_subject_targets_the_allocated_schema() -> None:
    assert linking.MODULE_SCHEMA == module_schema("tkt")


@pytest.mark.parametrize("helper", _LINK_HELPERS, ids=lambda h: h.__name__)
def test_link_helpers_reject_a_name_whose_generated_index_would_overflow(
    helper,
) -> None:
    """PostgreSQL truncates identifiers at 63 chars, and truncation collides."""
    with pytest.raises(ValueError, match="too long|1..63"):
        helper(
            table_name="a" * 60,
            subject_table="subscribers",
            on_delete_subject="RESTRICT",
        )


def test_the_platform_link_helper_refuses_a_table_nobody_could_reach() -> None:
    """`platform_roles=()` emits the REVOKE and no GRANT.

    That table is fully isolated and fully useless — it passes every
    prohibition in the platform contract and fails at the first control-plane
    request. Refused at authoring time, since a migration is the worst place to
    discover it.
    """
    with pytest.raises(ValueError, match="at least one role"):
        linking.link_platform_subject(
            table_name="vcp_ticket_account",
            subject_table="vendor_accounts",
            on_delete_subject="RESTRICT",
            platform_roles=(),
        )


def test_there_is_no_single_link_helper_with_a_plane_flag() -> None:
    """SENSITIVITY PROOF for the two-helper design.

    A `link_subject(..., platform=False)` would have a default, and whichever
    value that default took is the plane a caller gets by forgetting to think —
    on one side a missing RLS policy, on the other a control-plane table the
    product data plane can read. The plane must be named, and the name is the
    function.
    """
    assert not hasattr(linking, "link_subject")
    for helper in _LINK_HELPERS:
        parameters = inspect.signature(helper).parameters
        assert "platform" not in parameters, helper.__name__
        assert "plane" not in parameters, helper.__name__


def test_the_tenant_link_helper_emits_isolation_and_the_platform_one_a_revoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What each helper actually writes into a product's migration.

    The signatures above prove the API shape; this drives both helpers against a
    recording `op` and asserts the DDL, because "the helper adds the RLS policy"
    is the single strongest reason the helper exists at all.
    """

    def _record(helper) -> tuple[list[str], str]:
        """Drive one helper against a recording `op`, return (columns, sql)."""
        executed: list[str] = []
        columns: list[str] = []
        monkeypatch.setattr(
            linking.op,
            "create_table",
            lambda name, *args, **kwargs: columns.extend(
                a.name for a in args if hasattr(a, "name")
            ),
        )
        monkeypatch.setattr(linking.op, "create_index", lambda *a, **k: None)
        monkeypatch.setattr(linking.op, "execute", executed.append)
        helper(
            table_name="vcp_ticket_account",
            subject_table="vendor_accounts",
            on_delete_subject="RESTRICT",
        )
        return columns, " ".join(executed)

    for helper, expect_tenant in (
        (linking.link_tenant_subject, True),
        (linking.link_platform_subject, False),
    ):
        columns, sql = _record(helper)

        if expect_tenant:
            assert "tenant_id" in columns
            assert "FORCE ROW LEVEL SECURITY" in sql
            assert "app_current_tenant_id()" in sql
            assert "REVOKE" not in sql
        else:
            assert "tenant_id" not in columns, (
                "the platform link helper emitted a tenant column — it has no "
                "tenant context to populate it from"
            )
            assert "ROW LEVEL SECURITY" not in sql
            assert "REVOKE ALL ON public.vcp_ticket_account FROM app_user" in sql


def test_dunder_version_matches_the_distribution_and_manifest() -> None:
    """A wheel that misreports its own version lies to every consumer that logs
    it, and the release publishes whatever `pyproject` says regardless.

    This drifted for real and went unnoticed across three releases:
    `pyproject` and the manifest reached `0.1.0a3` while `__version__` sat at
    `0.1.0a1`, because nothing here compared them. `dotmac-files` grew the same
    guard after the same drift; ticketing had no equivalent until a4.
    """
    import tomllib

    import dotmac_ticketing

    declared = tomllib.loads(
        (REPO_ROOT / "packages/dotmac-ticketing/pyproject.toml").read_text(
            encoding="utf-8"
        )
    )["tool"]["poetry"]["version"]
    assert dotmac_ticketing.__version__ == declared, (
        f"dotmac_ticketing.__version__ is {dotmac_ticketing.__version__!r} but "
        f"the distribution declares {declared!r}"
    )
    assert (
        module.version == declared
    ), f"manifest version {module.version!r} != distribution {declared!r}"


def test_the_lineage_builds_only_the_planes_the_assembly_selected() -> None:
    """ADR-0028's central property, asserted on the artifact that runs.

    a3 chose its planes with `all_bound(TENANT_REQUIRES)` — provider
    availability standing in for product intent. Vendor CP is where those part
    company, so the migration must read the SELECTION and nothing else.
    """
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "selected_module_planes" in called
    assert "all_bound" not in called, (
        "a bound provider is not a decision to install a plane — read the "
        "assembly's ModulePlaneSelection instead"
    )
