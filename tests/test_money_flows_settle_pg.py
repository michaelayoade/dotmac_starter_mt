"""Hosted PostgreSQL proofs for money-flows F1 Settle: target, replay, race.

AT1: the target comes only from the payment intent. AT6: a replay makes no owner
call and leaves one row. The concurrent duplicate is two real transactions.
"""

from __future__ import annotations

import dataclasses
import time
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from dotmac_billing import models as billing_models
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_money_flows import (
    DepositApplication,
    SettleAttentionCursorV1,
    SettleConfirmationV1,
    SettlementTargetV1,
    SettlePolicyV1,
    SettlePorts,
    SettleState,
    is_settle_replay_conflict,
    list_settle_attention,
    settle_confirmation,
)
from dotmac_money_flows.models import SettleInstance
from dotmac_payments.contracts import ConfirmationSource, PaymentPurpose
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError

from tests import test_billing_isolation as b
from tests.money_flows_fixtures import (
    BarrierPolicy,
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


def _blocked_on_locks(database: str) -> tuple[int, int]:
    """Sessions waiting on a lock: by wait event, and by blocking pids."""
    engine = create_engine(b._superuser_url())
    with engine.connect() as connection:
        waiting = connection.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = :d AND wait_event_type = 'Lock'"
            ),
            {"d": database},
        )
        ungranted = connection.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = :d AND cardinality(pg_blocking_pids(pid)) > 0"
            ),
            {"d": database},
        )
    engine.dispose()
    return waiting, ungranted


def test_a_real_race_both_pass_the_replay_check_then_one_effect(scratch):
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
    results, errors = [], []

    def attempt() -> None:
        try:
            results.append(assembly_settle(scratch, scope, payment.command(), ports))
        except BaseException as exc:
            errors.append(exc)

    database = scratch[0].rsplit("/", 1)[1]
    threads = [Thread(target=attempt) for _ in range(2)]
    with session(scratch, scope) as holder:
        # A third session holds the account row lock Billing takes first.
        holder.execute(
            text(
                "SELECT id FROM mod_billing.billing_accounts WHERE id = :i FOR UPDATE"
            ),
            {"i": account_id},
        )
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            waiting, ungranted = _blocked_on_locks(database)
            if waiting >= 2 and ungranted >= 2 and ports.policy.reads == 2:
                break
            time.sleep(0.2)
        waiting, ungranted = _blocked_on_locks(database)
        # Both passed the initial replay check and are blocked on the lock.
        assert ports.policy.reads == 2
        assert waiting >= 2 and ungranted >= 2
        assert results == [] and errors == []
        holder.rollback()
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
        # The loser emitted nothing: exactly one settled event.
        assert (
            _count(
                db,
                OutboxEvent,
                OutboxEvent.event_type == "money_flows.settle.settled.v1",
            )
            == 1
        )


def test_refused_path_race_the_loser_is_classified_a_replay(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        # MANUAL is off by policy default: both copies refuse before any write.
        payment = seed_payment(
            db,
            scope,
            purpose=DEPOSIT,
            amount="20.00",
            target_reference="acct:a",
            source=ConfirmationSource.MANUAL,
        )
        db.commit()
        account_id = account.id
    targets = RecordingTargets({"acct:a": SettlementTargetV1(account_id)})
    policy = BarrierPolicy(SettlePolicyV1(version="p1"), Barrier(2))
    ports = SettlePorts(targets=targets, policy=policy)
    results, errors = [], []

    def attempt() -> None:
        try:
            with session(scratch, scope) as db:
                try:
                    outcome = settle_confirmation(
                        db, scope=scope, command=payment.command(), ports=ports
                    )
                    db.commit()
                    results.append(("won", outcome.reason_code))
                except IntegrityError as exc:
                    db.rollback()
                    results.append(("lost", is_settle_replay_conflict(exc)))
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == []
    assert sorted(results) == [("lost", True), ("won", "source_not_accepted")]
    assert policy.reads == 2  # both passed the replay check first
    with session(scratch, scope) as db:
        assert _count(db, SettleInstance) == 1
        assert _count(db, billing_models.ConfirmedSettlement) == 0


def test_attention_paging_is_keyset_on_created_at_and_id(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        # One transaction: every row shares the same created_at.
        for _ in range(5):
            db.add(
                SettleInstance(
                    id=uuid4(),
                    tenant_id=scope.tenant_id,
                    state="refused",
                    reason_code="x",
                    policy_version="p",
                    definition_version="d",
                    version=0,
                    confirmation_id=uuid4(),
                    intent_id=uuid4(),
                )
            )
        db.commit()
    seen, cursor, pages = [], None, 0
    with session(scratch, scope) as db:
        while True:
            page = list_settle_attention(db, scope=scope, after=cursor, limit=2)
            if not page:
                break
            pages += 1
            seen.extend(page)
            cursor = SettleAttentionCursorV1(page[-1].created_at, page[-1].id)
        assert len({row.created_at for row in seen}) == 1
    assert pages == 3
    assert len(seen) == 5 and len({row.id for row in seen}) == 5
    assert [r.id for r in seen] == sorted(r.id for r in seen)
