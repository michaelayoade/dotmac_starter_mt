"""Consumer tests for the audit-action registry (`dotmac_kernel.audit_actions`)
and its consumer, `dotmac_kernel.audit.write_audit_event`.

The registry DESCRIBES which audit actions exist (Kernel and manifests); the
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
    write_platform_audit_event,
)
from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.models import Party, Tenant
from dotmac_kernel.models_platform import PlatformAuditEvent
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
    assert reg.actions() == {
        "identity.history_imported",
        "credential.password.assigned",
        "role.create",
        "role.grant",
        "invoice.issue",
    }
    assert not reg.is_declared("role.granted")  # the typo
    assert reg.owner("role.granted") is None


def test_empty_manifests_compose_the_kernel_action() -> None:
    reg = AuditActionRegistry.from_manifests(())
    assert reg.actions() == {
        "identity.history_imported",
        "credential.password.assigned",
    }
    assert reg.owner("credential.password.assigned") == "kernel"
    reg.require("credential.password.assigned")
    assert reg.owner("identity.history_imported") == "kernel"
    reg.require("identity.history_imported")


def test_kernel_declaration_is_an_actual_feature_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dotmac_kernel.features as features

    constructed: list[FeatureManifest] = []

    def capture_manifest(
        *, name: str, audit_actions: tuple[str, ...]
    ) -> FeatureManifest:
        manifest = FeatureManifest(name=name, audit_actions=audit_actions)
        constructed.append(manifest)
        return manifest

    monkeypatch.setattr(features, "FeatureManifest", capture_manifest)
    reg = AuditActionRegistry.from_manifests(())

    assert len(constructed) == 1
    manifest = constructed[0]
    assert isinstance(manifest, FeatureManifest)
    assert manifest.name == "kernel"
    assert manifest.audit_actions == (
        "identity.history_imported",
        "credential.password.assigned",
    )
    assert reg.actions() == set(manifest.audit_actions)
    assert reg.owner("identity.history_imported") == manifest.name


def test_composition_derives_ownership_and_reservation_from_the_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dotmac_kernel.features as features

    declaration = FeatureManifest(
        name="manifest_owner", audit_actions=("manifest.event",)
    )

    def substitute_manifest(
        *, name: str, audit_actions: tuple[str, ...]
    ) -> FeatureManifest:
        assert name == "kernel"
        assert audit_actions == (
            "identity.history_imported",
            "credential.password.assigned",
        )
        return declaration

    monkeypatch.setattr(features, "FeatureManifest", substitute_manifest)
    reg = AuditActionRegistry.from_manifests(())
    assert reg.actions() == {"manifest.event"}
    assert reg.owner("manifest.event") == declaration.name
    with pytest.raises(DuplicateAuditActionError):
        AuditActionRegistry.from_manifests([_m("other", "manifest.event")])


def test_generator_manifests_compose_kernel_and_ordinary_owners() -> None:
    manifests = (_m(name, action) for name, action in [("academy", "academy.event")])
    reg = AuditActionRegistry.from_manifests(manifests)
    assert reg.actions() == {
        "identity.history_imported",
        "credential.password.assigned",
        "academy.event",
    }
    assert reg.owner("identity.history_imported") == "kernel"
    assert reg.owner("academy.event") == "academy"


@pytest.mark.parametrize("owner", ["academy", "other", "kernel"])
def test_manifest_cannot_declare_a_reserved_kernel_action(owner: str) -> None:
    with pytest.raises(DuplicateAuditActionError, match="reserved.*Kernel"):
        AuditActionRegistry.from_manifests([_m(owner, "identity.history_imported")])


def test_explicit_constructor_keeps_same_owner_duplicates() -> None:
    reg = AuditActionRegistry(
        [
            ("kernel", "identity.history_imported"),
            ("kernel", "identity.history_imported"),
        ]
    )
    assert reg.actions() == {"identity.history_imported"}
    assert reg.owner("identity.history_imported") == "kernel"


def test_explicit_constructor_rejects_different_owner_duplicates() -> None:
    with pytest.raises(DuplicateAuditActionError):
        AuditActionRegistry([("a", "shared.event"), ("b", "shared.event")])


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
    reg = AuditActionRegistry(())
    assert reg.actions() == frozenset()
    assert reg.owner("identity.history_imported") is None
    with pytest.raises(UndeclaredAuditActionError):
        reg.require("identity.history_imported")


# ── write_audit_event consumes the registry ─────────────────────────────────


def _count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(AuditEvent)) or 0


def _platform_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(PlatformAuditEvent)) or 0


def test_declared_action_is_written(
    db: Session, tenant_row: Tenant, party_row: Party
) -> None:
    install_audit_actions(AuditActionRegistry.from_manifests([_m("probe", "probe.ok")]))
    before = _count(db)
    event = write_audit_event(
        db,
        tenant_id=tenant_row.id,
        actor_party_id=party_row.id,
        actor_type="user",
        actor_id=str(party_row.id),
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
        actor_type="user",
        actor_id=str(party_row.id),
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
            actor_type="user",
            actor_id=str(party_row.id),
            action="probe.typo",
            entity_type="probe",
        )
    assert _count(db) == before


def test_declared_platform_action_is_written(db: Session) -> None:
    install_audit_actions(
        AuditActionRegistry.from_manifests([_m("probe", "probe.platform.ok")])
    )
    before = _platform_count(db)

    event = write_platform_audit_event(
        db,
        actor_admin_id=None,
        action="probe.platform.ok",
        entity_type="probe",
    )

    assert event.action == "probe.platform.ok"
    assert _platform_count(db) == before + 1


def test_undeclared_platform_action_is_rejected_before_add(db: Session) -> None:
    install_audit_actions(
        AuditActionRegistry.from_manifests([_m("probe", "probe.platform.ok")])
    )
    before = _platform_count(db)

    with pytest.raises(UndeclaredAuditActionError):
        write_platform_audit_event(
            db,
            actor_admin_id=None,
            action="probe.platform.typo",
            entity_type="probe",
        )

    assert _platform_count(db) == before


def test_a_real_feature_action_is_declared_by_its_own_module() -> None:
    """The assembly's own registry — not a synthetic one — owns the actions its
    features actually write."""
    from dotmac_kernel.features import load_manifests

    from app.features import FEATURE_MODULES

    reg = AuditActionRegistry.from_manifests(load_manifests(FEATURE_MODULES))
    assert reg.owner("identity.history_imported") == "kernel"
    assert reg.owner("credential.password.assigned") == "kernel"
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


@pytest.mark.parametrize("owner", ["academy", "other", "kernel"])
def test_manifest_cannot_redeclare_kernel_password_assignment(owner: str) -> None:
    with pytest.raises(DuplicateAuditActionError, match="reserved.*Kernel"):
        AuditActionRegistry.from_manifests([_m(owner, "credential.password.assigned")])
