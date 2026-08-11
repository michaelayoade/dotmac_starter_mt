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


def test_models_are_bound_to_the_allocated_schema() -> None:
    assert models.SCHEMA == module_schema("tkt")
    for model in (models.Ticket, models.TicketComment):
        assert model.__table__.schema == models.SCHEMA


# ── No product domain in the shared tables ───────────────────────────────────


@pytest.mark.parametrize(
    "forbidden",
    ["subscriber_id", "project_id", "customer_id", "lead_id", "licence_id", "site_id"],
)
def test_no_subject_column_leaked_onto_the_shared_ticket(forbidden: str) -> None:
    """Subjects are product-owned link tables, never columns here.

    A ticket has many subjects — Sub's has six, ERP's five — so a column per
    product would be both wrong and unbounded.
    """
    assert forbidden not in models.Ticket.__table__.columns


def test_every_shared_table_is_tenant_scoped() -> None:
    for model in (models.Ticket, models.TicketComment):
        tenant_id = model.__table__.columns.get("tenant_id")
        assert tenant_id is not None, f"{model.__name__} has no tenant_id"
        assert not tenant_id.nullable, f"{model.__name__}.tenant_id must be NOT NULL"


def test_comments_reference_their_ticket_compositely() -> None:
    """A bare ticket_id FK would let a comment cross tenants when an id leaks."""
    composite = [
        fk
        for fk in models.TicketComment.__table__.foreign_key_constraints
        if {"tenant_id", "ticket_id"} == {c.name for c in fk.columns}
    ]
    assert composite, (
        "ticket_comments must reference tickets on (tenant_id, ticket_id), not "
        "on ticket_id alone"
    )


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


def test_link_subject_requires_an_explicit_on_delete_for_the_subject() -> None:
    """Cascade-vs-restrict is product policy; a default would decide it silently."""
    signature = inspect.signature(linking.link_subject)
    subject = signature.parameters["on_delete_subject"]
    assert subject.default is inspect.Parameter.empty
    assert subject.kind is inspect.Parameter.KEYWORD_ONLY


def test_link_subject_targets_the_allocated_schema() -> None:
    assert linking.MODULE_SCHEMA == module_schema("tkt")


def test_link_subject_rejects_a_name_whose_generated_index_would_overflow() -> None:
    """PostgreSQL truncates identifiers at 63 chars, and truncation collides."""
    with pytest.raises(ValueError, match="too long|1..63"):
        linking.link_subject(
            table_name="a" * 60,
            subject_table="subscribers",
            on_delete_subject="RESTRICT",
        )
