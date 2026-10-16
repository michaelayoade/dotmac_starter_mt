"""Hosted migrated-PG canaries for exact pooled-cash draw observations."""

from dataclasses import replace
from uuid import uuid4

import pytest
from dotmac_billing import (
    CashCreditDrawState,
    GetCurrentCashCreditDrawV1,
    get_current_cash_credit_draw,
    models,
    service,
)
from dotmac_billing.commands import ApplyAvailableCreditCommand, DeallocationCommand
from dotmac_billing.errors import BillingRuleViolation
from sqlalchemy import select, text

from tests import test_billing_isolation as b
from tests import test_billing_non_cash_credit as runtime
from tests import test_billing_opening_cash_import as opening
from tests.unit import test_billing_opening_manifest as preflight

scratch = opening.scratch


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("shape", ["single", "multi", "mixed"])
def test_exact_draw_original_membership_and_linked_removal(scratch, plane, shape):
    scope, url = runtime._scope_url(scratch, plane)
    with opening._session(scratch, url, scope) as db:
        account = opening._new_account(db, scope)
        if shape == "mixed":
            opening._import(db, scope, account)
            opening._seal_window(db, scope)
        for amount in (
            ["120"]
            if shape == "single"
            else ["40", "80"]
            if shape == "multi"
            else ["40"]
        ):
            service.accept_settlement(
                db,
                scope=scope,
                command=b._settlement_command(
                    account, scope=scope, source_key=str(uuid4()), amount=amount
                ),
                accepted_confirmation_evidence=frozenset({"provider_settled"}),
            )
        invoice = opening._invoice(db, scope, account)
        command = ApplyAvailableCreditCommand(
            account, invoice.id, b._money("100"), preflight.NOW, str(uuid4())
        )
        group = service.apply_available_credit(db, scope=scope, command=command)
        request = GetCurrentCashCreditDrawV1(
            scope, account, group.id, invoice.id, command.amount, command.source_ref
        )
        db.commit()
        from dotmac_kernel.cache import TenantScope
        from dotmac_kernel.money import Currency, Money

        for currency in (Currency("USD", 2), Currency("NGN", 3)):
            with pytest.raises(BillingRuleViolation):
                get_current_cash_credit_draw(
                    db,
                    scope=scope,
                    request=replace(request, amount=Money.of("100", currency)),
                )
        with pytest.raises(BillingRuleViolation):
            get_current_cash_credit_draw(
                db, scope=TenantScope(uuid4()), request=request
            )
        observed = get_current_cash_credit_draw(db, scope=scope, request=request)
        assert observed.state is CashCreditDrawState.CURRENT
        assert observed.remaining_amount == command.amount
        for field, value in (
            ("posting_group_id", uuid4()),
            ("document_id", uuid4()),
            ("billing_account_id", uuid4()),
            ("source_ref", str(uuid4())),
            ("amount", b._money("99")),
        ):
            with pytest.raises(BillingRuleViolation):
                get_current_cash_credit_draw(
                    db, scope=scope, request=replace(request, **{field: value})
                )
        if shape == "mixed":
            service.reverse_cash_credit_draw(
                db, scope=scope, command=opening._inverse(scope, group.id, "25")
            )
            reduced = get_current_cash_credit_draw(db, scope=scope, request=request)
            assert reduced.state is CashCreditDrawState.REDUCED
            assert reduced.remaining_amount == b._money("75")
            service.reverse_cash_credit_draw(
                db, scope=scope, command=opening._inverse(scope, group.id, "75")
            )
            removed = get_current_cash_credit_draw(db, scope=scope, request=request)
            assert removed.state is CashCreditDrawState.REMOVED
            assert removed.remaining_amount == b._money("0")


def _setup(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    with opening._session(scratch, url, scope) as db:
        account = opening._new_account(db, scope)
        invoice = opening._invoice(db, scope, account)
        source = service.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                account, scope=scope, source_key=str(uuid4()), amount="120"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        command = ApplyAvailableCreditCommand(
            account, invoice.id, b._money("100"), preflight.NOW, str(uuid4())
        )
        group = service.apply_available_credit(db, scope=scope, command=command)
        request = GetCurrentCashCreditDrawV1(
            scope, account, group.id, invoice.id, command.amount, command.source_ref
        )
        db.commit()
        return url, scope, request, source.id


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_inverse_descendants_and_replacement_cash_do_not_restore_draw(scratch, plane):
    from dotmac_billing import models
    from dotmac_billing.commands import DeallocationCommand, ReversePostingGroupCommand
    from sqlalchemy import select

    url, scope, request, _ = _setup(scratch, plane)
    model = (
        models.AllocationEffect
        if plane == "tenant"
        else models.PlatformAllocationEffect
    )
    with b._session(url, scope) as db:
        root = db.execute(
            select(model.id).where(model.posting_group_id == request.posting_group_id)
        ).scalar_one()
        partial = service.deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(root, b._money("20"), b.NOW, str(uuid4())),
        )
        assert get_current_cash_credit_draw(
            db, scope=scope, request=request
        ).remaining_amount == b._money("80")
        inverse = service.reverse_posting_group(
            db,
            scope=scope,
            command=ReversePostingGroupCommand(partial.id, b.NOW, str(uuid4())),
        )
        assert (
            get_current_cash_credit_draw(db, scope=scope, request=request).state
            is CashCreditDrawState.CURRENT
        )
        service.reverse_posting_group(
            db,
            scope=scope,
            command=ReversePostingGroupCommand(inverse.id, b.NOW, str(uuid4())),
        )
        service.deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(root, b._money("80"), b.NOW, str(uuid4())),
        )
        from dotmac_billing.commands import AllocationCommand

        replacement_source = service.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                request.billing_account_id,
                scope=scope,
                source_key=str(uuid4()),
                amount="100",
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        replacement = AllocationCommand(
            replacement_source.id,
            request.document_id,
            request.amount,
            b.NOW,
            str(uuid4()),
        )
        service.allocate_settlement(db, scope=scope, command=replacement)
        observed = get_current_cash_credit_draw(db, scope=scope, request=request)
        assert observed.state is CashCreditDrawState.REMOVED
        assert observed.remaining_amount == b._money("0")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_dirty_identity_map_does_not_change_persisted_draw(scratch, plane):
    from dotmac_billing import models

    url, scope, request, source_id = _setup(scratch, plane)
    model = (
        models.ConfirmedSettlement
        if plane == "tenant"
        else models.PlatformConfirmedSettlement
    )
    with b._session(url, scope) as db:
        source = db.get(model, source_id)
        source.amount = b._money("1").amount
        assert (
            get_current_cash_credit_draw(db, scope=scope, request=request).state
            is CashCreditDrawState.CURRENT
        )
        assert source.amount == b._money("1").amount
        db.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("mode", ["REPEATABLE READ", "SERIALIZABLE", "READ ONLY"])
def test_current_draw_refuses_stale_or_readonly_transactions(scratch, plane, mode):
    from sqlalchemy import text

    url, scope, request, _ = _setup(scratch, plane)
    with b._session(url, scope) as db:
        if mode == "READ ONLY":
            db.execute(text("SET TRANSACTION READ ONLY"))
        else:
            db.rollback()
            db.connection(execution_options={"isolation_level": mode})
        with pytest.raises(BillingRuleViolation, match="read-write READ COMMITTED"):
            get_current_cash_credit_draw(db, scope=scope, request=request)


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
                    result["observed"] = get_current_cash_credit_draw(
                        waiter, scope=scope, request=request
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
                get_current_cash_credit_draw(holder, scope=scope, request=request).state
                == "current"
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
            observed = get_current_cash_credit_draw(
                holder, scope=scope, request=request
            )
        else:
            observed = result["observed"]
        assert observed.state == "reduced"
        assert observed.remaining_amount == b._money("80")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_original_receipt_backing_inactive_after_linked_removal(scratch, plane):
    from dotmac_billing.commands import ReversePostingGroupCommand

    url, scope, request, source_id = _setup(scratch, plane)
    allocation = (
        models.AllocationEffect
        if plane == "tenant"
        else models.PlatformAllocationEffect
    )
    group = models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
    with b._session(url, scope) as db:
        root = db.execute(
            select(allocation.id).where(
                allocation.posting_group_id == request.posting_group_id
            )
        ).scalar_one()
        service.deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(root, request.amount, b.NOW, str(uuid4())),
        )
        acceptance = db.execute(
            select(group.id).where(
                group.source_ref == f"settlement:{source_id}",
                group.group_kind == "settlement_accepted",
            )
        ).scalar_one()
        service.reverse_posting_group(
            db,
            scope=scope,
            command=ReversePostingGroupCommand(acceptance, b.NOW, str(uuid4())),
        )
        observed = get_current_cash_credit_draw(db, scope=scope, request=request)
        assert observed.state is CashCreditDrawState.INACTIVE_BACKING
        assert observed.remaining_amount == b._money("0")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_persisted_lane_corruption_refuses_and_append_guard_is_sensitive(
    scratch, plane
):
    from sqlalchemy.exc import DBAPIError

    _, scope, request, _ = _setup(scratch, plane)
    model = models.PostingEffect if plane == "tenant" else models.PlatformPostingEffect
    table = model.__table__
    with opening._session(scratch, scratch[0], scope) as owner:
        untouched = get_current_cash_credit_draw(owner, scope=scope, request=request)
        predicate = (table.c.posting_group_id == request.posting_group_id) & (
            table.c.lane == "receivable"
        )
        with pytest.raises(DBAPIError):
            owner.execute(
                table.update()
                .where(predicate)
                .values(amount_delta=b._money("-99").amount)
            )
        owner.rollback()
        # Fixture-only corruption; INSERT admission and all financial/FK/RLS
        # guards stay active. Rollback restores the row and trigger catalogue.
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"DISABLE TRIGGER {table.name}_append_only"
            )
        )
        owner.execute(
            table.update().where(predicate).values(amount_delta=b._money("-99").amount)
        )
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"ENABLE TRIGGER {table.name}_append_only"
            )
        )
        with pytest.raises(BillingRuleViolation, match="lanes disagree"):
            get_current_cash_credit_draw(owner, scope=scope, request=request)
        owner.rollback()
        assert (
            get_current_cash_credit_draw(owner, scope=scope, request=request)
            == untouched
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_disconnected_cyclic_inverse_cannot_hide_removal(scratch, plane):
    url, scope, request, _ = _setup(scratch, plane)
    model = (
        models.AllocationEffect
        if plane == "tenant"
        else models.PlatformAllocationEffect
    )
    table = model.__table__
    with b._session(url, scope) as db:
        root = db.execute(
            select(table.c.id).where(
                table.c.posting_group_id == request.posting_group_id
            )
        ).scalar_one()
        inverse = service.deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(root, b._money("20"), b.NOW, str(uuid4())),
        )
        inverse_id = inverse.id
        db.commit()
    with opening._session(scratch, scratch[0], scope) as owner:
        child = owner.execute(
            select(table.c.id).where(table.c.posting_group_id == inverse_id)
        ).scalar_one()
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"DISABLE TRIGGER {table.name}_append_only"
            )
        )
        owner.execute(
            table.update()
            .where(table.c.id == child)
            .values(offsets_allocation_id=child)
        )
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"ENABLE TRIGGER {table.name}_append_only"
            )
        )
        with pytest.raises(BillingRuleViolation):
            get_current_cash_credit_draw(owner, scope=scope, request=request)
        owner.rollback()
        assert get_current_cash_credit_draw(
            owner, scope=scope, request=request
        ).remaining_amount == b._money("80")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("shape", ["single", "mixed"])
def test_offset_receipt_survives_commit_and_replay_record_absence(
    scratch, plane, shape
):
    from datetime import timedelta, timezone
    from decimal import Decimal

    from dotmac_billing import GetConfirmedSettlementV1, get_confirmed_settlement
    from dotmac_kernel.money import Money

    scope, url = runtime._scope_url(scratch, plane)
    with opening._session(scratch, url, scope) as db:
        account = opening._new_account(db, scope)
        if shape == "mixed":
            opening._import(db, scope, account)
            opening._seal_window(db, scope)
        offered = replace(
            b._settlement_command(
                account,
                scope=scope,
                source_key=str(uuid4()),
                amount="40" if shape == "mixed" else "120",
            ),
            occurred_at=b.NOW.astimezone(timezone(timedelta(hours=1))),
        )
        # Money values are exact even when constructed with a scaled Decimal.
        offered = replace(
            offered,
            amount=Money.of(
                Decimal("40.0000" if shape == "mixed" else "120.0000"),
                offered.amount.currency,
            ),
        )
        receipt = service.accept_settlement(
            db,
            scope=scope,
            command=offered,
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        source_id = receipt.id
        stored_hash = receipt.request_fingerprint
        invoice = opening._invoice(db, scope, account)
        command = ApplyAvailableCreditCommand(
            account, invoice.id, b._money("100"), b.NOW, str(uuid4())
        )
        group = service.apply_available_credit(db, scope=scope, command=command)
        request = GetCurrentCashCreditDrawV1(
            scope, account, group.id, invoice.id, command.amount, command.source_ref
        )
        db.commit()
    # Replay retention is independent of permanent financial observations.
    with b._session(url, scope) as db:
        ledger = (
            b.IdempotencyRecord if plane == "tenant" else b.PlatformIdempotencyRecord
        )
        db.execute(
            ledger.__table__.delete().where(
                ledger.scope == "billing.settlement",
                ledger.key
                == f"{offered.source_system}:{offered.source_settlement_key}",
            )
        )
        db.commit()
    with b._session(url, scope) as db:
        assert (
            get_current_cash_credit_draw(db, scope=scope, request=request).state
            is CashCreditDrawState.CURRENT
        )
        observed = get_confirmed_settlement(
            db, scope=scope, request=GetConfirmedSettlementV1(scope, account, source_id)
        )
        assert observed.request_fingerprint == stored_hash
        assert observed.amount == offered.amount and observed.active_backing
