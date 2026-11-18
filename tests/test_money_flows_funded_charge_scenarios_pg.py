"""Hosted PostgreSQL scenario proofs for money-flows F2 FundedCharge.

Funded at due, not covered, whole-only top-ups, the floating prepaid period,
oldest-first greedy re-evaluation, source withdrawal, a contract-type switch,
the owner-refusal and retry path, tenant isolation and caller rollback. The
acceptance tests AT1-AT3 and AT16 are in ``test_money_flows_funded_charge_pg``.

An opening-lot credit funding a charge is not covered here: Billing admits an
opening lot through a sealed opening window under a restricted role, which this
fixture does not provision. F2 reads the funding availability and never tells
the lot kinds apart.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_billing import get_document_exposure
from dotmac_billing import models as billing_models
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_money_flows import (
    FundedChargeCursorV1,
    FundedChargeRefused,
    FundedChargeState,
    SettledFundsV1,
    StartFundedChargeV1,
    get_funded_charge_instance,
    is_funded_charge_replay_conflict,
    list_funded_charge_attention,
    list_pending_funded_charges,
    record_funded_charge_attention,
    retry_funded_charge,
    start_funded_charge,
)
from dotmac_money_flows.models import FundedChargeInstance
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests import test_billing_isolation as b
from tests.money_flows_fixtures import (
    BASE,
    FakeChargeSource,
    assembly_settled,
    assembly_start,
    deposit,
    make_funded_ports,
    new_scope,
    scratch_database,
    seed_customer_terms,
    session,
    unrestricted,
)
from tests.test_money_flows_funded_charge_pg import (
    _access,
    _count,
    _documents,
    _draws,
    _rows,
    _world,
)

TOTAL = Decimal("107.50")
LATE = BASE + timedelta(days=2)


@pytest.fixture(scope="module")
def scratch():
    with scratch_database() as urls:
        yield urls


def _settled(account, outcome, **kwargs) -> SettledFundsV1:
    return SettledFundsV1(account, outcome.settlement_id, **kwargs)


def _invoice(scratch, scope):
    with session(scratch, scope) as db:
        document = db.execute(select(billing_models.BillingDocument)).scalar_one()
        obligation = db.get(billing_models.RatedObligation, document.obligation_id)
        exposure = get_document_exposure(db, scope=scope, document_id=document.id)
        return document, obligation, exposure


# --------------------------------------------------------------------------
# Funded at due, and not covered at due
# --------------------------------------------------------------------------
def test_a_charge_the_balance_covers_is_discharged_at_its_due_date(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    deposit(scratch, scope, account, "200.00")

    outcome = assembly_start(scratch, scope, key, ports)

    assert outcome.state is FundedChargeState.DISCHARGED
    document, obligation, exposure = _invoice(scratch, scope)
    assert document.due_at == BASE and document.grand_total == TOTAL
    assert obligation.period_start == BASE
    assert obligation.period_end == BASE + timedelta(days=30)
    assert exposure.collectible_receivable == b._money("0")
    assert unrestricted(scratch, scope, account) == Decimal("200.00") - TOTAL
    assert _draws(scratch, scope) == 1
    assert _access(scratch, scope) == []  # the service was never off
    with session(scratch, scope) as db:
        view = get_funded_charge_instance(db, scope=scope, key=key)
    assert view.document_id == document.id and view.funding_settlement_id is None
    assert view.obligation_id == obligation.id and view.credit_draw_id is not None


def test_a_charge_the_balance_does_not_cover_is_held_pending_with_nothing_posted(
    scratch,
):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    deposit(scratch, scope, account, "50.00")
    with session(scratch, scope) as db:
        groups = _count(db, billing_models.PostingGroup)

    outcome = assembly_start(scratch, scope, key, ports)

    assert outcome.state is FundedChargeState.PENDING and outcome.document_id is None
    assert _documents(scratch, scope) == 0
    with session(scratch, scope) as db:
        assert _count(db, billing_models.PostingGroup) == groups
    assert unrestricted(scratch, scope, account) == Decimal("50.00")
    assert [event["active"] for event in _access(scratch, scope)] == [True]
    assert source.issues == []


def test_a_partial_top_up_leaves_the_charge_pending_and_the_credit_untouched(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    deposit(scratch, scope, account, "50.00")
    assembly_start(scratch, scope, key, ports)

    top_up = deposit(scratch, scope, account, "30.00")
    assert assembly_settled(scratch, scope, _settled(account, top_up), ports) == ()

    assert [row.state for row in _rows(scratch, scope)] == ["pending"]
    assert _documents(scratch, scope) == 0
    assert unrestricted(scratch, scope, account) == Decimal("80.00")
    assert len(_access(scratch, scope)) == 1


def test_a_later_deposit_starts_the_paid_period_at_the_cash_instant(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", due_at=BASE)
    assembly_start(scratch, scope, key, ports)

    cash = deposit(scratch, scope, account, "200.00", observed_at=LATE)
    assembly_settled(scratch, scope, _settled(account, cash), ports)

    document, obligation, _ = _invoice(scratch, scope)
    assert document.due_at == LATE
    assert obligation.period_start == LATE
    assert obligation.period_end == LATE + timedelta(days=30)
    assert source.issues == [(key, LATE)]
    (row,) = _rows(scratch, scope)
    assert row.state == "discharged"
    assert row.funding_settlement_id == cash.settlement_id
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]
    assert unrestricted(scratch, scope, account) == Decimal("200.00") - TOTAL


def test_a_deposit_before_the_due_date_never_pulls_the_period_earlier(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", due_at=LATE)
    assembly_start(scratch, scope, key, ports)

    cash = deposit(scratch, scope, account, "200.00", observed_at=BASE)
    assembly_settled(scratch, scope, _settled(account, cash), ports)

    assert source.issues == [(key, LATE)]
    assert _invoice(scratch, scope)[0].due_at == LATE


# --------------------------------------------------------------------------
# Greedy re-evaluation, withdrawal and a contract-type switch
# --------------------------------------------------------------------------
def test_pending_charges_are_funded_oldest_first_skipping_one_that_does_not_fit(
    scratch,
):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    keys = [
        source.add(scope, account, "a", total="80.00", service_ref="service:a"),
        source.add(scope, account, "b", total="200.00", service_ref="service:b"),
        source.add(scope, account, "c", total="30.00", service_ref="service:c"),
    ]
    for key in keys:
        assert (
            assembly_start(scratch, scope, key, ports).state
            is FundedChargeState.PENDING
        )

    cash = deposit(scratch, scope, account, "120.00")
    assembly_settled(scratch, scope, _settled(account, cash), ports)

    assert [row.state for row in _rows(scratch, scope)] == [
        "discharged",
        "pending",
        "discharged",
    ]
    assert unrestricted(scratch, scope, account) == Decimal("10.00")
    assert _draws(scratch, scope) == 2


def test_a_charge_the_source_withdrew_is_withdrawn_and_the_next_one_is_funded(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    gone = source.add(scope, account, "gone", service_ref="service:gone")
    kept = source.add(scope, account, "kept", service_ref="service:kept")
    assembly_start(scratch, scope, gone, ports)
    assembly_start(scratch, scope, kept, ports)
    source.withdraw(gone)

    cash = deposit(scratch, scope, account, "200.00")
    assembly_settled(scratch, scope, _settled(account, cash), ports)

    first, second = _rows(scratch, scope)
    assert (first.state, first.reason_code) == ("withdrawn", "charge_withdrawn")
    assert second.state == "discharged"
    assert _documents(scratch, scope) == 1
    assert _draws(scratch, scope) == 1
    # Both services were off while pending; each goes back on.
    assert sorted(
        (event["service_reference"], event["active"])
        for event in _access(scratch, scope)
    ) == [
        ("service:gone", False),
        ("service:gone", True),
        ("service:kept", False),
        ("service:kept", True),
    ]


def test_a_switch_to_postpaid_before_the_period_starts_withdraws_the_charge(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", due_at=BASE)
    assert assembly_start(scratch, scope, key, ports).state is FundedChargeState.PENDING
    with session(scratch, scope) as db:
        seed_customer_terms(
            db,
            scope,
            account,
            contract_type="postpaid",
            effective_from=BASE + timedelta(days=1),
        )
        db.commit()

    cash = deposit(scratch, scope, account, "200.00", observed_at=LATE)
    assembly_settled(scratch, scope, _settled(account, cash), ports)

    (row,) = _rows(scratch, scope)
    assert (row.state, row.reason_code) == ("withdrawn", "contract_type_changed")
    assert _documents(scratch, scope) == 0
    assert source.issues == []
    assert unrestricted(scratch, scope, account) == Decimal("200.00")


def test_starting_a_charge_on_a_postpaid_customer_is_refused_and_writes_nothing(
    scratch,
):
    scope, (account,) = _world(scratch)
    with session(scratch, scope) as db:
        seed_customer_terms(
            db,
            scope,
            account,
            contract_type="postpaid",
            effective_from=BASE - timedelta(hours=6),
        )
        db.commit()
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")

    with session(scratch, scope) as db:
        with pytest.raises(FundedChargeRefused) as refused:
            start_funded_charge(
                db, scope=scope, command=StartFundedChargeV1(key), ports=ports
            )
        db.rollback()

    assert refused.value.code == "not_prepaid"
    assert _rows(scratch, scope) == []
    with session(scratch, scope) as db:
        assert (
            _count(
                db,
                OutboxEvent,
                OutboxEvent.event_type.like("money_flows.funded_charge.%"),
            )
            == 0
        )


def test_starting_an_unknown_charge_is_refused_and_writes_nothing(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    key = source.add(scope, account, "renewal-1")
    source.withdraw(key)

    with session(scratch, scope) as db:
        with pytest.raises(FundedChargeRefused) as refused:
            start_funded_charge(
                db,
                scope=scope,
                command=StartFundedChargeV1(key),
                ports=make_funded_ports(source),
            )
        db.rollback()

    assert refused.value.code == "charge_not_found"
    assert _rows(scratch, scope) == []


# --------------------------------------------------------------------------
# An owner refusal, D-F2-7, and the retry path
# --------------------------------------------------------------------------
def _bad_tax(command):
    """Tax that does not match the Tax evidence: Billing refuses it."""
    obligation = replace(command.obligation, tax_amount=b._money("1.00"))
    return replace(command, obligation=obligation)


def _refused_charge(scratch, *, observed_at=LATE):
    """A pending charge, funded by a deposit, that Billing then refuses."""
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    assembly_start(scratch, scope, key, ports)
    source.mutators[key] = _bad_tax
    cash = deposit(scratch, scope, account, "200.00", observed_at=observed_at)
    assert assembly_settled(scratch, scope, _settled(account, cash), ports) == ()
    return scope, account, source, ports, key, cash


def test_a_tax_refusal_rolls_back_and_attention_leaves_the_service_on(scratch):
    scope, account, source, ports, key, cash = _refused_charge(scratch)

    (row,) = _rows(scratch, scope)
    assert row.state == "attention_required"
    assert row.reason_code.startswith("billing.")
    assert row.funding_settlement_id == cash.settlement_id
    assert _documents(scratch, scope) == 0
    assert _draws(scratch, scope) == 0
    # D-F2-7: the service was off while pending; attention puts it back on.
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]
    with session(scratch, scope) as db:
        (listed,) = list_funded_charge_attention(db, scope=scope)
        assert listed.id == row.id
        assert (
            list_pending_funded_charges(db, scope=scope, billing_account_id=account)
            == ()
        )


def test_a_start_refusal_leaves_no_row_until_attention_is_recorded(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    source.mutators[key] = _bad_tax
    deposit(scratch, scope, account, "200.00")

    outcome = assembly_start(scratch, scope, key, ports)

    assert outcome.state is FundedChargeState.ATTENTION_REQUIRED
    assert outcome.reason_code.startswith("billing.")
    assert _documents(scratch, scope) == 0
    assert _access(scratch, scope) == []  # funded at due: the service never went off


def test_a_retry_with_a_stale_version_is_refused_before_the_source_is_called(scratch):
    scope, account, source, ports, key, cash = _refused_charge(scratch)
    (row,) = _rows(scratch, scope)
    reads, issues = len(source.reads), len(source.issues)

    with session(scratch, scope) as db:
        with pytest.raises(FundedChargeRefused) as stale:
            retry_funded_charge(
                db,
                scope=scope,
                instance_id=row.id,
                expected_version=row.version + 1,
                actor_ref="staff:1",
                ports=ports,
            )
        with pytest.raises(FundedChargeRefused) as nameless:
            retry_funded_charge(
                db,
                scope=scope,
                instance_id=row.id,
                expected_version=row.version,
                actor_ref=" ",
                ports=ports,
            )
        db.rollback()

    assert stale.value.code == "stale_version"
    assert nameless.value.code == "actor_required"
    assert (len(source.reads), len(source.issues)) == (reads, issues)


def test_a_funded_retry_discharges_dated_at_the_funding_cash(scratch):
    scope, account, source, ports, key, cash = _refused_charge(scratch)
    (row,) = _rows(scratch, scope)
    del source.mutators[key]  # the source is fixed

    with session(scratch, scope) as db:
        outcome = retry_funded_charge(
            db,
            scope=scope,
            instance_id=row.id,
            expected_version=row.version,
            actor_ref="staff:1",
            ports=ports,
        )
        db.commit()

    assert outcome.state is FundedChargeState.DISCHARGED
    assert _invoice(scratch, scope)[0].due_at == LATE
    (final,) = _rows(scratch, scope)
    assert final.actor_ref == "staff:1" and final.version == row.version + 1
    assert _draws(scratch, scope) == 1


def test_an_unfunded_retry_returns_the_charge_to_pending(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    first = assembly_start(scratch, scope, key, ports)
    with session(scratch, scope) as db:
        recorded = record_funded_charge_attention(
            db,
            scope=scope,
            instance_id=first.instance_id,
            code="owner_refused",
            ports=ports,
        )
        db.commit()
    assert recorded.state is FundedChargeState.ATTENTION_REQUIRED

    with session(scratch, scope) as db:
        outcome = retry_funded_charge(
            db,
            scope=scope,
            instance_id=first.instance_id,
            expected_version=recorded.version,
            actor_ref="staff:1",
            ports=ports,
        )
        db.commit()

    assert outcome.state is FundedChargeState.PENDING and outcome.reason_code is None
    assert _documents(scratch, scope) == 0
    assert [event["active"] for event in _access(scratch, scope)] == [True, False, True]


def test_attention_never_touches_a_discharged_row_and_a_repeat_is_a_replay(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    deposit(scratch, scope, account, "200.00")
    done = assembly_start(scratch, scope, key, ports)

    with session(scratch, scope) as db:
        outcome = record_funded_charge_attention(
            db, scope=scope, key=key, code="late_refusal", ports=ports
        )
        db.commit()

    assert outcome.replayed and outcome.state is FundedChargeState.DISCHARGED
    assert outcome.version == done.version


# --------------------------------------------------------------------------
# Isolation, rollback, replay detection and paging
# --------------------------------------------------------------------------
def test_instances_are_tenant_isolated(scratch):
    scope, (account,) = _world(scratch)
    other = new_scope(scratch)
    source = FakeChargeSource()
    key = source.add(scope, account, "renewal-1")
    assembly_start(scratch, scope, key, make_funded_ports(source))

    with session(scratch, other) as db:
        assert _count(db, FundedChargeInstance) == 0
        assert get_funded_charge_instance(db, scope=other, key=key) is None
    assert len(_rows(scratch, scope)) == 1


def test_a_caller_rollback_leaves_no_row_and_no_event(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    key = source.add(scope, account, "renewal-1")

    with session(scratch, scope) as db:
        outcome = start_funded_charge(
            db,
            scope=scope,
            command=StartFundedChargeV1(key),
            ports=make_funded_ports(source),
        )
        assert outcome.state is FundedChargeState.PENDING
        db.rollback()

    assert _rows(scratch, scope) == []
    with session(scratch, scope) as db:
        assert (
            _count(
                db,
                OutboxEvent,
                OutboxEvent.event_type.like("money_flows.funded_charge.%"),
            )
            == 0
        )


def test_a_duplicate_charge_is_detected_by_constraint_name(scratch):
    scope, (account,) = _world(scratch)

    def row(charge_ref):
        return FundedChargeInstance(
            id=uuid4(),
            tenant_id=scope.tenant_id,
            state="pending",
            reason_code=None,
            policy_version="p",
            definition_version="d",
            version=0,
            charge_ref=charge_ref,
            billing_account_id=account,
            service_ref="service:one",
        )

    with session(scratch, scope) as db:
        db.add(row("same"))
        db.flush()
        db.add(row("same"))
        with pytest.raises(IntegrityError) as duplicate:
            db.flush()
        db.rollback()
        shape = row("shape")
        shape.reason_code = "x"  # a reason on a pending row: a CHECK, not a replay
        db.add(shape)
        with pytest.raises(IntegrityError) as other:
            db.flush()
        db.rollback()
    assert is_funded_charge_replay_conflict(duplicate.value)
    assert not is_funded_charge_replay_conflict(other.value)


def test_both_lists_page_by_keyset_on_created_at_and_id(scratch):
    scope, (account,) = _world(scratch)
    with session(scratch, scope) as db:
        # One transaction: every row shares the same created_at.
        for index in range(5):
            for state, reason in (("pending", None), ("attention_required", "x")):
                db.add(
                    FundedChargeInstance(
                        id=uuid4(),
                        tenant_id=scope.tenant_id,
                        state=state,
                        reason_code=reason,
                        policy_version="p",
                        definition_version="d",
                        version=0,
                        charge_ref=f"{state}-{index}",
                        billing_account_id=account,
                        service_ref=None,
                    )
                )
        db.commit()

    def walk(list_page):
        seen, cursor, pages = [], None, 0
        with session(scratch, scope) as db:
            while True:
                page = list_page(db, cursor)
                if not page:
                    return seen, pages
                pages += 1
                seen.extend(page)
                cursor = FundedChargeCursorV1(page[-1].created_at, page[-1].id)

    pending, pending_pages = walk(
        lambda db, cursor: list_pending_funded_charges(
            db, scope=scope, billing_account_id=account, after=cursor, limit=2
        )
    )
    attention, attention_pages = walk(
        lambda db, cursor: list_funded_charge_attention(
            db, scope=scope, after=cursor, limit=2
        )
    )
    for seen, pages in ((pending, pending_pages), (attention, attention_pages)):
        assert pages == 3
        assert len(seen) == 5 and len({row.id for row in seen}) == 5
        assert [row.id for row in seen] == sorted(row.id for row in seen)


# --------------------------------------------------------------------------
# Decisions that need no funds come before the funds check
# --------------------------------------------------------------------------
def _switch_to_postpaid(scratch, scope, account):
    # Terms cannot be back-dated: record a future version, then reach it with
    # cash observed after it.
    with session(scratch, scope) as db:
        seed_customer_terms(
            db,
            scope,
            account,
            contract_type="postpaid",
            effective_from=BASE + timedelta(days=1),
        )
        db.commit()


def test_a_switch_to_postpaid_withdraws_even_when_the_deposit_does_not_fit(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    assembly_start(scratch, scope, key, ports)
    _switch_to_postpaid(scratch, scope, account)

    cash = deposit(scratch, scope, account, "50.00", observed_at=LATE)
    assembly_settled(scratch, scope, _settled(account, cash), ports)

    (row,) = _rows(scratch, scope)
    assert (row.state, row.reason_code) == ("withdrawn", "contract_type_changed")
    assert _documents(scratch, scope) == 0
    assert unrestricted(scratch, scope, account) == Decimal("50.00")
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]


def test_an_unfunded_retry_after_a_switch_to_postpaid_withdraws(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    first = assembly_start(scratch, scope, key, ports)
    cash = deposit(scratch, scope, account, "50.00", observed_at=LATE)
    with session(scratch, scope) as db:
        recorded = record_funded_charge_attention(
            db,
            scope=scope,
            instance_id=first.instance_id,
            code="owner_refused",
            funding_settlement_id=cash.settlement_id,
            ports=ports,
        )
        db.commit()
    _switch_to_postpaid(scratch, scope, account)

    with session(scratch, scope) as db:
        outcome = retry_funded_charge(
            db,
            scope=scope,
            instance_id=first.instance_id,
            expected_version=recorded.version,
            actor_ref="staff:1",
            ports=ports,
        )
        db.commit()

    assert outcome.state is FundedChargeState.WITHDRAWN
    assert outcome.reason_code == "contract_type_changed"
    assert _documents(scratch, scope) == 0
    assert unrestricted(scratch, scope, account) == Decimal("50.00")
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]


def test_a_charge_the_port_reports_under_another_account_is_attention_no_write(
    scratch,
):
    scope, (account, other) = _world(scratch, 2)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    assembly_start(scratch, scope, key, ports)
    deposit(scratch, scope, other, "300.00")
    source.charges[key] = replace(source.charges[key], billing_account_id=other)
    cash = deposit(scratch, scope, account, "200.00")

    assembly_settled(scratch, scope, _settled(account, cash), ports)

    (row,) = _rows(scratch, scope)
    assert row.state == "attention_required"
    assert row.reason_code == "charge_command_mismatch"
    assert row.billing_account_id == account
    assert source.issues == []
    assert _documents(scratch, scope) == 0
    assert _draws(scratch, scope) == 0
    assert unrestricted(scratch, scope, other) == Decimal("300.00")
    assert unrestricted(scratch, scope, account) == Decimal("200.00")
