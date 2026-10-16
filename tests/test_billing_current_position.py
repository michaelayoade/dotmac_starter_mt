"""Hosted PostgreSQL current account-position admission canaries."""

from dataclasses import replace
from uuid import uuid4

import pytest
from dotmac_billing import (
    GetCurrentReceivablePositionV1,
    get_current_receivable_position,
    models,
    service,
)
from dotmac_billing.commands import DeallocationCommand
from dotmac_billing.errors import BillingRuleViolation
from dotmac_kernel.money import Currency
from sqlalchemy import select, text

from tests import test_billing_isolation as b
from tests import test_billing_non_cash_credit as runtime
from tests import test_billing_opening_cash_import as opening

scratch = opening.scratch


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_current_position_pristine_then_latest_invoice_and_cash(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    with opening._session(scratch, url, scope) as db:
        account = opening._new_account(db, scope)
        request = GetCurrentReceivablePositionV1(scope, account, Currency("NGN", 2))
        pristine = get_current_receivable_position(db, scope=scope, request=request)
        assert pristine.source_version == 0 and pristine.position is None
        opening._invoice(db, scope, account)
        issued = get_current_receivable_position(db, scope=scope, request=request)
        assert issued.source_version == 1
        assert issued.position.collectible_receivable == b._money("120")
        source = service.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                account, scope=scope, source_key=str(uuid4())
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        db.commit()
        source_id = source.id
    with b._session(url, scope) as db:
        current = get_current_receivable_position(db, scope=scope, request=request)
        assert current.source_version == 2
        assert current.position.available_credit == b._money("120")
        assert current.position.source_version == current.source_version
        model = (
            models.ConfirmedSettlement
            if plane == "tenant"
            else models.PlatformConfirmedSettlement
        )
        cached = db.get(model, source_id)
        cached.amount = b._money("1").amount
        assert (
            get_current_receivable_position(db, scope=scope, request=request) == current
        )
        db.rollback()
        for change in (
            replace(request, billing_account_id=uuid4()),
            replace(request, currency=Currency("NGN", 3)),
            replace(request, currency=Currency("USD", 2)),
        ):
            with pytest.raises(BillingRuleViolation):
                get_current_receivable_position(db, scope=scope, request=change)


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("mode", ["REPEATABLE READ", "SERIALIZABLE", "READ ONLY"])
def test_position_requires_readwrite_readcommitted(scratch, plane, mode):
    scope, url = runtime._scope_url(scratch, plane)
    with opening._session(scratch, url, scope) as db:
        account = opening._new_account(db, scope)
    with b._session(url, scope) as db:
        if mode == "READ ONLY":
            db.execute(text("SET TRANSACTION READ ONLY"))
        else:
            db.rollback()
            db.connection(execution_options={"isolation_level": mode})
        with pytest.raises(BillingRuleViolation):
            get_current_receivable_position(
                db,
                scope=scope,
                request=GetCurrentReceivablePositionV1(
                    scope, account, Currency("NGN", 2)
                ),
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("observer_holds", [False, True])
def test_current_position_retains_account_fence_and_refreshes_after_wait(
    scratch, plane, observer_holds
):
    # A real second backend must wait, then read the correction committed by
    # the account-lock holder. Merely checking a FOR UPDATE string is weaker.
    import threading
    import time

    from tests.test_billing_current_credit_draw import _setup

    url, scope, request, _ = _setup(scratch, plane)
    current_request = GetCurrentReceivablePositionV1(
        scope, request.billing_account_id, Currency("NGN", 2)
    )
    allocation = (
        models.AllocationEffect
        if plane == "tenant"
        else models.PlatformAllocationEffect
    )
    ready = threading.Event()
    result = {}

    def observe():
        try:
            with b._session(url, scope) as waiter:
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
                            original, b._money("20"), b.NOW, str(uuid4())
                        ),
                    )
                    waiter.commit()
                    result["corrected"] = True
                else:
                    result["observed"] = get_current_receivable_position(
                        waiter, scope=scope, request=current_request
                    )
        except BaseException as exc:
            result["error"] = exc
            ready.set()

    with b._session(url, scope) as holder:
        original = holder.execute(
            select(allocation.id).where(
                allocation.posting_group_id == request.posting_group_id
            )
        ).scalar_one()
        if observer_holds:
            assert (
                get_current_receivable_position(
                    holder, scope=scope, request=current_request
                ).source_version
                == 3
            )
        else:
            service.deallocate_settlement(
                holder,
                scope=scope,
                command=DeallocationCommand(
                    original, b._money("20"), b.NOW, str(uuid4())
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
            observed = get_current_receivable_position(
                holder, scope=scope, request=current_request
            )
        else:
            observed = result["observed"]
        assert observed.source_version == 4
        assert observed.position.collectible_receivable == b._money("40")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("fault", ["missing", "hash"])
def test_missing_or_corrupt_latest_position_never_falls_back(scratch, plane, fault):
    scope, url = runtime._scope_url(scratch, plane)
    with opening._session(scratch, url, scope) as db:
        account = opening._new_account(db, scope)
        opening._invoice(db, scope, account)
        db.commit()
    request = GetCurrentReceivablePositionV1(scope, account, Currency("NGN", 2))
    model = (
        models.ReceivablePositionFactV2
        if plane == "tenant"
        else models.PlatformReceivablePositionFactV2
    )
    table = model.__table__
    with opening._session(scratch, scratch[0], scope) as owner:
        untouched = get_current_receivable_position(owner, scope=scope, request=request)
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"DISABLE TRIGGER {table.name}_append_only"
            )
        )
        if fault == "missing":
            owner.execute(table.delete().where(table.c.billing_account_id == account))
        else:
            owner.execute(
                table.update()
                .where(table.c.billing_account_id == account)
                .values(state_fingerprint="0" * 64)
            )
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"ENABLE TRIGGER {table.name}_append_only"
            )
        )
        with pytest.raises(BillingRuleViolation):
            get_current_receivable_position(owner, scope=scope, request=request)
        owner.rollback()
        assert (
            get_current_receivable_position(owner, scope=scope, request=request)
            == untouched
        )
