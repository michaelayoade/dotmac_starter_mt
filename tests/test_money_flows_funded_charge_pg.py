"""Hosted PostgreSQL proofs for money-flows F2 FundedCharge: the acceptance tests.

AT1: only the charge's own invoice is posted, and the account comes from Billing.
AT2: one invoice, one credit draw and one access signal per change, under a
duplicate start, a real race and redelivery. AT3: flows emits the access event
and writes no Service Access Policy table. AT16: the batch cap bounds a
re-evaluation and the rest continues by event.

The timeline sits in the near future (see ``BASE``): account terms cannot be
back-dated against the database clock.
"""

from __future__ import annotations

import time
from dataclasses import fields, replace
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Barrier, Thread
from uuid import UUID

import pytest
from dotmac_billing import BillingError, ServicePeriodEvidenceV1, ServicePeriodStatus
from dotmac_billing import models as billing_models
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_money_flows import (
    FundedChargeCursorV1,
    FundedChargeState,
    SettledFundsV1,
    on_settlement_settled,
)
from dotmac_money_flows.models import FundedChargeInstance
from dotmac_service_access_policy import (
    AccessSignal,
    RecordAccessInputV2,
)
from dotmac_service_access_policy.versioning import (
    AccessInputDecision,
    StoredAccessStamp,
    decide_access_input,
)
from sqlalchemy import func, select, text

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
    session,
    unrestricted,
)
from tests.test_money_flows_settle_pg import _blocked_on_locks

TOTAL = "107.50"
ACCESS = "money_flows.funded_charge.access.v1"
DISCHARGED = "money_flows.funded_charge.discharged.v1"
REEVALUATE = "money_flows.funded_charge.reevaluate.v1"
DRAW_PREFIX = "money-flows:funded-charge:%"


@pytest.fixture(scope="module")
def scratch():
    with scratch_database(with_access_policy=True) as urls:
        yield urls


def _world(scratch, accounts: int = 1):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        seed_default_terms(db, scope)
        ids = [seed_account(db, scope).id for _ in range(accounts)]
        db.commit()
    return scope, ids


def _count(db, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where))


def _documents(scratch, scope) -> int:
    with session(scratch, scope) as db:
        return _count(db, billing_models.BillingDocument)


def _draws(scratch, scope) -> int:
    with session(scratch, scope) as db:
        return _count(
            db,
            billing_models.PostingGroup,
            billing_models.PostingGroup.source_ref.like(DRAW_PREFIX),
        )


def _events(scratch, scope, event_type: str) -> list[dict]:
    with session(scratch, scope) as db:
        rows = db.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == event_type)
        ).scalars()
        return [dict(row.payload) for row in rows]


def _access(scratch, scope) -> list[dict]:
    return sorted(
        _events(scratch, scope, ACCESS), key=lambda event: event["source_version"]
    )


def _rows(scratch, scope) -> list[FundedChargeInstance]:
    with session(scratch, scope) as db:
        rows = db.execute(
            select(FundedChargeInstance).order_by(
                FundedChargeInstance.created_at, FundedChargeInstance.id
            )
        ).scalars()
        return list(rows)


def _observation(payload: dict) -> RecordAccessInputV2:
    return RecordAccessInputV2(
        service_reference=payload["service_reference"],
        signal=AccessSignal(payload["signal"]),
        active=payload["active"],
        source_reference=payload["source_reference"],
        observed_at=datetime.fromisoformat(payload["observed_at"]),
        source_version=payload["source_version"],
        policy_version=payload["policy_version"],
    )


def _stamp(observation: RecordAccessInputV2) -> StoredAccessStamp:
    return StoredAccessStamp(
        source_version=observation.source_version,
        active=observation.active,
        source_reference=observation.source_reference,
        observed_at=observation.observed_at,
        policy_version=observation.policy_version,
    )


# --------------------------------------------------------------------------
# AT1: the charge's own invoice only; the account comes from Billing
# --------------------------------------------------------------------------
def test_at1_a_deposit_on_one_account_never_discharges_another_accounts_charge(
    scratch,
):
    scope, (left, right) = _world(scratch, 2)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    one = source.add(scope, left, "renewal-left", service_ref="service:left")
    two = source.add(scope, right, "renewal-right", service_ref="service:right")
    assert assembly_start(scratch, scope, one, ports).state is FundedChargeState.PENDING
    assert assembly_start(scratch, scope, two, ports).state is FundedChargeState.PENDING

    funded = deposit(scratch, scope, left, "200.00")
    assembly_settled(scratch, scope, SettledFundsV1(left, funded.settlement_id), ports)

    states = {row.billing_account_id: row.state for row in _rows(scratch, scope)}
    assert states == {left: "discharged", right: "pending"}
    assert _documents(scratch, scope) == 1
    assert _draws(scratch, scope) == 1


def test_at1_a_forged_settled_event_account_is_refused_by_billing(scratch):
    scope, (left, right) = _world(scratch, 2)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, right, "renewal-right", service_ref="service:right")
    assembly_start(scratch, scope, key, ports)
    cash = deposit(scratch, scope, left, "200.00")

    # The payload names the other account: Billing refuses the pair.
    with session(scratch, scope) as db, pytest.raises(BillingError):
        on_settlement_settled(
            db,
            scope=scope,
            event=SettledFundsV1(right, cash.settlement_id),
            ports=ports,
        )

    assert [row.state for row in _rows(scratch, scope)] == ["pending"]
    assert _documents(scratch, scope) == 0


def _other_identity(command, other):
    return replace(command, obligation=replace(command.obligation, source_fact_id="x"))


def _other_account(command, other):
    obligation = replace(command.obligation, billing_account_id=other)
    return replace(command, obligation=obligation)


def _wrong_start(command, other):
    period = command.obligation.service_period
    moved = ServicePeriodEvidenceV1(
        ServicePeriodStatus.VERIFIED,
        period.starts_at + timedelta(days=1),
        period.ends_at + timedelta(days=1),
    )
    obligation = replace(command.obligation, service_period=moved)
    return replace(command, obligation=obligation)


def _unverified_period(command, other):
    unknown = ServicePeriodEvidenceV1(ServicePeriodStatus.UNKNOWN_UNVERIFIED)
    obligation = replace(command.obligation, service_period=unknown)
    return replace(command, obligation=obligation)


@pytest.mark.parametrize(
    "mutator",
    [_other_identity, _other_account, _wrong_start, _unverified_period],
    ids=["identity", "account", "period_start", "period_unverified"],
)
def test_at1_a_command_that_is_not_the_charges_own_goes_to_attention_with_no_post(
    scratch, mutator
):
    scope, (account, other) = _world(scratch, 2)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    source.mutators[key] = lambda command: mutator(command, other)
    deposit(scratch, scope, account, "200.00")
    before = unrestricted(scratch, scope, account)

    outcome = assembly_start(scratch, scope, key, ports)

    assert outcome.state is FundedChargeState.ATTENTION_REQUIRED
    assert outcome.reason_code == "charge_command_mismatch"
    assert _documents(scratch, scope) == 0
    assert _draws(scratch, scope) == 0
    assert unrestricted(scratch, scope, account) == before


# --------------------------------------------------------------------------
# AT2: exactly one invoice, one draw and one access signal per change
# --------------------------------------------------------------------------
def test_at2_a_duplicate_start_and_a_redelivered_event_change_nothing_twice(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")

    first = assembly_start(scratch, scope, key, ports)
    again = assembly_start(scratch, scope, key, ports)
    assert first.state is FundedChargeState.PENDING and not first.replayed
    assert again.replayed and again.instance_id == first.instance_id
    assert [event["active"] for event in _access(scratch, scope)] == [True]

    cash = deposit(scratch, scope, account, "200.00")
    event = SettledFundsV1(account, cash.settlement_id)
    assert len(assembly_settled(scratch, scope, event, ports)) == 1
    assert assembly_settled(scratch, scope, event, ports) == ()
    assembly_start(scratch, scope, key, ports)

    assert [row.state for row in _rows(scratch, scope)] == ["discharged"]
    assert _documents(scratch, scope) == 1
    assert _draws(scratch, scope) == 1
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]
    assert len(_events(scratch, scope, DISCHARGED)) == 1
    assert [issue_at for _, issue_at in source.issues] == [BASE]


def test_at2_a_real_race_both_pass_the_replay_check_then_one_effect(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    key = source.add(scope, account, "renewal-1")
    deposit(scratch, scope, account, "200.00")
    ports = make_funded_ports(source, barrier=Barrier(2))
    results, errors = [], []

    def attempt() -> None:
        try:
            results.append(assembly_start(scratch, scope, key, ports))
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
            {"i": account},
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
        # Both passed the replay check and are blocked on the account lock.
        assert ports.policy.reads == 2
        assert waiting >= 2 and ungranted >= 2
        assert results == [] and errors == []
        holder.rollback()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == []
    assert sorted(outcome.replayed for outcome in results) == [False, True]
    assert {outcome.instance_id for outcome in results} == {results[0].instance_id}
    assert [row.state for row in _rows(scratch, scope)] == ["discharged"]
    assert _documents(scratch, scope) == 1
    assert _draws(scratch, scope) == 1
    assert len(_events(scratch, scope, DISCHARGED)) == 1
    assert _access(scratch, scope) == []  # funded at due: the service was never off


def test_at2_two_charges_of_one_service_give_increasing_versions_the_policy_applies(
    scratch,
):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    first = source.add(scope, account, "renewal-1", due_at=BASE)
    second = source.add(scope, account, "renewal-2", due_at=BASE + timedelta(days=30))

    assembly_start(scratch, scope, first, ports)  # pending: service off
    cash = deposit(scratch, scope, account, "200.00")
    assembly_settled(scratch, scope, SettledFundsV1(account, cash.settlement_id), ports)
    assembly_start(scratch, scope, second, ports)  # 92.50 left: pending again

    events = _access(scratch, scope)
    assert [event["active"] for event in events] == [True, False, True]
    versions = [event["source_version"] for event in events]
    assert versions == sorted(set(versions)) and len(versions) == 3
    stored = None
    for event in events:
        observation = _observation(event)
        assert decide_access_input(stored, observation) is AccessInputDecision.APPLY
        stored = _stamp(observation)
        # Redelivery of the same observation is idempotent.
        assert (
            decide_access_input(stored, observation) is AccessInputDecision.IDEMPOTENT
        )


# --------------------------------------------------------------------------
# AT3: flows emits the access event only
# --------------------------------------------------------------------------
def test_at3_the_access_payload_is_the_contract_and_no_policy_table_is_written(
    scratch,
):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source)
    key = source.add(scope, account, "renewal-1")
    assembly_start(scratch, scope, key, ports)
    cash = deposit(scratch, scope, account, "200.00")
    assembly_settled(scratch, scope, SettledFundsV1(account, cash.settlement_id), ports)

    events = _access(scratch, scope)
    assert len(events) == 2
    names = {field.name for field in fields(RecordAccessInputV2)}
    for payload in events:
        assert set(payload) == names
        observation = _observation(payload)
        for name in names:
            expected = getattr(observation, name)
            if name == "signal":
                expected = expected.value
            if name == "observed_at":
                expected = expected.isoformat()
            assert payload[name] == expected, name
        assert payload["signal"] == AccessSignal.PREPAID_DEPLETED.value
        assert payload["service_reference"] == "service:one"
    with session(scratch, scope) as db:
        for table in ("service_access_inputs", "desired_access_decisions"):
            count = db.scalar(
                text(f"SELECT count(*) FROM mod_serviceaccess.{table}")  # noqa: S608
            )
            assert count == 0, table


# --------------------------------------------------------------------------
# AT16: the batch cap bounds a re-evaluation; the rest continues by event
# --------------------------------------------------------------------------
def test_at16_the_cap_bounds_the_batch_and_the_continuation_finishes_it(scratch):
    scope, (account,) = _world(scratch)
    source = FakeChargeSource()
    ports = make_funded_ports(source, cap=2)
    for index in range(3):
        key = source.add(
            scope, account, f"renewal-{index}", service_ref=f"service:{index}"
        )
        assert (
            assembly_start(scratch, scope, key, ports).state
            is FundedChargeState.PENDING
        )
    cash = deposit(scratch, scope, account, "400.00")

    first = assembly_settled(
        scratch, scope, SettledFundsV1(account, cash.settlement_id), ports
    )

    assert len(first) == 2
    assert [row.state for row in _rows(scratch, scope)] == [
        "discharged",
        "discharged",
        "pending",
    ]
    (continuation,) = _events(scratch, scope, REEVALUATE)
    assert continuation["settlement_id"] == str(cash.settlement_id)
    event = SettledFundsV1(
        account,
        cash.settlement_id,
        FundedChargeCursorV1(
            datetime.fromisoformat(continuation["after_created_at"]),
            UUID(continuation["after_id"]),
        ),
    )
    second = assembly_settled(scratch, scope, event, ports)

    assert len(second) == 1
    assert [row.state for row in _rows(scratch, scope)] == ["discharged"] * 3
    assert len(_events(scratch, scope, REEVALUATE)) == 1  # no further continuation
    assert _documents(scratch, scope) == 3
    assert _draws(scratch, scope) == 3
    assert unrestricted(scratch, scope, account) == Decimal("400.00") - 3 * Decimal(
        TOTAL
    )
