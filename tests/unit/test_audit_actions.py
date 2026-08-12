"""Consumer tests for the audit-action registry (`dotmac_kernel.audit_actions`)
and its consumer, `dotmac_kernel.audit.write_audit_event`.

The registry DESCRIBES which audit actions exist (declared by manifests); the
writer is the one place an action is recorded. These pin the public contract:
one owning module per action, fail-closed on duplicates, and a write of an
undeclared action rejected BEFORE anything reaches the session.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from dotmac_kernel import (
    AuditActionRegistry,
    DuplicateAuditActionError,
    FeatureManifest,
    UndeclaredAuditActionError,
    install_audit_actions,
    write_audit_event,
)
from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.models import Party, Tenant
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _m(name: str, *actions: str) -> FeatureManifest:
    return FeatureManifest(name=name, audit_actions=actions)


# ── Registry mechanics ──────────────────────────────────────────────────────


def test_manifest_audit_actions_defaults_to_empty() -> None:
    assert FeatureManifest(name="x").audit_actions == ()


def test_registry_declares_and_owns_actions() -> None:
    reg = AuditActionRegistry.from_manifests(
        [_m("rbac", "role.create", "role.grant"), _m("billing", "invoice.issue")]
    )
    assert reg.is_declared("role.grant")
    assert reg.owner("role.grant") == "rbac"
    assert reg.actions() == {"role.create", "role.grant", "invoice.issue"}
    assert not reg.is_declared("role.granted")  # the typo
    assert reg.owner("role.granted") is None


def test_require_rejects_an_undeclared_action() -> None:
    reg = AuditActionRegistry.from_manifests([_m("rbac", "role.grant")])
    reg.require("role.grant")  # declared → no raise
    with pytest.raises(UndeclaredAuditActionError):
        reg.require("role.granted")


def test_duplicate_action_across_modules_fails_closed() -> None:
    with pytest.raises(DuplicateAuditActionError):
        AuditActionRegistry.from_manifests(
            [_m("a", "shared.event"), _m("b", "shared.event")]
        )


def test_one_module_may_repeat_its_own_action() -> None:
    reg = AuditActionRegistry.from_manifests([_m("a", "a.event", "a.event")])
    assert reg.owner("a.event") == "a"


def test_uninstalled_registry_declares_nothing() -> None:
    assert AuditActionRegistry(()).actions() == frozenset()


# ── write_audit_event consumes the registry ─────────────────────────────────


def _count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(AuditEvent)) or 0


def test_declared_action_is_written(
    db: Session, tenant_row: Tenant, party_row: Party
) -> None:
    install_audit_actions(AuditActionRegistry.from_manifests([_m("probe", "probe.ok")]))
    before = _count(db)
    event = write_audit_event(
        db,
        tenant_id=tenant_row.id,
        actor_party_id=party_row.id,
        action="probe.ok",
        entity_type="probe",
    )
    assert event.action == "probe.ok"
    assert event.occurred_at is not None
    assert _count(db) == before + 1


def test_explicit_domain_time_is_preserved(
    db: Session, tenant_row: Tenant, party_row: Party
) -> None:
    install_audit_actions(AuditActionRegistry.from_manifests([_m("probe", "probe.ok")]))
    domain_time = datetime(2026, 8, 1, 12, 30, tzinfo=UTC)

    event = write_audit_event(
        db,
        tenant_id=tenant_row.id,
        actor_party_id=party_row.id,
        action="probe.ok",
        entity_type="probe",
        occurred_at=domain_time,
    )

    assert event.occurred_at == domain_time


def test_undeclared_action_is_rejected_and_writes_nothing(
    db: Session, tenant_row: Tenant, party_row: Party
) -> None:
    """Rejected BEFORE the add/flush — a refused write must not leave a partial
    row or a dirty session behind."""
    install_audit_actions(AuditActionRegistry.from_manifests([_m("probe", "probe.ok")]))
    before = _count(db)
    with pytest.raises(UndeclaredAuditActionError):
        write_audit_event(
            db,
            tenant_id=tenant_row.id,
            actor_party_id=party_row.id,
            action="probe.typo",
            entity_type="probe",
        )
    assert _count(db) == before


def test_a_real_feature_action_is_declared_by_its_own_module() -> None:
    """The assembly's own registry — not a synthetic one — owns the actions its
    features actually write."""
    from dotmac_kernel.features import load_manifests

    from app.features import FEATURE_MODULES

    reg = AuditActionRegistry.from_manifests(load_manifests(FEATURE_MODULES))
    assert reg.owner("role.grant") == "rbac"
    assert reg.owner("settings.update") == "settings"
    assert reg.owner("platform.tenant.create") == "tenants"
    assert reg.owner("licence.rejected") == "licensing"


def test_not_installed_is_distinguishable_from_installed_and_empty(monkeypatch):
    """A wiring mistake must not masquerade as an undeclared action.

    A process that builds no app — a worker, a Celery task, a CLI, a migration
    helper — never installs a registry. Reporting that as "not declared by any
    installed module" sends the reader to the module manifests, which are fine;
    the actual fault is that the vocabulary was never loaded. The two states are
    therefore different exceptions with different messages.
    """
    import dotmac_kernel.audit_actions as module

    monkeypatch.setattr(module, "_active_registry", None)
    with pytest.raises(module.AuditActionsNotInstalledError) as not_installed:
        module.active_audit_actions()
    assert "install_audit_actions" in str(not_installed.value)

    # Installed-but-empty is a legitimate, DIFFERENT state: this deployment
    # declares no audit actions, so a write is genuinely undeclared.
    monkeypatch.setattr(module, "_active_registry", module.AuditActionRegistry(()))
    with pytest.raises(module.UndeclaredAuditActionError) as undeclared:
        module.active_audit_actions().require("role.granted")
    assert "not declared" in str(undeclared.value)
    assert not isinstance(undeclared.value, module.AuditActionsNotInstalledError)
