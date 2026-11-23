"""Hosted PostgreSQL proofs for money-flows GAP-L1: credit without a deposit.

F2 holds an uncovered prepaid charge ``pending`` (the service is off). Credit can
reach the account with no deposit: an opening cash lot, an admitted held
partition, a refund or deallocation reversed. ``on_account_credit_changed`` is
routed for every ``billing.receivable.position.v2`` and pays the pending charges
the new credit covers, dated ``max(due_at, newest credit line since due_at)``.

L1-a/b/c: the opening-lot release gate (a held partition admitted after the seal,
a lot imported inside the open window, and the dating). T-1..T-11: one test per
trigger and edge. R1..R4: real two-session races in both lock orders. Replay: the
same event twice, redelivery after a rollback and F2's own self-triggered event.

The timeline sits in the near future (see ``BASE``): account terms cannot be
back-dated against the database clock. Funds that must not predate a charge's due
time are therefore dated explicitly.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Barrier, Thread
from types import SimpleNamespace
from uuid import UUID

import pytest
from dotmac_billing import BillingRuleViolation
from dotmac_billing import models as billing_models
from dotmac_billing.commands import (
    AllocationCommand,
    DeallocationCommand,
    RefundCommand,
    ReversePostingGroupCommand,
)
from dotmac_billing.opening_commands import (
    CorrectOpeningCashRefundV1,
    OpeningCashMovementEvidenceV1,
)
from dotmac_billing.service import (
    allocate_settlement,
    correct_opening_cash_refund,
    deallocate_settlement,
    grant_non_cash_credit,
    record_opening_cash_refund,
    refund_settlement,
    reverse_posting_group,
)
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_money_flows import (
    AccountCreditChangedV1,
    BalancePassFailed,
    CreditRecheckOutcomeV1,
    FundedChargeCursorV1,
    FundedChargeState,
    FundedChargeStepFailed,
    SettledFundsV1,
    on_account_credit_changed,
    record_funded_charge_attention,
)
from dotmac_money_flows import _credit_dating as dating
from dotmac_money_flows.models import FundedChargeInstance
from sqlalchemy import create_engine, select, text

from tests import test_billing_isolation as b
from tests import test_billing_opening_cash_import as cash
from tests import test_billing_opening_hold_admission as hold_fx
from tests.money_flows_fixtures import (
    BASE,
    FakeChargeSource,
    assembly_settled,
    assembly_start,
    deposit,
    make_funded_ports,
    new_scope,
    scratch_database,
    seed_account,
    seed_default_terms,
    seed_invoice,
    session,
    unrestricted,
)
from tests.test_billing_non_cash_credit import _grant
from tests.test_money_flows_funded_charge_pg import (
    _access,
    _count,
    _documents,
    _draws,
    _events,
    _rows,
    _world,
)
from tests.test_money_flows_terms_recheck_pg import (
    SWITCH,
    _postpaid,
    _request,
    assembly_recheck,
)
from tests.unit import test_billing_opening_manifest as preflight

RECHECK = "money_flows.funded_charge.credit_recheck.v1"
POSITION = "billing.receivable.position.v2"
EARLY = preflight.NOW  # before every charge's due time (BASE is tomorrow)
LATER = BASE + timedelta(hours=2)
LATEST = BASE + timedelta(days=2)


@pytest.fixture(scope="module")
def scratch():
    with scratch_database() as urls:
        yield urls


@pytest.fixture
def opening(scratch):
    """Per-test ephemeral opening principal over the money-flows scratch database."""
    with cash._opening_scratch(scratch, ("tenant",)) as scoped:
        yield scoped


# --------------------------------------------------------------------------
# The assembly transport for ``on_account_credit_changed``
# --------------------------------------------------------------------------
def assembly_credit(
    scratch, scope, request: AccountCreditChangedV1, ports
) -> CreditRecheckOutcomeV1 | None:
    """Run the command as the transport does.

    A step failure rolls everything back, records attention for the failed row in
    a NEW transaction with ``continue_credit_recheck=True`` and returns None. Any
    other exception (a technical fault) propagates: nothing was written and the
    event delivery retries.
    """
    try:
        with session(scratch, scope) as db:
            outcome = on_account_credit_changed(
                db, scope=scope, request=request, ports=ports
            )
            db.commit()
            return outcome
    except FundedChargeStepFailed as exc:
        failure = exc
    with session(scratch, scope) as db:
        record_funded_charge_attention(
            db,
            scope=scope,
            instance_id=failure.instance_id,
            code=failure.code,
            ports=ports,
            continue_credit_recheck=True,
        )
        db.commit()
    return None


def _route(account, after=None) -> AccountCreditChangedV1:
    return AccountCreditChangedV1(account, after)


def _continuation(scratch, scope, account, index: int = -1) -> AccountCreditChangedV1:
    payload = _events(scratch, scope, RECHECK)[index]
    assert payload["billing_account_id"] == str(account)
    return _route(
        account,
        FundedChargeCursorV1(
            datetime.fromisoformat(payload["after_created_at"]),
            UUID(payload["after_id"]),
        ),
    )


def _states(scratch, scope):
    return [(row.state, row.reason_code) for row in _rows(scratch, scope)]


def _pending_charge(scratch, scope, account, *, total="107.50", ref="renewal-1", **kw):
    """A charge started with no funds, so it is held pending."""
    source = kw.pop("source", None) or FakeChargeSource()
    ports = kw.pop("ports", None) or make_funded_ports(source)
    key = source.add(
        scope, account, ref, total=total, due_at=kw.pop("due_at", BASE), **kw
    )
    assert assembly_start(scratch, scope, key, ports).state is FundedChargeState.PENDING
    return source, ports, key


def _discharged_once(scratch, scope, *, documents=1):
    assert [row.state for row in _rows(scratch, scope)] == ["discharged"] * documents
    assert _documents(scratch, scope) == documents
    assert _draws(scratch, scope) == documents


# --------------------------------------------------------------------------
# Opening-lot world: a sealed window with an admitted and a held partition
# --------------------------------------------------------------------------
def _sealed_window(scratch, opening, *, held_occurred_at=None):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        seed_default_terms(db, scope)
        db.commit()
    with cash._session(opening, scratch[1], scope) as sess:
        admitted, held = hold_fx._two_partitions(sess, scope)
        hold_fx._import(sess, scope, admitted)
        sess.commit()
        hold_fx._seal(sess, scope, held=[hold_fx._held_id(held)])
        sess.commit()
    if held_occurred_at is not None:
        held = replace(held, occurred_at=held_occurred_at)
    return scope, admitted, held


def _admit(scratch, opening, scope, held) -> None:
    with cash._session(opening, scratch[1], scope) as sess:
        hold_fx._admit(sess, scope, held)
        sess.commit()


def _position_events(scratch, scope, account) -> list[dict]:
    with session(scratch, scope) as db:
        rows = db.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == POSITION)
        ).scalars()
        return [dict(r.payload) for r in rows if str(account) in str(dict(r.payload))]


# --------------------------------------------------------------------------
# L1-a: a held partition admitted after the seal pays the pending charge
# --------------------------------------------------------------------------
def test_l1_a_an_admitted_held_lot_pays_the_pending_charge(scratch, opening):
    scope, _, held = _sealed_window(scratch, opening)
    account = hold_fx._held_id(held)
    source, ports, key = _pending_charge(scratch, scope, account, total="50.00")
    assert [event["active"] for event in _access(scratch, scope)] == [True]
    assert unrestricted(scratch, scope, account) == Decimal("0")

    _admit(scratch, opening, scope, held)
    assert _position_events(scratch, scope, account)  # the event Sub-thin routes

    outcome = assembly_credit(scratch, scope, _route(account), ports)

    assert outcome is not None and outcome.reason_code is None
    (changed,) = outcome.changed
    assert changed.state is FundedChargeState.DISCHARGED and not changed.replayed
    assert changed.billing_account_id == account
    (row,) = _rows(scratch, scope)
    assert (row.state, row.funding_settlement_id) == ("discharged", None)
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]
    assert source.issues == [(key, BASE)]  # the lot predates the due time
    assert row.document_id is not None  # the charge's own invoice
    assert _draws(scratch, scope) == 1
    assert unrestricted(scratch, scope, account) == Decimal("10.00")


# --------------------------------------------------------------------------
# L1-b: pending before the import, inside an open window; the seal still works
# --------------------------------------------------------------------------
def test_l1_b_a_lot_imported_in_the_open_window_pays_and_the_seal_succeeds(
    scratch, opening
):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        seed_default_terms(db, scope)
        db.commit()
    with cash._session(opening, scratch[1], scope) as sess:
        admitted, held = hold_fx._two_partitions(sess, scope)
    account = hold_fx._held_id(admitted)
    source, ports, key = _pending_charge(scratch, scope, account, total="50.00")

    with cash._session(opening, scratch[1], scope) as sess:
        hold_fx._import(sess, scope, admitted)
        sess.commit()
    outcome = assembly_credit(scratch, scope, _route(account), ports)

    assert outcome is not None and len(outcome.changed) == 1
    assert [row.state for row in _rows(scratch, scope)] == ["discharged"]
    assert source.issues == [(key, BASE)]
    with cash._session(opening, scratch[1], scope) as sess:
        seal = hold_fx._seal(sess, scope, held=[hold_fx._held_id(held)])
        sess.commit()
    assert seal is not None
    assert unrestricted(scratch, scope, account) == Decimal("10.00")


# --------------------------------------------------------------------------
# L1-c: the dating
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("lot_at", "expected"),
    [(LATEST, LATEST), (EARLY, BASE)],
    ids=["lot after due: the lot date", "lot before due: the due time"],
)
def test_l1_c_the_charge_is_dated_the_later_of_due_and_the_lot(
    scratch, opening, lot_at, expected
):
    scope, _, held = _sealed_window(scratch, opening, held_occurred_at=lot_at)
    account = hold_fx._held_id(held)
    source, ports, key = _pending_charge(scratch, scope, account, total="50.00")
    _admit(scratch, opening, scope, held)

    outcome = assembly_credit(scratch, scope, _route(account), ports)

    assert outcome is not None and len(outcome.changed) == 1
    assert source.issues == [(key, expected)]
    events = _access(scratch, scope)
    assert [event["active"] for event in events] == [True, False]
    assert events[-1]["observed_at"] == expected.isoformat()


# --------------------------------------------------------------------------
# T-1 .. T-4: one test per other trigger
# --------------------------------------------------------------------------
def test_t1_a_refund_reversed_pays_the_pending_charge(scratch):
    scope, (account,) = _world(scratch)
    cash_in = deposit(scratch, scope, account, "200.00")
    with session(scratch, scope) as db:
        refund = refund_settlement(
            db,
            scope=scope,
            command=RefundCommand(
                cash_in.settlement_id, b._money("150.00"), BASE, "t1-refund"
            ),
        )
        db.commit()
        refund_id = refund.id
    source, ports, key = _pending_charge(scratch, scope, account)
    assert unrestricted(scratch, scope, account) == Decimal("50.00")

    with session(scratch, scope) as db:
        reverse_posting_group(
            db,
            scope=scope,
            command=ReversePostingGroupCommand(refund_id, LATER, "t1-reversal"),
        )
        db.commit()
    outcome = assembly_credit(scratch, scope, _route(account), ports)

    assert outcome is not None and len(outcome.changed) == 1
    _discharged_once(scratch, scope)
    assert source.issues == [(key, LATER)]  # dated when the credit came back
    assert unrestricted(scratch, scope, account) == Decimal("92.50")


def test_t2_a_corrected_opening_refund_pays_the_pending_charge(scratch, opening):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        seed_default_terms(db, scope)
        db.commit()
    with cash._session(opening, scratch[1], scope) as sess:
        account = cash._new_account(sess, scope)
        _, receipt = cash._import(sess, scope, account)
        cash._seal_window(sess, scope)
        sess.commit()
        effect = record_opening_cash_refund(
            sess,
            scope=scope,
            command=cash._refund(scope, receipt.lot_ids[0], "20.00"),
            verifier=cash.TrustedVerifier(),
        )
        sess.commit()
    assert unrestricted(scratch, scope, account) == Decimal("40.00")
    source, ports, key = _pending_charge(scratch, scope, account, total="50.00")

    with cash._session(opening, scratch[1], scope) as sess:
        correct_opening_cash_refund(
            sess,
            scope=scope,
            command=CorrectOpeningCashRefundV1(
                scope,
                effect.effect_id,
                b._money("20.00"),
                OpeningCashMovementEvidenceV1(
                    "payments-correction", "t2-correction", "2", "c" * 64, "corrected"
                ),
                "independent-correction-authority",
                "t2-correction-key",
                LATEST,
            ),
            verifier=cash.TrustedVerifier(),
        )
        sess.commit()
    outcome = assembly_credit(scratch, scope, _route(account), ports)

    assert outcome is not None and len(outcome.changed) == 1
    assert [row.state for row in _rows(scratch, scope)] == ["discharged"]
    assert source.issues == [(key, LATEST)]


def test_t3_a_deallocation_pays_the_pending_charge(scratch):
    scope, (account,) = _world(scratch)
    cash_in = deposit(scratch, scope, account, "200.00")
    with session(scratch, scope) as db:
        invoice = seed_invoice(db, scope, SimpleNamespace(id=account))
        group = allocate_settlement(
            db,
            scope=scope,
            command=AllocationCommand(
                cash_in.settlement_id, invoice.id, b._money("120.00"), BASE, "t3-alloc"
            ),
        )
        allocation_id = db.execute(
            select(billing_models.AllocationEffect.id).where(
                billing_models.AllocationEffect.posting_group_id == group.id
            )
        ).scalar_one()
        db.commit()
    documents = _documents(scratch, scope)
    source, ports, key = _pending_charge(scratch, scope, account)
    assert unrestricted(scratch, scope, account) == Decimal("80.00")

    with session(scratch, scope) as db:
        deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(
                allocation_id, b._money("120.00"), LATER, "t3-dealloc"
            ),
        )
        db.commit()
    outcome = assembly_credit(scratch, scope, _route(account), ports)

    assert outcome is not None and len(outcome.changed) == 1
    assert [row.state for row in _rows(scratch, scope)] == ["discharged"]
    assert _documents(scratch, scope) == documents + 1
    assert source.issues == [(key, LATER)]


def test_t4_a_non_cash_grant_does_not_fund_the_charge(scratch):
    scope, (account,) = _world(scratch)
    source, ports, _ = _pending_charge(scratch, scope, account)
    with session(scratch, scope) as db:
        grant_non_cash_credit(
            db, scope=scope, command=_grant(scope, account, amount="500")
        )
        db.commit()
    documents = _documents(scratch, scope)

    outcome = assembly_credit(scratch, scope, _route(account), ports)

    assert outcome is not None and outcome.changed == ()
    assert outcome.reason_code in ("no_balance", "nothing_fits")
    assert _states(scratch, scope) == [("pending", None)]
    assert [event["active"] for event in _access(scratch, scope)] == [True]
    assert _documents(scratch, scope) == documents
    assert source.issues == []


# --------------------------------------------------------------------------
# T-5 .. T-11
# --------------------------------------------------------------------------
def test_t5_a_postpaid_account_has_nothing_pending(scratch):
    scope, (account,) = _world(scratch)
    _postpaid(scratch, scope, account, BASE - timedelta(hours=6))
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    deposit(scratch, scope, account, "200.00", observed_at=LATER)

    outcome = assembly_credit(scratch, scope, _route(account), ports)

    assert outcome == CreditRecheckOutcomeV1(account, (), "no_pending_charges")
    assert _rows(scratch, scope) == [] and _draws(scratch, scope) == 0
    assert ports.policy.reads == 0  # the fast path read nothing else


def test_t6_a_switch_to_postpaid_before_the_credit_withdraws_not_pays(scratch):
    scope, (account,) = _world(scratch)
    source, ports, _ = _pending_charge(scratch, scope, account)
    _postpaid(scratch, scope, account, SWITCH)
    # The credit is dated after the switch, so the period would start postpaid.
    deposit(scratch, scope, account, "200.00", observed_at=SWITCH + timedelta(hours=1))

    outcome = assembly_credit(scratch, scope, _route(account), ports)

    assert outcome is not None
    (changed,) = outcome.changed
    assert changed.state is FundedChargeState.WITHDRAWN
    assert changed.reason_code == "contract_type_changed"
    assert _documents(scratch, scope) == 0 and _draws(scratch, scope) == 0
    assert source.issues == []
    assert unrestricted(scratch, scope, account) == Decimal("200.00")
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]


def test_t7_the_cap_bounds_the_batch_and_the_continuation_resumes(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source, cap=2)
    for index in range(3):
        _pending_charge(
            scratch,
            scope,
            account,
            total="50.00",
            ref=f"renewal-{index}",
            service_ref=f"service:{index}",
            source=source,
            ports=ports,
        )
    deposit(scratch, scope, account, "200.00", observed_at=LATER)

    first = assembly_credit(scratch, scope, _route(account), ports)
    assert first is not None and len(first.changed) == 2 and first.reason_code is None
    assert len(_events(scratch, scope, RECHECK)) == 1
    resumed = _continuation(scratch, scope, account)
    assert resumed.after is not None
    second = assembly_credit(scratch, scope, resumed, ports)

    assert second is not None and len(second.changed) == 1
    assert len(_events(scratch, scope, RECHECK)) == 1  # no third continuation
    _discharged_once(scratch, scope, documents=3)
    assert unrestricted(scratch, scope, account) == Decimal("50.00")


def test_t8_an_owner_refusal_on_row_two_is_attention_and_the_rest_continue(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    keys = []
    for index in range(3):
        _, _, key = _pending_charge(
            scratch,
            scope,
            account,
            ref=f"renewal-{index}",
            service_ref=f"service:{index}",
            source=source,
            ports=ports,
        )
        keys.append(key)

    def bad_tax(command):
        obligation = replace(command.obligation, tax_amount=b._money("1.00"))
        return replace(command, obligation=obligation)

    source.mutators[keys[1]] = bad_tax
    deposit(scratch, scope, account, "400.00", observed_at=LATER)

    assert assembly_credit(scratch, scope, _route(account), ports) is None

    # Rolled back as a whole; only the failed row recorded, and a continuation.
    assert [row.state for row in _rows(scratch, scope)] == [
        "pending",
        "attention_required",
        "pending",
    ]
    assert _documents(scratch, scope) == 0 and _draws(scratch, scope) == 0
    (continuation,) = _events(scratch, scope, RECHECK)
    assert continuation["after_id"] is None  # restarts from the beginning

    outcome = assembly_credit(scratch, scope, _route(account), ports)

    assert outcome is not None and len(outcome.changed) == 2
    assert [row.state for row in _rows(scratch, scope)] == [
        "discharged",
        "attention_required",
        "discharged",
    ]
    assert _documents(scratch, scope) == 2
    # No loop: the failed row is no longer pending, so nothing more is enqueued.
    assert len(_events(scratch, scope, RECHECK)) == 1
    assert unrestricted(scratch, scope, account) == Decimal("185.00")


def test_t9_a_statement_fault_raises_and_writes_nothing_then_a_rerun_pays(
    scratch, monkeypatch
):
    scope, (account,) = _world(scratch)
    source, ports, _ = _pending_charge(scratch, scope, account)
    deposit(scratch, scope, account, "200.00", observed_at=LATER)
    real = dating.get_account_statement

    def broken(db, *, scope, request):
        raise BillingRuleViolation("statement_integrity", "bad ledger")

    monkeypatch.setattr(dating, "get_account_statement", broken)
    with pytest.raises(BalancePassFailed) as faulted:
        assembly_credit(scratch, scope, _route(account), ports)
    assert faulted.value.code == "credit_dating_failed"
    assert _states(scratch, scope) == [("pending", None)]  # no attention row
    assert _documents(scratch, scope) == 0 and _draws(scratch, scope) == 0
    assert _events(scratch, scope, RECHECK) == []

    monkeypatch.setattr(dating, "get_account_statement", real)
    outcome = assembly_credit(scratch, scope, _route(account), ports)
    assert outcome is not None and len(outcome.changed) == 1
    _discharged_once(scratch, scope)


def test_t10_another_tenant_cannot_trigger_or_see_this_tenants_charges(scratch):
    scope, (account,) = _world(scratch)
    other = new_scope(scratch)
    with session(scratch, other) as db:
        seed_default_terms(db, other)
        other_account = seed_account(db, other).id
        db.commit()
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    _pending_charge(scratch, scope, account, source=source, ports=ports)
    _pending_charge(
        scratch, other, other_account, ref="theirs", source=source, ports=ports
    )
    deposit(scratch, scope, account, "200.00", observed_at=LATER)

    # The other tenant routes this tenant's account: no row of its own is there.
    foreign = assembly_credit(scratch, other, _route(account), ports)
    assert foreign == CreditRecheckOutcomeV1(account, (), "no_pending_charges")
    assert _states(scratch, scope) == [("pending", None)]
    assert _states(scratch, other) == [("pending", None)]

    # This tenant's credit does not reach the other tenant's pending row.
    mine = assembly_credit(scratch, scope, _route(account), ports)
    assert mine is not None and len(mine.changed) == 1
    assert _states(scratch, scope) == [("discharged", None)]
    assert _states(scratch, other) == [("pending", None)]
    with session(scratch, other) as db:
        assert _count(db, FundedChargeInstance) == 1


def test_t11_a_misfit_is_skipped_and_a_smaller_charge_is_paid(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    _pending_charge(
        scratch,
        scope,
        account,
        ref="big",
        service_ref="service:big",
        source=source,
        ports=ports,
    )
    _pending_charge(
        scratch,
        scope,
        account,
        ref="small",
        total="30.00",
        service_ref="service:small",
        source=source,
        ports=ports,
    )
    deposit(scratch, scope, account, "50.00", observed_at=LATER)

    outcome = assembly_credit(scratch, scope, _route(account), ports)

    assert outcome is not None and len(outcome.changed) == 1
    assert _states(scratch, scope) == [("pending", None), ("discharged", None)]
    assert unrestricted(scratch, scope, account) == Decimal("20.00")


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------
def test_replay_the_same_event_twice_the_second_is_a_no_op(scratch):
    scope, (account,) = _world(scratch)
    source, ports, _ = _pending_charge(scratch, scope, account)
    deposit(scratch, scope, account, "200.00", observed_at=LATER)

    first = assembly_credit(scratch, scope, _route(account), ports)
    again = assembly_credit(scratch, scope, _route(account), ports)

    assert first is not None and len(first.changed) == 1
    assert again == CreditRecheckOutcomeV1(account, (), "no_pending_charges")
    _discharged_once(scratch, scope)
    assert len(source.issues) == 1
    assert unrestricted(scratch, scope, account) == Decimal("92.50")


def test_replay_a_redelivery_after_a_rollback_pays_exactly_once(scratch):
    scope, (account,) = _world(scratch)
    source, ports, _ = _pending_charge(scratch, scope, account)
    deposit(scratch, scope, account, "200.00", observed_at=LATER)

    with session(scratch, scope) as db:
        outcome = on_account_credit_changed(
            db, scope=scope, request=_route(account), ports=ports
        )
        assert len(outcome.changed) == 1
        db.rollback()  # the transport failed after the command ran
    assert _states(scratch, scope) == [("pending", None)]
    assert _documents(scratch, scope) == 0 and _draws(scratch, scope) == 0
    assert [event["active"] for event in _access(scratch, scope)] == [True]

    redelivered = assembly_credit(scratch, scope, _route(account), ports)

    assert redelivered is not None and len(redelivered.changed) == 1
    _discharged_once(scratch, scope)
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]


def test_replay_the_self_triggered_event_after_f2s_own_discharge_is_a_no_op(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    _pending_charge(
        scratch,
        scope,
        account,
        ref="big",
        service_ref="service:big",
        source=source,
        ports=ports,
    )
    _pending_charge(
        scratch,
        scope,
        account,
        ref="small",
        total="30.00",
        service_ref="service:small",
        source=source,
        ports=ports,
    )
    deposit(scratch, scope, account, "50.00", observed_at=LATER)
    assert len(assembly_credit(scratch, scope, _route(account), ports).changed) == 1
    documents, draws = _documents(scratch, scope), _draws(scratch, scope)
    states = _states(scratch, scope)

    # F2's own draw emitted position.v2; Sub-thin routes it back to the command.
    echo = assembly_credit(scratch, scope, _route(account), ports)

    assert echo is not None and echo.changed == ()
    assert echo.reason_code in ("no_balance", "nothing_fits")
    assert _states(scratch, scope) == states
    assert (_documents(scratch, scope), _draws(scratch, scope)) == (documents, draws)


# --------------------------------------------------------------------------
# Races: two real sessions queued on the Billing account lock
# --------------------------------------------------------------------------
def _locked_race(scratch, scope, account, jobs, *, ordered: bool):
    """Run ``jobs`` in threads that all queue on the account lock.

    A holder session keeps the lock until every job is proven blocked behind it
    (``pg_stat_activity``/``pg_blocking_pids``). ``ordered`` starts them one by
    one so the lock queue (FIFO) fixes who runs first; otherwise all start
    together (a barrier, inside the job, has already aligned them).
    """
    results: list[tuple] = []
    errors: list[tuple] = []

    def wrap(name, job):
        def run() -> None:
            try:
                results.append((name, job()))
            except BaseException as exc:  # reported below, never swallowed
                errors.append((name, exc))

        return run

    database = scratch[0].rsplit("/", 1)[1]
    threads = [Thread(target=wrap(name, job)) for name, job in jobs]

    def blocked_by(holder_pid: int) -> list[tuple[int, set[int]]]:
        """Every session waiting on a lock in this database and who blocks it."""
        engine = create_engine(b._superuser_url())
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT pid, pg_blocking_pids(pid) FROM pg_stat_activity "
                    "WHERE datname = :d AND wait_event_type = 'Lock'"
                ),
                {"d": database},
            ).all()
        engine.dispose()
        return [(pid, set(blocking)) for pid, blocking in rows]

    def wait_blocked(count: int, holder_pid: int) -> None:
        """``count`` sessions wait, each behind the holder or an earlier waiter."""
        deadline = time.monotonic() + 90
        waiters: list[tuple[int, set[int]]] = []
        while time.monotonic() < deadline:
            waiters = blocked_by(holder_pid)
            if len(waiters) >= count:
                break
            time.sleep(0.2)
        assert len(waiters) >= count, waiters
        pids = {pid for pid, _ in waiters} | {holder_pid}
        for pid, blocking in waiters:
            # Never an unrelated lock: the holder, or a session queued ahead.
            assert blocking and blocking <= pids - {pid}, (pid, blocking, pids)
        assert any(holder_pid in blocking for _, blocking in waiters)

    with session(scratch, scope) as holder:
        holder_pid = holder.scalar(text("SELECT pg_backend_pid()"))
        holder.execute(
            text(
                "SELECT id FROM mod_billing.billing_accounts WHERE id = :i FOR UPDATE"
            ),
            {"i": account},
        )
        if ordered:
            for position, thread in enumerate(threads, 1):
                thread.start()
                wait_blocked(position, holder_pid)
        else:
            for thread in threads:
                thread.start()
            wait_blocked(len(threads), holder_pid)
        assert results == [] and errors == []  # every session is blocked
        holder.rollback()
    for thread in threads:
        thread.join(timeout=120)
    assert all(not thread.is_alive() for thread in threads), "deadlock"
    assert errors == [], errors
    return dict(results)


@pytest.mark.parametrize("credit_first", [True, False], ids=["l1 first", "l1 second"])
def test_r1_credit_recheck_against_a_concurrent_settlement_recheck(
    scratch, credit_first
):
    scope, (account,) = _world(scratch)
    source, ports, _ = _pending_charge(scratch, scope, account)
    cash_in = deposit(scratch, scope, account, "200.00", observed_at=BASE)
    jobs = [
        ("l1", lambda: assembly_credit(scratch, scope, _route(account), ports)),
        (
            "f1",
            lambda: assembly_settled(
                scratch, scope, SettledFundsV1(account, cash_in.settlement_id), ports
            ),
        ),
    ]
    results = _locked_race(
        scratch, scope, account, jobs if credit_first else jobs[::-1], ordered=True
    )

    paid = len(results["l1"].changed) + len(results["f1"])
    assert paid == 1  # exactly one of them paid it
    _discharged_once(scratch, scope)
    (row,) = _rows(scratch, scope)
    assert row.version == 1
    assert len(source.issues) == 1
    assert unrestricted(scratch, scope, account) == Decimal("92.50")  # never negative
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]


@pytest.mark.parametrize(
    "start_first", [True, False], ids=["start first", "start last"]
)
def test_r2_an_admission_racing_a_start_for_the_same_account(
    scratch, opening, start_first
):
    scope, _, held = _sealed_window(scratch, opening)
    account = hold_fx._held_id(held)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", total="50.00", due_at=BASE)
    jobs = [
        ("start", lambda: assembly_start(scratch, scope, key, ports)),
        ("admit", lambda: _admit(scratch, opening, scope, held)),
    ]
    results = _locked_race(
        scratch, scope, account, jobs if start_first else jobs[::-1], ordered=True
    )

    if start_first:
        # The start took the lock first: no credit yet, so the row is pending
        # and the admission lands behind it. The event the admission emitted
        # then pays it.
        assert results["start"].state is FundedChargeState.PENDING
        assert [row.state for row in _rows(scratch, scope)] == ["pending"]
        outcome = assembly_credit(scratch, scope, _route(account), ports)
        assert outcome is not None and len(outcome.changed) == 1
    else:
        # The credit committed first: the start sees it and pays at once, and
        # the routed event finds nothing pending.
        assert results["start"].state is FundedChargeState.DISCHARGED
        outcome = assembly_credit(scratch, scope, _route(account), ports)
        assert outcome == CreditRecheckOutcomeV1(account, (), "no_pending_charges")
    # The imported open items add documents of their own: count F2's by its draw.
    assert [row.state for row in _rows(scratch, scope)] == ["discharged"]
    assert _draws(scratch, scope) == 1
    assert len(source.issues) == 1
    assert unrestricted(scratch, scope, account) == Decimal("10.00")
    signals = [event["active"] for event in _access(scratch, scope)]
    # Pending then paid switches the service off and on; paid at the start never
    # turned it off, so there is no signal at all.
    assert signals == ([True, False] if start_first else [])


def test_r3_two_credit_rechecks_at_once_pay_once(scratch):
    scope, (account,) = _world(scratch)
    source, ports, _ = _pending_charge(scratch, scope, account)
    deposit(scratch, scope, account, "200.00", observed_at=LATER)
    # Every caller finds the row pending (the fast path) before either locks.
    racing = make_funded_ports(source, barrier=Barrier(2))
    jobs = [
        (name, lambda: assembly_credit(scratch, scope, _route(account), racing))
        for name in ("one", "two")
    ]
    results = _locked_race(scratch, scope, account, jobs, ordered=False)

    outcomes = list(results.values())
    assert sorted(len(outcome.changed) for outcome in outcomes) == [0, 1]
    assert {o.reason_code for o in outcomes if not o.changed} == {"no_pending_charges"}
    _discharged_once(scratch, scope)
    assert len(source.issues) == 1
    assert unrestricted(scratch, scope, account) == Decimal("92.50")


@pytest.mark.parametrize("credit_first", [True, False], ids=["l1 first", "l1 second"])
def test_r4_credit_recheck_against_a_terms_recheck(scratch, credit_first):
    scope, (account,) = _world(scratch)
    source, ports, _ = _pending_charge(scratch, scope, account)
    deposit(scratch, scope, account, "200.00", observed_at=BASE)  # prepaid then
    _postpaid(scratch, scope, account, SWITCH)
    jobs = [
        ("l1", lambda: assembly_credit(scratch, scope, _route(account), ports)),
        (
            "terms",
            lambda: assembly_recheck(scratch, scope, _request(account), ports),
        ),
    ]
    results = _locked_race(
        scratch, scope, account, jobs if credit_first else jobs[::-1], ordered=True
    )

    assert len(results["l1"].changed) + len(results["terms"]) == 1  # one outcome
    (row,) = _rows(scratch, scope)
    assert row.version == 1
    assert (_documents(scratch, scope), _draws(scratch, scope)) in ((1, 1), (0, 0))
    if credit_first:
        assert row.state == "discharged"  # paid while still prepaid
        assert unrestricted(scratch, scope, account) == Decimal("92.50")
    else:
        assert (row.state, row.reason_code) == ("withdrawn", "contract_type_changed")
        assert unrestricted(scratch, scope, account) == Decimal("200.00")
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]


# --------------------------------------------------------------------------
# Footprint: the command creates no settlement or cash evidence
# --------------------------------------------------------------------------
def test_the_command_creates_no_settlement_evidence(scratch):
    scope, (account,) = _world(scratch)
    source, ports, _ = _pending_charge(scratch, scope, account)
    deposit(scratch, scope, account, "200.00", observed_at=LATER)
    with session(scratch, scope) as db:
        before = _count(db, billing_models.ConfirmedSettlement)

    assembly_credit(scratch, scope, _route(account), ports)

    with session(scratch, scope) as db:
        assert _count(db, billing_models.ConfirmedSettlement) == before
    _discharged_once(scratch, scope)
