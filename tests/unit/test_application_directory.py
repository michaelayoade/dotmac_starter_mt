"""Behaviour of the application directory: digests, lifecycle, reconciliation.

Structure — the ledger allocation, schema binding, the no-authorization-column
rule — is in `tests/architecture/test_application_directory_module.py`.

Tenancy correctness is NOT tested here: SQLite has no RLS. The Postgres canary
is `tests/test_application_directory_isolation.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from dotmac_application_directory import (
    ActivationRefused,
    ApplicationDescriptor,
    ApplicationRole,
    BindingLifecycleError,
    BindingSource,
    BindingState,
    DescriptorError,
    ReconcileOutcome,
    ReconciliationStatus,
    activate_binding,
    allowed_transitions,
    attach_application,
    can_transition,
    is_launchable,
    launchable_bindings,
    list_bindings,
    mark_reconciliation_failed,
    reconcile_descriptor,
    transition,
)
from dotmac_application_directory.service import (
    BindingAlreadyExists,
    DirectoryError,
)
from dotmac_kernel.models import Tenant
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


def _descriptor(**overrides) -> ApplicationDescriptor:
    base = {
        "application_code": "sub",
        "instance_ref": "sub-lagos-1",
        "local_tenant_ref": "local-9f2c",
        "admin_url": "https://sub.example.net/admin",
        "api_audience": "https://sub.example.net/api",
        "descriptor_version": 1,
        "roles": (
            ApplicationRole("support.agent", "Support agent", delegable=True),
            ApplicationRole("owner", "Account owner"),
        ),
    }
    return ApplicationDescriptor(**{**base, **overrides})


# ── The descriptor contract ──────────────────────────────────────────────────


def test_a_descriptor_digest_is_deterministic() -> None:
    assert _descriptor().digest == _descriptor().digest


def test_role_order_does_not_change_the_catalogue_digest() -> None:
    """Two applications listing the same roles in different orders agree.

    Without this, a target application that reorders its catalogue for display
    would look to the Workspace like it had changed its roles.
    """
    forward = _descriptor(
        roles=(
            ApplicationRole("a.one", "One", delegable=True),
            ApplicationRole("b.two", "Two"),
        )
    )
    reverse = _descriptor(
        roles=(
            ApplicationRole("b.two", "Two"),
            ApplicationRole("a.one", "One", delegable=True),
        )
    )
    assert forward.role_catalogue_digest == reverse.role_catalogue_digest
    assert forward.digest == reverse.digest


def test_the_two_digests_answer_different_questions() -> None:
    """A moved admin URL changes the descriptor digest and not the catalogue's.

    That is the whole reason there are two: an allocation authored against the
    role catalogue does not need re-issuing because someone changed a hostname.
    """
    original = _descriptor()
    moved = _descriptor(admin_url="https://sub2.example.net/admin")
    assert moved.role_catalogue_digest == original.role_catalogue_digest
    assert moved.digest != original.digest


def test_changing_a_role_changes_both_digests() -> None:
    original = _descriptor()
    widened = _descriptor(
        roles=(
            ApplicationRole("support.agent", "Support agent", delegable=True),
            # The application newly permits delegation of `owner`.
            ApplicationRole("owner", "Account owner", delegable=True),
        )
    )
    assert widened.role_catalogue_digest != original.role_catalogue_digest
    assert widened.digest != original.digest


def test_the_catalogue_and_descriptor_domains_are_separated() -> None:
    """An empty catalogue's digest is not the empty descriptor's digest.

    Domain separation, so a digest computed over one kind of payload can never
    be presented as a digest of the other.
    """
    empty = _descriptor(roles=())
    assert empty.role_catalogue_digest != empty.digest


def test_only_delegable_roles_are_requestable() -> None:
    """`delegable` is the APPLICATION's statement, and defaults to False.

    A catalogue that has not thought about delegation delegates nothing.
    """
    assert _descriptor().delegable_role_codes == frozenset({"support.agent"})


@pytest.mark.parametrize(
    "overrides",
    [
        {"application_code": ""},
        {"instance_ref": "  "},
        {"local_tenant_ref": ""},
        {"api_audience": ""},
        {"descriptor_version": 0},
        {"admin_url": "sub.example.net/admin"},  # no scheme
        {"admin_url": "ftp://sub.example.net"},  # wrong scheme
        {
            "roles": (
                ApplicationRole("dup", "One"),
                ApplicationRole("dup", "Two"),
            )
        },
    ],
)
def test_an_invalid_descriptor_fails_closed_at_construction(overrides) -> None:
    """It must never reach a binding — the binding's digest would then attest
    to nonsense, and every later comparison against it would be meaningless."""
    with pytest.raises(DescriptorError):
        _descriptor(**overrides)


# ── The lifecycle ────────────────────────────────────────────────────────────


def test_detached_is_terminal() -> None:
    assert allowed_transitions(BindingState.DETACHED) == frozenset()


def test_every_live_state_can_reach_detached_directly() -> None:
    """Disconnecting must never require first repairing a broken binding."""
    for state in BindingState:
        if state is BindingState.DETACHED:
            continue
        assert can_transition(state, BindingState.DETACHED), state


def test_a_self_transition_is_not_legal() -> None:
    """Re-asserting the current state is a no-op the caller should recognise as
    one; permitting it silently hides a caller that believed it made a change."""
    for state in BindingState:
        assert not can_transition(state, state), state


def test_only_active_is_launchable() -> None:
    """And launchable is not authorized — see ADR-0021 §3."""
    launchable = {state for state in BindingState if is_launchable(state)}
    assert launchable == {BindingState.ACTIVE}


# ── The service ──────────────────────────────────────────────────────────────


def _attached(db: Session, tenant: Tenant, **overrides):
    return attach_application(
        db,
        tenant_id=tenant.id,
        descriptor=_descriptor(**overrides),
        source=BindingSource.VENDOR_ALLOCATION,
    )


def test_a_new_binding_is_invited_and_unverified(
    db: Session, tenant_row: Tenant
) -> None:
    """Nothing has been confirmed by the application itself yet.

    `UNKNOWN` and `STALE` are different operational problems — "never looked"
    versus "looked and it has moved on" — so the initial state must not be
    `STALE`, and must not pretend to be `FRESH`.
    """
    binding = _attached(db, tenant_row)
    assert binding.state == BindingState.INVITED
    assert binding.reconciliation_status == ReconciliationStatus.UNKNOWN
    assert binding.descriptor_refreshed_at is None


def test_attach_cannot_be_asked_for_a_state_at_all(
    db: Session, tenant_row: Tenant
) -> None:
    """The defect this closes: `state=ACTIVE` with no descriptor read behind it
    produced a binding that was launchable and had never been verified. There is
    no such parameter now, so the failure is a TypeError rather than a silently
    unverified row."""
    with pytest.raises(TypeError):
        attach_application(
            db,
            tenant_id=tenant_row.id,
            descriptor=_descriptor(),
            source=BindingSource.CUSTOMER_ATTACHED,
            state=BindingState.ACTIVE,
        )


def test_attaching_twice_is_refused(db: Session, tenant_row: Tenant) -> None:
    _attached(db, tenant_row)
    with pytest.raises(BindingAlreadyExists):
        _attached(db, tenant_row)


def test_a_second_instance_of_the_same_application_is_allowed(
    db: Session, tenant_row: Tenant
) -> None:
    """Uniqueness is over (application, instance), not over application."""
    _attached(db, tenant_row)
    _attached(db, tenant_row, instance_ref="sub-abuja-1")
    assert len(list_bindings(db, tenant_id=tenant_row.id)) == 2


# ── Activation carries proof ─────────────────────────────────────────────────


def test_activation_requires_a_descriptor_read_from_the_application(
    db: Session, tenant_row: Tenant
) -> None:
    binding = _attached(db, tenant_row)
    activated = activate_binding(
        db,
        tenant_id=tenant_row.id,
        binding_id=binding.id,
        observed=_descriptor(),
        now=NOW,
    )
    assert activated.state == BindingState.ACTIVE
    assert activated.reconciliation_status == ReconciliationStatus.FRESH
    # Activation is also a read: the freshness timestamp is the proof's receipt.
    assert activated.descriptor_refreshed_at == NOW


def test_transition_refuses_to_produce_active(db: Session, tenant_row: Tenant) -> None:
    """The only route to ACTIVE is `activate_binding`. Routing through the
    generic transition would put back the unverified-but-launchable binding."""
    binding = _attached(db, tenant_row)
    with pytest.raises(DirectoryError, match="activate_binding"):
        transition(
            db,
            tenant_id=tenant_row.id,
            binding_id=binding.id,
            target=BindingState.ACTIVE,
        )
    assert binding.state == BindingState.INVITED


def test_activation_is_refused_when_the_descriptor_is_not_adopted(
    db: Session, tenant_row: Tenant
) -> None:
    """A same-version content conflict is not a descriptor to go live on."""
    binding = _attached(db, tenant_row)
    tampered = _descriptor(
        descriptor_version=1, admin_url="https://evil.example.net/admin"
    )
    with pytest.raises(ActivationRefused):
        activate_binding(
            db,
            tenant_id=tenant_row.id,
            binding_id=binding.id,
            observed=tampered,
            now=NOW,
        )
    assert binding.state == BindingState.INVITED
    assert binding.admin_url == "https://sub.example.net/admin"


def test_activation_is_refused_when_the_application_names_another_tenant(
    db: Session, tenant_row: Tenant
) -> None:
    """A descriptor for a different local tenant is proof of a different
    binding.

    The version is bumped deliberately. At the SAME version a changed
    `local_tenant_ref` changes the digest, so reconciliation refuses it as a
    content conflict and activation never reaches the tenant check. The tenant
    check is reachable exactly when the application publishes a NEW version that
    names a different local tenant — which reconciliation adopts, and which
    activation must still refuse.

    Compared against the value captured before reconciling: adopting rewrites
    `local_tenant_ref`, so a post-reconcile comparison would compare it to
    itself.
    """
    binding = _attached(db, tenant_row)
    with pytest.raises(ActivationRefused, match="local tenant"):
        activate_binding(
            db,
            tenant_id=tenant_row.id,
            binding_id=binding.id,
            observed=_descriptor(descriptor_version=2, local_tenant_ref="someone-else"),
            now=NOW,
        )
    assert binding.state == BindingState.INVITED


def test_a_detached_binding_cannot_be_reactivated(
    db: Session, tenant_row: Tenant
) -> None:
    binding = _attached(db, tenant_row)
    activate_binding(
        db,
        tenant_id=tenant_row.id,
        binding_id=binding.id,
        observed=_descriptor(),
        now=NOW,
    )
    transition(
        db,
        tenant_id=tenant_row.id,
        binding_id=binding.id,
        target=BindingState.DETACHED,
    )
    with pytest.raises(BindingLifecycleError):
        activate_binding(
            db,
            tenant_id=tenant_row.id,
            binding_id=binding.id,
            observed=_descriptor(),
            now=NOW,
        )


def test_only_active_bindings_are_launchable(db: Session, tenant_row: Tenant) -> None:
    live = _attached(db, tenant_row)
    activate_binding(
        db,
        tenant_id=tenant_row.id,
        binding_id=live.id,
        observed=_descriptor(),
        now=NOW,
    )
    suspended = _attached(db, tenant_row, instance_ref="sub-abuja-1")
    activate_binding(
        db,
        tenant_id=tenant_row.id,
        binding_id=suspended.id,
        observed=_descriptor(instance_ref="sub-abuja-1"),
        now=NOW,
    )
    transition(
        db,
        tenant_id=tenant_row.id,
        binding_id=suspended.id,
        target=BindingState.SUSPENDED,
    )

    launchable = launchable_bindings(db, tenant_id=tenant_row.id)
    assert [b.instance_ref for b in launchable] == ["sub-lagos-1"]


# ── Reconciliation ───────────────────────────────────────────────────────────


def _live(db: Session, tenant: Tenant):
    binding = _attached(db, tenant)
    activate_binding(
        db,
        tenant_id=tenant.id,
        binding_id=binding.id,
        observed=_descriptor(),
        now=NOW,
    )
    return binding


def _reconcile(db: Session, tenant: Tenant, binding, observed, *, now=NOW):
    return reconcile_descriptor(
        db,
        tenant_id=tenant.id,
        binding_id=binding.id,
        observed=observed,
        now=now,
    )


def test_reconciling_an_identical_descriptor_is_unchanged(
    db: Session, tenant_row: Tenant
) -> None:
    binding = _live(db, tenant_row)
    later = NOW + timedelta(hours=1)
    outcome = _reconcile(db, tenant_row, binding, _descriptor(), now=later)
    assert outcome is ReconcileOutcome.UNCHANGED
    assert binding.reconciliation_status == ReconciliationStatus.FRESH
    assert binding.descriptor_refreshed_at == later


def test_a_newer_descriptor_is_adopted(db: Session, tenant_row: Tenant) -> None:
    binding = _live(db, tenant_row)
    observed = _descriptor(
        descriptor_version=2, admin_url="https://sub2.example.net/admin"
    )
    outcome = _reconcile(db, tenant_row, binding, observed)
    assert outcome is ReconcileOutcome.UPDATED
    assert binding.descriptor_version == 2
    assert binding.admin_url == "https://sub2.example.net/admin"
    assert binding.descriptor_digest == observed.digest
    assert binding.reconciliation_status == ReconciliationStatus.FRESH


def test_a_regressed_version_is_not_adopted(db: Session, tenant_row: Tenant) -> None:
    """Usually a lagging replica, so `stale` rather than `failed` — and the
    stored copy is kept intact."""
    binding = _live(db, tenant_row)
    _reconcile(db, tenant_row, binding, _descriptor(descriptor_version=5))
    outcome = _reconcile(db, tenant_row, binding, _descriptor(descriptor_version=4))
    assert outcome is ReconcileOutcome.REGRESSED
    assert binding.descriptor_version == 5
    assert binding.reconciliation_status == ReconciliationStatus.STALE


def test_same_version_different_content_is_a_conflict(
    db: Session, tenant_row: Tenant
) -> None:
    """A version is a promise that content did not change beneath it.

    Adopting silently would make every later digest comparison meaningless, and
    the case is indistinguishable from tampering.
    """
    binding = _live(db, tenant_row)
    tampered = _descriptor(
        descriptor_version=1, admin_url="https://evil.example.net/admin"
    )
    outcome = _reconcile(db, tenant_row, binding, tampered)
    assert outcome is ReconcileOutcome.CONFLICT
    assert binding.admin_url == "https://sub.example.net/admin"
    assert binding.reconciliation_status == ReconciliationStatus.FAILED
    assert "not adopted" in binding.reconciliation_error


def test_a_descriptor_for_another_application_is_refused(
    db: Session, tenant_row: Tenant
) -> None:
    binding = _live(db, tenant_row)
    with pytest.raises(DirectoryError):
        _reconcile(db, tenant_row, binding, _descriptor(application_code="erp"))


def test_a_failed_read_does_not_move_the_freshness_timestamp(
    db: Session, tenant_row: Tenant
) -> None:
    """An unreachable application must not look freshly checked."""
    binding = _live(db, tenant_row)
    mark_reconciliation_failed(
        db,
        tenant_id=tenant_row.id,
        binding_id=binding.id,
        error="connection refused",
    )
    assert binding.reconciliation_status == ReconciliationStatus.FAILED
    assert binding.descriptor_refreshed_at == NOW


def test_a_success_clears_a_previous_failure_explanation(
    db: Session, tenant_row: Tenant
) -> None:
    """A stale explanation must not outlive the failure it described."""
    binding = _live(db, tenant_row)
    mark_reconciliation_failed(
        db,
        tenant_id=tenant_row.id,
        binding_id=binding.id,
        error="connection refused",
    )
    _reconcile(db, tenant_row, binding, _descriptor())
    assert binding.reconciliation_error is None


def test_every_mutation_takes_ids_rather_than_a_loaded_row(
    db: Session, tenant_row: Tenant
) -> None:
    """The signatures are the concurrency guarantee.

    A mutation that accepted a caller-supplied object could not lock the row it
    was about to write, and both races this closes — reconciliations committing
    out of order, a suspend landing after a detach — were invisible at the call
    site. Serialisation itself is proven on PostgreSQL; this pins the shape that
    makes it possible.
    """
    import inspect

    from dotmac_application_directory import service

    for name in (
        "activate_binding",
        "reconcile_descriptor",
        "mark_reconciliation_failed",
        "transition",
    ):
        parameters = inspect.signature(getattr(service, name)).parameters
        assert "binding_id" in parameters, name
        assert "tenant_id" in parameters, name
        assert "binding" not in parameters, name
