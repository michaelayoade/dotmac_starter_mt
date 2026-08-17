"""Unit tests for the PLATFORM-scoped kernel primitives (0.1.0a2) — SQLite,
no RLS.

The platform-scoped counterparts to the tenant-scoped inbox/audit:
- `process_once_platform` — idempotent command processing keyed on `command_id`
  ALONE (a platform command carries no tenant), replaying a prior result.
- `write_platform_audit_event` — the platform audit trail (actor = a
  `PlatformAdmin`, no tenant context).

Grant/RLS boundaries for these platform catalog tables are proven on Postgres
(`tests/test_rls_catalog.py`); here we cover the write-side behavior only.
"""

from __future__ import annotations

from dotmac_kernel import AuditActionRegistry, FeatureManifest, install_audit_actions
from dotmac_kernel.audit import write_platform_audit_event
from dotmac_kernel.idempotency_models import PlatformIdempotencyRecord
from dotmac_kernel.messaging import process_once_platform
from dotmac_kernel.models_platform import PlatformAdmin, PlatformAuditEvent
from sqlalchemy import select
from sqlalchemy.orm import Session


def _admin(db: Session) -> PlatformAdmin:
    admin = PlatformAdmin(email="ops@dotmac.io", password_hash="x", is_active=True)
    db.add(admin)
    db.flush()
    return admin


def _declare_platform_action(action: str) -> None:
    install_audit_actions(
        AuditActionRegistry.from_manifests(
            [FeatureManifest(name="platform-audit-probe", audit_actions=(action,))]
        )
    )


# ── process_once_platform ────────────────────────────────────────────────────
def test_process_once_platform_runs_handler_and_records_result(db: Session) -> None:
    calls: list[str] = []

    def handler(session: Session) -> dict[str, object]:
        calls.append("ran")
        return {"provisioned": True}

    outcome = process_once_platform(
        db,
        command_id="pcmd-1",
        command_type="account.provision",
        handler=handler,
        correlation_id="corr-1",
    )
    assert outcome.status == "processed"
    assert not outcome.was_duplicate
    assert outcome.result == {"provisioned": True}
    assert calls == ["ran"]

    record = db.execute(
        select(PlatformIdempotencyRecord).where(
            PlatformIdempotencyRecord.key == "pcmd-1"
        )
    ).scalar_one()
    assert record.operation == "account.provision"
    assert record.status == "executed"
    assert record.result == {"provisioned": True}
    assert record.correlation_id == "corr-1"


def test_process_once_platform_is_idempotent_and_replays_result(db: Session) -> None:
    calls: list[str] = []

    def handler(session: Session) -> dict[str, object]:
        calls.append("ran")
        return {"n": len(calls)}

    first = process_once_platform(
        db, command_id="pcmd-dup", command_type="t", handler=handler
    )
    assert first.status == "processed"
    assert first.result == {"n": 1}

    # Same command_id again: handler NOT re-run, prior result replayed.
    second = process_once_platform(
        db, command_id="pcmd-dup", command_type="t", handler=handler
    )
    assert second.was_duplicate
    assert second.result == {"n": 1}
    assert calls == ["ran"]  # exactly once

    rows = db.execute(
        select(PlatformIdempotencyRecord).where(
            PlatformIdempotencyRecord.key == "pcmd-dup"
        )
    ).all()
    assert len(rows) == 1


def test_process_once_platform_distinct_ids_each_run(db: Session) -> None:
    calls: list[str] = []

    def handler(session: Session) -> None:
        calls.append("ran")
        return None

    for cid in ("a", "b", "c"):
        process_once_platform(db, command_id=cid, command_type="t", handler=handler)
    assert calls == ["ran", "ran", "ran"]


# ── write_platform_audit_event ───────────────────────────────────────────────
def test_write_platform_audit_event_records_row(db: Session) -> None:
    _declare_platform_action("account.created")
    admin = _admin(db)
    event = write_platform_audit_event(
        db,
        actor_admin_id=admin.id,
        action="account.created",
        entity_type="vendor_account",
        entity_id="acct-42",
        details={"plan": "pro"},
    )
    assert event.id is not None
    fetched = db.execute(
        select(PlatformAuditEvent).where(PlatformAuditEvent.id == event.id)
    ).scalar_one()
    assert fetched.actor_admin_id == admin.id
    assert fetched.action == "account.created"
    assert fetched.entity_type == "vendor_account"
    assert fetched.entity_id == "acct-42"
    assert fetched.details == {"plan": "pro"}
    assert fetched.created_at is not None


def test_write_platform_audit_event_allows_no_actor_and_defaults_details(
    db: Session,
) -> None:
    # A system-initiated platform action has no admin actor; details default to {}.
    _declare_platform_action("system.reconcile")
    event = write_platform_audit_event(
        db,
        actor_admin_id=None,
        action="system.reconcile",
        entity_type="vendor_account",
    )
    assert event.actor_admin_id is None
    assert event.entity_id is None
    assert event.details == {}
