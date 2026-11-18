"""Hosted PostgreSQL proofs for money-flows F2 D3: the per-account terms re-check.

A switch to postpaid withdraws the pending charge and turns the service back on
with no deposit; the re-check never pays or issues; a future switch is a no-op
until its instant; a superseded switch is a no-op; a settlement racing the
re-check gives exactly one outcome and no deadlock; the batch cap continues by
event; attention rows, other tenants and a caller rollback are untouched.

The timeline sits in the near future (see ``BASE``): account terms cannot be
back-dated against the database clock. ``at`` is the ``effective_from`` of the
terms version that caused the call; flows reads no clock.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Thread
from uuid import UUID

import pytest
from dotmac_billing import BillingError, RecordDefaultTermsV1, record_default_terms
from dotmac_billing import models as billing_models
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_money_flows import (
    AccountTermsChangedV1,
    FundedChargeCursorV1,
    FundedChargeState,
    FundedChargeStepFailed,
    SettledFundsV1,
    record_funded_charge_attention,
    reevaluate_account_terms,
)
from dotmac_money_flows.models import FundedChargeInstance
from sqlalchemy import text

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
    seed_customer_terms,
    seed_default_terms,
    session,
    unrestricted,
)
from tests.test_money_flows_funded_charge_pg import (
    _access,
    _count,
    _documents,
    _draws,
    _events,
    _rows,
    _world,
)
from tests.test_money_flows_settle_pg import _blocked_on_locks

RECHECK = "money_flows.funded_charge.terms_recheck.v1"
SWITCH = BASE + timedelta(days=1)
LATER = BASE + timedelta(days=5)


@pytest.fixture(scope="module")
def scratch():
    with scratch_database() as urls:
        yield urls


def assembly_recheck(scratch, scope, request, ports):
    """Run ``reevaluate_account_terms`` as the transport does.

    A step failure rolls everything back, records attention for the failed row in
    a NEW transaction, and runs the re-check again for the same ``(account, at)``.
    """
    while True:
        try:
            with session(scratch, scope) as db:
                outcomes = reevaluate_account_terms(
                    db, scope=scope, request=request, ports=ports
                )
                db.commit()
                return outcomes
        except FundedChargeStepFailed as exc:
            failure = exc
        with session(scratch, scope) as db:
            record_funded_charge_attention(
                db,
                scope=scope,
                instance_id=failure.instance_id,
                code=failure.code,
                ports=ports,
            )
            db.commit()


def _postpaid(scratch, scope, account, effective_from=SWITCH, *, contract="postpaid"):
    with session(scratch, scope) as db:
        seed_customer_terms(
            db,
            scope,
            account,
            contract_type=contract,
            effective_from=effective_from,
        )
        db.commit()


def _request(account, at=SWITCH, after=None) -> AccountTermsChangedV1:
    return AccountTermsChangedV1(account, at, after)


def _posting_groups(scratch, scope) -> int:
    with session(scratch, scope) as db:
        return _count(db, billing_models.PostingGroup)


def _states(scratch, scope):
    return [(row.state, row.reason_code, row.version) for row in _rows(scratch, scope)]


# --------------------------------------------------------------------------
# (a) a switch effective now withdraws, the service resumes, nothing is posted
# --------------------------------------------------------------------------
def test_a_a_switch_to_postpaid_withdraws_and_posts_nothing(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", due_at=BASE)
    deposit(scratch, scope, account, "50.00")  # not enough: the charge is pending
    assert assembly_start(scratch, scope, key, ports).state is FundedChargeState.PENDING
    groups = _posting_groups(scratch, scope)
    _postpaid(scratch, scope, account)

    (outcome,) = assembly_recheck(scratch, scope, _request(account), ports)

    assert outcome.state is FundedChargeState.WITHDRAWN
    assert outcome.reason_code == "contract_type_changed" and not outcome.replayed
    (row,) = _rows(scratch, scope)
    assert (row.state, row.reason_code, row.funding_settlement_id) == (
        "withdrawn",
        "contract_type_changed",
        None,
    )
    events = _access(scratch, scope)
    assert [event["active"] for event in events] == [True, False]
    assert events[-1]["observed_at"] == max(BASE, SWITCH).isoformat()
    assert _documents(scratch, scope) == 0  # no invoice, so no VAT either
    assert _draws(scratch, scope) == 0
    assert _posting_groups(scratch, scope) == groups
    assert unrestricted(scratch, scope, account) == Decimal("50.00")
    assert source.issues == []
    assert _events(scratch, scope, RECHECK) == []


def test_a_the_recheck_never_pays_even_when_the_balance_covers_the_charge(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", due_at=BASE)
    assembly_start(scratch, scope, key, ports)
    # Cash lands (F1 only; no F2 settlement re-evaluation runs): the balance
    # now covers the pending charge, and the terms are still prepaid.
    deposit(scratch, scope, account, "200.00", observed_at=BASE)

    assert assembly_recheck(scratch, scope, _request(account), ports) == ()

    assert _states(scratch, scope) == [("pending", None, 0)]
    assert _documents(scratch, scope) == 0 and _draws(scratch, scope) == 0
    assert source.issues == []
    assert unrestricted(scratch, scope, account) == Decimal("200.00")


# --------------------------------------------------------------------------
# (b) a future switch: before it is a no-op, at it withdraws
# --------------------------------------------------------------------------
def test_b_a_future_switch_is_a_no_op_before_it_and_withdraws_at_it(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", due_at=BASE)
    assembly_start(scratch, scope, key, ports)
    _postpaid(scratch, scope, account, LATER)

    assert assembly_recheck(scratch, scope, _request(account, SWITCH), ports) == ()
    assert _states(scratch, scope) == [("pending", None, 0)]
    assert [event["active"] for event in _access(scratch, scope)] == [True]

    (outcome,) = assembly_recheck(scratch, scope, _request(account, LATER), ports)
    assert outcome.state is FundedChargeState.WITHDRAWN
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]


# --------------------------------------------------------------------------
# (c) M2: a switch superseded back to prepaid is a no-op
# --------------------------------------------------------------------------
def test_c_a_switch_superseded_back_to_prepaid_is_a_no_op(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", due_at=BASE)
    assembly_start(scratch, scope, key, ports)
    _postpaid(scratch, scope, account, SWITCH)
    _postpaid(scratch, scope, account, SWITCH, contract="prepaid")  # M2 supersede

    assert assembly_recheck(scratch, scope, _request(account), ports) == ()
    assert _states(scratch, scope) == [("pending", None, 0)]
    assert [event["active"] for event in _access(scratch, scope)] == [True]


# --------------------------------------------------------------------------
# (d) a real two-session race, settlement against re-check
# --------------------------------------------------------------------------
def test_d_a_settlement_racing_the_recheck_gives_exactly_one_outcome(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", due_at=BASE)
    assembly_start(scratch, scope, key, ports)
    # Cash observed at BASE would pay the charge (prepaid then); the switch
    # withdraws it. Whichever session takes the account lock second finds the
    # row no longer pending.
    cash = deposit(scratch, scope, account, "200.00", observed_at=BASE)
    _postpaid(scratch, scope, account, SWITCH)
    results: list[tuple] = []
    errors: list[BaseException] = []

    def settle() -> None:
        try:
            event = SettledFundsV1(account, cash.settlement_id)
            results.append(("settle", assembly_settled(scratch, scope, event, ports)))
        except BaseException as exc:
            errors.append(exc)

    def recheck() -> None:
        try:
            outcomes = assembly_recheck(scratch, scope, _request(account), ports)
            results.append(("recheck", outcomes))
        except BaseException as exc:
            errors.append(exc)

    database = scratch[0].rsplit("/", 1)[1]
    threads = [Thread(target=settle), Thread(target=recheck)]
    with session(scratch, scope) as holder:
        holder.execute(
            text(
                "SELECT id FROM mod_billing.billing_accounts WHERE id = :i FOR UPDATE"
            ),
            {"i": account},
        )
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            waiting, ungranted = _blocked_on_locks(database)
            if waiting >= 2 and ungranted >= 2:
                break
            time.sleep(0.2)
        waiting, ungranted = _blocked_on_locks(database)
        assert waiting >= 2 and ungranted >= 2  # both queued on the account lock
        assert results == [] and errors == []
        holder.rollback()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == [] and len(results) == 2
    assert sum(len(outcomes) for _, outcomes in results) == 1  # exactly one outcome
    (row,) = _rows(scratch, scope)
    assert row.state in ("discharged", "withdrawn") and row.version == 1
    if row.state == "discharged":
        assert (_documents(scratch, scope), _draws(scratch, scope)) == (1, 1)
        assert unrestricted(scratch, scope, account) < Decimal("200.00")
    else:
        assert row.reason_code == "contract_type_changed"
        assert (_documents(scratch, scope), _draws(scratch, scope)) == (0, 0)
        assert unrestricted(scratch, scope, account) == Decimal("200.00")
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]


# --------------------------------------------------------------------------
# (e) re-check then deposit: the deposit stays credit
# --------------------------------------------------------------------------
def test_e_a_deposit_after_the_recheck_stays_credit(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", due_at=BASE)
    assembly_start(scratch, scope, key, ports)
    _postpaid(scratch, scope, account)
    assert len(assembly_recheck(scratch, scope, _request(account), ports)) == 1

    cash = deposit(scratch, scope, account, "200.00", observed_at=SWITCH)
    event = SettledFundsV1(account, cash.settlement_id)
    assert assembly_settled(scratch, scope, event, ports) == ()

    assert _states(scratch, scope) == [("withdrawn", "contract_type_changed", 1)]
    assert _documents(scratch, scope) == 0 and _draws(scratch, scope) == 0
    assert unrestricted(scratch, scope, account) == Decimal("200.00")
    assert source.issues == []


# --------------------------------------------------------------------------
# (f) the cap: 2 over 5 rows, the chain finishes
# --------------------------------------------------------------------------
def test_f_the_cap_bounds_the_batch_and_the_chain_finishes(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source, cap=2)
    for index in range(5):
        key = source.add(
            scope,
            account,
            f"renewal-{index}",
            due_at=BASE,
            service_ref=f"service:{index}",
        )
        assembly_start(scratch, scope, key, ports)
    _postpaid(scratch, scope, account)

    request, sizes = _request(account), []
    for _ in range(10):
        sizes.append(len(assembly_recheck(scratch, scope, request, ports)))
        continuations = _events(scratch, scope, RECHECK)
        if len(continuations) < len(sizes):
            break
        last = continuations[-1]
        assert last["billing_account_id"] == str(account)
        assert datetime.fromisoformat(last["at"]) == SWITCH
        request = _request(
            account,
            SWITCH,
            FundedChargeCursorV1(
                datetime.fromisoformat(last["after_created_at"]),
                UUID(last["after_id"]),
            ),
        )

    assert sizes == [2, 2, 1]
    assert len(_events(scratch, scope, RECHECK)) == 2
    assert [row.state for row in _rows(scratch, scope)] == ["withdrawn"] * 5
    assert _documents(scratch, scope) == 0


# --------------------------------------------------------------------------
# (g) an account on the tenant default terms resolves through the default
# --------------------------------------------------------------------------
def test_g_an_account_with_no_override_follows_the_default(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", due_at=BASE)
    assembly_start(scratch, scope, key, ports)
    with session(scratch, scope) as db:
        record_default_terms(
            db,
            command=RecordDefaultTermsV1(
                scope=scope,
                effective_from=SWITCH,
                contract_type="postpaid",
                period="monthly",
                postpaid_billing_timing="period_end",
                postpaid_grace=timedelta(days=3),
                actor_ref="fixture",
                reason="default switch",
            ),
        )
        db.commit()

    (outcome,) = assembly_recheck(scratch, scope, _request(account), ports)

    assert outcome.reason_code == "contract_type_changed"
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]


# --------------------------------------------------------------------------
# (h) an attention row is untouched
# --------------------------------------------------------------------------
def test_h_an_attention_row_is_untouched(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1", due_at=BASE)
    first = assembly_start(scratch, scope, key, ports)
    with session(scratch, scope) as db:
        record_funded_charge_attention(
            db,
            scope=scope,
            instance_id=first.instance_id,
            code="owner_refused",
            ports=ports,
        )
        db.commit()
    _postpaid(scratch, scope, account)
    before = _states(scratch, scope)
    events = len(_access(scratch, scope))

    assert assembly_recheck(scratch, scope, _request(account), ports) == ()

    assert (
        _states(scratch, scope)
        == before
        == [("attention_required", "owner_refused", 1)]
    )
    assert len(_access(scratch, scope)) == events


# --------------------------------------------------------------------------
# (i) tenant isolation and a caller rollback
# --------------------------------------------------------------------------
def test_i_the_recheck_is_tenant_isolated(scratch):
    scope, (account,) = _world(scratch)
    other = new_scope(scratch)
    with session(scratch, other) as db:
        seed_default_terms(db, other)
        other_account = seed_account(db, other).id
        db.commit()
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    mine = source.add(scope, account, "renewal-mine", due_at=BASE, service_ref="s:a")
    theirs = source.add(
        other, other_account, "renewal-theirs", due_at=BASE, service_ref="s:b"
    )
    assembly_start(scratch, scope, mine, ports)
    assembly_start(scratch, other, theirs, ports)
    _postpaid(scratch, scope, account)
    _postpaid(scratch, other, other_account)

    # The other tenant's scope cannot reach this tenant's account.
    with session(scratch, other) as db, pytest.raises(BillingError):
        reevaluate_account_terms(
            db, scope=other, request=_request(account), ports=ports
        )
    assert _states(scratch, scope) == [("pending", None, 0)]

    assert len(assembly_recheck(scratch, scope, _request(account), ports)) == 1
    assert _states(scratch, scope) == [("withdrawn", "contract_type_changed", 1)]
    assert _states(scratch, other) == [("pending", None, 0)]
    with session(scratch, other) as db:
        assert _count(db, FundedChargeInstance) == 1


def test_i_a_caller_rollback_leaves_no_change_and_no_event(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source, cap=1)
    for index in range(2):
        key = source.add(
            scope, account, f"renewal-{index}", due_at=BASE, service_ref=f"s:{index}"
        )
        assembly_start(scratch, scope, key, ports)
    _postpaid(scratch, scope, account)
    access = len(_access(scratch, scope))

    with session(scratch, scope) as db:
        outcomes = reevaluate_account_terms(
            db, scope=scope, request=_request(account), ports=ports
        )
        assert len(outcomes) == 1
        db.rollback()

    assert [row.state for row in _rows(scratch, scope)] == ["pending", "pending"]
    assert len(_access(scratch, scope)) == access
    with session(scratch, scope) as db:
        assert _count(db, OutboxEvent, OutboxEvent.event_type == RECHECK) == 0
