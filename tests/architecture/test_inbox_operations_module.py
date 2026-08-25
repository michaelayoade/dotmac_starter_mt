"""Structural canaries for staffed inbox operations."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_inbox_operations import models, service
from dotmac_inbox_operations.contracts import AssignmentStatus, PresenceState
from dotmac_inbox_operations.manifest import module
from dotmac_kernel.namespaces import (
    INBOX_OPERATIONS_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
)

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/io_0001_inbox_operations.py"
ADMISSION = ROOT / "migrations/versions/io_0002_queue_admission.py"
SAFETY = ROOT / "migrations/versions/io_0003_operational_safety.py"
AVAILABILITY = ROOT / "migrations/versions/io_0004_availability_transfers.py"
# The lineage root created five tables; a2 added admission state; a3 adds
# durable routing evidence while changing lifecycle uniqueness to active-only;
# a4 adds presence-transition, transfer, escalation and offline-policy evidence.
ROOT_TABLES = (
    "inbox_queues",
    "inbox_routing_rules",
    "inbox_agent_presence",
    "conversation_assignments",
    "inbox_workflow_events",
)
ADMISSION_TABLES = ("inbox_queue_entries", "inbox_round_robin_cursors")
SAFETY_TABLES = ("inbox_routing_decisions",)
AVAILABILITY_TABLES = (
    "inbox_presence_events",
    "inbox_transfer_requests",
    "inbox_escalation_requests",
    "inbox_offline_dispositions",
)
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_and_secure_plane_are_exact() -> None:
    assert INBOX_OPERATIONS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (
        module.code,
        module.short_code,
        module.migration_prefix,
        module.db_schema,
    ) == ("inbox_operations", "inbox_ops", "io", "mod_inbox_ops")
    assert tuple(module.tables) == (
        "inbox_queues",
        "inbox_routing_rules",
        "inbox_agent_presence",
        "conversation_assignments",
        "inbox_workflow_events",
        "inbox_queue_entries",
        "inbox_round_robin_cursors",
        "inbox_routing_decisions",
        "inbox_presence_events",
        "inbox_transfer_requests",
        "inbox_escalation_requests",
        "inbox_offline_dispositions",
    )
    assert tuple(module.platform_tables) == ()
    for name in module.tables:
        table = models.metadata_table(name)
        assert table.schema == "mod_inbox_ops"
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques


def test_inbox_content_transport_and_workforce_stay_out() -> None:
    forbidden = {
        "message_body",
        "message_content",
        "read_cursor",
        "provider_message_id",
        "webhook_payload",
        "shift_id",
        "work_order_id",
        "payroll_id",
    }
    columns = {
        column.name
        for name in module.tables
        for column in models.metadata_table(name).columns
    }
    assert not columns & forbidden


def test_service_is_flush_only_and_package_independent() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "rollback"}
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name == "app" or name.startswith("app."):
                    offenders.append(name)
                if name.startswith("dotmac_") and not name.startswith(
                    ("dotmac_inbox_operations", "dotmac_kernel")
                ):
                    offenders.append(name)
    assert not offenders


def test_root_migration_declares_the_whole_forced_rls_plane() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "io_0001_inbox_operations"
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("inbox_operations",)
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    for table in ROOT_TABLES:
        qualified = f"mod_inbox_ops.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in source
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY {table}_tenant_isolation" in source
        assert f"ON {qualified} TO app_user" in source


def test_the_admission_revision_extends_the_same_forced_rls_plane() -> None:
    """A later revision is where a tenant table most often arrives WITHOUT its
    policy: the root migration gets reviewed as a plane, an increment gets
    reviewed as a feature. Both admission tables are checked here for the same
    four properties the root is."""
    source = ADMISSION.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "io_0002_queue_admission"
    assert ast.literal_eval(assigned["down_revision"]) == "io_0001_inbox_operations"
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    assert ast.literal_eval(assigned["_TENANT_TABLES"]) == ADMISSION_TABLES
    assert set(ROOT_TABLES) | set(ADMISSION_TABLES) <= set(module.tables)
    assert "for table in _TENANT_TABLES:" in source
    assert "ALTER TABLE mod_inbox_ops.{table} ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE mod_inbox_ops.{table} FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY {table}_tenant_isolation ON mod_inbox_ops.{table}" in source
    assert "mod_inbox_ops.{table} TO app_user" in source


def test_a_stored_queue_position_is_unique_within_its_queue() -> None:
    """The FIFO guarantee only holds if two conversations cannot occupy one
    place; without this the position is decoration."""
    from dotmac_inbox_operations import models

    table = models.metadata_table("inbox_queue_entries")
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("tenant_id", "queue_id", "queue_position") in unique_columns


def test_assignment_and_queue_uniqueness_only_cover_active_work() -> None:
    assignments = models.metadata_table("conversation_assignments")
    entries = models.metadata_table("inbox_queue_entries")
    assignment_index = next(
        index
        for index in assignments.indexes
        if index.name == "uq_conversation_assignments_active_conversation"
    )
    queue_index = next(
        index
        for index in entries.indexes
        if index.name == "uq_inbox_queue_entries_active_conversation"
    )
    assert assignment_index.unique
    assert queue_index.unique
    assert str(assignment_index.dialect_options["postgresql"]["where"]) == (
        "status = 'ASSIGNED'"
    )
    assert str(queue_index.dialect_options["postgresql"]["where"]) == (
        "status = 'QUEUED'"
    )


def test_operational_safety_revision_extends_rls_and_replaces_lifecycle_keys() -> None:
    source = SAFETY.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "io_0003_operational_safety"
    assert ast.literal_eval(assigned["down_revision"]) == "io_0002_queue_admission"
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    assert ast.literal_eval(assigned["_TENANT_TABLES"]) == SAFETY_TABLES
    assert set(ROOT_TABLES) | set(ADMISSION_TABLES) | set(SAFETY_TABLES) <= set(
        module.tables
    )
    assert "uq_conversation_assignments_tenant_conversation" in source
    assert "uq_conversation_assignments_active_conversation" in source
    assert "status = 'ASSIGNED'" in source
    assert "uq_inbox_queue_entries_tenant_conversation" in source
    assert "uq_inbox_queue_entries_active_conversation" in source
    assert "status = 'QUEUED'" in source
    assert "CREATE FUNCTION mod_inbox_ops.refuse_routing_decision_mutation()" in source
    assert "CREATE TRIGGER inbox_routing_decisions_append_only" in source
    assert "BEFORE UPDATE OR DELETE ON mod_inbox_ops.inbox_routing_decisions" in source
    assert "for table in _TENANT_TABLES:" in source
    assert "ALTER TABLE mod_inbox_ops.{table} ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE mod_inbox_ops.{table} FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY {table}_tenant_isolation ON mod_inbox_ops.{table}" in source


def test_lineage_passes_the_composed_migration_gate() -> None:
    from dotmac_kernel.migrations.gate import run_gate

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    report = run_gate(
        [module],
        [
            REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions",
            REPO_ROOT / "alembic/versions",
            MIGRATION.parent,
        ],
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
    )
    assert report.ok, report.violations


def test_availability_revision_extends_the_same_forced_rls_plane() -> None:
    """Four tables arriving at once is exactly where one loses its policy."""
    source = AVAILABILITY.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "io_0004_availability_transfers"
    assert len(ast.literal_eval(assigned["revision"])) <= 32
    assert ast.literal_eval(assigned["down_revision"]) == "io_0003_operational_safety"
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    assert ast.literal_eval(assigned["_TENANT_TABLES"]) == AVAILABILITY_TABLES
    assert (
        set(ROOT_TABLES)
        | set(ADMISSION_TABLES)
        | set(SAFETY_TABLES)
        | set(AVAILABILITY_TABLES)
    ) == set(module.tables)
    assert "for table in _TENANT_TABLES:" in source
    assert "ALTER TABLE mod_inbox_ops.{table} ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE mod_inbox_ops.{table} FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY {table}_tenant_isolation ON mod_inbox_ops.{table}" in source
    assert "mod_inbox_ops.{table} TO app_user" in source
    assert "CREATE TRIGGER inbox_presence_events_append_only" in source
    assert "CREATE TRIGGER inbox_escalation_requests_append_only" in source
    # The widened value lists must name the constraints io_0001 actually
    # issued, not the ones the model layer derives for its own metadata.
    assert "ck_inbox_agent_presence_state" in source
    assert "ck_conversation_assignments_status" in source
    assert ast.literal_eval(assigned["_PRESENCE_STATES"]) == tuple(
        member.value for member in PresenceState
    )
    assert ast.literal_eval(assigned["_ASSIGNMENT_STATUSES"]) == tuple(
        member.value for member in AssignmentStatus
    )


def test_only_available_agents_can_be_dispatched_to() -> None:
    """CRM routes to online AND away agents; Sub routes only to online. This
    module resolves that conflict once, in one frozen set, and the dispatch
    query reads it rather than naming a state inline — so the policy cannot
    drift apart between assignment, promotion, claim and transfer."""
    from dotmac_inbox_operations.contracts import (
        DISPATCHABLE_PRESENCE_STATES,
        PresenceState,
    )

    assert DISPATCHABLE_PRESENCE_STATES == frozenset({PresenceState.AVAILABLE})
    assert PresenceState.AWAY not in DISPATCHABLE_PRESENCE_STATES
    assert PresenceState.ON_BREAK not in DISPATCHABLE_PRESENCE_STATES

    presence_source = (ROOT / "presence.py").read_text(encoding="utf-8")
    dispatch_query = presence_source[presence_source.index("def available_agents(") :]
    assert "DISPATCHABLE_PRESENCE_STATES" in dispatch_query
    assert "PresenceState.AVAILABLE" not in dispatch_query


def test_busy_is_derived_and_never_stored() -> None:
    """A stored busy flag is wrong in both directions the moment a chat opens
    or closes, so the vocabulary must not offer one to store."""
    from dotmac_inbox_operations.contracts import PresenceState

    assert "BUSY" not in {member.name for member in PresenceState}
    columns = {
        column.name
        for name in module.tables
        for column in models.metadata_table(name).columns
    }
    assert "busy" not in columns
    assert "is_busy" not in columns


def test_escalation_cannot_reassign_a_conversation() -> None:
    """The separation of escalate from transfer is structural, not conventional:
    the record has no target-agent column to write."""
    requests = models.metadata_table("inbox_escalation_requests")
    names = {column.name for column in requests.columns}
    assert "to_agent_reference" not in names
    assert "assigned_agent_reference" not in names
    assert "agent_reference" not in names
    assert {"severity", "notify_reference", "reason", "dedup_key"} <= names


def test_the_escalation_decision_has_exactly_one_owner() -> None:
    """`dotmac-operational-escalations` owns whether an escalation should exist,
    under which policy version, and who answered it — for tickets, outages and
    staffed inboxes alike. This module records only that an agent ASKED, so it
    has no settlement state and declares no severity vocabulary. A status column
    here would be a second answer to "is this conversation escalated?"."""
    from dotmac_inbox_operations import contracts

    requests = models.metadata_table("inbox_escalation_requests")
    names = {column.name for column in requests.columns}
    assert "status" not in names
    assert not names & {"acknowledged_at", "resolved_at", "settled_at"}
    assert not hasattr(contracts, "EscalationStatus")
    assert not hasattr(contracts, "EscalationSeverity")
    assert requests.c.severity.type.python_type is str


def test_a_manager_override_cannot_be_written_without_its_evidence() -> None:
    events = models.metadata_table("inbox_presence_events")
    checks = {
        constraint.name
        for constraint in events.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_inbox_presence_events_manager_evidence" in checks


def test_the_module_declares_its_own_authorization_vocabulary() -> None:
    """The point of the slice: inbox decisions stop borrowing a ticket
    permission. Self-presence is separate from managing someone else's, and
    cross-team transfer and supervisor override are separate from a plain one."""
    codes = {spec.code for spec in module.permissions}
    assert codes == {
        "inbox_operations.presence.self",
        "inbox_operations.presence.manage",
        "inbox_operations.conversation.claim",
        "inbox_operations.conversation.transfer",
        "inbox_operations.conversation.transfer_cross_team",
        "inbox_operations.conversation.escalate",
        "inbox_operations.supervisor.override",
    }
    for spec in module.permissions:
        assert spec.description.strip(), spec.code
        assert spec.default_roles
    # Only the self-service code reaches an ordinary agent by default.
    by_code = {spec.code: spec for spec in module.permissions}
    assert "agent" in by_code["inbox_operations.presence.self"].default_roles
    assert "agent" not in by_code["inbox_operations.presence.manage"].default_roles
    assert (
        "agent"
        not in by_code[
            "inbox_operations.conversation.transfer_cross_team"
        ].default_roles
    )
    assert "agent" not in by_code["inbox_operations.supervisor.override"].default_roles
    assert not codes & {"support:ticket:update"}


def test_no_declared_presence_source_is_unwritable() -> None:
    """A HEARTBEAT source existed and nothing could ever write it, because a
    heartbeat causes no transition. A declared member the data can never carry
    is a claim the schema does not support."""
    from dotmac_inbox_operations.contracts import PresenceSource

    source = (ROOT / "presence.py").read_text(encoding="utf-8")
    for member in PresenceSource:
        assert f"PresenceSource.{member.name}" in source, member.name


def test_every_ownership_move_can_record_its_actor() -> None:
    """The trail validated an actor and then discarded it: `REQUEUED` could not
    answer who did it. A move nobody is attributable for is not an audit story."""
    events = models.metadata_table("inbox_workflow_events")
    assert "actor_reference" in {column.name for column in events.columns}
    source = (ROOT / "transfers.py").read_text(encoding="utf-8")
    for event_type in ("TRANSFERRED_OUT", "TRANSFERRED_IN", "REQUEUED", "CLAIMED"):
        block = source[source.index(f'event_type="{event_type}"') :][:400]
        assert "actor_reference" in block, event_type


def test_the_promised_alerts_are_declared_and_enqueued() -> None:
    """`notify_reference` in a column records who a future adapter ought to
    tell. It does not tell them, and the docs promised a notification."""
    declared = set(module.outbox_event_types)
    assert declared == {
        "inbox_operations.transfer_requested.v1",
        "inbox_operations.conversation_transferred.v1",
        "inbox_operations.escalation_requested.v1",
    }
    source = (ROOT / "transfers.py").read_text(encoding="utf-8")
    assert "enqueue_event(" in source
    for code in declared:
        assert f'"{code}"' in source, code


def test_settlement_state_cannot_contradict_its_timestamps() -> None:
    for table_name, constraint in (
        ("inbox_transfer_requests", "ck_inbox_transfer_requests_settled_coherence"),
        (
            "inbox_offline_dispositions",
            "ck_inbox_offline_dispositions_settled_coherence",
        ),
    ):
        table = models.metadata_table(table_name)
        names = {
            c.name
            for c in table.constraints
            if c.__class__.__name__ == "CheckConstraint"
        }
        assert constraint in names, table_name


def test_a_transfer_result_points_at_a_real_assignment() -> None:
    """`resulting_assignment_id` without a composite FK can name a row in
    another tenant, or none at all."""
    table = models.metadata_table("inbox_transfer_requests")
    composite = {
        tuple(sorted(element.parent.name for element in constraint.elements))
        for constraint in table.foreign_key_constraints
    }
    assert ("resulting_assignment_id", "tenant_id") in composite


def test_the_downgrade_refuses_rather_than_widening_forever() -> None:
    """Dropping the widened CHECK leaves the column accepting values the
    pre-a5 code cannot read — a downgrade in name only."""
    source = AVAILABILITY.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade()") :]
    assert "RAISE EXCEPTION" in downgrade
    assert "'TRANSFERRED', 'REQUEUED'" in downgrade
    assert "ON_BREAK" in downgrade
    assert '("ASSIGNED", "RELEASED")' in downgrade
    assert '("AVAILABLE", "AWAY", "OFFLINE")' in downgrade
    assert 'drop_column("inbox_workflow_events", "actor_reference"' in downgrade
