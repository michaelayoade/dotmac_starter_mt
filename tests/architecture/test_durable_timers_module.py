"""Structural boundary canaries for ``dotmac-durable-timers``."""

from __future__ import annotations

import ast
import inspect
import tomllib
from dataclasses import fields
from pathlib import Path

import dotmac_durable_timers
from dotmac_durable_timers import models, service
from dotmac_durable_timers.manifest import module
from dotmac_kernel.namespaces import (
    DURABLE_TIMERS_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
)
from dotmac_kernel.planes import ModulePlane

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-durable-timers"
MODULE_ROOT = Path(inspect.getfile(service)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/dt_0001_durable_timers.py"
JOB_STATES_MIGRATION = MODULE_ROOT / "migrations/versions/dt_0002_job_states.py"

# The composition roots, named so the canary below can state EXACTLY what each
# may do that the rest of the module may not:
#  - dispatch.py is a relay transport; like the kernel's own transports it
#    commits the failure record inside `deliver` before telling the relay to
#    retry, because the worker rolls back on raise;
#  - run.py is the process entrypoint: it alone reads the ambient clock;
#  - runtime.py holds the staff surface's single request clock read.
COMMIT_ALLOWED = frozenset({"dispatch.py"})
CLOCK_ALLOWED = frozenset({"run.py", "runtime.py"})


def _module_source(exclude: frozenset[str] = frozenset()) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MODULE_ROOT.rglob("*.py"))
        if path.name not in exclude
    )


def _clock_reads(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            rendered = f"{func.value.id}.{func.attr}"
            if rendered in {
                "date.today",
                "datetime.today",
                "datetime.now",
                "datetime.utcnow",
                "time.time",
            }:
                found.add(rendered)
    return found


def test_manifest_matches_the_permanent_namespace_allocation() -> None:
    assert DURABLE_TIMERS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == DURABLE_TIMERS_MIGRATION_OWNER.owner
    assert module.short_code == "timers"
    assert module.migration_prefix == DURABLE_TIMERS_MIGRATION_OWNER.prefix == "dt"
    assert module.migration_branch == DURABLE_TIMERS_MIGRATION_OWNER.branch_label
    assert models.SCHEMA == DURABLE_TIMERS_MIGRATION_OWNER.db_schema == "mod_timers"


def test_manifest_declares_both_selectable_planes_and_the_relay_prerequisite() -> None:
    assert module.tables == models.TENANT_TABLES
    assert module.platform_tables == models.PLATFORM_TABLES
    assert module.requires == ("module_database_roles.v1", "outbox_relay.v1")
    assert module.tenant_requires == (
        "tenant_scope_catalog.v1",
        "tenant_audit_log.v1",
    )
    assert {frozenset(planes) for planes in module.supported_plane_sets} == {
        frozenset((ModulePlane.TENANT,)),
        frozenset((ModulePlane.PLATFORM,)),
        frozenset((ModulePlane.TENANT, ModulePlane.PLATFORM)),
    }


def _declared_requires(path: Path) -> dict[str, tuple[str, ...]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        target = (
            node.target
            if isinstance(node, ast.AnnAssign)
            else node.targets[0]
            if isinstance(node, ast.Assign)
            else None
        )
        if isinstance(target, ast.Name) and target.id in {
            "COMMON_REQUIRES",
            "TENANT_REQUIRES",
            "PLATFORM_REQUIRES",
        }:
            assert node.value is not None
            found[target.id] = tuple(ast.literal_eval(node.value))
    return found


def test_migrations_declare_exactly_the_manifest_prerequisites() -> None:
    first = _declared_requires(MIGRATION)
    second = _declared_requires(JOB_STATES_MIGRATION)
    assert first["COMMON_REQUIRES"] + second["COMMON_REQUIRES"] == module.requires
    assert (
        first["TENANT_REQUIRES"] + second["TENANT_REQUIRES"] == module.tenant_requires
    )
    assert (
        first["PLATFORM_REQUIRES"] + second["PLATFORM_REQUIRES"]
        == module.platform_requires
    )


def test_planes_are_declared_disjoint_and_never_cross_linked() -> None:
    assert module.tables and module.platform_tables
    assert not set(module.tables) & set(module.platform_tables)
    for model in models.ALL_MODELS:
        if model.__tablename__.startswith("platform_"):
            assert "tenant_id" not in model.__table__.c
        else:
            tenant = model.__table__.c["tenant_id"]
            assert tenant.nullable is False
        for foreign_key in model.__table__.foreign_keys:
            target = str(foreign_key.target_fullname)
            if model.__tablename__.startswith("platform_"):
                if target.startswith("mod_timers."):
                    assert target.startswith("mod_timers.platform_")
                assert not target.startswith("mod_timers.timers")
            else:
                assert not target.startswith("mod_timers.platform_")


def test_module_has_no_clock_due_scan_claim_loop_or_transaction_authority() -> None:
    assert _clock_reads(_module_source(CLOCK_ALLOWED)) == set()
    assert _module_source(CLOCK_ALLOWED) != _module_source()
    source = _module_source(COMMIT_ALLOWED)
    for forbidden in (
        "claim_outbox_batch",
        "claim_platform_outbox_batch",
        "SKIP LOCKED",
        "skip_locked",
        "SessionLocal(",
        "sessionmaker(",
        ".commit(",
        ".rollback(",
    ):
        assert forbidden not in source
    # The exemptions are narrow: the transports still never claim or build a
    # session, and the entrypoint never builds one directly either (it goes
    # through the kernel's DatabaseRuntime).
    for forbidden in (
        "claim_outbox_batch",
        "claim_platform_outbox_batch",
        "SKIP LOCKED",
        "SessionLocal(",
        "sessionmaker(",
        "create_engine(",
        ".rollback(",
    ):
        assert forbidden not in _module_source()


def test_clock_guard_has_a_sensitivity_proof() -> None:
    assert _clock_reads("from datetime import datetime\ndatetime.now()") == {
        "datetime.now"
    }
    assert _clock_reads("import time\ntime.time()") == {"time.time"}


def test_no_product_vocabulary_or_native_status_enum_is_shipped() -> None:
    source = _module_source()
    for product_word in (
        "subscriber",
        "invoice",
        "collections_case",
        "support_ticket",
        "cx_handoff",
    ):
        assert product_word not in source
    assert "sqlalchemy.Enum" not in source
    assert "sa.Enum" not in source


def test_extraction_dossier_is_the_reconciled_product_first_contract() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text("utf-8"))
    assert dossier["package"] == "dotmac-durable-timers"
    assert dossier["classification"] == "optional-module"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "product-first"
    assert "dotmac_sub" in dossier["source_repositories"]
    assert dossier["contract_consumers"] == []
    assert "dotmac_sub" in dossier["candidate_consumers"]


def test_cancellation_and_source_evidence_match_the_accepted_contract() -> None:
    assert {outcome.value for outcome in service.CancelOutcome} == {
        "canceled",
        "already_fired",
        "nothing_scheduled",
        "stale",
    }
    assert [field.name for field in fields(service.CancelResult)] == [
        "outcome",
        "observed_generation",
        "current_generation",
    ]
    signature = inspect.signature(service.cancel_timer)
    observed = signature.parameters["observed_generation"]
    assert observed.default is inspect.Parameter.empty
    assert "expected_source_version" in {
        field.name for field in fields(service.TimerTrigger)
    }
    assert "expected_source_version" in {
        field.name for field in fields(service.AcceptanceResult)
    }


def test_package_root_exports_ports_not_orm_models() -> None:
    for private_model in (
        "Timer",
        "TimerAcceptance",
        "TimerRejection",
        "PlatformTimer",
        "PlatformTimerAcceptance",
        "PlatformTimerRejection",
    ):
        assert private_model not in dotmac_durable_timers.__all__
        assert not hasattr(dotmac_durable_timers, private_model)
