"""Unit proofs for money-flows F2 FundedCharge: graph, identity, decisions.

No Postgres: owners are replaced by plain fakes at the module seam, and the
instance table is exercised on in-memory SQLite (schema stripped) where a query
decides something (the access aggregate, paging).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from dotmac_billing import BillingRuleViolation
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Currency, Money
from dotmac_money_flows import (
    AccountTermsChangedV1,
    FundedChargeCursorV1,
    FundedChargePolicyV1,
    FundedChargePorts,
    FundedChargeRefused,
    FundedChargeState,
    FundedChargeStepFailed,
    RatedChargeKeyV1,
    RatedChargeV1,
    SettledFundsV1,
    StartFundedChargeV1,
)
from dotmac_money_flows import funded_charge as flows
from dotmac_money_flows._service_period import period_end
from dotmac_money_flows.contracts import (
    FUNDED_CHARGE_INSERT_STATES,
    FUNDED_CHARGE_TRANSITIONS,
    funded_charge_transition_allowed,
)
from dotmac_money_flows.models import FundedChargeInstance
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

NGN = Currency("NGN", 2)
DUE = datetime(2026, 11, 20, tzinfo=UTC)
SCOPE = TenantScope(uuid4())
ACCOUNT = uuid4()
MIGRATION = next(
    Path(__file__)
    .resolve()
    .parents[2]
    .glob(
        "packages/dotmac-money-flows/src/dotmac_money_flows/migrations/versions/"
        "mf_0002_funded_charge_instances.py"
    )
)
KEY = RatedChargeKeyV1("src", "kind", "fact-1", "1")


def _money(amount: str) -> Money:
    return Money.of(amount, NGN)


def _charge(total: str = "10.00", *, key: RatedChargeKeyV1 = KEY, due=DUE):
    return RatedChargeV1(key, ACCOUNT, "service:one", _money(total), due)


# --------------------------------------------------------------------------
# Transition table
# --------------------------------------------------------------------------
def test_the_python_table_matches_the_database_trigger():
    source = " ".join(MIGRATION.read_text(encoding="utf-8").split())
    insertable = re.search(r"NEW\.state NOT IN \(([^)]*)\)", source)
    assert insertable is not None
    assert {s.strip(" '") for s in insertable.group(1).split(",")} == {
        s.value for s in FUNDED_CHARGE_INSERT_STATES
    }
    pairs = set()
    for old, new_clause in re.findall(
        r"OLD\.state = '(\w+)'\s+AND NEW\.state (IN \([^)]*\)|= '\w+')", source
    ):
        for new in re.findall(r"'(\w+)'", new_clause):
            pairs.add((old, new))
    assert pairs == {
        (old.value, new.value)
        for old, news in FUNDED_CHARGE_TRANSITIONS.items()
        for new in news
    }
    assert pairs == {
        (old.value, new.value)
        for old, new in product(FundedChargeState, FundedChargeState)
        if funded_charge_transition_allowed(old, new)
    }
    for terminal in (FundedChargeState.DISCHARGED, FundedChargeState.WITHDRAWN):
        assert terminal not in FUNDED_CHARGE_TRANSITIONS


# --------------------------------------------------------------------------
# The charge identity
# --------------------------------------------------------------------------
def test_the_charge_ref_is_alias_proof():
    a = flows.charge_ref_for(RatedChargeKeyV1("a", "bc", "d", "1"))
    b = flows.charge_ref_for(RatedChargeKeyV1("ab", "c", "d", "1"))
    c = flows.charge_ref_for(RatedChargeKeyV1("a", "b", "cd", "1"))
    d = flows.charge_ref_for(RatedChargeKeyV1("1:a", "", "", ""))
    assert len({a, b, c, d}) == 4


@pytest.mark.parametrize(
    "key",
    [
        KEY,
        RatedChargeKeyV1("1:a", "2:bc", "::", "3"),
        RatedChargeKeyV1("", "", "", ""),
        RatedChargeKeyV1("sistema", "tipo", "é" * 40, "ü"),
    ],
)
def test_the_charge_ref_round_trips_to_its_key(key):
    assert flows.key_for_charge_ref(flows.charge_ref_for(key)) == key


def test_a_charge_ref_over_1024_bytes_is_refused_by_bytes_not_characters():
    just_fits = RatedChargeKeyV1("s", "k", "x" * 1000, "1")
    assert len(flows.charge_ref_for(just_fits).encode()) <= 1024
    with pytest.raises(ValueError):
        flows.charge_ref_for(RatedChargeKeyV1("s", "k", "x" * 1100, "1"))
    # 600 characters, 1200 bytes: under the character count, over the byte cap.
    with pytest.raises(ValueError):
        flows.charge_ref_for(RatedChargeKeyV1("s", "k", "é" * 600, "1"))


def test_a_malformed_charge_ref_is_not_a_key():
    with pytest.raises(ValueError):
        flows.key_for_charge_ref("1:a1:b")


def test_the_instance_id_is_deterministic_and_tenant_scoped():
    ref = flows.charge_ref_for(KEY)
    assert flows.instance_id_for(SCOPE, ref) == flows.instance_id_for(SCOPE, ref)
    assert flows.instance_id_for(SCOPE, ref) != flows.instance_id_for(
        TenantScope(uuid4()), ref
    )


@pytest.mark.parametrize("name", sorted(flows.REPLAY_CONSTRAINTS))
def test_the_duplicate_charge_constraints_are_a_replay(name):
    orig = SimpleNamespace(diag=SimpleNamespace(constraint_name=name))
    assert flows.is_funded_charge_replay_conflict(IntegrityError("x", {}, orig))


def test_nothing_else_is_a_replay_not_even_by_message():
    other = IntegrityError(
        "x", {}, SimpleNamespace(diag=SimpleNamespace(constraint_name="ck_other"))
    )
    by_text = IntegrityError("uq_funded_charge_instances_charge", {}, Exception())
    assert not flows.is_funded_charge_replay_conflict(other)
    assert not flows.is_funded_charge_replay_conflict(by_text)
    assert not flows.is_funded_charge_replay_conflict(ValueError("x"))


# --------------------------------------------------------------------------
# Command checks and the period instant
# --------------------------------------------------------------------------
ZONE = ZoneInfo("UTC")


def _end(start):
    """The monthly period end under the UTC policy (GAP-5)."""
    return None if start is None else period_end(start, "monthly", ZONE)


def _command(
    *,
    key=KEY,
    account=ACCOUNT,
    total="10.00",
    status="verified",
    start=DUE,
    end="monthly",
    timing="advance",
    due_at="issue",
    timezone="UTC",
):
    return SimpleNamespace(
        timezone=timezone,
        due_at=start if due_at == "issue" else due_at,
        obligation=SimpleNamespace(
            collection_timing=timing,
            source_system=key.source_system,
            source_kind=key.source_kind,
            source_fact_id=key.source_fact_id,
            source_fact_version=key.source_fact_version,
            billing_account_id=account,
            total_amount=_money(total),
            service_period=SimpleNamespace(
                status=SimpleNamespace(value=status),
                starts_at=start,
                ends_at=_end(start) if end == "monthly" else end,
            ),
        ),
    )


@pytest.fixture
def monthly_terms(monkeypatch):
    """The Billing terms a command check reads: monthly.

    Not autouse: the ``_boom`` guards elsewhere in this file rely on
    ``get_effective_account_terms`` staying reachable to fail the test.
    """
    monkeypatch.setattr(
        flows,
        "_terms",
        lambda *a, **k: SimpleNamespace(period=SimpleNamespace(value="monthly")),
    )


def _matches(command, charge=None):
    ctx = flows._Ctx(None, SCOPE, SCOPE.tenant_id, None, FundedChargePolicyV1("p1"))
    return flows._command_matches(ctx, command, KEY, ACCOUNT, charge or _charge(), DUE)


def test_a_command_that_is_the_charges_own_passes(monthly_terms):
    assert _matches(_command())


@pytest.mark.parametrize(
    "command",
    [
        _command(key=RatedChargeKeyV1("src", "kind", "other", "1")),
        _command(key=RatedChargeKeyV1("src", "kind", "fact-1", "2")),
        _command(account=uuid4()),
        _command(total="10.01"),
        _command(status="unknown_unverified"),
        _command(start=DUE + timedelta(seconds=1)),
        _command(start=None),
        _command(end=DUE + timedelta(days=29)),  # not the terms period
        _command(timing="arrears"),
        _command(due_at=DUE + timedelta(days=1)),
        _command(timezone="Africa/Lagos"),  # the policy says UTC
    ],
)
def test_a_command_that_is_not_the_charges_own_is_refused(command, monthly_terms):
    assert not _matches(command)


class _Policy:
    def __init__(self, cap=20):
        self.policy = FundedChargePolicyV1("p1", cap)
        self.reads = 0

    def read(self, db, *, scope):
        self.reads += 1
        return self.policy


class _Charges:
    def __init__(self, *charges):
        self.charges = {charge.key: charge for charge in charges}
        self.reads = []
        self.issues = []

    def read(self, db, *, scope, key):
        self.reads.append(key)
        return self.charges.get(key)

    def issue_command(self, db, *, scope, key, issue_at):
        self.issues.append((key, issue_at))
        return _command(key=key, start=issue_at)


def _ports(charges, cap=20):
    return FundedChargePorts(charges, _Policy(cap), object(), object())


def _boom(*args, **kwargs):
    raise AssertionError("an owner was called")


def _spy_discharge(monkeypatch):
    seen = []

    def discharge(ctx, row, charge, *, charge_ref, issue_at, funding_settlement_id):
        seen.append((issue_at, funding_settlement_id))
        target = row or SimpleNamespace(
            id=uuid4(),
            billing_account_id=ACCOUNT,
            reason_code=None,
            version=0,
            document_id=None,
        )
        target.state = "discharged"
        return target

    monkeypatch.setattr(flows, "_discharge", discharge)
    monkeypatch.setattr(flows, "_signal", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_begin_service", lambda *a, **k: False)
    monkeypatch.setattr(flows, "_is_prepaid", lambda *a, **k: True)
    return seen


def test_a_started_charge_that_is_funded_is_issued_at_its_due_instant(monkeypatch):
    seen = _spy_discharge(monkeypatch)
    monkeypatch.setattr(flows, "_find", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_is_prepaid", lambda *a, **k: True)
    monkeypatch.setattr(flows, "_fits", lambda *a, **k: True)
    charges = _Charges(_charge())

    outcome = flows.start_funded_charge(
        None,
        scope=SCOPE,
        command=StartFundedChargeV1(KEY),
        ports=_ports(charges),
    )

    assert outcome.state is FundedChargeState.DISCHARGED
    assert seen == [(DUE, None)]


@pytest.mark.parametrize(
    "cash_at, expected",
    [(-5, DUE), (0, DUE), (5, DUE + timedelta(days=5))],
    ids=["cash before due", "cash at due", "cash after due"],
)
def test_a_re_evaluated_charge_is_issued_at_the_later_of_due_and_the_cash(
    monkeypatch, cash_at, expected
):
    seen = _spy_discharge(monkeypatch)
    monkeypatch.setattr(flows, "_fits", lambda *a, **k: True)
    settlement = SimpleNamespace(
        observed_at=DUE + timedelta(days=cash_at), settlement_id=uuid4()
    )
    row = SimpleNamespace(
        charge_ref=flows.charge_ref_for(KEY),
        service_ref="service:one",
        billing_account_id=ACCOUNT,
    )
    ctx = flows._Ctx(
        None,
        SCOPE,
        SCOPE.tenant_id,
        _ports(_Charges(_charge())),
        FundedChargePolicyV1("p1"),
    )

    flows._reevaluate(ctx, row, settlement)

    assert seen == [(expected, settlement.settlement_id)]


@pytest.mark.parametrize(
    ("funded_by", "credit_days"),
    [(None, None), (3, None), (None, 5), (None, -2)],
    ids=["due", "settlement", "credit_after_due", "credit_before_due"],
)
def test_a_retry_is_issued_at_due_or_the_later_funding_cash_or_credit(
    monkeypatch, funded_by, credit_days
):
    seen = _spy_discharge(monkeypatch)
    monkeypatch.setattr(
        flows,
        "newest_credit_at",
        lambda *a, **k: None
        if credit_days is None
        else DUE + timedelta(days=credit_days),
    )
    row = SimpleNamespace(
        id=uuid4(),
        tenant_id=SCOPE.tenant_id,
        version=2,
        state="attention_required",
        charge_ref=flows.charge_ref_for(KEY),
        service_ref="service:one",
        billing_account_id=ACCOUNT,
        funding_settlement_id=None if funded_by is None else uuid4(),
        reason_code="x",
        document_id=None,
    )
    monkeypatch.setattr(flows, "_find_id", lambda *a, **k: row)
    monkeypatch.setattr(flows, "get_funding_availability", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_fits", lambda *a, **k: True)
    monkeypatch.setattr(
        flows,
        "get_confirmed_settlement",
        lambda *a, **k: SimpleNamespace(
            observed_at=DUE + timedelta(days=funded_by or 0)
        ),
    )

    flows.retry_funded_charge(
        None,
        scope=SCOPE,
        instance_id=row.id,
        expected_version=2,
        actor_ref="staff:1",
        ports=_ports(_Charges(_charge())),
    )

    expected = DUE + timedelta(days=max(funded_by or 0, credit_days or 0))
    assert seen == [(expected, None)]


def test_the_terms_read_reaches_the_billing_owner_so_the_boom_guards_can_fire(
    monkeypatch,
):
    # No autouse terms stub: a guard that makes ``get_effective_account_terms``
    # raise must be reachable through ``_terms`` and ``_is_prepaid``.
    monkeypatch.setattr(flows, "get_effective_account_terms", _boom)
    with pytest.raises(AssertionError, match="an owner was called"):
        flows._terms(None, SCOPE, ACCOUNT, DUE)
    with pytest.raises(AssertionError, match="an owner was called"):
        flows._is_prepaid(None, SCOPE, ACCOUNT, DUE)


@pytest.mark.parametrize(
    "policy",
    [
        FundedChargePolicyV1("p1", proration="actual_elapsed_time"),
        FundedChargePolicyV1("p1", proration=""),
        FundedChargePolicyV1("p1", billing_timezone="Mars/Olympus"),
        FundedChargePolicyV1("p1", billing_timezone=""),
        FundedChargePolicyV1("p1", billing_timezone="Africa"),
    ],
    ids=["proration", "proration_empty", "zone", "zone_empty", "zone_directory"],
)
def test_an_unknown_proration_or_zone_is_refused_before_any_owner_call(
    monkeypatch, policy
):
    for name in ("get_funding_availability", "get_effective_account_terms", "_write"):
        monkeypatch.setattr(flows, name, _boom)
    monkeypatch.setattr(flows, "_find", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_is_prepaid", lambda *a, **k: True)
    ports = _ports(_Charges(_charge()))
    ports.policy.policy = policy
    with pytest.raises(ValueError):
        flows.start_funded_charge(
            None, scope=SCOPE, command=StartFundedChargeV1(KEY), ports=ports
        )
    with pytest.raises(ValueError):
        flows.on_settlement_settled(
            None,
            scope=SCOPE,
            event=SettledFundsV1(ACCOUNT, uuid4()),
            ports=ports,
        )
    with pytest.raises(ValueError):
        flows.reevaluate_account_terms(
            None,
            scope=SCOPE,
            request=AccountTermsChangedV1(ACCOUNT, AT),
            ports=ports,
        )


def test_a_known_proration_and_zone_pass_the_policy_check():
    for proration in ("actual_calendar_days", "none"):
        ports = _ports(_Charges())
        ports.policy.policy = FundedChargePolicyV1(
            "p1", proration=proration, billing_timezone="Africa/Lagos"
        )
        assert flows._read_policy(None, SCOPE, ports).proration == proration


# --------------------------------------------------------------------------
# Refusals before any write, and replays
# --------------------------------------------------------------------------
def test_a_replay_calls_no_owner(monkeypatch):
    row = SimpleNamespace(
        id=uuid4(),
        state="discharged",
        reason_code=None,
        version=1,
        billing_account_id=ACCOUNT,
        document_id=uuid4(),
    )
    for name in (
        "get_effective_account_terms",
        "get_funding_availability",
        "get_confirmed_settlement",
        "issue_rated_invoice",
        "apply_available_credit",
        "get_document_exposure",
    ):
        monkeypatch.setattr(flows, name, _boom)
    monkeypatch.setattr(flows, "_find", lambda *a, **k: row)
    charges = _Charges(_charge())
    ports = _ports(charges)

    outcome = flows.start_funded_charge(
        None, scope=SCOPE, command=StartFundedChargeV1(KEY), ports=ports
    )

    assert outcome.replayed and outcome.state is FundedChargeState.DISCHARGED
    assert charges.reads == [] and ports.policy.reads == 0


def test_an_unknown_charge_is_refused_with_no_row_and_no_owner_call(monkeypatch):
    monkeypatch.setattr(flows, "_find", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_write", _boom)
    monkeypatch.setattr(flows, "get_effective_account_terms", _boom)
    with pytest.raises(FundedChargeRefused) as refused:
        flows.start_funded_charge(
            None,
            scope=SCOPE,
            command=StartFundedChargeV1(KEY),
            ports=_ports(_Charges()),
        )
    assert refused.value.code == "charge_not_found"


def test_a_charge_that_is_not_prepaid_is_refused_with_no_row(monkeypatch):
    monkeypatch.setattr(flows, "_find", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_is_prepaid", lambda *a, **k: False)
    monkeypatch.setattr(flows, "_write", _boom)
    monkeypatch.setattr(flows, "get_funding_availability", _boom)
    ports = _ports(_Charges(_charge()))
    with pytest.raises(FundedChargeRefused) as refused:
        flows.start_funded_charge(
            None, scope=SCOPE, command=StartFundedChargeV1(KEY), ports=ports
        )
    assert refused.value.code == "not_prepaid"
    assert ports.policy.reads == 0


def test_the_terms_are_read_again_after_the_account_lock_and_gate_the_start(
    monkeypatch,
):
    calls = []
    answers = iter([True, False])  # prepaid before the lock, postpaid after it
    monkeypatch.setattr(flows, "_find", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_fits", lambda *a, **k: calls.append("lock") or False)
    monkeypatch.setattr(
        flows, "_is_prepaid", lambda *a, **k: calls.append("terms") or next(answers)
    )
    monkeypatch.setattr(flows, "_write", _boom)
    monkeypatch.setattr(flows, "_begin_service", _boom)
    with pytest.raises(FundedChargeRefused) as refused:
        flows.start_funded_charge(
            None,
            scope=SCOPE,
            command=StartFundedChargeV1(KEY),
            ports=_ports(_Charges(_charge())),
        )
    assert refused.value.code == "not_prepaid"
    assert calls == ["terms", "lock", "terms"]  # authoritative read under the lock


def test_a_duplicate_that_won_the_lock_is_replayed_without_the_post_lock_terms_read(
    monkeypatch,
):
    winner = SimpleNamespace(
        id=uuid4(),
        state="pending",
        reason_code=None,
        version=0,
        billing_account_id=ACCOUNT,
        document_id=None,
    )
    finds = iter([None, winner])
    monkeypatch.setattr(flows, "_find", lambda *a, **k: next(finds))
    monkeypatch.setattr(flows, "_fits", lambda *a, **k: False)
    reads = []
    monkeypatch.setattr(
        flows, "_is_prepaid", lambda *a, **k: reads.append("terms") or True
    )
    outcome = flows.start_funded_charge(
        None,
        scope=SCOPE,
        command=StartFundedChargeV1(KEY),
        ports=_ports(_Charges(_charge())),
    )
    assert outcome.replayed
    assert reads == ["terms"]  # the fast path only; none after the lock


def test_a_re_evaluated_row_that_is_no_longer_prepaid_is_withdrawn_unposted(
    monkeypatch,
):
    written = []
    monkeypatch.setattr(flows, "_is_prepaid", lambda *a, **k: False)
    monkeypatch.setattr(
        flows, "_write", lambda ctx, row, **kw: written.append(kw) or row
    )
    monkeypatch.setattr(flows, "issue_rated_invoice", _boom)
    charges = _Charges(_charge())
    ctx = flows._Ctx(
        None, SCOPE, SCOPE.tenant_id, _ports(charges), FundedChargePolicyV1("p1")
    )
    row = SimpleNamespace(billing_account_id=ACCOUNT)

    flows._discharge(
        ctx,
        row,
        _charge(),
        charge_ref=flows.charge_ref_for(KEY),
        issue_at=DUE,
        funding_settlement_id=None,
    )

    assert [(w["state"], w["reason_code"]) for w in written] == [
        (FundedChargeState.WITHDRAWN, "contract_type_changed")
    ]
    assert charges.issues == []


def test_a_command_that_is_not_the_charges_own_goes_to_attention_with_no_post(
    monkeypatch, monthly_terms
):
    written = []
    monkeypatch.setattr(flows, "_is_prepaid", lambda *a, **k: True)
    monkeypatch.setattr(
        flows, "_write", lambda ctx, row, **kw: written.append(kw) or row
    )
    monkeypatch.setattr(flows, "issue_rated_invoice", _boom)
    charges = _Charges(_charge())
    charges.issue_command = lambda *a, **k: _command(start=DUE + timedelta(days=1))
    ctx = flows._Ctx(
        None, SCOPE, SCOPE.tenant_id, _ports(charges), FundedChargePolicyV1("p1")
    )

    flows._discharge(
        ctx,
        None,
        _charge(),
        charge_ref=flows.charge_ref_for(KEY),
        issue_at=DUE,
        funding_settlement_id=None,
    )

    assert [(w["state"], w["reason_code"]) for w in written] == [
        (FundedChargeState.ATTENTION_REQUIRED, "charge_command_mismatch")
    ]


def test_a_retry_names_its_actor_and_a_stale_version_before_the_source(monkeypatch):
    row = SimpleNamespace(
        id=uuid4(),
        version=3,
        state="attention_required",
        billing_account_id=ACCOUNT,
    )
    monkeypatch.setattr(flows, "_find_id", lambda *a, **k: row)
    monkeypatch.setattr(flows, "get_funding_availability", lambda *a, **k: None)
    charges = _Charges(_charge())
    ports = _ports(charges)
    kwargs = {"scope": SCOPE, "instance_id": row.id, "ports": ports}
    with pytest.raises(FundedChargeRefused) as nameless:
        flows.retry_funded_charge(None, expected_version=3, actor_ref=" ", **kwargs)
    with pytest.raises(FundedChargeRefused) as stale:
        flows.retry_funded_charge(
            None, expected_version=2, actor_ref="staff:1", **kwargs
        )
    row.state = "pending"
    with pytest.raises(FundedChargeRefused) as wrong_state:
        flows.retry_funded_charge(
            None, expected_version=3, actor_ref="staff:1", **kwargs
        )
    assert [e.value.code for e in (nameless, stale, wrong_state)] == [
        "actor_required",
        "stale_version",
        "retry_not_allowed",
    ]
    assert charges.reads == [] and ports.policy.reads == 0


def test_attention_names_exactly_one_of_instance_and_key():
    ports = _ports(_Charges())
    for kwargs in ({}, {"instance_id": uuid4(), "key": KEY}):
        with pytest.raises(ValueError):
            flows.record_funded_charge_attention(
                None, scope=SCOPE, code="x", ports=ports, **kwargs
            )
    with pytest.raises(ValueError):
        flows.record_funded_charge_attention(
            None, scope=SCOPE, code="Not A Code", ports=ports, key=KEY
        )


# --------------------------------------------------------------------------
# Re-evaluation: greedy whole-only order, the cap, the continuation
# --------------------------------------------------------------------------
class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, statement):
        return self

    def scalars(self):
        return iter(self.rows)


def _pending(total, created):
    key = RatedChargeKeyV1("src", "kind", f"fact-{total}-{created}", "1")
    row = SimpleNamespace(
        id=uuid4(),
        created_at=DUE + timedelta(minutes=created),
        charge_ref=flows.charge_ref_for(key),
        service_ref=f"service:{created}",
        billing_account_id=ACCOUNT,
        state="pending",
    )
    return row, _charge(total, key=key)


def _evaluate(monkeypatch, rows, balance, cap):
    """Run ``on_settlement_settled`` over fake rows; return (order, funded, events)."""
    charges = _Charges(*(charge for _, charge in rows))
    remaining = [Money.of(balance, NGN)]
    order, funded, events = [], [], []

    def fits(ctx, charge, account_id, issue_at):
        order.append(charge.key.source_fact_id)
        return remaining[0] >= charge.total

    def discharge(ctx, row, charge, *, charge_ref, issue_at, funding_settlement_id):
        remaining[0] = remaining[0] - charge.total
        funded.append(charge.key.source_fact_id)
        row.state = "discharged"
        row.reason_code, row.version, row.document_id = None, 1, uuid4()
        return row

    monkeypatch.setattr(flows, "_fits", fits)
    monkeypatch.setattr(flows, "_discharge", discharge)
    monkeypatch.setattr(flows, "_signal", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_begin_service", lambda *a, **k: False)
    monkeypatch.setattr(flows, "_is_prepaid", lambda *a, **k: True)
    monkeypatch.setattr(
        flows,
        "get_confirmed_settlement",
        lambda *a, **k: SimpleNamespace(observed_at=DUE, settlement_id=uuid4()),
    )
    monkeypatch.setattr(flows, "_enqueue_reevaluate", lambda *args: events.append(args))
    outcomes = flows.on_settlement_settled(
        _Rows([row for row, _ in rows]),
        scope=SCOPE,
        event=SettledFundsV1(ACCOUNT, uuid4()),
        ports=_ports(charges, cap),
    )
    return order, funded, events, outcomes


def test_pending_charges_are_funded_oldest_first_whole_only_skipping_misfits(
    monkeypatch,
):
    rows = [_pending("10.00", 1), _pending("3.00", 2), _pending("4.00", 3)]
    order, funded, events, outcomes = _evaluate(monkeypatch, rows, "5.00", cap=10)
    # 5.00 never touches the 10.00 charge; the 3.00 fits; 4.00 no longer does.
    assert order == [row[1].key.source_fact_id for row in rows]
    assert funded == [rows[1][1].key.source_fact_id]
    assert len(outcomes) == 1 and events == []


def test_the_cap_bounds_the_batch_and_the_continuation_resumes_after_the_last(
    monkeypatch,
):
    rows = [_pending("1.00", index) for index in range(1, 6)]
    order, funded, events, outcomes = _evaluate(monkeypatch, rows, "100.00", cap=2)
    assert len(order) == 2 and len(funded) == 2 and len(outcomes) == 2
    ((_, tenant, account, settlement, after),) = events
    assert (tenant, account) == (SCOPE.tenant_id, ACCOUNT)
    assert after == FundedChargeCursorV1(rows[1][0].created_at, rows[1][0].id)


def test_a_cap_of_exactly_the_pending_rows_enqueues_no_continuation(monkeypatch):
    rows = [_pending("1.00", index) for index in range(1, 3)]
    _, funded, events, _ = _evaluate(monkeypatch, rows, "100.00", cap=2)
    assert len(funded) == 2 and events == []


def test_a_cap_below_one_is_refused():
    with pytest.raises(ValueError):
        flows.on_settlement_settled(
            None,
            scope=SCOPE,
            event=SettledFundsV1(ACCOUNT, uuid4()),
            ports=_ports(_Charges(), 0),
        )


def test_an_owner_refusal_names_the_failed_row_and_keeps_the_code(monkeypatch):
    rows = [_pending("1.00", 1), _pending("1.00", 2)]

    def refuse(*args, **kwargs):
        raise BillingRuleViolation("tax_evidence_invalid", "bad evidence")

    monkeypatch.setattr(flows, "_reevaluate", refuse)
    monkeypatch.setattr(
        flows,
        "get_confirmed_settlement",
        lambda *a, **k: SimpleNamespace(observed_at=DUE),
    )
    with pytest.raises(FundedChargeStepFailed) as failed:
        flows.on_settlement_settled(
            _Rows([row for row, _ in rows]),
            scope=SCOPE,
            event=SettledFundsV1(ACCOUNT, uuid4()),
            ports=_ports(_Charges()),
        )
    assert failed.value.instance_id == rows[0][0].id
    assert failed.value.code == "billing.tax_evidence_invalid"
    assert isinstance(failed.value.__cause__, BillingRuleViolation)


def test_a_withdrawn_charge_is_recorded_and_the_loop_moves_on(monkeypatch):
    written = []
    monkeypatch.setattr(
        flows, "_write", lambda ctx, row, **kw: written.append(kw) or row
    )
    monkeypatch.setattr(flows, "_signal", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_begin_service", lambda *a, **k: False)
    row = SimpleNamespace(
        charge_ref=flows.charge_ref_for(KEY),
        service_ref=None,
        billing_account_id=ACCOUNT,
    )
    ctx = flows._Ctx(
        None, SCOPE, SCOPE.tenant_id, _ports(_Charges()), FundedChargePolicyV1("p1")
    )

    assert flows._reevaluate(ctx, row, SimpleNamespace(observed_at=DUE)) is row

    assert [(w["state"], w["reason_code"]) for w in written] == [
        (FundedChargeState.WITHDRAWN, "charge_withdrawn")
    ]


# --------------------------------------------------------------------------
# The access signal (SQLite stands in for the aggregate query)
# --------------------------------------------------------------------------
@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", execution_options={"schema_translate_map": {"mod_mflows": None}}
    )
    FundedChargeInstance.__table__.create(engine)
    with Session(engine) as session:
        yield session


_REFS = {"obligation_id": uuid4(), "document_id": uuid4(), "credit_draw_id": uuid4()}


def _instance(db, state, *, service="service:one", version=0, account=ACCOUNT):
    row = FundedChargeInstance(
        id=uuid4(),
        tenant_id=SCOPE.tenant_id,
        state=state,
        reason_code="x" if state in ("attention_required", "withdrawn") else None,
        policy_version="p",
        definition_version="d",
        version=version,
        charge_ref=f"ref-{uuid4()}",
        billing_account_id=account,
        service_ref=service,
        **_REFS if state == "discharged" else {},
    )
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize(
    "states, active",
    [
        ([], False),
        (["pending"], True),
        (["attention_required"], False),  # D-F2-7: our own refusal leaves it on
        (["discharged", "withdrawn"], False),
        (["attention_required", "pending"], True),
        (["discharged", "pending", "attention_required"], True),
    ],
)
def test_the_service_is_off_exactly_while_a_pending_row_exists(db, states, active):
    for state in states:
        _instance(db, state)
    _instance(db, "pending", service="service:other")
    assert flows._service_state(db, SCOPE.tenant_id, "service:one")[0] is active


def test_the_source_version_is_the_sum_of_version_plus_one_and_only_rises(db):
    seen = []
    first = _instance(db, "pending")
    seen.append(flows._service_state(db, SCOPE.tenant_id, "service:one")[1])
    first.state, first.version = "discharged", 1
    first.obligation_id, first.document_id, first.credit_draw_id = _REFS.values()
    db.flush()
    seen.append(flows._service_state(db, SCOPE.tenant_id, "service:one")[1])
    _instance(db, "pending")  # the next charge of the same service
    seen.append(flows._service_state(db, SCOPE.tenant_id, "service:one")[1])
    assert seen == [1, 2, 3]
    assert seen == sorted(set(seen))


def _signal_ctx(db):
    return flows._Ctx(
        db, SCOPE, SCOPE.tenant_id, _ports(_Charges()), FundedChargePolicyV1("p1")
    )


def test_the_signal_is_emitted_only_when_the_service_state_changes(db, monkeypatch):
    sent = []
    monkeypatch.setattr(
        flows, "enqueue_event", lambda db, **kwargs: sent.append(kwargs)
    )
    row = _instance(db, "pending")
    ctx = _signal_ctx(db)

    flows._signal(ctx, row, False, DUE)  # off -> on: changed
    flows._signal(ctx, row, True, DUE)  # still on: nothing
    row.state, row.version = "discharged", 1
    row.obligation_id, row.document_id, row.credit_draw_id = _REFS.values()
    db.flush()
    flows._signal(ctx, row, True, DUE)  # on -> off: changed

    assert [e["payload"]["active"] for e in sent] == [True, False]
    assert [e["payload"]["source_version"] for e in sent] == [1, 2]
    assert {e["event_type"] for e in sent} == {"money_flows.funded_charge.access.v1"}
    payload = sent[0]["payload"]
    assert payload["signal"] == "PREPAID_DEPLETED"
    assert payload["service_reference"] == "service:one"
    assert payload["observed_at"] == DUE.isoformat()
    assert payload["source_reference"] == f"money_flows:funded_charge:{row.id}"


def test_a_row_without_a_service_emits_no_signal(db, monkeypatch):
    monkeypatch.setattr(flows, "enqueue_event", _boom)
    row = _instance(db, "pending", service=None)
    flows._signal(_signal_ctx(db), row, False, DUE)


# --------------------------------------------------------------------------
# Reads: keyset paging and the clamped limit
# --------------------------------------------------------------------------
def test_listing_pages_by_keyset_and_clamps_the_limit(db):
    created = datetime(2026, 1, 1)
    rows = [_instance(db, "pending") for _ in range(5)]
    for row in rows:
        row.created_at = created  # one transaction: every row ties on created_at
    db.flush()
    seen, cursor = [], None
    while True:
        page = flows.list_pending_funded_charges(
            db, scope=SCOPE, billing_account_id=ACCOUNT, after=cursor, limit=2
        )
        if not page:
            break
        seen.extend(page)
        cursor = FundedChargeCursorV1(page[-1].created_at, page[-1].id)
    assert [row.id for row in seen] == sorted(row.id for row in rows)
    assert (
        len(
            flows.list_pending_funded_charges(
                db, scope=SCOPE, billing_account_id=ACCOUNT, limit=0
            )
        )
        == 1
    )
    assert (
        flows.list_pending_funded_charges(db, scope=SCOPE, billing_account_id=uuid4())
        == ()
    )


# --------------------------------------------------------------------------
# Decisions that need no funds come before the funds check
# --------------------------------------------------------------------------
def _pending_row(**overrides):
    values = {
        "id": uuid4(),
        "charge_ref": flows.charge_ref_for(KEY),
        "service_ref": "service:one",
        "billing_account_id": ACCOUNT,
        "state": "pending",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _written(monkeypatch):
    written = []
    monkeypatch.setattr(
        flows, "_write", lambda ctx, row, **kw: written.append(kw) or row
    )
    monkeypatch.setattr(flows, "_signal", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_begin_service", lambda *a, **k: False)
    monkeypatch.setattr(flows, "_fits", _boom)
    monkeypatch.setattr(flows, "get_funding_availability", _boom)
    return written


def test_a_re_evaluated_row_that_became_postpaid_is_withdrawn_before_funds(
    monkeypatch,
):
    written = _written(monkeypatch)
    monkeypatch.setattr(flows, "_is_prepaid", lambda *a, **k: False)
    ctx = flows._Ctx(
        None,
        SCOPE,
        SCOPE.tenant_id,
        _ports(_Charges(_charge())),
        FundedChargePolicyV1("p1"),
    )

    flows._reevaluate(
        ctx, _pending_row(), SimpleNamespace(observed_at=DUE, settlement_id=uuid4())
    )

    assert [(w["state"], w["reason_code"]) for w in written] == [
        (FundedChargeState.WITHDRAWN, "contract_type_changed")
    ]


@pytest.mark.parametrize("other", ["account", "key"])
def test_a_charge_the_port_reports_under_another_account_or_key_is_attention(
    monkeypatch, other
):
    written = _written(monkeypatch)
    monkeypatch.setattr(flows, "_is_prepaid", _boom)
    if other == "account":
        charge = RatedChargeV1(KEY, uuid4(), "service:one", _money("10.00"), DUE)
    else:
        charge = _charge()
        row_key = RatedChargeKeyV1("src", "kind", "fact-2", "1")
    row = _pending_row()
    charges = _Charges(charge)
    if other == "key":
        row = _pending_row(charge_ref=flows.charge_ref_for(row_key))
        charges.charges[row_key] = charge  # the port answers with a different key
    ctx = flows._Ctx(
        None, SCOPE, SCOPE.tenant_id, _ports(charges), FundedChargePolicyV1("p1")
    )

    flows._reevaluate(ctx, row, SimpleNamespace(observed_at=DUE, settlement_id=uuid4()))

    assert [(w["state"], w["reason_code"]) for w in written] == [
        (FundedChargeState.ATTENTION_REQUIRED, "charge_command_mismatch")
    ]
    assert charges.issues == []


def test_a_charge_source_failure_on_one_row_is_a_step_failure_for_that_row(
    monkeypatch,
):
    class Broken(_Charges):
        def read(self, db, *, scope, key):
            raise RuntimeError("source down")

    row = _pending_row()
    ctx = flows._Ctx(
        None, SCOPE, SCOPE.tenant_id, _ports(Broken()), FundedChargePolicyV1("p1")
    )
    with pytest.raises(FundedChargeStepFailed) as failed:
        flows._reevaluate(ctx, row, SimpleNamespace(observed_at=DUE))
    assert failed.value.instance_id == row.id
    assert failed.value.code == "charge_source_error"


def test_a_database_fault_reading_a_charge_propagates_for_redelivery(monkeypatch):
    class Down(_Charges):
        def read(self, db, *, scope, key):
            raise OperationalError("SELECT 1", {}, Exception("connection lost"))

    row = _pending_row()
    ctx = flows._Ctx(
        None, SCOPE, SCOPE.tenant_id, _ports(Down()), FundedChargePolicyV1("p1")
    )
    with pytest.raises(OperationalError):
        flows._reevaluate(ctx, row, SimpleNamespace(observed_at=DUE))


# --------------------------------------------------------------------------
# Per-account terms re-check (D3)
# --------------------------------------------------------------------------
AT = DUE + timedelta(days=3)


def _recheck(
    monkeypatch,
    rows,
    *,
    cap=20,
    prepaid=lambda *a, **k: False,
    at=AT,
    after=None,
    charges=None,
):
    """Run ``reevaluate_account_terms`` over fake rows; return what happened."""
    if charges is None:
        charges = _Charges(*(charge for _, charge in rows))
    written, events, prepaid_at, signals = [], [], [], []

    def write(ctx, row, **kw):
        written.append(
            (row, kw["state"], kw["reason_code"], kw.get("funding_settlement_id"))
        )
        row.state, row.reason_code, row.version = (
            kw["state"].value,
            kw["reason_code"],
            1,
        )
        row.document_id = None
        return row

    def is_prepaid(db, scope, account_id, instant):
        prepaid_at.append(instant)
        return prepaid(db, scope, account_id, instant)

    monkeypatch.setattr(flows, "_write", write)
    monkeypatch.setattr(flows, "_is_prepaid", is_prepaid)
    monkeypatch.setattr(
        flows, "_signal", lambda ctx, row, was, at_: signals.append(at_)
    )
    monkeypatch.setattr(flows, "_begin_service", lambda *a, **k: False)
    monkeypatch.setattr(flows, "get_funding_availability", lambda *a, **k: None)
    for name in (
        "_fits",
        "_discharge",
        "get_confirmed_settlement",
        "issue_rated_invoice",
        "apply_available_credit",
    ):
        monkeypatch.setattr(flows, name, _boom)
    monkeypatch.setattr(
        flows, "_enqueue_terms_recheck", lambda *args: events.append(args)
    )
    outcomes = flows.reevaluate_account_terms(
        _Rows([row for row, _ in rows]),
        scope=SCOPE,
        request=AccountTermsChangedV1(ACCOUNT, at, after),
        ports=_ports(charges, cap),
    )
    return outcomes, written, events, prepaid_at, signals, charges


def test_a_switch_to_postpaid_withdraws_every_pending_row_and_calls_no_owner(
    monkeypatch,
):
    rows = [_pending("1.00", i) for i in range(1, 4)]
    outcomes, written, events, _, _, charges = _recheck(monkeypatch, rows)
    assert [(w[1], w[2], w[3]) for w in written] == [
        (FundedChargeState.WITHDRAWN, "contract_type_changed", None)
    ] * 3
    assert len(outcomes) == 3 and events == [] and charges.issues == []
    assert all(not o.replayed for o in outcomes)


def test_a_row_that_is_still_prepaid_is_left_unchanged_with_no_owner_write(
    monkeypatch,
):
    rows = [_pending("1.00", 1)]
    outcomes, written, events, _, signals, charges = _recheck(
        monkeypatch, rows, prepaid=lambda *a, **k: True
    )
    assert outcomes == () and written == [] and events == [] and signals == []
    assert charges.issues == []


@pytest.mark.parametrize(
    "days, expected", [(-2, DUE), (0, DUE), (3, DUE + timedelta(days=3))]
)
def test_the_terms_are_read_at_the_later_of_due_and_at(monkeypatch, days, expected):
    rows = [_pending("1.00", 1)]
    _, _, _, prepaid_at, signals, _ = _recheck(
        monkeypatch, rows, at=DUE + timedelta(days=days)
    )
    assert prepaid_at == [expected]
    assert signals == [expected]  # the withdrawal is dated when the service resumes


def test_a_charge_the_source_no_longer_holds_is_withdrawn(monkeypatch):
    rows = [_pending("1.00", 1)]
    _, written, _, prepaid_at, _, _ = _recheck(monkeypatch, rows, charges=_Charges())
    assert [(w[1], w[2]) for w in written] == [
        (FundedChargeState.WITHDRAWN, "charge_withdrawn")
    ]
    assert prepaid_at == []


def test_a_source_failure_is_a_step_failure_for_that_row(monkeypatch):
    class Broken(_Charges):
        def read(self, db, *, scope, key):
            raise RuntimeError("source down")

    rows = [_pending("1.00", 1)]
    with pytest.raises(FundedChargeStepFailed) as failed:
        _recheck(monkeypatch, rows, charges=Broken())
    assert failed.value.instance_id == rows[0][0].id
    assert failed.value.code == "charge_source_error"


def test_a_database_fault_reading_a_charge_in_the_recheck_propagates(monkeypatch):
    class Down(_Charges):
        def read(self, db, *, scope, key):
            raise OperationalError("SELECT 1", {}, Exception("connection lost"))

    rows = [_pending("1.00", 1)]
    with pytest.raises(OperationalError):
        _recheck(monkeypatch, rows, charges=Down())


def test_a_billing_refusal_is_a_step_failure_with_its_code(monkeypatch):
    rows = [_pending("1.00", 1), _pending("1.00", 2)]

    def refuse(*args, **kwargs):
        raise BillingRuleViolation("terms_unreadable", "bad")

    with pytest.raises(FundedChargeStepFailed) as failed:
        _recheck(monkeypatch, rows, prepaid=refuse)
    assert failed.value.instance_id == rows[0][0].id
    assert failed.value.code == "billing.terms_unreadable"
    assert isinstance(failed.value.__cause__, BillingRuleViolation)


def test_an_idempotency_conflict_is_a_step_failure(monkeypatch):
    from dotmac_kernel.idempotency import IdempotencyConflict

    def conflict(*args, **kwargs):
        raise IdempotencyConflict("x")

    rows = [_pending("1.00", 1)]
    with pytest.raises(FundedChargeStepFailed) as failed:
        _recheck(monkeypatch, rows, prepaid=conflict)
    assert failed.value.code == "idempotency_conflict"


@pytest.mark.parametrize("other", ["account", "key"])
def test_a_mismatch_is_attention_with_no_funding_settlement(monkeypatch, other):
    if other == "account":
        charge = RatedChargeV1(KEY, uuid4(), "service:one", _money("1.00"), DUE)
        row = _pending_row()
        charges = _Charges(charge)
    else:
        row_key = RatedChargeKeyV1("src", "kind", "fact-2", "1")
        row = _pending_row(charge_ref=flows.charge_ref_for(row_key))
        charges = _Charges(_charge())
        charges.charges[row_key] = _charge()
    _, written, _, prepaid_at, _, _ = _recheck(
        monkeypatch, [(row, None)], charges=charges
    )
    assert [(w[1], w[2], w[3]) for w in written] == [
        (FundedChargeState.ATTENTION_REQUIRED, "charge_command_mismatch", None)
    ]
    assert prepaid_at == []


def test_a_terms_recheck_cap_bounds_the_batch_and_the_continuation_resumes(
    monkeypatch,
):
    rows = [_pending("1.00", i) for i in range(1, 6)]
    outcomes, _, events, _, _, _ = _recheck(monkeypatch, rows, cap=2)
    assert len(outcomes) == 2
    ((_, tenant, account, at, after),) = events
    assert (tenant, account, at) == (SCOPE.tenant_id, ACCOUNT, AT)
    assert after == FundedChargeCursorV1(rows[1][0].created_at, rows[1][0].id)


def test_a_cap_equal_to_the_rows_enqueues_nothing(monkeypatch):
    rows = [_pending("1.00", i) for i in range(1, 3)]
    outcomes, _, events, _, _, _ = _recheck(monkeypatch, rows, cap=2)
    assert len(outcomes) == 2 and events == []


def test_a_cap_below_one_is_refused_before_any_owner_call(monkeypatch):
    monkeypatch.setattr(flows, "get_funding_availability", _boom)
    with pytest.raises(ValueError):
        flows.reevaluate_account_terms(
            None,
            scope=SCOPE,
            request=AccountTermsChangedV1(ACCOUNT, AT),
            ports=_ports(_Charges(), 0),
        )


def test_a_repeat_with_no_pending_rows_returns_nothing_and_emits_nothing(
    monkeypatch,
):
    outcomes, written, events, prepaid_at, signals, charges = _recheck(monkeypatch, [])
    assert outcomes == () and written == [] and events == []
    assert prepaid_at == [] and signals == [] and charges.reads == []


def test_a_naive_at_is_refused_before_any_read(monkeypatch):
    monkeypatch.setattr(flows, "get_funding_availability", _boom)
    ports = _ports(_Charges())
    with pytest.raises(FundedChargeRefused) as refused:
        flows.reevaluate_account_terms(
            None,
            scope=SCOPE,
            request=AccountTermsChangedV1(ACCOUNT, datetime(2026, 11, 20)),
            ports=ports,
        )
    assert refused.value.code == "at_not_timezone_aware"
    assert ports.policy.reads == 0


def test_the_query_selects_only_pending_rows_of_this_account():
    seen = []

    class Capture(_Rows):
        def execute(self, statement):
            seen.append(
                " ".join(
                    str(
                        statement.compile(compile_kwargs={"literal_binds": True})
                    ).split()
                )
            )
            return self

    import dotmac_money_flows.funded_charge as module

    original = module.get_funding_availability
    module.get_funding_availability = lambda *a, **k: None
    try:
        flows.reevaluate_account_terms(
            Capture([]),
            scope=SCOPE,
            request=AccountTermsChangedV1(ACCOUNT, AT),
            ports=_ports(_Charges()),
        )
    finally:
        module.get_funding_availability = original
    (sql,) = seen
    assert "state = 'pending'" in sql and str(ACCOUNT).replace("-", "") in sql.replace(
        "-", ""
    )
    assert "FOR UPDATE" in sql and "ORDER BY" in sql


def test_another_pending_row_on_the_same_service_keeps_it_off(db, monkeypatch):
    sent = []
    monkeypatch.setattr(flows, "enqueue_event", lambda db, **kw: sent.append(kw))
    first = _instance(db, "pending")
    _instance(db, "pending")  # a second pending charge on the same service
    ctx = _signal_ctx(db)
    first.state, first.reason_code, first.version = (
        "withdrawn",
        "contract_type_changed",
        1,
    )
    db.flush()
    flows._signal(ctx, first, True, AT)  # still off: no signal
    assert sent == []


@pytest.mark.parametrize("with_cursor", [False, True])
def test_the_continuation_cursor_clause_is_in_the_query_only_when_set(
    monkeypatch, with_cursor
):
    seen = []

    class Capture(_Rows):
        def execute(self, statement):
            seen.append(
                " ".join(
                    str(
                        statement.compile(compile_kwargs={"literal_binds": True})
                    ).split()
                )
            )
            return self

    monkeypatch.setattr(flows, "get_funding_availability", lambda *a, **k: None)
    after = FundedChargeCursorV1(DUE + timedelta(minutes=7), uuid4())
    flows.reevaluate_account_terms(
        Capture([]),
        scope=SCOPE,
        request=AccountTermsChangedV1(ACCOUNT, AT, after if with_cursor else None),
        ports=_ports(_Charges()),
    )
    (sql,) = seen
    marker = str(after.id).replace("-", "")
    assert (marker in sql.replace("-", "")) is with_cursor
    assert ("created_at >" in sql) is with_cursor
