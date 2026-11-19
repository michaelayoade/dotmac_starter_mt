"""Unit proofs for GAP-3 ``apply_balance_on_invoice_issued``: gates and ordering.

No database: the Billing owner calls are replaced by recording fakes at the
module seam, so each gate, the lock order, the source reference and the dating
rule are exercised in isolation. ``db`` is a bare object: any write or event
through it would fail the test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from dotmac_billing import BillingRuleViolation, OpenReceivableDocumentV1
from dotmac_billing.contracts import DueDateBasisStatus
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Currency, Money
from dotmac_money_flows import (
    DepositApplication,
    InvoiceIssuedV1,
    SettlePolicyV1,
    SettlePorts,
    SettleRefused,
    apply_balance_on_invoice_issued,
)
from dotmac_money_flows import settle as flows

NOW = datetime(2026, 11, 1, tzinfo=UTC)
NGN = Currency("NGN", 2)
ACCOUNT = uuid4()
SCOPE = TenantScope(uuid4())


def _money(amount: str) -> Money:
    return Money.of(amount, NGN)


def _doc(doc_id, amount, *, issued=0, due=0):
    return OpenReceivableDocumentV1(
        document_id=doc_id,
        issued_at=NOW + timedelta(days=issued),
        due_at=NOW + timedelta(days=due),
        collectible_receivable=_money(amount),
        service_ref=None,
        source_version=1,
        financial_state="open",
        due_at_basis=DueDateBasisStatus.VERIFIED,
    )


class _World:
    """Records every owner call the command makes, in order."""

    def __init__(self, monkeypatch, *, documents, trigger, **kw):
        self.calls: list[str] = []
        self.paid: list[SimpleNamespace] = []
        self.terms_at: list[datetime] = []
        self.policy = kw.get("policy", SettlePolicyV1(version="p"))
        self.balance = kw.get("balance", "500.00")
        self.contract = kw.get("contract", "postpaid")
        self.outstanding = kw.get("outstanding", "100.00")
        self.walk = documents
        self.refuse = kw.get("refuse", {})
        self.fail_with = kw.get("fail_with")
        self.trigger = trigger

        def exposure(db, *, scope, document_id):
            self.calls.append("exposure")
            return SimpleNamespace(
                billing_account_id=ACCOUNT,
                collectible_receivable=_money(self.outstanding),
            )

        def availability(db, *, scope, request):
            self.calls.append("lock")
            assert request.billing_account_id == ACCOUNT
            return SimpleNamespace(unrestricted=_money(self.balance))

        def listing(db, scope, account_id, currency):
            self.calls.append("walk")
            return self.walk

        def terms(db, *, scope, account_id, at):
            self.calls.append("terms")
            self.terms_at.append(at)
            return SimpleNamespace(contract_type=SimpleNamespace(value=self.contract))

        def draw(db, *, scope, command):
            self.calls.append("draw")
            if self.fail_with is not None:
                raise self.fail_with
            code = self.refuse.get(command.document_id)
            if code is not None:
                raise BillingRuleViolation(code, "refused")
            self.paid.append(command)

        def no_event(*a, **k):
            raise AssertionError("GAP-3 emits no event")

        monkeypatch.setattr(flows, "get_document_exposure", exposure)
        monkeypatch.setattr(flows, "get_funding_availability", availability)
        monkeypatch.setattr(flows, "_walk_open_documents", listing)
        monkeypatch.setattr(flows, "get_effective_account_terms", terms)
        monkeypatch.setattr(flows, "apply_available_credit", draw)
        monkeypatch.setattr(flows, "enqueue_event", no_event)

    def run(self):
        reader = SimpleNamespace(read=lambda db, *, scope: self.policy)
        ports = SettlePorts(targets=SimpleNamespace(), policy=reader)
        return apply_balance_on_invoice_issued(
            object(),
            scope=SCOPE,
            request=InvoiceIssuedV1(self.trigger),
            ports=ports,
        )


def test_the_account_lock_is_taken_first_then_the_gates_are_read(monkeypatch):
    new = uuid4()
    world = _World(monkeypatch, trigger=new, documents=[_doc(new, "100.00")])
    outcome = world.run()
    assert world.calls[:2] == ["exposure", "lock"]
    assert world.calls.index("lock") < world.calls.index("terms")
    assert outcome.applied_document_ids == (new,)
    assert outcome.reason_code is None
    assert outcome.billing_account_id == ACCOUNT


def test_policy_none_is_a_quiet_no_op(monkeypatch):
    new = uuid4()
    policy = SettlePolicyV1(version="p", deposit_application=DepositApplication.NONE)
    world = _World(
        monkeypatch, trigger=new, documents=[_doc(new, "100.00")], policy=policy
    )
    outcome = world.run()
    assert outcome.reason_code == "deposit_application_off"
    assert outcome.applied_document_ids == ()
    assert "draw" not in world.calls and "walk" not in world.calls


def test_a_new_invoice_that_is_no_longer_open_is_a_no_op(monkeypatch):
    new = uuid4()
    world = _World(
        monkeypatch, trigger=new, documents=[_doc(new, "100.00")], outstanding="0.00"
    )
    assert world.run().reason_code == "invoice_not_open"
    assert "draw" not in world.calls


def test_a_new_invoice_missing_from_the_open_list_is_a_no_op(monkeypatch):
    new = uuid4()
    world = _World(monkeypatch, trigger=new, documents=[_doc(uuid4(), "10.00")])
    assert world.run().reason_code == "invoice_not_open"
    assert "draw" not in world.calls


def test_no_balance_exits_before_any_document_walk(monkeypatch):
    new = uuid4()
    world = _World(
        monkeypatch, trigger=new, documents=[_doc(new, "100.00")], balance="0.00"
    )
    assert world.run().reason_code == "no_balance"
    assert "walk" not in world.calls and "draw" not in world.calls


def test_a_prepaid_account_is_a_no_op_even_with_balance_and_an_open_invoice(
    monkeypatch,
):
    new = uuid4()
    world = _World(
        monkeypatch,
        trigger=new,
        documents=[_doc(new, "100.00")],
        contract="prepaid",
    )
    outcome = world.run()
    assert outcome.reason_code == "account_not_postpaid"
    assert outcome.applied_document_ids == ()
    assert "draw" not in world.calls


def test_the_terms_are_read_at_the_new_invoices_issue_date(monkeypatch):
    new = uuid4()
    world = _World(monkeypatch, trigger=new, documents=[_doc(new, "100.00", issued=3)])
    world.run()
    assert world.terms_at == [NOW + timedelta(days=3)]


def test_the_pass_pays_oldest_due_first_with_deterministic_source_refs(monkeypatch):
    new, older = uuid4(), uuid4()
    world = _World(
        monkeypatch,
        trigger=new,
        balance="500.00",
        documents=[
            _doc(new, "100.00", issued=10, due=40),
            _doc(older, "80.00", issued=1, due=5),
        ],
    )
    outcome = world.run()
    assert outcome.applied_document_ids == (older, new)
    assert [c.source_ref for c in world.paid] == [
        f"money-flows:settle:issue:{new}:{older}",
        f"money-flows:settle:issue:{new}:{new}",
    ]
    assert [c.amount for c in world.paid] == [_money("80.00"), _money("100.00")]


def test_a_replay_derives_the_same_source_references(monkeypatch):
    new = uuid4()
    docs = [_doc(new, "100.00")]
    first = _World(monkeypatch, trigger=new, documents=docs)
    first.run()
    second = _World(monkeypatch, trigger=new, documents=docs)
    second.run()
    assert [c.source_ref for c in first.paid] == [c.source_ref for c in second.paid]


def test_the_application_date_is_the_later_of_the_two_issue_dates(monkeypatch):
    new, older, newer = uuid4(), uuid4(), uuid4()
    world = _World(
        monkeypatch,
        trigger=new,
        documents=[
            _doc(older, "10.00", issued=1, due=1),
            _doc(new, "10.00", issued=5, due=2),
            _doc(newer, "10.00", issued=9, due=3),
        ],
    )
    world.run()
    dates = {c.document_id: c.occurred_at for c in world.paid}
    assert dates[older] == NOW + timedelta(days=5)  # never before the new invoice
    assert dates[new] == NOW + timedelta(days=5)
    assert dates[newer] == NOW + timedelta(days=9)  # never before the paid invoice


def test_a_misfit_is_skipped_and_a_smaller_invoice_is_still_paid(monkeypatch):
    new, big = uuid4(), uuid4()
    world = _World(
        monkeypatch,
        trigger=new,
        documents=[_doc(big, "900.00", due=1), _doc(new, "50.00", due=2)],
        refuse={big: "credit_insufficient_backing"},
    )
    outcome = world.run()
    assert outcome.applied_document_ids == (new,)


def test_when_nothing_fits_the_outcome_says_so_and_posts_nothing(monkeypatch):
    new = uuid4()
    world = _World(
        monkeypatch,
        trigger=new,
        documents=[_doc(new, "900.00")],
        refuse={new: "credit_exceeds_non_withholding_room"},
    )
    outcome = world.run()
    assert outcome.applied_document_ids == ()
    assert outcome.reason_code == "no_invoice_fits"
    assert world.paid == []


def test_any_other_owner_refusal_raises_and_nothing_is_swallowed(monkeypatch):
    new = uuid4()
    world = _World(
        monkeypatch,
        trigger=new,
        documents=[_doc(new, "100.00")],
        fail_with=BillingRuleViolation("account_held", "held"),
    )
    with pytest.raises(BillingRuleViolation):
        world.run()


def test_an_unreadable_open_document_list_raises_rather_than_staying_quiet(
    monkeypatch,
):
    new = uuid4()
    world = _World(monkeypatch, trigger=new, documents=None)
    with pytest.raises(SettleRefused) as caught:
        world.run()
    assert caught.value.code == "open_documents_unreadable"
    assert "draw" not in world.calls


def test_a_document_of_another_account_is_refused(monkeypatch):
    new = uuid4()
    world = _World(monkeypatch, trigger=new, documents=[_doc(new, "1.00")])
    calls = iter([ACCOUNT, uuid4()])

    def exposure(db, *, scope, document_id):
        return SimpleNamespace(
            billing_account_id=next(calls), collectible_receivable=_money("1.00")
        )

    monkeypatch.setattr(flows, "get_document_exposure", exposure)
    with pytest.raises(SettleRefused):
        world.run()


def test_the_trigger_is_only_a_document_id():
    assert InvoiceIssuedV1(UUID(int=1)).document_id == UUID(int=1)
