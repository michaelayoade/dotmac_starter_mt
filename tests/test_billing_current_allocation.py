"""Hosted migrated PostgreSQL proofs for exact CURRENT allocation observations."""

from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest
from dotmac_billing import (
    GetCurrentSettlementAllocationV1,
    get_current_settlement_allocation,
    models,
    service,
)
from dotmac_billing.commands import (
    AllocationCommand,
    DeallocationCommand,
    ReallocationCommand,
    RefundCommand,
    ReversePostingGroupCommand,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_kernel.cache import PlatformScope, TenantScope
from sqlalchemy import select, text

from tests import test_billing_isolation as billing

scratch = billing.scratch


def _setup(scratch, plane):
    admin, tenant, platform = scratch
    scope = (
        TenantScope(billing._create_tenants(admin, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant if plane == "tenant" else platform
    with billing._session(url, scope) as db:
        account, _, invoice = billing._seed_invoice(db, scope, suffix=str(uuid4()))
        settlement = service.accept_settlement(
            db,
            scope=scope,
            command=billing._settlement_command(
                account.id, scope=scope, source_key=str(uuid4())
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        command = AllocationCommand(
            settlement.id, invoice.id, billing._money("60"), billing.NOW, str(uuid4())
        )
        group = service.allocate_settlement(db, scope=scope, command=command)
        request = GetCurrentSettlementAllocationV1(
            scope, account.id, group.id, settlement.id, invoice.id, command.amount
        )
        db.commit()
    return url, scope, request, command


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_exact_current_allocation_scope_identity_replay_and_frozen(scratch, plane):
    url, scope, request, command = _setup(scratch, plane)
    with billing._session(url, scope) as db:
        for field in (
            "billing_account_id",
            "posting_group_id",
            "settlement_id",
            "document_id",
        ):
            with pytest.raises(BillingRuleViolation):
                get_current_settlement_allocation(
                    db, scope=scope, request=replace(request, **{field: uuid4()})
                )
        with pytest.raises(BillingRuleViolation):
            get_current_settlement_allocation(
                db, scope=scope, request=replace(request, amount=billing._money("61"))
            )
        with pytest.raises(BillingRuleViolation):
            get_current_settlement_allocation(
                db, scope=TenantScope(uuid4()), request=request
            )
        observed = get_current_settlement_allocation(db, scope=scope, request=request)
        assert observed.state == "current"
        assert observed.original_amount == observed.remaining_amount == command.amount
        assert (
            service.allocate_settlement(db, scope=scope, command=command).id
            == request.posting_group_id
        )
        assert (
            get_current_settlement_allocation(db, scope=scope, request=request)
            == observed
        )
        with pytest.raises(FrozenInstanceError):
            observed.state = "removed"
        if plane == "tenant":
            db.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(uuid4())},
            )
            with pytest.raises(BillingRuleViolation):
                get_current_settlement_allocation(db, scope=scope, request=request)


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_deallocation_inverse_chain_refund_and_transaction_rollback(scratch, plane):
    url, scope, request, _ = _setup(scratch, plane)
    allocation = (
        models.AllocationEffect
        if plane == "tenant"
        else models.PlatformAllocationEffect
    )
    with billing._session(url, scope) as db:
        original = db.execute(
            select(allocation.id).where(
                allocation.posting_group_id == request.posting_group_id
            )
        ).scalar_one()
        partial = service.deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(
                original, billing._money("20"), billing.NOW, str(uuid4())
            ),
        )
        observed = get_current_settlement_allocation(db, scope=scope, request=request)
        assert (
            observed.state == "reduced"
            and observed.remaining_amount == billing._money("40")
        )
        inverse = service.reverse_posting_group(
            db,
            scope=scope,
            command=ReversePostingGroupCommand(partial.id, billing.NOW, str(uuid4())),
        )
        assert (
            get_current_settlement_allocation(db, scope=scope, request=request).state
            == "current"
        )
        service.reverse_posting_group(
            db,
            scope=scope,
            command=ReversePostingGroupCommand(inverse.id, billing.NOW, str(uuid4())),
        )
        assert get_current_settlement_allocation(
            db, scope=scope, request=request
        ).remaining_amount == billing._money("40")
        db.rollback()
        assert (
            get_current_settlement_allocation(db, scope=scope, request=request).state
            == "current"
        )
        service.deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(
                original, billing._money("60"), billing.NOW, str(uuid4())
            ),
        )
        service.refund_settlement(
            db,
            scope=scope,
            command=RefundCommand(
                request.settlement_id, billing._money("120"), billing.NOW, str(uuid4())
            ),
        )
        observed = get_current_settlement_allocation(db, scope=scope, request=request)
        assert (
            observed.state == "removed"
            and observed.remaining_amount == billing._money("0")
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("fault", ["reversal", "backing", "reallocation"])
def test_inverse_backing_and_unlinked_reallocation_fail_closed(scratch, plane, fault):
    url, scope, request, _ = _setup(scratch, plane)
    group_model = (
        models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
    )
    with billing._session(url, scope) as db:
        if fault == "reallocation":
            target = billing._additional_credit_invoice(
                db, scope, request.billing_account_id, str(uuid4())
            )
            service.issue_document(
                db,
                scope=scope,
                command=billing._issue_command(target.id),
                numbering=billing.NUMBERING,
            )
            service.reallocate_settlement(
                db,
                scope=scope,
                command=ReallocationCommand(
                    request.settlement_id,
                    request.document_id,
                    target.id,
                    billing._money("20"),
                    billing.NOW,
                    str(uuid4()),
                ),
            )
            with pytest.raises(BillingRuleViolation, match="unlinked"):
                get_current_settlement_allocation(db, scope=scope, request=request)
        else:
            target = request.posting_group_id
            if fault == "backing":
                target = db.execute(
                    select(group_model.id).where(
                        group_model.source_ref == f"settlement:{request.settlement_id}",
                        group_model.group_kind == "settlement_accepted",
                    )
                ).scalar_one()
                # A receipt cannot lose its backing while its money still
                # covers an invoice. Restore the cash first through a linked
                # deallocation, then retain the backing inverse as history.
                with pytest.raises(
                    BillingRuleViolation, match="undo receipt consumption"
                ):
                    service.reverse_posting_group(
                        db,
                        scope=scope,
                        command=ReversePostingGroupCommand(
                            target, billing.NOW, str(uuid4())
                        ),
                    )
                assert (
                    get_current_settlement_allocation(
                        db, scope=scope, request=request
                    ).state
                    == "current"
                )
                allocation_model = (
                    models.AllocationEffect
                    if plane == "tenant"
                    else models.PlatformAllocationEffect
                )
                original = db.execute(
                    select(allocation_model.id).where(
                        allocation_model.posting_group_id == request.posting_group_id
                    )
                ).scalar_one()
                service.deallocate_settlement(
                    db,
                    scope=scope,
                    command=DeallocationCommand(
                        original, request.amount, billing.NOW, str(uuid4())
                    ),
                )
            service.reverse_posting_group(
                db,
                scope=scope,
                command=ReversePostingGroupCommand(target, billing.NOW, str(uuid4())),
            )
            observed = get_current_settlement_allocation(
                db, scope=scope, request=request
            )
            assert observed.state == (
                "removed" if fault == "reversal" else "inactive_backing"
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_observation_uses_persisted_rows_without_autoflush(scratch, plane):
    url, scope, request, _ = _setup(scratch, plane)
    settlement_model = (
        models.ConfirmedSettlement
        if plane == "tenant"
        else models.PlatformConfirmedSettlement
    )
    with billing._session(url, scope) as db:
        cached = db.get(settlement_model, request.settlement_id)
        cached.amount = billing._money("1").amount
        assert (
            get_current_settlement_allocation(db, scope=scope, request=request).state
            == "current"
        )
        assert cached.amount == billing._money("1").amount
        db.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_current_observation_refuses_stale_snapshot_isolation(
    scratch, plane, isolation
):
    url, scope, request, _ = _setup(scratch, plane)
    with billing._session(url, scope) as db:
        db.rollback()
        db.connection(execution_options={"isolation_level": isolation})
        with pytest.raises(BillingRuleViolation, match="READ COMMITTED"):
            get_current_settlement_allocation(db, scope=scope, request=request)


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("observer_holds", [False, True])
def test_account_lock_serializes_current_observation_and_correction(
    scratch, plane, observer_holds
):
    # A real second backend must wait, then read the correction committed by
    # the account-lock holder. Merely checking a FOR UPDATE string is weaker.
    import threading
    import time

    url, scope, request, _ = _setup(scratch, plane)
    allocation = (
        models.AllocationEffect
        if plane == "tenant"
        else models.PlatformAllocationEffect
    )
    ready = threading.Event()
    result = {}

    def observe():
        try:
            with billing._session(url, scope) as waiter:
                waiter.execute(text("SET LOCAL statement_timeout = '10s'"))
                result["pid"] = waiter.execute(
                    text("SELECT pg_backend_pid()")
                ).scalar_one()
                ready.set()
                if observer_holds:
                    service.deallocate_settlement(
                        waiter,
                        scope=scope,
                        command=DeallocationCommand(
                            original, billing._money("20"), billing.NOW, str(uuid4())
                        ),
                    )
                    waiter.commit()
                    result["corrected"] = True
                else:
                    result["observed"] = get_current_settlement_allocation(
                        waiter, scope=scope, request=request
                    )
        except BaseException as exc:
            result["error"] = exc
            ready.set()

    with billing._session(url, scope) as holder:
        original = holder.execute(
            select(allocation.id).where(
                allocation.posting_group_id == request.posting_group_id
            )
        ).scalar_one()
        if observer_holds:
            assert (
                get_current_settlement_allocation(
                    holder, scope=scope, request=request
                ).state
                == "current"
            )
        else:
            service.deallocate_settlement(
                holder,
                scope=scope,
                command=DeallocationCommand(
                    original, billing._money("20"), billing.NOW, str(uuid4())
                ),
            )
        thread = threading.Thread(target=observe)
        thread.start()
        try:
            assert ready.wait(5)
            assert "error" not in result
            deadline = time.monotonic() + 5
            blocked = False
            while time.monotonic() < deadline:
                blocked = holder.execute(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                    {"pid": result["pid"]},
                ).scalar_one()
                if blocked:
                    break
                time.sleep(0.02)
            assert blocked
            assert "observed" not in result and "corrected" not in result
            # Product decision still owns this transaction/account lock here.
            holder.commit()
        finally:
            holder.rollback()
            thread.join(12)
        assert not thread.is_alive()
        assert "error" not in result
        if observer_holds:
            assert result["corrected"] is True
            observed = get_current_settlement_allocation(
                holder, scope=scope, request=request
            )
        else:
            observed = result["observed"]
        assert observed.state == "reduced"
        assert observed.remaining_amount == billing._money("40")
