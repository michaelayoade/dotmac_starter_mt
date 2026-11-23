"""Unit proofs for GAP-L1 ``on_account_credit_changed`` and ``_credit_dating``.

No database: Billing owner calls are replaced by recording fakes at the module
seam, so the fast path, the lock order, the cap, the dating and the failure
classes are each exercised alone. The hosted proofs are in
``test_money_flows_credit_recheck_pg.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_billing import BillingRuleViolation
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Currency, Money
from dotmac_money_flows import (
    AccountCreditChangedV1,
    BalancePassFailed,
    FundedChargeCursorV1,
    FundedChargePolicyV1,
    FundedChargePorts,
    FundedChargeStepFailed,
    RatedChargeKeyV1,
    RatedChargeV1,
)
from dotmac_money_flows import _credit_dating as dating
from dotmac_money_flows import funded_charge as flows

NGN = Currency("NGN", 2)
DUE = datetime(2026, 11, 20, tzinfo=UTC)
SCOPE = TenantScope(uuid4())
ACCOUNT = uuid4()
KEY = RatedChargeKeyV1("src", "kind", "fact-1", "1")


def _money(amount: str) -> Money:
    return Money.of(amount, NGN)


class _Policy:
    def __init__(self, cap: int = 20) -> None:
        self.policy = FundedChargePolicyV1("p1", cap)
        self.reads = 0

    def read(self, db, *, scope):
        self.reads += 1
        return self.policy


def _ports(cap: int = 20) -> FundedChargePorts:
    return FundedChargePorts(object(), _Policy(cap), object(), object())


class _Rows:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.queries = 0

    def execute(self, statement):
        self.queries += 1
        return self

    def scalars(self):
        return iter(self.rows)


def _row(index: int):
    return SimpleNamespace(
        id=uuid4(),
        created_at=DUE + timedelta(minutes=index),
        charge_ref=flows.charge_ref_for(KEY),
        service_ref=f"service:{index}",
        billing_account_id=ACCOUNT,
        state="pending",
    )


def _world(monkeypatch, *, pending=True, balance="10.00"):
    calls: list[str] = []

    def has_pending(db, tenant_id, account_id):
        calls.append("has_pending")
        return pending

    def availability(db, *, scope, request):
        calls.append("lock")
        return SimpleNamespace(unrestricted=_money(balance))

    monkeypatch.setattr(flows, "_has_pending", has_pending)
    monkeypatch.setattr(flows, "get_funding_availability", availability)
    return calls


def _run(db, ports, after=None):
    return flows.on_account_credit_changed(
        db,
        scope=SCOPE,
        request=AccountCreditChangedV1(ACCOUNT, after),
        ports=ports,
    )


def test_no_pending_row_returns_before_the_policy_the_lock_or_any_query(monkeypatch):
    calls = _world(monkeypatch, pending=False)
    ports, db = _ports(), _Rows([])

    outcome = _run(db, ports)

    assert (outcome.changed, outcome.reason_code) == ((), "no_pending_charges")
    assert calls == ["has_pending"]  # no lock was taken
    assert ports.policy.reads == 0 and db.queries == 0


def test_a_cap_below_one_is_refused_before_the_lock(monkeypatch):
    calls = _world(monkeypatch)
    with pytest.raises(ValueError):
        _run(_Rows([]), _ports(0))
    assert "lock" not in calls


def test_no_unrestricted_credit_is_a_quiet_no_balance_after_the_lock(monkeypatch):
    calls = _world(monkeypatch, balance="0.00")
    db = _Rows([_row(1)])

    outcome = _run(db, _ports())

    assert (outcome.changed, outcome.reason_code) == ((), "no_balance")
    assert calls == ["has_pending", "lock"] and db.queries == 0


def test_rows_gone_under_the_lock_are_no_pending_charges(monkeypatch):
    _world(monkeypatch)
    assert _run(_Rows([]), _ports()).reason_code == "no_pending_charges"


def test_nothing_fitting_is_a_quiet_outcome_and_enqueues_nothing(monkeypatch):
    _world(monkeypatch)
    events = []
    monkeypatch.setattr(flows, "_reevaluate_funded", lambda *a, **k: None)
    monkeypatch.setattr(flows, "_enqueue_credit_recheck", lambda *a: events.append(a))

    outcome = _run(_Rows([_row(1), _row(2)]), _ports())

    assert (outcome.changed, outcome.reason_code) == ((), "nothing_fits")
    assert events == []


def test_the_cap_bounds_the_batch_and_the_continuation_resumes_after_the_last(
    monkeypatch,
):
    _world(monkeypatch)
    rows = [_row(i) for i in range(1, 6)]
    seen, events = [], []

    def evaluate(ctx, row, funded_at, settlement):
        seen.append((row, settlement))
        row.state = "discharged"
        row.reason_code = None
        row.version = 1
        row.document_id = None
        return row

    monkeypatch.setattr(flows, "_reevaluate_funded", evaluate)
    monkeypatch.setattr(flows, "_enqueue_credit_recheck", lambda *a: events.append(a))

    outcome = _run(_Rows(rows[:3]), _ports(2))  # cap + 1 rows were read

    assert len(outcome.changed) == 2 and outcome.reason_code is None
    assert [row for row, _ in seen] == rows[:2]
    assert all(settlement is None for _, settlement in seen)  # no funding settlement
    ((_, tenant, account, after),) = events
    assert (tenant, account) == (SCOPE.tenant_id, ACCOUNT)
    assert after == FundedChargeCursorV1(rows[1].created_at, rows[1].id)


def test_a_cap_equal_to_the_rows_enqueues_nothing(monkeypatch):
    _world(monkeypatch)
    events = []

    def evaluate(ctx, row, funded_at, settlement):
        row.state, row.reason_code, row.version, row.document_id = (
            "discharged",
            None,
            1,
            None,
        )
        return row

    monkeypatch.setattr(flows, "_reevaluate_funded", evaluate)
    monkeypatch.setattr(flows, "_enqueue_credit_recheck", lambda *a: events.append(a))

    assert len(_run(_Rows([_row(1), _row(2)]), _ports(2)).changed) == 2
    assert events == []


def test_an_owner_refusal_is_a_step_failure_naming_the_row(monkeypatch):
    _world(monkeypatch)
    rows = [_row(1), _row(2)]

    def refuse(*args, **kwargs):
        raise BillingRuleViolation("tax_evidence_invalid", "bad evidence")

    monkeypatch.setattr(flows, "_reevaluate_funded", refuse)
    with pytest.raises(FundedChargeStepFailed) as failed:
        _run(_Rows(rows), _ports())
    assert failed.value.instance_id == rows[0].id
    assert failed.value.code == "billing.tax_evidence_invalid"


def _capture_dating(monkeypatch, newest):
    """Run one row and return the ``funded_at`` callable the command built."""
    _world(monkeypatch)
    captured = {}
    reads = []

    def evaluate(ctx, row, funded_at, settlement):
        captured["funded_at"] = funded_at
        return None

    def newest_credit(db, scope, account_id, currency, start, *a, **k):
        reads.append((account_id, currency, start, k))
        if isinstance(newest, BaseException):
            raise newest
        return newest

    monkeypatch.setattr(flows, "_reevaluate_funded", evaluate)
    monkeypatch.setattr(flows, "newest_credit_at", newest_credit)
    _run(_Rows([_row(1)]), _ports())
    return captured["funded_at"], reads


def _charge(due=DUE):
    return RatedChargeV1(KEY, ACCOUNT, "service:one", _money("10.00"), due)


@pytest.mark.parametrize(
    ("newest", "expected"),
    [
        (None, DUE),  # no credit line since due: the due time
        (DUE + timedelta(days=3), DUE + timedelta(days=3)),  # a later lot
    ],
)
def test_the_dating_is_the_newest_credit_line_since_due_or_the_due_time(
    monkeypatch, newest, expected
):
    funded_at, reads = _capture_dating(monkeypatch, newest)

    assert funded_at(_charge()) == expected
    # Any statement line kind, from the charge's due time, for this account.
    assert reads == [(ACCOUNT, NGN, DUE, {})]


def test_the_dating_read_is_memoised_per_due_time_within_a_call(monkeypatch):
    funded_at, reads = _capture_dating(monkeypatch, DUE + timedelta(days=1))
    for _ in range(3):
        funded_at(_charge())
    funded_at(_charge(DUE + timedelta(days=9)))
    assert [read[2] for read in reads] == [DUE, DUE + timedelta(days=9)]


def test_a_billing_error_on_the_statement_is_a_technical_fault_not_attention(
    monkeypatch,
):
    funded_at, _ = _capture_dating(
        monkeypatch, BillingRuleViolation("statement_integrity", "bad ledger")
    )
    with pytest.raises(BalancePassFailed) as fault:
        funded_at(_charge())
    assert fault.value.code == "credit_dating_failed"
    assert not isinstance(fault.value, FundedChargeStepFailed)


def test_an_unsplittable_statement_window_propagates_as_the_flows_fault(monkeypatch):
    funded_at, _ = _capture_dating(
        monkeypatch, BalancePassFailed("statement_unsplittable")
    )
    with pytest.raises(BalancePassFailed) as fault:
        funded_at(_charge())
    assert fault.value.code == "statement_unsplittable"


def test_the_command_takes_no_clock_and_does_not_write_a_row(monkeypatch):
    # ``db`` only offers ``execute``; any ``add``/``flush`` would raise.
    _world(monkeypatch, pending=False)
    outcome = _run(object(), _ports())
    assert outcome.reason_code == "no_pending_charges"


# --------------------------------------------------------------------------
# _credit_dating: kind filter, newest wins, the split
# --------------------------------------------------------------------------
def _line(kind: str, amount: str, at: datetime):
    delta = SimpleNamespace(available_credit=_money(amount))
    return SimpleNamespace(group_kind=kind, occurred_at=at, delta=delta)


def _statements(monkeypatch, lines, *, cap=None):
    seen = []

    def statement(db, *, scope, request):
        seen.append(request)
        inside = [
            line
            for line in lines
            if request.occurred_from <= line.occurred_at < request.occurred_to
        ]
        if cap is not None and len(inside) > cap:
            raise BillingRuleViolation("statement_too_large", "too large")
        return SimpleNamespace(lines=inside)

    monkeypatch.setattr(dating, "get_account_statement", statement)
    return seen


def _newest(**kwargs):
    return dating.newest_credit_at(None, SCOPE, ACCOUNT, NGN, DUE, **kwargs)


def test_any_line_that_adds_credit_counts_when_no_kind_filter_is_given(monkeypatch):
    _statements(
        monkeypatch,
        [
            _line("opening_account_imported", "60.00", DUE + timedelta(days=2)),
            _line("settlement_accepted", "10.00", DUE + timedelta(days=1)),
            _line("allocation", "-5.00", DUE + timedelta(days=9)),  # not credit
            _line("reversal", "0.00", DUE + timedelta(days=8)),  # adds none
        ],
    )
    assert _newest() == DUE + timedelta(days=2)


def test_a_kind_filter_keeps_only_those_lines(monkeypatch):
    _statements(
        monkeypatch,
        [
            _line("opening_account_imported", "60.00", DUE + timedelta(days=2)),
            _line("settlement_accepted", "10.00", DUE + timedelta(days=1)),
        ],
    )
    assert _newest(kinds=frozenset({"settlement_accepted"})) == DUE + timedelta(days=1)
    assert _newest(kinds=frozenset({"deallocation"})) is None


def test_an_overflowing_window_is_split_newer_half_first(monkeypatch):
    lines = [_line("reversal", "1.00", DUE + timedelta(hours=h)) for h in range(8)]
    seen = _statements(monkeypatch, lines, cap=3)

    assert _newest() == DUE + timedelta(hours=7)
    assert len(seen) > 1 and len(seen) < 200


def test_a_window_that_cannot_be_split_raises_the_flows_fault(monkeypatch):
    lines = [_line("reversal", "1.00", DUE)] * 5
    _statements(monkeypatch, lines, cap=1)
    with pytest.raises(BalancePassFailed) as fault:
        _newest()
    assert fault.value.code == "statement_unsplittable"


def test_another_billing_refusal_on_the_statement_is_not_split(monkeypatch):
    seen = []

    def refuse(db, *, scope, request):
        seen.append(request)
        raise BillingRuleViolation("invalid_window", "bad")

    monkeypatch.setattr(dating, "get_account_statement", refuse)
    with pytest.raises(BillingRuleViolation):
        _newest()
    assert len(seen) == 1
