"""Structural canaries for response obligations."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    RESPONSE_OBLIGATIONS_MIGRATION_OWNER,
)
from dotmac_response_obligations import models, service
from dotmac_response_obligations.manifest import module

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/ro_0001_response_obligations.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_and_secure_plane_are_exact() -> None:
    assert RESPONSE_OBLIGATIONS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (
        module.code,
        module.short_code,
        module.migration_prefix,
        module.db_schema,
    ) == ("response_obligations", "sla", "ro", "mod_sla")
    assert tuple(module.tables) == (
        "sla_policies",
        "sla_targets",
        "sla_clocks",
        "sla_clock_pauses",
        "sla_observations",
    )
    assert tuple(module.platform_tables) == ()
    for name in module.tables:
        table = models.metadata_table(name)
        assert table.schema == "mod_sla"
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques


def test_the_module_owns_a_clock_and_nothing_it_measures() -> None:
    """`subject_reference` is opaque. The moment a ticket, conversation or work
    order column appears here, three domains stop being able to share it."""
    forbidden = {
        "ticket_id",
        "conversation_id",
        "work_order_id",
        "agent_reference",
        "queue_id",
        "message_body",
        "customer_id",
    }
    columns = {
        column.name
        for name in module.tables
        for column in models.metadata_table(name).columns
    }
    assert not columns & forbidden
    assert "subject_reference" in columns


def test_the_escalation_decision_stays_with_its_owner() -> None:
    """`dotmac-operational-escalations` owns whether a breach should escalate,
    under which policy version, at what level and who answered — for tickets,
    outages and inboxes alike. An observation with a status here would be a
    second answer with no reconciliation path."""
    observations = models.metadata_table("sla_observations")
    names = {column.name for column in observations.columns}
    assert "status" not in names
    assert not names & {"acknowledged_at", "resolved_at", "acknowledged_by"}
    # The consequence leaves as a request the assembly forwards, and this
    # module never reaches the escalation owner itself.
    source = (ROOT / "service.py").read_text(encoding="utf-8")
    assert "EscalationRequested(" in source
    assert "operational_escalations" not in source.replace("`", "")


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
                    ("dotmac_response_obligations", "dotmac_kernel")
                ):
                    offenders.append(name)
    assert not offenders


def test_the_root_migration_declares_the_whole_forced_rls_plane() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "ro_0001_response_obligations"
    assert len(ast.literal_eval(assigned["revision"])) <= 32
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("response_obligations",)
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    assert ast.literal_eval(assigned["_TENANT_TABLES"]) == tuple(module.tables)
    for table in module.tables:
        qualified = f"mod_sla.{table}"
        assert "ALTER TABLE mod_sla.{table}" in source
        assert (
            f"CREATE POLICY {{table}}_tenant_isolation ON {qualified[:8]}"
            in (source[: source.index("def downgrade")] + qualified)
            or "CREATE POLICY {table}_tenant_isolation ON mod_sla.{table}" in source
        )
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "mod_sla.{table} TO app_user" in source
    assert "CREATE TRIGGER sla_observations_append_only" in source


def test_the_migration_vocabularies_match_the_python_enums() -> None:
    from dotmac_response_obligations.contracts import (
        ClockStatus,
        ObligationKind,
        ObservationKind,
        PauseReason,
    )

    source = MIGRATION.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    for name, enum_type in (
        ("_KINDS", ObligationKind),
        ("_CLOCK_STATUSES", ClockStatus),
        ("_PAUSE_REASONS", PauseReason),
        ("_OBSERVATIONS", ObservationKind),
    ):
        assert ast.literal_eval(assigned[name]) == tuple(
            member.value for member in enum_type
        ), name


def test_one_default_target_per_policy_and_kind_is_actually_enforced() -> None:
    """PostgreSQL permits many NULLs in a UNIQUE, so the default-row rule has to
    be a partial index or it is not a rule at all."""
    table = models.metadata_table("sla_targets")
    index = next(
        i for i in table.indexes if i.name == "uq_sla_targets_default_per_kind"
    )
    assert index.unique
    assert str(index.dialect_options["postgresql"]["where"]) == "priority IS NULL"


def test_a_subject_cannot_hold_two_live_clocks_of_one_kind() -> None:
    table = models.metadata_table("sla_clocks")
    index = next(
        i for i in table.indexes if i.name == "uq_sla_clocks_live_subject_kind"
    )
    assert index.unique
    assert str(index.dialect_options["postgresql"]["where"]) == (
        "status IN ('RUNNING', 'PAUSED')"
    )


def test_the_sweep_reads_an_index_not_the_table() -> None:
    """A periodic full-table rescan is the thing durable timers exist to avoid;
    the ordering column must be indexed with the status it filters on."""
    table = models.metadata_table("sla_clocks")
    index = next(
        i for i in table.indexes if i.name == "ix_sla_clocks_tenant_status_due"
    )
    assert [column.name for column in index.columns] == [
        "tenant_id",
        "status",
        "due_at",
    ]
    source = (ROOT / "service.py").read_text(encoding="utf-8")
    sweep = source[source.index("def sweep_due_clocks(") :]
    assert ".limit(command.limit)" in sweep
    assert "skip_locked=True" in sweep


def test_the_promised_alerts_are_declared_and_enqueued() -> None:
    declared = set(module.outbox_event_types)
    assert declared == {
        "response_obligations.obligation_at_risk.v1",
        "response_obligations.obligation_breached.v1",
    }
    source = (ROOT / "service.py").read_text(encoding="utf-8")
    assert "enqueue_event(" in source
    for code in declared:
        assert f'"{code}"' in source, code


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
