"""Hosted PostgreSQL proofs for money-flows F1 Settle: target, replay, race.

AT1: the target comes only from the payment intent. AT6: a replay makes no owner
call and leaves one row. The concurrent duplicate is two real transactions.
"""

from __future__ import annotations

import dataclasses
from threading import Barrier, Thread

import pytest
from dotmac_billing import models as billing_models
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_money_flows import (
    DepositApplication,
    SettleConfirmationV1,
    SettlementTargetV1,
    SettlePolicyV1,
    SettleState,
)
from dotmac_money_flows.models import SettleInstance
from dotmac_payments.contracts import PaymentPurpose
from sqlalchemy import func, select

from tests.money_flows_fixtures import (
    RecordingTargets,
    assembly_settle,
    make_ports,
    new_scope,
    scratch_database,
    seed_account,
    seed_invoice,
    seed_payment,
    session,
)

DEPOSIT = PaymentPurpose.ACCOUNT_CREDIT_DEPOSIT


@pytest.fixture(scope="module")
def scratch():
    with scratch_database() as urls:
        yield urls


def _count(db, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_at1_the_target_comes_only_from_the_intent(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        right = seed_account(db, scope)
        wrong = seed_account(db, scope)
        # The provider-controlled external reference names the OTHER account.
        payment = seed_payment(
            db,
            scope,
            purpose=DEPOSIT,
            amount="50.00",
            target_reference="acct:right",
            external_reference="acct:wrong",
        )
        db.commit()
        right_id, wrong_id = right.id, wrong.id
    targets = RecordingTargets(
        {
            "acct:right": SettlementTargetV1(right_id),
            "acct:wrong": SettlementTargetV1(wrong_id),
        }
    )
    ports = make_ports(
        targets,
        SettlePolicyV1(version="p1", deposit_application=DepositApplication.NONE),
    )

    outcome = assembly_settle(scratch, scope, payment.command(), ports)

    assert outcome.state is SettleState.SETTLED
    assert outcome.billing_account_id == right_id
    assert targets.calls == [("acct:right", "ACCOUNT_CREDIT_DEPOSIT")]
    # The command has no field a provider payload could use to pick a target.
    assert {f.name for f in dataclasses.fields(SettleConfirmationV1)} == {
        "confirmation_id",
        "intent_reference",
        "occurred_at",
        "withholding",
    }


def test_at1_an_intent_mismatch_is_refused_before_the_resolver(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        one = seed_payment(
            db, scope, purpose=DEPOSIT, amount="50.00", target_reference="acct:a"
        )
        two = seed_payment(
            db, scope, purpose=DEPOSIT, amount="60.00", target_reference="acct:a"
        )
        db.commit()
        account_id = account.id
    targets = RecordingTargets({"acct:a": SettlementTargetV1(account_id)})
    ports = make_ports(targets)

    outcome = assembly_settle(
        scratch, scope, one.command(intent_reference=two.intent_reference), ports
    )

    assert outcome.state is SettleState.REFUSED
    assert outcome.reason_code == "intent_mismatch"
    assert targets.calls == []
    with session(scratch, scope) as db:
        assert _count(db, billing_models.ConfirmedSettlement) == 0


def test_at6_a_replay_makes_no_owner_call_and_leaves_one_row(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        seed_invoice(db, scope, account)
        payment = seed_payment(
            db, scope, purpose=DEPOSIT, amount="50.00", target_reference="acct:a"
        )
        db.commit()
        account_id = account.id
    targets = RecordingTargets({"acct:a": SettlementTargetV1(account_id)})
    ports = make_ports(targets)

    first = assembly_settle(scratch, scope, payment.command(), ports)
    second = assembly_settle(scratch, scope, payment.command(), ports)

    assert first.state is SettleState.SETTLED and not first.replayed
    assert second.replayed
    assert (second.instance_id, second.settlement_id, second.version) == (
        first.instance_id,
        first.settlement_id,
        first.version,
    )
    assert len(targets.calls) == 1 and ports.policy.reads == 1
    with session(scratch, scope) as db:
        assert _count(db, SettleInstance) == 1
        assert _count(db, billing_models.ConfirmedSettlement) == 1
        assert (
            _count(
                db,
                OutboxEvent,
                OutboxEvent.event_type == "money_flows.settle.settled.v1",
            )
            == 1
        )


def test_a_concurrent_duplicate_is_a_replay_with_one_effect(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        seed_invoice(db, scope, account)
        seed_invoice(db, scope, account)
        payment = seed_payment(
            db, scope, purpose=DEPOSIT, amount="200.00", target_reference="acct:a"
        )
        db.commit()
        account_id = account.id
    targets = RecordingTargets({"acct:a": SettlementTargetV1(account_id)})
    ports = make_ports(targets)
    barrier = Barrier(2)
    results, errors = [], []

    def attempt() -> None:
        try:
            barrier.wait(timeout=30)
            results.append(assembly_settle(scratch, scope, payment.command(), ports))
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == []
    assert sorted(outcome.replayed for outcome in results) == [False, True]
    assert {outcome.instance_id for outcome in results} == {results[0].instance_id}
    with session(scratch, scope) as db:
        assert _count(db, SettleInstance) == 1
        assert _count(db, billing_models.ConfirmedSettlement) == 1
        assert (
            _count(
                db,
                billing_models.AllocationEffect,
                billing_models.AllocationEffect.effect_kind == "allocation",
            )
            == 2
        )
        assert (
            _count(
                db,
                OutboxEvent,
                OutboxEvent.event_type == "money_flows.settle.settled.v1",
            )
            == 1
        )
