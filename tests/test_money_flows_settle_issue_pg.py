"""Hosted PostgreSQL proofs for GAP-3: the balance pays new postpaid invoices.

``apply_balance_on_invoice_issued`` runs the deposit pass when Billing issues an
invoice. Covered: pay at issue, short balance, older invoice first, misfit skip,
policy off, prepaid no-op, replay, a raised failure that leaves nothing posted
and a re-run that completes, tenant and account isolation, a reseller account,
and real two-session races (a concurrent deposit in both orders, two issue
triggers on one account).
"""

from __future__ import annotations

import time
from datetime import timedelta
from threading import Event, Thread, current_thread
from types import SimpleNamespace

import pytest
from dotmac_billing import get_document_exposure
from dotmac_billing import models as billing_models
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.service import rebuild_receivable_position
from dotmac_money_flows import (
    DepositApplication,
    InvoiceIssuedV1,
    SettlementTargetV1,
    SettlePolicyV1,
    apply_balance_on_invoice_issued,
    list_settle_attention,
)
from dotmac_money_flows import settle as flows
from dotmac_money_flows.models import SettleInstance
from dotmac_payments.contracts import PaymentPurpose
from sqlalchemy import func, select, text

from tests.money_flows_fixtures import (
    BASE,
    RecordingTargets,
    assembly_settle,
    deposit,
    make_ports,
    new_scope,
    scratch_database,
    seed_account,
    seed_default_terms,
    seed_invoice,
    seed_payment,
    session,
    unrestricted,
)
from tests.test_money_flows_settle_pg import _blocked_on_locks

ISSUED = BASE + timedelta(hours=1)  # after the default terms take effect


@pytest.fixture(scope="module")
def scratch():
    with scratch_database() as urls:
        yield urls


def _count(db, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where))


def _allocations(db) -> int:
    return _count(
        db,
        billing_models.AllocationEffect,
        billing_models.AllocationEffect.effect_kind == "allocation",
    )


def _outstanding(db, scope, document_id):
    exposure = get_document_exposure(db, scope=scope, document_id=document_id)
    return exposure.collectible_receivable.amount


def _credit(db, scope, account_id):
    return rebuild_receivable_position(
        db,
        scope=scope,
        billing_account_id=account_id,
        currency="NGN",
        minor_units=2,
    ).available_credit


def _ports(policy=None):
    return make_ports(RecordingTargets(), policy)


def _issue(scratch, scope, document_id, *, policy=None):
    """Own the transaction as the assembly contract says: commit on success."""
    with session(scratch, scope) as db:
        outcome = apply_balance_on_invoice_issued(
            db,
            scope=scope,
            request=InvoiceIssuedV1(document_id),
            ports=_ports(policy),
        )
        db.commit()
        return outcome


def _world(scratch, *, contract="postpaid", balance=None, invoices=(("120.00", 0),)):
    """A tenant and account with a balance and invoices issued after it.

    ``invoices`` are (net, hours after ISSUED); the invoice total is net + 20%.
    Returns scope, account id and the invoice ids in the order given.
    """
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        seed_default_terms(db, scope, contract_type=contract)
        account = seed_account(db, scope)
        db.commit()
        account_id = account.id
    if balance is not None:
        deposit(scratch, scope, account_id, balance)
    ids = []
    with session(scratch, scope) as db:
        for net, hours in invoices:
            ids.append(
                seed_invoice(
                    db,
                    scope,
                    SimpleNamespace(id=account_id),
                    net=net,
                    issued_at=ISSUED + timedelta(hours=hours),
                ).id
            )
        db.commit()
    return scope, account_id, ids


def test_a_balance_that_covers_the_new_invoice_pays_it_at_issue(scratch):
    scope, account_id, (doc,) = _world(scratch, balance="500.00")
    outcome = _issue(scratch, scope, doc)
    assert outcome.applied_document_ids == (doc,)
    assert outcome.reason_code is None
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, doc) == 0
        assert _credit(db, scope, account_id) == 500 - 144
        assert _allocations(db) == 1
        # No flows row and no attention: the command writes no instance.
        assert _count(db, SettleInstance) == 1  # the deposit's own, only
        assert list_settle_attention(db, scope=scope) == ()


def test_a_short_balance_leaves_the_invoice_open_and_posts_nothing(scratch):
    scope, account_id, (doc,) = _world(scratch, balance="50.00")
    outcome = _issue(scratch, scope, doc)
    assert outcome.applied_document_ids == ()
    assert outcome.reason_code == "no_invoice_fits"
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, doc) == 144
        assert _credit(db, scope, account_id) == 50
        assert _allocations(db) == 0


def test_the_older_open_invoice_is_paid_first(scratch):
    # 200 covers one 144 invoice: the older (earlier due) wins, never the new one.
    scope, account_id, (older, new) = _world(
        scratch, balance="200.00", invoices=(("120.00", 0), ("120.00", 5))
    )
    outcome = _issue(scratch, scope, new)
    assert outcome.applied_document_ids == (older,)
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, older) == 0
        assert _outstanding(db, scope, new) == 144
        assert _credit(db, scope, account_id) == 56


def test_a_misfit_is_skipped_and_a_smaller_invoice_is_still_paid_whole(scratch):
    scope, account_id, (big, small) = _world(
        scratch, balance="100.00", invoices=(("500.00", 0), ("50.00", 5))
    )
    outcome = _issue(scratch, scope, small)
    assert outcome.applied_document_ids == (small,)
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, big) == 600
        assert _outstanding(db, scope, small) == 0
        assert _credit(db, scope, account_id) == 40


def test_policy_none_is_a_no_op(scratch):
    scope, account_id, (doc,) = _world(scratch, balance="500.00")
    policy = SettlePolicyV1(version="p", deposit_application=DepositApplication.NONE)
    outcome = _issue(scratch, scope, doc, policy=policy)
    assert outcome.reason_code == "deposit_application_off"
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, doc) == 144
        assert _allocations(db) == 0


def test_a_prepaid_account_with_a_leftover_open_invoice_is_a_no_op(scratch):
    scope, account_id, (doc,) = _world(scratch, contract="prepaid", balance="500.00")
    outcome = _issue(scratch, scope, doc)
    assert outcome.reason_code == "account_not_postpaid"
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, doc) == 144
        assert _credit(db, scope, account_id) == 500
        assert _allocations(db) == 0


def test_a_replay_posts_nothing_more(scratch):
    scope, account_id, (doc,) = _world(scratch, balance="500.00")
    _issue(scratch, scope, doc)
    again = _issue(scratch, scope, doc)
    assert again.applied_document_ids == ()
    assert again.reason_code == "invoice_not_open"
    with session(scratch, scope) as db:
        assert _allocations(db) == 1
        assert _credit(db, scope, account_id) == 500 - 144


def test_a_raised_failure_leaves_nothing_posted_and_a_rerun_completes(
    scratch, monkeypatch
):
    scope, account_id, (older, new) = _world(
        scratch, balance="400.00", invoices=(("120.00", 0), ("120.00", 5))
    )
    real = flows.apply_available_credit
    calls = []

    def flaky(db, *, scope, command):
        calls.append(command.document_id)
        if len(calls) == 2:
            raise BillingRuleViolation("account_held", "held")
        return real(db, scope=scope, command=command)

    monkeypatch.setattr(flows, "apply_available_credit", flaky)
    with pytest.raises(BillingRuleViolation):
        _issue(scratch, scope, new)  # the first draw is rolled back with the second
    with session(scratch, scope) as db:
        assert _allocations(db) == 0
        assert _outstanding(db, scope, older) == 144
        assert _outstanding(db, scope, new) == 144
        assert _count(db, SettleInstance) == 1  # no attention row written
        assert list_settle_attention(db, scope=scope) == ()
    monkeypatch.setattr(flows, "apply_available_credit", real)
    outcome = _issue(scratch, scope, new)
    assert set(outcome.applied_document_ids) == {older, new}
    with session(scratch, scope) as db:
        assert _allocations(db) == 2
        assert _credit(db, scope, account_id) == 400 - 288


def test_another_accounts_balance_never_pays_this_invoice_and_vice_versa(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        seed_default_terms(db, scope, contract_type="postpaid")
        rich, poor = seed_account(db, scope), seed_account(db, scope)
        db.commit()
        rich_id, poor_id = rich.id, poor.id
    deposit(scratch, scope, rich_id, "1000.00")
    with session(scratch, scope) as db:
        rich_doc = seed_invoice(
            db, scope, SimpleNamespace(id=rich_id), issued_at=ISSUED
        ).id
        poor_doc = seed_invoice(
            db, scope, SimpleNamespace(id=poor_id), issued_at=ISSUED
        ).id
        db.commit()
    outcome = _issue(scratch, scope, poor_doc)
    assert outcome.billing_account_id == poor_id
    assert outcome.reason_code == "no_balance"
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, poor_doc) == 120
        assert _outstanding(db, scope, rich_doc) == 120  # not touched either
        assert _allocations(db) == 0
    # The rich account's own trigger pays only its own invoice.
    paid = _issue(scratch, scope, rich_doc)
    assert paid.applied_document_ids == (rich_doc,)
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, poor_doc) == 120


def test_another_tenant_cannot_trigger_a_document_it_does_not_own(scratch):
    scope, _, (doc,) = _world(scratch, balance="500.00")
    other = new_scope(scratch)
    with pytest.raises(BillingRuleViolation):
        _issue(scratch, other, doc)
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, doc) == 144
        assert _allocations(db) == 0


def test_a_reseller_account_pays_invoices_of_every_service_ref(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        seed_default_terms(db, scope, contract_type="postpaid")
        account = seed_account(db, scope)
        db.commit()
        account_id = account.id
    deposit(scratch, scope, account_id, "1000.00")
    with session(scratch, scope) as db:
        docs = [
            seed_invoice(
                db,
                scope,
                SimpleNamespace(id=account_id),
                service_ref=ref,
                issued_at=ISSUED + timedelta(hours=hours),
            ).id
            for hours, ref in enumerate(("service:a", "service:b", "service:c"))
        ]
        db.commit()
    outcome = _issue(scratch, scope, docs[-1])
    assert set(outcome.applied_document_ids) == set(docs)
    with session(scratch, scope) as db:
        assert {_outstanding(db, scope, d) for d in docs} == {0}
        assert _credit(db, scope, account_id) == 1000 - 360


# --------------------------------------------------------------------------
# Real two-session races
# --------------------------------------------------------------------------
def _deposit_payment(scratch, scope, account_id, amount):
    reference = f"acct:{account_id}"
    with session(scratch, scope) as db:
        payment = seed_payment(
            db,
            scope,
            purpose=PaymentPurpose.ACCOUNT_CREDIT_DEPOSIT,
            amount=amount,
            target_reference=reference,
        )
        db.commit()
    targets = RecordingTargets({reference: SettlementTargetV1(account_id)})
    return payment, make_ports(targets)


def _wait(event: Event, what: str) -> None:
    assert event.wait(timeout=60), what


def _wait_blocked(scratch) -> None:
    database = scratch[0].rsplit("/", 1)[1]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        waiting, ungranted = _blocked_on_locks(database)
        if waiting >= 1 and ungranted >= 1:
            return
        time.sleep(0.2)
    raise AssertionError("the second session never blocked on the account lock")


def test_race_the_issue_pass_locks_first_the_deposit_waits_and_lands_after(
    scratch, monkeypatch
):
    # Balance 144 exactly covers the invoice. The issue pass holds the account
    # lock; a concurrent deposit of 50 must wait, then land as plain balance.
    scope, account_id, (doc,) = _world(scratch, balance="144.00")
    payment, ports = _deposit_payment(scratch, scope, account_id, "50.00")
    locked, release = Event(), Event()
    real = flows.get_funding_availability

    def hold_after_lock(*args, **kwargs):
        result = real(*args, **kwargs)
        if current_thread().name == "issue":
            locked.set()
            _wait(release, "release")
        return result

    monkeypatch.setattr(flows, "get_funding_availability", hold_after_lock)
    results, errors = {}, []

    def issue():
        try:
            results["issue"] = _issue(scratch, scope, doc)
        except BaseException as exc:
            errors.append(exc)

    def dep():
        try:
            results["deposit"] = assembly_settle(
                scratch, scope, payment.command(), ports
            )
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=issue, name="issue")
    first.start()
    _wait(locked, "the issue pass never took the lock")
    second = Thread(target=dep, name="deposit")
    second.start()
    _wait_blocked(scratch)
    assert "deposit" not in results  # still waiting behind the issue pass
    release.set()
    first.join(timeout=120)
    second.join(timeout=120)
    assert errors == []
    assert results["issue"].applied_document_ids == (doc,)
    assert results["deposit"].applied_document_ids == ()
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, doc) == 0
        assert _allocations(db) == 1
        assert _credit(db, scope, account_id) == 50  # never negative


def test_race_the_deposit_locks_first_and_pays_the_invoice_the_issue_pass_sees_resolved(
    scratch, monkeypatch
):
    scope, account_id, (doc,) = _world(scratch)  # no balance yet
    payment, ports = _deposit_payment(scratch, scope, account_id, "144.00")
    locked, release = Event(), Event()
    real = flows.accept_settlement

    def hold_after_lock(*args, **kwargs):
        result = real(*args, **kwargs)
        if current_thread().name == "deposit":
            locked.set()
            _wait(release, "release")
        return result

    monkeypatch.setattr(flows, "accept_settlement", hold_after_lock)
    results, errors = {}, []

    def dep():
        try:
            results["deposit"] = assembly_settle(
                scratch, scope, payment.command(), ports
            )
        except BaseException as exc:
            errors.append(exc)

    def issue():
        try:
            results["issue"] = _issue(scratch, scope, doc)
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=dep, name="deposit")
    first.start()
    _wait(locked, "the deposit never took the lock")
    second = Thread(target=issue, name="issue")
    second.start()
    _wait_blocked(scratch)
    assert "issue" not in results
    release.set()
    first.join(timeout=120)
    second.join(timeout=120)
    assert errors == []
    assert results["deposit"].applied_document_ids == (doc,)
    assert results["issue"].applied_document_ids == ()
    assert results["issue"].reason_code == "invoice_not_open"
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, doc) == 0
        assert _allocations(db) == 1  # paid once, not twice
        assert _credit(db, scope, account_id) == 0


def test_race_two_issue_triggers_on_one_account_pay_one_whole_invoice(scratch):
    # 144 covers exactly one of two 144 invoices; both triggers run a full pass.
    scope, account_id, (one, two) = _world(
        scratch, balance="144.00", invoices=(("120.00", 0), ("120.00", 1))
    )
    results, errors = [], []

    def attempt(doc):
        try:
            results.append(_issue(scratch, scope, doc))
        except BaseException as exc:
            errors.append(exc)

    database = scratch[0].rsplit("/", 1)[1]
    threads = [Thread(target=attempt, args=(d,)) for d in (one, two)]
    with session(scratch, scope) as holder:
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
            if waiting >= 2 and ungranted >= 2:
                break
            time.sleep(0.2)
        holder.rollback()
    for thread in threads:
        thread.join(timeout=120)
    assert errors == []
    with session(scratch, scope) as db:
        remaining = sorted(_outstanding(db, scope, d) for d in (one, two))
        assert remaining == [0, 144]  # one paid whole, the other untouched
        assert _allocations(db) == 1
        assert _credit(db, scope, account_id) == 0
    assert sum(len(r.applied_document_ids) for r in results) == 1
    assert unrestricted(scratch, scope, account_id) == 0
