"""Current account reads on the real Alembic-migrated PostgreSQL planes."""

from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest
from dotmac_billing import (
    BillingAccountObservationV1,
    GetBillingAccountObservationV1,
    get_billing_account_observation,
    models,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.service import create_billing_account
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Currency
from sqlalchemy import event, select, text

from tests import test_billing_isolation as billing_fixture
from tests.test_billing_isolation import _create_tenants, _session

scratch = billing_fixture.scratch


def test_account_observation_isolates_tenants_planes_and_actual_rls(scratch):
    admin_url, tenant_url, platform_url = scratch
    left, right = _create_tenants(admin_url)
    tenant_scope, platform_scope = TenantScope(left), PlatformScope()
    shared_id = uuid4()
    with _session(admin_url, tenant_scope) as owner:
        owner.add(
            models.BillingAccount(
                id=shared_id,
                tenant_id=left,
                external_account_ref="sub:left",
                currency="BHD",
                minor_units=3,
            )
        )
        owner.add(
            models.PlatformBillingAccount(
                id=shared_id,
                external_account_ref="platform:separate",
                currency="JPY",
                minor_units=0,
            )
        )
        owner.commit()
    for url, scope, external, currency in (
        (tenant_url, tenant_scope, "sub:left", Currency("BHD", 3)),
        (platform_url, platform_scope, "platform:separate", Currency("JPY", 0)),
    ):
        with _session(url, scope) as session:
            request = GetBillingAccountObservationV1(scope, shared_id)
            observed = get_billing_account_observation(
                session, scope=scope, request=request
            )
            assert observed == BillingAccountObservationV1(
                scope, shared_id, external, currency
            )
            assert not hasattr(observed, "_sa_instance_state")
            with pytest.raises(FrozenInstanceError):
                observed.external_account_ref = "changed"
            with pytest.raises(FrozenInstanceError):
                observed.currency.minor_units = 6
            with pytest.raises(BillingRuleViolation, match="not found"):
                get_billing_account_observation(
                    session, scope=scope, request=replace(request, account_id=uuid4())
                )
            statements = []

            def capture(
                _connection,
                _cursor,
                statement,
                _parameters,
                _context,
                _many,
                statements=statements,
            ):
                statements.append(statement)

            event.listen(session.get_bind(), "before_cursor_execute", capture)
            try:
                for wrong in (
                    TenantScope(right),
                    platform_scope if scope == tenant_scope else tenant_scope,
                    None,
                ):
                    with pytest.raises(BillingRuleViolation):
                        get_billing_account_observation(
                            session, scope=wrong, request=request
                        )
                with pytest.raises(BillingRuleViolation):
                    get_billing_account_observation(
                        session, scope=scope, request=object()
                    )
            finally:
                event.remove(session.get_bind(), "before_cursor_execute", capture)
            assert statements == []
    with _session(tenant_url, TenantScope(right)) as other:
        # Explicit predicates AND genuine role-level RLS both deny the other tenant.
        assert (
            other.execute(
                select(models.BillingAccount.__table__).where(
                    models.BillingAccount.id == shared_id
                )
            ).first()
            is None
        )
        for scope in (TenantScope(right), tenant_scope):
            with pytest.raises(BillingRuleViolation, match="not found"):
                get_billing_account_observation(
                    other,
                    scope=scope,
                    request=GetBillingAccountObservationV1(scope, shared_id),
                )
        other.execute(text("RESET app.current_tenant"))
        assert (
            other.execute(
                select(models.BillingAccount.__table__).where(
                    models.BillingAccount.id == shared_id
                )
            ).first()
            is None
        )
        with pytest.raises(BillingRuleViolation, match="not found"):
            get_billing_account_observation(
                other,
                scope=tenant_scope,
                request=GetBillingAccountObservationV1(tenant_scope, shared_id),
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_account_observation_reads_persistence_without_flush_and_reobserves_link_drop(
    scratch, plane
):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    model = (
        models.BillingAccount if plane == "tenant" else models.PlatformBillingAccount
    )
    with _session(url, scope) as session:
        row = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"sub:{uuid4()}",
            currency="BHD",
            minor_units=3,
        )
        account_id = row.id
        session.commit()
        request = GetBillingAccountObservationV1(scope, account_id)
        original = get_billing_account_observation(
            session, scope=scope, request=request
        )
        pending = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"pending:{uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        pending_id = pending.id
        row.external_account_ref = "unflushed-link"
        row.currency, row.minor_units = "JPY", 0
        flushes = []

        def capture_flush(_session, _context, _instances):
            flushes.append(True)

        event.listen(session, "before_flush", capture_flush)
        try:
            assert (
                get_billing_account_observation(session, scope=scope, request=request)
                == original
            )
        finally:
            event.remove(session, "before_flush", capture_flush)
        assert flushes == [] and row in session.dirty
        session.rollback()
    # The persistence owner explicitly flushes its mutable link; the query neither
    # locks that link nor turns its previous observation into an admission token.
    with _session(admin_url, scope) as owner:
        row = owner.get(model, account_id)
        row.external_account_ref = f"relinked:{uuid4()}"
        owner.flush()
        linked = get_billing_account_observation(owner, scope=scope, request=request)
        assert linked.external_account_ref == row.external_account_ref
        assert linked.currency == original.currency == Currency("BHD", 3)
        assert original.external_account_ref != linked.external_account_ref
        owner.delete(row)
        owner.flush()
        with pytest.raises(BillingRuleViolation, match="not found"):
            get_billing_account_observation(owner, scope=scope, request=request)
        owner.rollback()
    with _session(url, scope) as fresh:
        assert fresh.get(model, pending_id) is None
        assert (
            get_billing_account_observation(fresh, scope=scope, request=request)
            == original
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("fault", ["external_account_ref", "currency"])
def test_account_observation_refuses_persisted_corrupt_identity(scratch, plane, fault):
    admin_url, _, _ = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    with _session(admin_url, scope) as owner:
        row = create_billing_account(
            owner,
            scope=scope,
            external_account_ref=f"corrupt:{uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        request = GetBillingAccountObservationV1(scope, row.id)
        setattr(row, fault, "" if fault == "external_account_ref" else "A1!")
        owner.flush()  # Both shapes satisfy the existing SQL constraints.
        with pytest.raises(BillingRuleViolation) as error:
            get_billing_account_observation(owner, scope=scope, request=request)
        assert error.value.code == "billing.account_observation_corrupt"


@pytest.mark.parametrize(
    "scope,account_id",
    [(None, uuid4()), (TenantScope("bad"), uuid4()), (PlatformScope(), "bad")],
)
def test_account_observation_request_rejects_unsupported_identity(scope, account_id):
    with pytest.raises(BillingRuleViolation):
        GetBillingAccountObservationV1(scope, account_id)
