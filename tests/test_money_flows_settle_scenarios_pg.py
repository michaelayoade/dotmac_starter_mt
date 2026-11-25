"""Hosted PostgreSQL scenario proofs for money-flows F1 Settle.

Application of cash (whole invoices only: short, exact, over, multi-document,
skip-non-fitting, reseller), the attention
states and their retry, withholding, atomicity with the caller, tenant
isolation, and the live-catalog vocabulary of ``mod_mflows``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_billing import get_document_exposure
from dotmac_billing import models as billing_models
from dotmac_billing.service import rebuild_receivable_position
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_money_flows import (
    DepositApplication,
    SettlementTargetV1,
    SettlePolicyV1,
    SettleRefused,
    SettleState,
    SettleWithholdingV1,
    get_settle_instance,
    list_settle_attention,
    retry_settle,
    settle_confirmation,
)
from dotmac_money_flows.manifest import INSTANCE_TABLE_ALLOWLIST
from dotmac_money_flows.models import SettleInstance
from dotmac_money_flows.settle import order_open_documents
from dotmac_payments.contracts import ConfirmationSource, PaymentPurpose
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError

from tests.architecture.test_money_flows_module import schema_violations
from tests.billing_frozen_fixtures import withholding_command
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
INVOICE = PaymentPurpose.INVOICE_SETTLEMENT
MANUAL = ConfirmationSource.MANUAL


@pytest.fixture(scope="module")
def scratch():
    with scratch_database() as urls:
        yield urls


def _count(db, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where))


def _credit(db, scope, account_id):
    return rebuild_receivable_position(
        db,
        scope=scope,
        billing_account_id=account_id,
        currency="NGN",
        minor_units=2,
    ).available_credit


def _collectible(db, scope, document_id):
    exposure = get_document_exposure(db, scope=scope, document_id=document_id)
    return exposure.financial_state, exposure.collectible_receivable.amount


def _bound(scratch, *, amount, purpose=INVOICE, invoices=1, **seed):
    """An account with ``invoices`` open invoices and one payment bound to #1."""
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        documents = [seed_invoice(db, scope, account) for _ in range(invoices)]
        payment = seed_payment(
            db,
            scope,
            purpose=purpose,
            amount=amount,
            target_reference="target:1",
            **seed,
        )
        db.commit()
        account_id = account.id
        document_ids = [d.id for d in documents]
    bound = document_ids[0] if purpose != DEPOSIT else None
    targets = RecordingTargets({"target:1": SettlementTargetV1(account_id, bound)})
    return scope, account_id, document_ids, payment, targets


# --------------------------------------------------------------------------
# Application of cash
# --------------------------------------------------------------------------
def _seed_invoices(scratch, specs, *, bound=None, amount, purpose=INVOICE):
    """An account with invoices ``specs`` = [(net, due_month)] and one payment.

    Each invoice totals ``net`` plus 20% VAT. ``bound`` is the index of the
    invoice the payment is bound to (None for a deposit).
    """
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        documents = [
            seed_invoice(
                db,
                scope,
                account,
                net=net,
                due_at=datetime(2026, month, 1, 10, tzinfo=UTC),
            )
            for net, month in specs
        ]
        payment = seed_payment(
            db, scope, purpose=purpose, amount=amount, target_reference="target:1"
        )
        db.commit()
        account_id = account.id
        document_ids = [d.id for d in documents]
    target = SettlementTargetV1(
        account_id, None if bound is None else document_ids[bound]
    )
    targets = RecordingTargets({"target:1": target})
    return scope, account_id, document_ids, payment, targets


def _assert_whole_only(db, scope, document_ids, totals):
    """No document is ever part-paid: outstanding is its full total or zero."""
    for document_id, total in zip(document_ids, totals, strict=True):
        _, outstanding = _collectible(db, scope, document_id)
        assert outstanding in (0, total), (document_id, outstanding, total)


def test_a_short_bound_payment_stays_as_balance_and_the_invoice_stays_open(scratch):
    scope, account_id, (doc,), payment, targets = _bound(scratch, amount="50.00")
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    # Cash accepted, remainder as balance: SETTLED (not UNAPPLIED, which is the
    # human-attention state for a bound document that was not collectible).
    assert outcome.state is SettleState.SETTLED
    assert outcome.reason_code is None
    assert outcome.applied_document_ids == ()
    assert outcome.settlement_id is not None
    with session(scratch, scope) as db:
        state, outstanding = _collectible(db, scope, doc)
        assert (state, outstanding) != ("resolved", 0)
        assert outstanding == 120
        assert _credit(db, scope, account_id) == 50
        assert _count(db, billing_models.AllocationEffect) == 0
        assert list_settle_attention(db, scope=scope) == ()


def test_an_exact_bound_payment_pays_the_invoice_whole(scratch):
    scope, account_id, (doc,), payment, targets = _bound(scratch, amount="120.00")
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert outcome.state is SettleState.SETTLED
    assert outcome.applied_document_ids == (doc,)
    with session(scratch, scope) as db:
        assert _collectible(db, scope, doc) == ("resolved", 0)
        assert _credit(db, scope, account_id) == 0


def test_an_over_payment_pays_the_document_and_keeps_the_rest_as_credit(scratch):
    scope, account_id, (doc,), payment, targets = _bound(scratch, amount="200.00")
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert outcome.state is SettleState.SETTLED
    with session(scratch, scope) as db:
        assert _collectible(db, scope, doc) == ("resolved", 0)
        assert _credit(db, scope, account_id) == 80


def test_bound_excess_pays_other_whole_invoices_oldest_first_skipping_misfits(
    scratch,
):
    # Totals: bound 120; Sep 180 (does not fit the 130 excess); Oct 120 (fits);
    # Nov 60 (does not fit the 10 left, though it is smaller than Sep).
    specs = [("100.00", 12), ("150.00", 9), ("100.00", 10), ("50.00", 11)]
    totals = [120, 180, 120, 60]
    scope, account_id, docs, payment, targets = _seed_invoices(
        scratch, specs, bound=0, amount="250.00"
    )
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert outcome.state is SettleState.SETTLED
    assert outcome.applied_document_ids == (docs[0], docs[2])
    with session(scratch, scope) as db:
        assert _collectible(db, scope, docs[0]) == ("resolved", 0)
        assert _collectible(db, scope, docs[2]) == ("resolved", 0)
        assert _collectible(db, scope, docs[1])[1] == 180
        assert _collectible(db, scope, docs[3])[1] == 60
        assert _credit(db, scope, account_id) == 10
        _assert_whole_only(db, scope, docs, totals)


def test_a_short_bound_payment_pays_no_other_invoice(scratch):
    # 70 is short of the bound 120 but would cover the 60 invoice: nothing is
    # "excess" while the bound invoice is unpaid, so the 70 stays as balance.
    scope, account_id, docs, payment, targets = _seed_invoices(
        scratch, [("100.00", 12), ("50.00", 9)], bound=0, amount="70.00"
    )
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert outcome.state is SettleState.SETTLED
    assert outcome.applied_document_ids == ()
    with session(scratch, scope) as db:
        _assert_whole_only(db, scope, docs, [120, 60])
        assert _collectible(db, scope, docs[0])[1] == 120
        assert _collectible(db, scope, docs[1])[1] == 60
        assert _credit(db, scope, account_id) == 70


def test_a_misfit_refusal_leaves_no_posting_group_and_no_idempotency_record(
    scratch,
):
    from dotmac_kernel.idempotency_models import IdempotencyRecord

    scope, account_id, (doc,), payment, targets = _bound(scratch, amount="50.00")
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert outcome.applied_document_ids == ()
    with session(scratch, scope) as db:
        # The draw was attempted and refused; nothing of it survives.
        assert _count(db, billing_models.AllocationEffect) == 0
        assert (
            _count(
                db,
                billing_models.PostingGroup,
                billing_models.PostingGroup.source_ref.like("money-flows:settle:%"),
            )
            == 0
        )
        assert (
            _count(
                db,
                IdempotencyRecord,
                IdempotencyRecord.scope == "billing.credit.apply",
            )
            == 0
        )


def test_any_other_billing_refusal_at_the_draw_propagates(scratch, monkeypatch):
    from dotmac_billing import BillingRuleViolation
    from dotmac_money_flows import settle as settle_module

    def refuse(*args, **kwargs):
        raise BillingRuleViolation("account_held", "account is held")

    monkeypatch.setattr(settle_module, "apply_available_credit", refuse)
    scope, account_id, (doc,), payment, targets = _bound(scratch, amount="120.00")
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    # Not swallowed as a misfit: the whole transaction rolled back and the
    # refusal was recorded, so no cash is in the ledger.
    assert outcome.state is SettleState.REFUSED
    assert outcome.reason_code == "billing.account_held"
    with session(scratch, scope) as db:
        assert _count(db, billing_models.ConfirmedSettlement) == 0
        assert _collectible(db, scope, doc)[1] == 120


def test_a_withholding_room_misfit_does_not_cap_later_invoices(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        older = seed_invoice(
            db, scope, account, due_at=datetime(2026, 9, 1, 10, tzinfo=UTC)
        )
        newer = seed_invoice(
            db, scope, account, due_at=datetime(2026, 10, 1, 10, tzinfo=UTC)
        )
        # Expected withholding frozen on the older invoice, never evidenced:
        # the balance may not pay it whole (Q-F), whatever the balance is.
        withholding_command(
            db, account.id, older.id, scope=scope, claimed="30.00", accepted="30.00"
        )
        payment = seed_payment(
            db, scope, purpose=DEPOSIT, amount="240.00", target_reference="target:1"
        )
        db.commit()
        account_id, old_id, new_id = account.id, older.id, newer.id
    targets = RecordingTargets({"target:1": SettlementTargetV1(account_id)})
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert outcome.state is SettleState.SETTLED
    assert outcome.applied_document_ids == (new_id,)
    with session(scratch, scope) as db:
        assert _collectible(db, scope, old_id)[1] == 120
        assert _collectible(db, scope, new_id) == ("resolved", 0)
        assert _credit(db, scope, account_id) == 120


def test_a_payment_net_of_withholding_without_a_certificate_stays_as_balance(
    scratch,
):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        invoice = seed_invoice(db, scope, account)
        withholding_command(
            db, account.id, invoice.id, scope=scope, claimed="30.00", accepted="30.00"
        )
        payment = seed_payment(
            db, scope, purpose=INVOICE, amount="90.00", target_reference="target:1"
        )
        db.commit()
        account_id, doc = account.id, invoice.id
    targets = RecordingTargets({"target:1": SettlementTargetV1(account_id, doc)})
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert outcome.state is SettleState.SETTLED
    assert outcome.applied_document_ids == ()
    with session(scratch, scope) as db:
        assert _collectible(db, scope, doc)[1] == 120
        assert _credit(db, scope, account_id) == 90
        assert _count(db, billing_models.WithholdingDischarge) == 0


def test_the_balance_and_a_later_payment_together_pay_a_whole_invoice(scratch):
    scope, account_id, (doc,), first, targets = _bound(scratch, amount="50.00")
    with session(scratch, scope) as db:
        second = seed_payment(
            db, scope, purpose=INVOICE, amount="70.00", target_reference="target:1"
        )
        db.commit()
    ports = make_ports(targets)
    first_outcome = assembly_settle(scratch, scope, first.command(), ports)
    assert first_outcome.applied_document_ids == ()

    outcome = assembly_settle(scratch, scope, second.command(), ports)

    assert outcome.state is SettleState.SETTLED
    assert outcome.applied_document_ids == (doc,)
    with session(scratch, scope) as db:
        assert _collectible(db, scope, doc) == ("resolved", 0)
        assert _credit(db, scope, account_id) == 0


def test_a_deposit_pays_open_documents_in_policy_order_across_documents(scratch):
    scope, account_id, docs, payment, targets = _bound(
        scratch, amount="250.00", purpose=DEPOSIT, invoices=3
    )
    with session(scratch, scope) as db:
        from dotmac_billing import list_open_receivable_documents
        from dotmac_kernel.money import Currency

        page = list_open_receivable_documents(
            db,
            scope=scope,
            account=account_id,
            currency=Currency("NGN", 2),
            limit=100,
        )
        expected = [d.document_id for d in order_open_documents(list(page.entries))]
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert outcome.state is SettleState.SETTLED
    # 250 pays two whole 120 invoices; the third (120) does not fit the 10 left.
    assert list(outcome.applied_document_ids) == expected[:2]
    with session(scratch, scope) as db:
        first, second, third = (_collectible(db, scope, d) for d in expected)
        assert first == ("resolved", 0)
        assert second == ("resolved", 0)
        assert third[1] == 120 and third[0] != "resolved"
        assert _credit(db, scope, account_id) == 10
        _assert_whole_only(db, scope, docs, [120, 120, 120])


def test_a_deposit_skips_invoices_that_do_not_fit_and_never_part_pays(scratch):
    # Oldest due first: Sep 180 fits (20 left); Oct 120 and Nov 60 do not;
    # Dec 24 fits (the smaller one is reached after two misfits).
    specs = [("100.00", 10), ("150.00", 9), ("50.00", 11), ("20.00", 12)]
    totals = [120, 180, 60, 24]
    scope, account_id, docs, payment, targets = _seed_invoices(
        scratch, specs, amount="204.00", purpose=DEPOSIT
    )
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert outcome.state is SettleState.SETTLED
    assert outcome.applied_document_ids == (docs[1], docs[3])
    with session(scratch, scope) as db:
        assert _collectible(db, scope, docs[1]) == ("resolved", 0)
        assert _collectible(db, scope, docs[3]) == ("resolved", 0)
        assert _collectible(db, scope, docs[0])[1] == 120
        assert _collectible(db, scope, docs[2])[1] == 60
        assert _credit(db, scope, account_id) == 0
        _assert_whole_only(db, scope, docs, totals)


def test_a_deposit_that_fits_no_invoice_stays_entirely_as_balance(scratch):
    scope, account_id, docs, payment, targets = _bound(
        scratch, amount="100.00", purpose=DEPOSIT, invoices=2
    )
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert outcome.state is SettleState.SETTLED
    assert outcome.applied_document_ids == ()
    with session(scratch, scope) as db:
        _assert_whole_only(db, scope, docs, [120, 120])
        assert _credit(db, scope, account_id) == 100
        assert _count(db, billing_models.AllocationEffect) == 0


def test_a_deposit_under_policy_none_only_accepts_the_cash(scratch):
    scope, account_id, docs, payment, targets = _bound(
        scratch, amount="50.00", purpose=DEPOSIT, invoices=2
    )
    policy = SettlePolicyV1(version="p1", deposit_application=DepositApplication.NONE)
    outcome = assembly_settle(
        scratch, scope, payment.command(), make_ports(targets, policy)
    )
    assert outcome.state is SettleState.SETTLED and outcome.applied_document_ids == ()
    with session(scratch, scope) as db:
        assert _credit(db, scope, account_id) == 50
        assert _count(db, billing_models.AllocationEffect) == 0


def test_a_reseller_remittance_spans_documents_of_different_service_refs(scratch):
    scope = new_scope(scratch)
    refs = ("service:a", "service:b", "service:c")
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        docs = [seed_invoice(db, scope, account, service_ref=ref) for ref in refs]
        payment = seed_payment(
            db, scope, purpose=DEPOSIT, amount="360.00", target_reference="reseller:1"
        )
        db.commit()
        account_id, ids = account.id, {d.id for d in docs}
    targets = RecordingTargets({"reseller:1": SettlementTargetV1(account_id)})
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert set(outcome.applied_document_ids) == ids
    with session(scratch, scope) as db:
        seen = set()
        for document_id in ids:
            exposure = get_document_exposure(db, scope=scope, document_id=document_id)
            assert exposure.financial_state == "resolved"
            seen.add(exposure.service_ref)
        assert seen == set(refs)


def test_a_closed_bound_document_leaves_the_cash_as_credit_unapplied(scratch):
    scope, account_id, (doc,), first, targets = _bound(scratch, amount="120.00")
    with session(scratch, scope) as db:
        second = seed_payment(
            db, scope, purpose=INVOICE, amount="40.00", target_reference="target:1"
        )
        db.commit()
    ports = make_ports(targets)
    assert assembly_settle(scratch, scope, first.command(), ports).state is (
        SettleState.SETTLED
    )

    outcome = assembly_settle(scratch, scope, second.command(), ports)

    assert outcome.state is SettleState.UNAPPLIED
    assert outcome.reason_code == "bound_document_not_collectible"
    assert outcome.settlement_id is not None
    with session(scratch, scope) as db:
        assert _credit(db, scope, account_id) == 40
        assert [i.state for i in list_settle_attention(db, scope=scope)] == [
            SettleState.UNAPPLIED
        ]


def test_exactly_one_resolved_exposure_event_per_paid_document(scratch):
    scope, account_id, docs, payment, targets = _bound(
        scratch, amount="240.00", purpose=DEPOSIT, invoices=2
    )
    ports = make_ports(targets)
    assembly_settle(scratch, scope, payment.command(), ports)
    assembly_settle(scratch, scope, payment.command(), ports)  # replay

    with session(scratch, scope) as db:
        rows = db.execute(
            select(OutboxEvent.payload).where(
                OutboxEvent.event_type == "billing.receivable.exposure.v1"
            )
        ).scalars()
        resolved = [p for p in rows if p.get("financial_state") == "resolved"]
    assert len(resolved) == 2
    assert len({p["exposure_ref"] for p in resolved}) == 2


# --------------------------------------------------------------------------
# Refusal and retry
# --------------------------------------------------------------------------
def test_an_owner_refusal_is_recorded_then_retried_to_settled(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        wrong = seed_account(db, scope, currency="USD")
        right = seed_account(db, scope)
        payment = seed_payment(
            db, scope, purpose=DEPOSIT, amount="30.00", target_reference="acct:a"
        )
        db.commit()
        wrong_id, right_id = wrong.id, right.id
    targets = RecordingTargets({"acct:a": SettlementTargetV1(wrong_id)})
    ports = make_ports(targets, SettlePolicyV1(version="p1"))

    refused = assembly_settle(scratch, scope, payment.command(), ports)

    assert refused.state is SettleState.REFUSED
    assert refused.reason_code == "billing.money_identity_mismatch"
    with session(scratch, scope) as db:
        assert _count(db, billing_models.ConfirmedSettlement) == 0
        assert [i.id for i in list_settle_attention(db, scope=scope)] == [
            refused.instance_id
        ]
        attention = _count(
            db, OutboxEvent, OutboxEvent.event_type == "money_flows.settle.attention.v1"
        )
        assert attention == 1

    # The account is corrected; a stale version and a wrong state are refused
    # before any owner call.
    targets.mapping["acct:a"] = SettlementTargetV1(right_id)
    calls = len(targets.calls)
    with session(scratch, scope) as db:
        with pytest.raises(SettleRefused) as stale:
            retry_settle(
                db,
                scope=scope,
                instance_id=refused.instance_id,
                expected_version=7,
                actor_ref="finance-user:1",
                command=payment.command(),
                ports=ports,
            )
        assert stale.value.code == "stale_version"
    assert len(targets.calls) == calls

    ports_v2 = make_ports(targets, SettlePolicyV1(version="p2"))
    with session(scratch, scope) as db:
        retried = retry_settle(
            db,
            scope=scope,
            instance_id=refused.instance_id,
            expected_version=refused.version,
            actor_ref="finance-user:1",
            command=payment.command(),
            ports=ports_v2,
        )
        db.commit()
    assert retried.state is SettleState.SETTLED
    assert retried.version == refused.version + 1
    with session(scratch, scope) as db:
        view = get_settle_instance(db, scope=scope, instance_id=refused.instance_id)
        assert view.policy_version == "p2"
        assert view.actor_ref == "finance-user:1"
        assert view.billing_account_id == right_id
        assert _count(db, billing_models.ConfirmedSettlement) == 1
        with pytest.raises(SettleRefused) as again:
            retry_settle(
                db,
                scope=scope,
                instance_id=refused.instance_id,
                expected_version=view.version,
                actor_ref="finance-user:1",
                command=payment.command(),
                ports=ports_v2,
            )
        assert again.value.code == "retry_not_allowed"


def test_a_refusal_record_never_overwrites_a_settled_instance(scratch):
    from dotmac_money_flows import record_settle_refusal

    scope, account_id, _, payment, targets = _bound(
        scratch, amount="10.00", purpose=DEPOSIT
    )
    ports = make_ports(targets)
    settled = assembly_settle(scratch, scope, payment.command(), ports)
    with session(scratch, scope) as db:
        outcome = record_settle_refusal(
            db,
            scope=scope,
            command=payment.command(),
            ports=ports,
            refusal_code="billing.late_owner_error",
        )
        db.commit()
    assert outcome.replayed and outcome.state is SettleState.SETTLED
    assert outcome.instance_id == settled.instance_id


# --------------------------------------------------------------------------
# Withholding
# --------------------------------------------------------------------------
def test_manual_cash_with_withholding_discharges_the_invoice(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        invoice = seed_invoice(db, scope, account)
        payment = seed_payment(
            db,
            scope,
            purpose=INVOICE,
            amount="90.00",
            target_reference="target:1",
            source=MANUAL,
        )
        wht = withholding_command(
            db, account.id, invoice.id, scope=scope, claimed="30.00", accepted="30.00"
        )
        db.commit()
        account_id, doc = account.id, invoice.id
    withholding = SettleWithholdingV1(
        tax_evidence=wht.tax_evidence,
        claim_ref=wht.claim_ref,
        claimed=wht.claimed_amount,
        accepted=wht.accepted_amount,
        source_owner=wht.source_owner,
        source_ref=wht.source_ref,
        idempotency_key=wht.idempotency_key,
        authorization_actor_ref=wht.authorization_actor_ref,
        authorization_evidence_ref=wht.authorization_evidence_ref,
    )
    targets = RecordingTargets({"target:1": SettlementTargetV1(account_id, doc)})
    policy = SettlePolicyV1(
        version="p1", accepted_confirmation_sources=frozenset({"MANUAL"})
    )
    ports = make_ports(targets, policy)
    command = payment.command(withholding=withholding)

    outcome = assembly_settle(scratch, scope, command, ports)
    replay = assembly_settle(scratch, scope, command, ports)

    assert outcome.state is SettleState.SETTLED, outcome
    assert replay.replayed
    with session(scratch, scope) as db:
        assert _collectible(db, scope, doc) == ("resolved", 0)
        assert _count(db, billing_models.WithholdingDischarge) == 1
        view = get_settle_instance(db, scope=scope, instance_id=outcome.instance_id)
        assert view.withholding_discharge_ref is not None
        # V1 caller: one reference, mirrored in the new list view.
        assert view.withholding_discharge_refs == (view.withholding_discharge_ref,)


def test_withholding_on_a_non_manual_source_is_refused_before_any_write(scratch):
    scope, account_id, (doc,), payment, targets = _bound(scratch, amount="90.00")
    evidence = SimpleNamespace(document_id=doc)
    withholding = SettleWithholdingV1(
        tax_evidence=evidence,
        claim_ref="c",
        claimed=None,
        accepted=None,
        source_owner="o",
        source_ref="s",
        idempotency_key="k",
        authorization_actor_ref="a",
        authorization_evidence_ref="e",
    )
    outcome = assembly_settle(
        scratch, scope, payment.command(withholding=withholding), make_ports(targets)
    )
    assert outcome.state is SettleState.REFUSED
    assert outcome.reason_code == "withholding_requires_manual_source"
    with session(scratch, scope) as db:
        assert _count(db, billing_models.ConfirmedSettlement) == 0
        assert _count(db, billing_models.WithholdingDischarge) == 0
        assert _count(db, billing_models.AllocationEffect) == 0
    assert targets.calls == []


# --------------------------------------------------------------------------
# C2b: several deductions on one MANUAL confirmation
# --------------------------------------------------------------------------
def _deduction(wht) -> SettleWithholdingV1:
    return SettleWithholdingV1(
        tax_evidence=wht.tax_evidence,
        claim_ref=wht.claim_ref,
        claimed=wht.claimed_amount,
        accepted=wht.accepted_amount,
        source_owner=wht.source_owner,
        source_ref=wht.source_ref,
        idempotency_key=wht.idempotency_key,
        authorization_actor_ref=wht.authorization_actor_ref,
        authorization_evidence_ref=wht.authorization_evidence_ref,
    )


def _manual_policy() -> SettlePolicyV1:
    return SettlePolicyV1(
        version="p1",
        accepted_confirmation_sources=frozenset({"MANUAL", "PROVIDER_CALLBACK"}),
    )


def test_two_deductions_plus_cash_discharge_one_invoice(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        invoice = seed_invoice(db, scope, account)  # 120 with VAT
        payment = seed_payment(
            db,
            scope,
            purpose=INVOICE,
            amount="90.00",
            target_reference="target:1",
            source=MANUAL,
        )
        tax = withholding_command(
            db,
            account.id,
            invoice.id,
            scope=scope,
            suffix="wht",
            claimed="20.00",
            accepted="20.00",
        )
        stamp = withholding_command(
            db,
            account.id,
            invoice.id,
            scope=scope,
            suffix="stamp",
            claimed="10.00",
            accepted="10.00",
        )
        db.commit()
        account_id, doc = account.id, invoice.id
    targets = RecordingTargets({"target:1": SettlementTargetV1(account_id, doc)})
    ports = make_ports(targets, _manual_policy())
    command = payment.command_v2(deductions=(_deduction(tax), _deduction(stamp)))

    outcome = assembly_settle(scratch, scope, command, ports)
    replay = assembly_settle(scratch, scope, command, ports)

    assert outcome.state is SettleState.SETTLED, outcome
    assert outcome.applied_document_ids == (doc,)
    assert replay.replayed
    with session(scratch, scope) as db:
        assert _collectible(db, scope, doc) == ("resolved", 0)
        assert _count(db, billing_models.WithholdingDischarge) == 2
        view = get_settle_instance(db, scope=scope, instance_id=outcome.instance_id)
        assert len(view.withholding_discharge_refs) == 2
        assert len(set(view.withholding_discharge_refs)) == 2
        assert view.withholding_discharge_ref == view.withholding_discharge_refs[0]


def test_a_duplicate_deduction_component_is_refused_before_any_write(scratch):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        invoice = seed_invoice(db, scope, account)
        payment = seed_payment(
            db,
            scope,
            purpose=INVOICE,
            amount="90.00",
            target_reference="target:1",
            source=MANUAL,
        )
        tax = withholding_command(
            db,
            account.id,
            invoice.id,
            scope=scope,
            suffix="same",
            claimed="30.00",
            accepted="30.00",
        )
        db.commit()
        account_id, doc = account.id, invoice.id
    targets = RecordingTargets({"target:1": SettlementTargetV1(account_id, doc)})
    command = payment.command_v2(deductions=(_deduction(tax), _deduction(tax)))

    outcome = assembly_settle(
        scratch, scope, command, make_ports(targets, _manual_policy())
    )

    assert outcome.state is SettleState.REFUSED
    assert outcome.reason_code == "duplicate_deduction_component"
    with session(scratch, scope) as db:
        assert _count(db, billing_models.ConfirmedSettlement) == 0
        assert _count(db, billing_models.WithholdingDischarge) == 0
        assert _count(db, billing_models.AllocationEffect) == 0
        assert _collectible(db, scope, doc)[1] == 120
    assert targets.calls == []


def test_deductions_on_a_non_manual_confirmation_are_refused_before_any_write(
    scratch,
):
    scope, account_id, (doc,), payment, targets = _bound(scratch, amount="90.00")
    one = SettleWithholdingV1(
        tax_evidence=SimpleNamespace(document_id=doc, component_ref="c1"),
        claim_ref="c",
        claimed=None,
        accepted=None,
        source_owner="o",
        source_ref="s",
        idempotency_key="k1",
        authorization_actor_ref="a",
        authorization_evidence_ref="e",
    )
    two = SettleWithholdingV1(
        tax_evidence=SimpleNamespace(document_id=doc, component_ref="c2"),
        claim_ref="c",
        claimed=None,
        accepted=None,
        source_owner="o",
        source_ref="s",
        idempotency_key="k2",
        authorization_actor_ref="a",
        authorization_evidence_ref="e",
    )
    outcome = assembly_settle(
        scratch,
        scope,
        payment.command_v2(deductions=(one, two)),
        make_ports(targets),
    )
    assert outcome.state is SettleState.REFUSED
    assert outcome.reason_code == "withholding_requires_manual_source"
    with session(scratch, scope) as db:
        assert _count(db, billing_models.ConfirmedSettlement) == 0
        assert _count(db, billing_models.WithholdingDischarge) == 0
    assert targets.calls == []


def test_a_deduction_then_the_balance_pays_the_now_fitting_invoice(scratch):
    """Cash already on the account pays the invoice once a deduction fits it.

    The first payment (90.00) stays as balance: the invoice has 30.00 of
    frozen withholding not yet discharged. The second, MANUAL, confirmation
    brings the deduction and 1.00 more cash; the balance pass runs after the
    deduction is accepted and pays the invoice whole.
    """
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        account = seed_account(db, scope)
        invoice = seed_invoice(db, scope, account)
        first = seed_payment(
            db, scope, purpose=DEPOSIT, amount="90.00", target_reference="target:1"
        )
        second = seed_payment(
            db,
            scope,
            purpose=DEPOSIT,
            amount="1.00",
            target_reference="target:1",
            source=MANUAL,
        )
        tax = withholding_command(
            db, account.id, invoice.id, scope=scope, claimed="30.00", accepted="30.00"
        )
        db.commit()
        account_id, doc = account.id, invoice.id
    targets = RecordingTargets({"target:1": SettlementTargetV1(account_id)})
    ports = make_ports(targets, _manual_policy())

    before = assembly_settle(scratch, scope, first.command(), ports)
    assert before.applied_document_ids == ()
    with session(scratch, scope) as db:
        assert _collectible(db, scope, doc)[1] == 120

    outcome = assembly_settle(
        scratch, scope, second.command_v2(deductions=(_deduction(tax),)), ports
    )

    assert outcome.state is SettleState.SETTLED, outcome
    assert outcome.applied_document_ids == (doc,)
    with session(scratch, scope) as db:
        assert _collectible(db, scope, doc) == ("resolved", 0)
        assert _credit(db, scope, account_id) == 1


# --------------------------------------------------------------------------
# Atomicity, isolation, vocabulary
# --------------------------------------------------------------------------
def test_a_caller_rollback_leaves_no_row_and_no_owner_effect(scratch):
    scope, account_id, docs, payment, targets = _bound(
        scratch, amount="200.00", purpose=DEPOSIT, invoices=2
    )
    with session(scratch, scope) as db:
        outcome = settle_confirmation(
            db, scope=scope, command=payment.command(), ports=make_ports(targets)
        )
        assert outcome.state is SettleState.SETTLED
        db.rollback()
    with session(scratch, scope) as db:
        assert _count(db, SettleInstance) == 0
        assert _count(db, billing_models.ConfirmedSettlement) == 0
        assert _count(db, billing_models.AllocationEffect) == 0
        assert (
            _count(db, OutboxEvent, OutboxEvent.event_type.like("money_flows.%")) == 0
        )


def test_events_carry_references_only(scratch):
    scope, account_id, docs, payment, targets = _bound(
        scratch, amount="200.00", purpose=DEPOSIT, invoices=2
    )
    assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    with session(scratch, scope) as db:
        payload = db.execute(
            select(OutboxEvent.payload).where(
                OutboxEvent.event_type == "money_flows.settle.settled.v1"
            )
        ).scalar_one()
    assert set(payload) == {
        "instance_id",
        "confirmation_id",
        "intent_id",
        "state",
        "reason_code",
        "version",
        "billing_account_id",
        "settlement_id",
    }
    assert all(
        value is None or isinstance(value, str | int) for value in payload.values()
    )


def test_instances_are_tenant_isolated(scratch):
    scope, account_id, docs, payment, targets = _bound(
        scratch, amount="10.00", purpose=DEPOSIT
    )
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    other = new_scope(scratch)
    with session(scratch, other) as db:
        assert _count(db, SettleInstance) == 0
        assert (
            get_settle_instance(db, scope=other, instance_id=outcome.instance_id)
            is None
        )
        assert list_settle_attention(db, scope=other) == ()
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
        with pytest.raises(DBAPIError):
            db.flush()
        db.rollback()
    with session(scratch, scope) as db:
        assert _count(db, SettleInstance) == 1


_TYPES = {
    "numeric": "Numeric",
    "double precision": "Double",
    "real": "REAL",
    "money": "Numeric",
}


def _catalog(connection) -> dict[str, dict[str, str]]:
    rows = connection.execute(
        text(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = 'mod_mflows'"
        )
    ).all()
    tables: dict[str, dict[str, str]] = {}
    for table, column, data_type in rows:
        tables.setdefault(table, {})[column] = _TYPES.get(data_type, data_type)
    return tables


def test_f4_the_live_schema_obeys_the_vocabulary_rule(scratch):
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        live = _catalog(connection)
        assert set(live) == set(INSTANCE_TABLE_ALLOWLIST)
        assert schema_violations(live) == []
        # Planted: the same rule refuses an amount column and a float column.
        connection.rollback()  # end the read's autobegun transaction
        transaction = connection.begin()
        connection.execute(
            text(
                "ALTER TABLE mod_mflows.settle_instances "
                "ADD COLUMN paid_amount numeric(20,6), "
                "ADD COLUMN ratio_ref double precision"
            )
        )
        planted = schema_violations(_catalog(connection))
        transaction.rollback()
    engine.dispose()
    assert any("forbidden word" in p for p in planted)
    assert any("money-capable" in p for p in planted)
    assert any("neither a common column" in p for p in planted)
