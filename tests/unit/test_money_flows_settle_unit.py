"""Unit proofs for money-flows F1 Settle: transition table, ordering, decisions.

No database: owners are replaced by plain fakes at the module seam, so each
decision (and each refusal) of the pre-write phase is exercised in isolation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from dotmac_billing import BillingRuleViolation, OpenReceivableDocumentV1
from dotmac_billing.contracts import DueDateBasisStatus
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Currency, Money
from dotmac_money_flows import (
    BalancePassFailed,
    DepositApplication,
    SettleConfirmationV1,
    SettleConfirmationV2,
    SettlementTargetV1,
    SettlePolicyV1,
    SettlePorts,
    SettleRefused,
    SettleState,
    SettleWithholdingV1,
    WithholdingAcceptedV1,
)
from dotmac_money_flows import settle as flows
from dotmac_money_flows.contracts import (
    SETTLE_INSERT_STATES,
    SETTLE_TRANSITIONS,
    confirmation_deductions,
    settle_transition_allowed,
)
from sqlalchemy.exc import IntegrityError

NOW = datetime(2026, 11, 1, tzinfo=UTC)
NGN = Currency("NGN", 2)
MIGRATION = next(
    Path(__file__)
    .resolve()
    .parents[2]
    .glob(
        "packages/dotmac-money-flows/src/dotmac_money_flows/migrations/versions/"
        "mf_0001_settle_instances.py"
    )
)


# --------------------------------------------------------------------------
# Transition table
# --------------------------------------------------------------------------
def test_the_transition_table_is_exactly_the_governed_graph():
    allowed = {
        (old, new)
        for old, new in product(SettleState, SettleState)
        if settle_transition_allowed(old, new)
    }
    assert allowed == {
        (SettleState.REFUSED, SettleState.REFUSED),
        (SettleState.REFUSED, SettleState.SETTLED),
        (SettleState.REFUSED, SettleState.UNAPPLIED),
        (SettleState.UNAPPLIED, SettleState.RESOLVED),
    }
    assert SETTLE_INSERT_STATES == {
        SettleState.SETTLED,
        SettleState.REFUSED,
        SettleState.UNAPPLIED,
    }
    for terminal in (SettleState.SETTLED, SettleState.RESOLVED):
        assert terminal not in SETTLE_TRANSITIONS


def test_the_python_table_matches_the_database_trigger():
    source = " ".join(MIGRATION.read_text(encoding="utf-8").split())
    insertable = re.search(r"NEW\.state NOT IN \(([^)]*)\)", source)
    assert insertable is not None
    assert {s.strip(" '") for s in insertable.group(1).split(",")} == {
        s.value for s in SETTLE_INSERT_STATES
    }
    pairs = set()
    for old, new_clause in re.findall(
        r"OLD\.state = '(\w+)'\s+AND NEW\.state (IN \([^)]*\)|= '\w+')", source
    ):
        for new in re.findall(r"'(\w+)'", new_clause):
            pairs.add((old, new))
    assert pairs == {
        (old.value, new.value)
        for old, news in SETTLE_TRANSITIONS.items()
        for new in news
    }


# --------------------------------------------------------------------------
# Oldest-due-first ordering
# --------------------------------------------------------------------------
def _document(*, due, issued, basis=DueDateBasisStatus.VERIFIED, doc_id=None):
    return OpenReceivableDocumentV1(
        document_id=doc_id or uuid4(),
        issued_at=NOW + timedelta(days=issued),
        due_at=None if due is None else NOW + timedelta(days=due),
        collectible_receivable=Money.of("10.00", NGN),
        service_ref=None,
        source_version=1,
        financial_state="open",
        due_at_basis=basis,
    )


def test_oldest_due_first_with_unknown_basis_last_then_issued_then_id():
    low, high = UUID(int=1), UUID(int=2)
    due_late = _document(due=30, issued=0)
    due_early_issued_late = _document(due=10, issued=5, doc_id=high)
    due_early_issued_early = _document(due=10, issued=1, doc_id=high)
    tie_a = _document(due=10, issued=1, doc_id=low)
    unverified = _document(due=1, issued=0, basis=DueDateBasisStatus.UNKNOWN_UNVERIFIED)
    no_date = _document(due=None, issued=-3)
    ordered = flows.order_open_documents(
        [
            unverified,
            due_late,
            no_date,
            due_early_issued_late,
            due_early_issued_early,
            tie_a,
        ]
    )
    assert ordered == [
        tie_a,
        due_early_issued_early,
        due_early_issued_late,
        due_late,
        no_date,  # unknown basis group: issued_at ascending
        unverified,
    ]


# --------------------------------------------------------------------------
# Pre-write decisions with fake owners
# --------------------------------------------------------------------------
class _Targets:
    def __init__(self, target):
        self.target = target
        self.calls = []

    def resolve(self, db, *, scope, target_reference, purpose):
        self.calls.append((target_reference, purpose))
        return self.target


class _Policy:
    def __init__(self, policy):
        self.policy = policy

    def read(self, db, *, scope):
        return self.policy


def _install(
    monkeypatch,
    *,
    source="PROVIDER_CALLBACK",
    purpose="ACCOUNT_CREDIT_DEPOSIT",
    intent_id=None,
    cash_intent_id=None,
    requested="NGN",
    target_ref="acct:a",
    found=True,
):
    intent_id = intent_id or uuid4()
    cash = SimpleNamespace(
        intent_id=cash_intent_id or intent_id,
        confirmed=Money.of("50.00", NGN),
        source=SimpleNamespace(value=source),
        provider_type="TESTPAY",
        external_reference="ext-1",
        observed_at=NOW,
    )
    intent = SimpleNamespace(
        intent_id=intent_id,
        requested=Money.of("50.00", Currency(requested, 2)),
        target_reference=target_ref,
        purpose=SimpleNamespace(value=purpose),
    )
    monkeypatch.setattr(flows, "get_confirmed_cash_v1", lambda *a, **k: cash)
    monkeypatch.setattr(
        flows, "get_payment_intent_v1", lambda *a, **k: intent if found else None
    )
    return cash, intent


def _ports(target, policy=None):
    return SettlePorts(
        targets=_Targets(target), policy=_Policy(policy or SettlePolicyV1("p1"))
    )


def _command(**kw):
    return SettleConfirmationV1(confirmation_id=uuid4(), intent_reference="ref-1", **kw)


def _prepare(ports, command=None):
    return flows._prepare(None, TenantScope(uuid4()), command or _command(), ports)


def _wht():
    return SettleWithholdingV1(
        tax_evidence=SimpleNamespace(document_id=uuid4()),
        claim_ref="c",
        claimed=Money.of("1.00", NGN),
        accepted=Money.of("1.00", NGN),
        source_owner="o",
        source_ref="s",
        idempotency_key="k",
        authorization_actor_ref="a",
        authorization_evidence_ref="e",
    )


ACCOUNT = SettlementTargetV1(uuid4())
BOUND = SettlementTargetV1(uuid4(), uuid4())


@pytest.mark.parametrize(
    "kwargs, target, command, code",
    [
        ({"found": False}, ACCOUNT, None, "intent_not_found"),
        ({"cash_intent_id": uuid4()}, ACCOUNT, None, "intent_mismatch"),
        ({"requested": "USD"}, ACCOUNT, None, "currency_mismatch"),
        ({"source": "MANUAL"}, ACCOUNT, None, "source_not_accepted"),
        ({"source": "TRANSFER_PROOF"}, ACCOUNT, None, "source_not_accepted"),
        (
            {},
            ACCOUNT,
            _command(withholding=_wht()),
            "withholding_requires_manual_source",
        ),
        ({"purpose": "PURCHASE_FUNDING"}, ACCOUNT, None, "purpose_not_settled_here"),
        ({}, None, None, "target_unresolved"),
        ({"purpose": "INVOICE_SETTLEMENT"}, ACCOUNT, None, "bound_document_required"),
        ({"purpose": "SERVICE_FEE"}, ACCOUNT, None, "bound_document_required"),
        ({}, BOUND, None, "deposit_must_not_bind_document"),
    ],
)
def test_pre_write_refusals(monkeypatch, kwargs, target, command, code):
    _install(monkeypatch, **kwargs)
    ports = _ports(target)
    result = _prepare(ports, command)
    assert isinstance(result, flows._Refusal)
    assert result.code == code
    assert result.policy_version == "p1"


def test_manual_and_transfer_proof_are_off_by_policy_default():
    assert SettlePolicyV1("p1").accepted_confirmation_sources == {
        "PROVIDER_CALLBACK",
        "PROVIDER_RECONCILIATION",
    }
    assert (
        SettlePolicyV1("p1").deposit_application is DepositApplication.OLDEST_DUE_FIRST
    )


def test_manual_is_accepted_only_when_the_policy_names_it(monkeypatch):
    _install(monkeypatch, source="MANUAL")
    policy = SettlePolicyV1("p2", accepted_confirmation_sources=frozenset({"MANUAL"}))
    plan = _prepare(_ports(ACCOUNT, policy), _command(withholding=_wht()))
    assert isinstance(plan, flows._Plan)
    assert plan.policy.version == "p2"


def test_the_resolver_receives_only_the_intents_reference_and_purpose(monkeypatch):
    _install(monkeypatch, target_ref="acct:from-intent")
    ports = _ports(ACCOUNT)
    assert isinstance(_prepare(ports), flows._Plan)
    assert ports.targets.calls == [("acct:from-intent", "ACCOUNT_CREDIT_DEPOSIT")]


def test_a_replay_calls_no_owner(monkeypatch):
    row = SimpleNamespace(
        id=uuid4(),
        state="settled",
        reason_code=None,
        version=0,
        settlement_id=uuid4(),
        billing_account_id=uuid4(),
    )

    def boom(*args, **kwargs):
        raise AssertionError("an owner was called on a replay")

    for name in (
        "get_confirmed_cash_v1",
        "get_payment_intent_v1",
        "accept_settlement",
        "apply_available_credit",
        "accept_withholding_discharge",
        "get_document_exposure",
        "list_open_receivable_documents",
    ):
        monkeypatch.setattr(flows, name, boom)
    monkeypatch.setattr(flows, "_find", lambda *a, **k: row)
    ports = _ports(ACCOUNT)
    outcome = flows.settle_confirmation(
        None, scope=TenantScope(uuid4()), command=_command(), ports=ports
    )
    assert outcome.replayed and outcome.state is SettleState.SETTLED
    assert ports.targets.calls == []


# --------------------------------------------------------------------------
# Small guards
# --------------------------------------------------------------------------
def test_a_retry_names_its_actor_before_anything_is_read():
    with pytest.raises(SettleRefused) as caught:
        flows.retry_settle(
            None,
            scope=TenantScope(uuid4()),
            instance_id=uuid4(),
            expected_version=0,
            actor_ref=" ",
            command=_command(),
            ports=_ports(ACCOUNT),
        )
    assert caught.value.code == "actor_required"


@pytest.mark.parametrize("code", ["", "x" * 81, "Has Space", "semi;colon"])
def test_refusal_codes_are_short_machine_codes(code):
    with pytest.raises(ValueError):
        flows._check_code(code)
    assert flows._check_code("billing.money_identity_mismatch")


class _Orig(Exception):
    def __init__(self, name, message=None):
        super().__init__(message or f"duplicate key {name}")
        self.diag = SimpleNamespace(constraint_name=name)


@pytest.mark.parametrize(
    "name",
    [
        "uq_settle_instances_confirmation",
        "settle_instances_pkey",
        "uq_settle_instances_tenant_id_id",
    ],
)
def test_the_duplicate_confirmation_constraints_are_a_replay(name):
    assert flows.is_settle_replay_conflict(IntegrityError("insert", {}, _Orig(name)))


def test_nothing_else_is_a_replay_not_even_by_message():
    other = IntegrityError("insert", {}, _Orig("some_other_constraint"))
    by_text = IntegrityError(
        "insert",
        {},
        _Orig("some_other_constraint", "mentions uq_settle_instances_confirmation"),
    )
    no_diag = IntegrityError(
        "insert", {}, Exception("uq_settle_instances_confirmation")
    )
    assert not flows.is_settle_replay_conflict(other)
    assert not flows.is_settle_replay_conflict(by_text)
    assert not flows.is_settle_replay_conflict(no_diag)
    assert not flows.is_settle_replay_conflict(ValueError("x"))


def test_the_posting_instant_is_the_cash_observation_not_a_caller_choice(monkeypatch):
    cash, _ = _install(monkeypatch)
    plan = _prepare(_ports(ACCOUNT))
    seen = {}

    def capture(db, *, scope, command, accepted_confirmation_evidence):
        seen["occurred_at"] = command.occurred_at
        seen["observed_at"] = command.observed_at
        raise RuntimeError("stop after the claim")

    monkeypatch.setattr(flows, "accept_settlement", capture)
    with pytest.raises(RuntimeError):
        flows._execute(
            None, TenantScope(uuid4()), _command(), plan, uuid4(), recheck=False
        )
    assert seen == {"occurred_at": cash.observed_at, "observed_at": cash.observed_at}
    assert not hasattr(_command(), "occurred_at")


# --------------------------------------------------------------------------
# Open-document walk: all pages, and a read refusal never refuses the cash
# --------------------------------------------------------------------------
def _page(entries, cursor):
    return SimpleNamespace(entries=tuple(entries), next_cursor=cursor)


def test_the_walk_reads_every_page(monkeypatch):
    first, second, third = (
        _document(due=1, issued=0),
        _document(due=2, issued=0),
        _document(due=3, issued=0),
    )
    pages = {
        None: _page([first], "c1"),
        "c1": _page([second], "c2"),
        "c2": _page([third], None),
    }
    seen = []

    def read(db, *, scope, account, currency, cursor, limit):
        seen.append(cursor)
        return pages[cursor]

    monkeypatch.setattr(flows, "list_open_receivable_documents", read)
    found = flows._walk_open_documents(None, TenantScope(uuid4()), uuid4(), NGN)
    assert found == [first, second, third]
    assert seen == [None, "c1", "c2"]


def test_a_read_refusal_or_a_stalled_cursor_is_unreadable_not_a_refusal(monkeypatch):
    def refuse(*args, **kwargs):
        raise BillingRuleViolation("stale_exposure", "stale exposure")

    monkeypatch.setattr(flows, "list_open_receivable_documents", refuse)
    assert flows._walk_open_documents(None, TenantScope(uuid4()), uuid4(), NGN) is None

    monkeypatch.setattr(
        flows,
        "list_open_receivable_documents",
        lambda *a, **k: _page([], "same"),
    )
    assert flows._walk_open_documents(None, TenantScope(uuid4()), uuid4(), NGN) is None


# --------------------------------------------------------------------------
# C2b: several deductions on one MANUAL confirmation
# --------------------------------------------------------------------------
def _deduction(component=None, key=None):
    return SettleWithholdingV1(
        tax_evidence=SimpleNamespace(
            document_id=uuid4(), component_ref=str(component or uuid4())
        ),
        claim_ref="c",
        claimed=Money.of("1.00", NGN),
        accepted=Money.of("1.00", NGN),
        source_owner="o",
        source_ref="s",
        idempotency_key=key or f"k-{uuid4()}",
        authorization_actor_ref="a",
        authorization_evidence_ref="e",
    )


def _command_v2(*deductions, **kw):
    return SettleConfirmationV2(
        confirmation_id=uuid4(),
        intent_reference="ref-1",
        deductions=tuple(deductions),
        **kw,
    )


_MANUAL_POLICY = SettlePolicyV1(
    "p1", accepted_confirmation_sources=frozenset({"MANUAL"})
)


def test_v1_callers_are_unchanged():
    one = _wht()
    assert confirmation_deductions(_command()) == ()
    assert confirmation_deductions(_command(withholding=one)) == (one,)
    assert not hasattr(_command(), "deductions")
    assert issubclass(SettleConfirmationV2, SettleConfirmationV1)


def test_a_v2_confirmation_carries_a_tuple_of_deductions_in_order():
    first, second = _deduction(), _deduction()
    assert confirmation_deductions(_command_v2(first, second)) == (first, second)
    assert confirmation_deductions(_command_v2()) == ()
    # A V1-style single claim set on V2 reads first, so duplicates still count.
    assert confirmation_deductions(_command_v2(second, withholding=first)) == (
        first,
        second,
    )


def test_two_distinct_components_are_accepted_on_a_manual_confirmation(monkeypatch):
    _install(monkeypatch, source="MANUAL")
    plan = _prepare(
        _ports(ACCOUNT, _MANUAL_POLICY), _command_v2(_deduction(), _deduction())
    )
    assert isinstance(plan, flows._Plan)


@pytest.mark.parametrize("source", ["PROVIDER_CALLBACK", "TRANSFER_PROOF"])
def test_deductions_on_a_non_manual_confirmation_are_refused(monkeypatch, source):
    _install(monkeypatch, source=source)
    policy = SettlePolicyV1("p1", accepted_confirmation_sources=frozenset({source}))
    result = _prepare(_ports(ACCOUNT, policy), _command_v2(_deduction(), _deduction()))
    assert isinstance(result, flows._Refusal)
    assert result.code == "withholding_requires_manual_source"


def test_the_same_component_twice_is_refused_before_any_write(monkeypatch):
    _install(monkeypatch, source="MANUAL")
    component = uuid4()
    ports = _ports(ACCOUNT, _MANUAL_POLICY)
    command = _command_v2(_deduction(component), _deduction(component))
    result = _prepare(ports, command)
    assert isinstance(result, flows._Refusal)
    assert result.code == "duplicate_deduction_component"
    assert ports.targets.calls == []  # refused before the target is even resolved

    def boom(*args, **kwargs):
        raise AssertionError("an owner was written")

    for name in ("accept_settlement", "accept_withholding_discharge"):
        monkeypatch.setattr(flows, name, boom)
    monkeypatch.setattr(flows, "_find", lambda *a, **k: None)
    inserted = {}

    def capture(db, tenant_id, instance_id, command, **kw):
        inserted.update(kw)
        return SimpleNamespace(
            id=instance_id,
            state=kw["state"].value,
            reason_code=kw["reason_code"],
            version=0,
            settlement_id=None,
            billing_account_id=None,
        )

    monkeypatch.setattr(flows, "_insert", capture)
    outcome = flows.settle_confirmation(
        None, scope=TenantScope(uuid4()), command=command, ports=ports
    )
    assert outcome.state is SettleState.REFUSED
    assert inserted["discharge_refs"] == ()


def test_the_same_retry_key_twice_is_refused_before_any_write(monkeypatch):
    _install(monkeypatch, source="MANUAL")
    result = _prepare(
        _ports(ACCOUNT, _MANUAL_POLICY),
        _command_v2(_deduction(key="same"), _deduction(key="same")),
    )
    assert isinstance(result, flows._Refusal)
    assert result.code == "duplicate_deduction_component"


def test_every_deduction_is_accepted_in_order_before_the_whole_invoice_pass(
    monkeypatch,
):
    _install(monkeypatch, source="MANUAL")
    events = []
    ports = _ports(
        ACCOUNT,
        SettlePolicyV1(
            "p1",
            accepted_confirmation_sources=frozenset({"MANUAL"}),
            deposit_application=DepositApplication.NONE,
        ),
    )
    first, second = _deduction(), _deduction()
    command = _command_v2(first, second)
    plan = _prepare(ports, command)
    settlement_id = uuid4()
    monkeypatch.setattr(
        flows,
        "accept_settlement",
        lambda *a, **k: events.append("cash") or SimpleNamespace(id=settlement_id),
    )

    def discharge(db, *, scope, command):
        events.append(command.idempotency_key)
        assert command.settlement_id == settlement_id
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(flows, "accept_withholding_discharge", discharge)
    result = flows._execute(
        None, TenantScope(uuid4()), command, plan, uuid4(), recheck=False
    )
    assert events == ["cash", first.idempotency_key, second.idempotency_key]
    assert len(result.discharge_refs) == 2
    assert len(set(result.discharge_refs)) == 2


def test_a_v1_withholding_is_stored_as_a_single_reference(monkeypatch):
    _install(monkeypatch, source="MANUAL")
    ports = _ports(
        ACCOUNT,
        SettlePolicyV1(
            "p1",
            accepted_confirmation_sources=frozenset({"MANUAL"}),
            deposit_application=DepositApplication.NONE,
        ),
    )
    command = _command(withholding=_wht())
    plan = _prepare(ports, command)
    ref = uuid4()
    monkeypatch.setattr(
        flows, "accept_settlement", lambda *a, **k: SimpleNamespace(id=uuid4())
    )
    monkeypatch.setattr(
        flows, "accept_withholding_discharge", lambda *a, **k: SimpleNamespace(id=ref)
    )
    result = flows._execute(
        None, TenantScope(uuid4()), command, plan, uuid4(), recheck=False
    )
    assert result.discharge_refs == (str(ref),)


def test_stored_references_prefer_the_list_and_fall_back_to_the_legacy_column():
    both = SimpleNamespace(
        withholding_discharge_list_ref=["a", "b"], withholding_discharge_ref="a"
    )
    legacy = SimpleNamespace(
        withholding_discharge_list_ref=None, withholding_discharge_ref="x"
    )
    none = SimpleNamespace(
        withholding_discharge_list_ref=None, withholding_discharge_ref=None
    )
    assert flows._stored_refs(both) == ("a", "b")
    assert flows._stored_refs(legacy) == ("x",)
    assert flows._stored_refs(none) == ()


def test_a_v2_confirmation_setting_both_withholding_and_deductions_is_refused(
    monkeypatch,
):
    _install(monkeypatch, source="MANUAL")
    ports = _ports(ACCOUNT, _MANUAL_POLICY)
    result = _prepare(ports, _command_v2(_deduction(), withholding=_deduction()))
    assert isinstance(result, flows._Refusal)
    assert result.code == "withholding_and_deductions_both_set"
    assert ports.targets.calls == []


def test_a_v2_deduction_on_another_document_than_the_bound_one_is_refused(
    monkeypatch,
):
    _install(monkeypatch, source="MANUAL", purpose="INVOICE_SETTLEMENT")
    bound = BOUND.bound_document_id

    def on(document_id, component):
        base = _deduction()
        return SettleWithholdingV1(
            tax_evidence=SimpleNamespace(
                document_id=document_id, component_ref=component
            ),
            claim_ref=base.claim_ref,
            claimed=base.claimed,
            accepted=base.accepted,
            source_owner=base.source_owner,
            source_ref=base.source_ref,
            idempotency_key=f"k-{component}",
            authorization_actor_ref=base.authorization_actor_ref,
            authorization_evidence_ref=base.authorization_evidence_ref,
        )

    monkeypatch.setattr(
        flows,
        "get_document_exposure",
        lambda *a, **k: SimpleNamespace(billing_account_id=BOUND.billing_account_id),
    )
    ports = _ports(BOUND, _MANUAL_POLICY)
    refused = _prepare(ports, _command_v2(on(bound, "a"), on(uuid4(), "b")))
    assert isinstance(refused, flows._Refusal)
    assert refused.code == "deduction_document_not_bound"
    accepted = _prepare(ports, _command_v2(on(bound, "a"), on(bound, "b")))
    assert isinstance(accepted, flows._Plan)


def test_a_failing_deduction_is_named_by_position_and_component(monkeypatch):
    _install(monkeypatch, source="MANUAL")
    ports = _ports(
        ACCOUNT,
        SettlePolicyV1(
            "p1",
            accepted_confirmation_sources=frozenset({"MANUAL"}),
            deposit_application=DepositApplication.NONE,
        ),
    )
    first, second = _deduction(), _deduction()
    command = _command_v2(first, second)
    plan = _prepare(ports, command)
    monkeypatch.setattr(
        flows, "accept_settlement", lambda *a, **k: SimpleNamespace(id=uuid4())
    )
    calls = []

    def discharge(db, *, scope, command):
        calls.append(command.idempotency_key)
        if len(calls) == 2:
            raise BillingRuleViolation("no_component", "refused")
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(flows, "accept_withholding_discharge", discharge)
    with pytest.raises(BillingRuleViolation) as caught:
        flows._execute(
            None, TenantScope(uuid4()), command, plan, uuid4(), recheck=False
        )
    assert caught.value.code == "billing.no_component"
    notes = " ".join(caught.value.__notes__)
    assert "deduction 1" in notes
    assert second.tax_evidence.component_ref in notes
    assert first.tax_evidence.component_ref not in notes


def test_the_withholding_entry_point_refuses_another_accounts_document(monkeypatch):
    document = uuid4()
    monkeypatch.setattr(
        flows,
        "get_document_exposure",
        lambda *a, **k: SimpleNamespace(billing_account_id=uuid4()),
    )

    def boom(*args, **kwargs):
        raise AssertionError("the account lock was taken for a mismatched account")

    monkeypatch.setattr(flows, "get_funding_availability", boom)
    with pytest.raises(BalancePassFailed) as caught:
        flows.apply_balance_on_withholding_accepted(
            None,
            scope=TenantScope(uuid4()),
            request=WithholdingAcceptedV1(uuid4(), document, uuid4(), NOW),
            ports=_ports(ACCOUNT),
        )
    assert caught.value.code == "document_account_mismatch"
