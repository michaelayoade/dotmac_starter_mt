"""Hosted PostgreSQL proofs for money-flows GAP-5: the period comes from the terms.

F2 refuses an invoice command whose service period is not the Billing terms
period counted from ``issue_at`` in the policy's billing time zone (day or month
units, clamped to the month end), that is not ``advance``, not due at
``issue_at``, or not in the policy's time zone. A charge carrying an enclosing
period is prorated by actual calendar days when the policy allows (P7a-P7f,
P14-P17); nothing left to bill is withdrawn as ``period_elapsed``.

The timeline sits in the near future (see ``BASE``): account terms cannot be
back-dated against the database clock. Calendar dates are therefore built from
the first non-leap year (``YEAR``) and the first leap year (``LEAP``) after
``BASE``, so they never fall behind the terms.
"""

from __future__ import annotations

import calendar
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from dotmac_billing import ServicePeriodEvidenceV1
from dotmac_billing import models as billing_models
from dotmac_billing.commands import AllocationCommand, DeallocationCommand
from dotmac_billing.service import allocate_settlement, deallocate_settlement
from dotmac_money_flows import (
    AccountCreditChangedV1,
    FundedChargeRefused,
    FundedChargeState,
    SettledFundsV1,
    StartFundedChargeV1,
    get_funded_charge_instance,
    retry_funded_charge,
    start_funded_charge,
)
from sqlalchemy import select

from tests import test_billing_isolation as b
from tests.money_flows_fixtures import (
    BASE,
    LAGOS,
    FakeChargeSource,
    add_months_local,
    assembly_settled,
    assembly_start,
    deposit,
    local_days,
    make_funded_ports,
    scratch_database,
    seed_customer_terms,
    seed_invoice,
    session,
    unrestricted,
)
from tests.test_money_flows_credit_recheck_pg import assembly_credit
from tests.test_money_flows_funded_charge_pg import (
    _access,
    _documents,
    _draws,
    _rows,
    _world,
)
from tests.test_money_flows_terms_recheck_pg import _request, assembly_recheck

MISMATCH = "charge_command_mismatch"
ZONE = ZoneInfo(LAGOS)
YEAR = BASE.year + 1 if not calendar.isleap(BASE.year + 1) else BASE.year + 2
LEAP = next(y for y in range(BASE.year + 1, BASE.year + 9) if calendar.isleap(y))


def at(year: int, month: int, day: int, hour: int = 10, minute: int = 0) -> datetime:
    """A wall-clock time in Lagos, as an instant."""
    return datetime(year, month, day, hour, minute, tzinfo=ZONE).astimezone(UTC)


APRIL = (at(YEAR, 4, 1, 0), at(YEAR, 5, 1, 0))


@pytest.fixture(scope="module")
def scratch():
    with scratch_database() as urls:
        yield urls


def _fixture(scratch, *, accounts: int = 1, funded: str | None = "5000.00", **kw):
    scope, ids = _world(scratch, accounts)
    source = FakeChargeSource()
    ports = make_funded_ports(source, **kw)
    if funded is not None:
        for account in ids:
            deposit(scratch, scope, account, funded)
    return scope, ids, source, ports


def _terms(scratch, scope, account, period, effective_from=None):
    with session(scratch, scope) as db:
        seed_customer_terms(
            db,
            scope,
            account,
            contract_type="prepaid",
            effective_from=effective_from or BASE - timedelta(hours=6),
            period=period,
        )
        db.commit()


def _invoices(scratch, scope):
    """``(document, obligation)`` pairs, oldest period first."""
    with session(scratch, scope) as db:
        documents = db.execute(select(billing_models.BillingDocument)).scalars()
        pairs = [
            (d, db.get(billing_models.RatedObligation, d.obligation_id))
            for d in documents
        ]
    return sorted(pairs, key=lambda pair: pair[1].period_start)


def _periods(scratch, scope):
    return [(o.period_start, o.period_end) for _, o in _invoices(scratch, scope)]


def _with_period(command, *, ends=None):
    period = command.obligation.service_period
    return replace(
        command,
        obligation=replace(
            command.obligation,
            service_period=ServicePeriodEvidenceV1(
                period.status, period.starts_at, ends or period.ends_at
            ),
        ),
    )


def _refused_with_nothing_posted(scratch, scope, account, outcome, before):
    assert outcome.state is FundedChargeState.ATTENTION_REQUIRED
    assert outcome.reason_code == MISMATCH
    assert _documents(scratch, scope) == 0
    assert _draws(scratch, scope) == 0
    assert unrestricted(scratch, scope, account) == before


def _discharged(scratch, scope, account, source, ports, fact, **kw):
    kw.setdefault("total", "300.00")  # a zero-tax charge
    key = source.add(scope, account, fact, **kw)
    outcome = assembly_start(scratch, scope, key, ports)
    assert outcome.state is FundedChargeState.DISCHARGED, outcome
    return key


# --------------------------------------------------------------------------
# P1-P4: the length, the calendar rule and the units
# --------------------------------------------------------------------------
def test_p1_a_monthly_start_covers_one_calendar_month(scratch):
    scope, (account,), source, ports = _fixture(scratch)
    key = _discharged(scratch, scope, account, source, ports, "r1", due_at=BASE)

    assert _periods(scratch, scope) == [(BASE, add_months_local(BASE, 1))]
    assert source.issues == [(key, BASE)]
    ((document, _),) = _invoices(scratch, scope)
    assert document.due_at == BASE and document.grand_total == Decimal("300.00")


def test_p2_a_first_charge_on_15_january_lagos_covers_to_15_february_at_full_price(
    scratch,
):
    scope, (account,), source, ports = _fixture(scratch)
    start = at(YEAR, 1, 15, 10)
    _discharged(scratch, scope, account, source, ports, "r1", due_at=start)

    assert _periods(scratch, scope) == [(start, at(YEAR, 2, 15, 10))]
    ((document, _),) = _invoices(scratch, scope)
    assert document.grand_total == Decimal("300.00")  # the full price
    assert unrestricted(scratch, scope, account) == Decimal("4700.00")


@pytest.mark.parametrize(
    ("timezone", "start", "end"),
    [
        (LAGOS, at(YEAR, 1, 31, 10), at(YEAR, 2, 28, 10)),
        (LAGOS, at(LEAP, 1, 31, 10), at(LEAP, 2, 29, 10)),
        # 31 Jan 23:30Z is 1 Feb 00:30 in Lagos: 1 Mar 00:30 Lagos.
        (
            LAGOS,
            datetime(YEAR, 1, 31, 23, 30, tzinfo=UTC),
            datetime(YEAR, 2, 28, 23, 30, tzinfo=UTC),
        ),
        # 30 Jan 23:30Z is 31 Jan 00:30 in Lagos: clamped to 28 Feb 00:30 Lagos.
        (
            LAGOS,
            datetime(YEAR, 1, 30, 23, 30, tzinfo=UTC),
            datetime(YEAR, 2, 27, 23, 30, tzinfo=UTC),
        ),
        # The same instant counted in a UTC policy: 30 Jan clamps to 28 Feb 23:30Z.
        (
            "UTC",
            datetime(YEAR, 1, 30, 23, 30, tzinfo=UTC),
            datetime(YEAR, 2, 28, 23, 30, tzinfo=UTC),
        ),
    ],
    ids=["31jan", "31jan_leap", "utc_2330_is_1feb_lagos", "lagos_day", "utc_policy"],
)
def test_p3_the_month_end_is_clamped_in_the_policy_time_zone(
    scratch, timezone, start, end
):
    scope, (account,), source, ports = _fixture(scratch, timezone=timezone)
    _discharged(scratch, scope, account, source, ports, "r1", due_at=start)

    assert _periods(scratch, scope) == [(start, end)]


def test_p3_a_chained_period_from_the_clamped_end_runs_28_feb_to_28_mar(scratch):
    scope, (account,), source, ports = _fixture(scratch)
    first = at(YEAR, 1, 31, 10)
    _discharged(scratch, scope, account, source, ports, "r1", due_at=first)
    _discharged(
        scratch,
        scope,
        account,
        source,
        ports,
        "r2",
        due_at=at(YEAR, 2, 28, 10),
        service_ref="service:two",
    )

    assert _periods(scratch, scope) == [
        (first, at(YEAR, 2, 28, 10)),
        (at(YEAR, 2, 28, 10), at(YEAR, 3, 28, 10)),
    ]


def test_p4_daily_quarterly_and_annual_lengths(scratch):
    scope, accounts, source, ports = _fixture(scratch, accounts=4)
    cases = [
        ("daily", at(YEAR, 3, 10, 8), at(YEAR, 3, 11, 8)),
        ("quarterly", at(YEAR, 1, 31, 10), at(YEAR, 4, 30, 10)),
        ("annually", at(YEAR, 2, 28, 10), at(YEAR + 1, 2, 28, 10)),
        ("annually", at(LEAP, 2, 29, 10), at(LEAP + 1, 2, 28, 10)),
    ]
    for account, (period, start, _) in zip(accounts, cases, strict=True):
        _terms(scratch, scope, account, period)
        _discharged(
            scratch,
            scope,
            account,
            source,
            ports,
            f"r-{period}-{start:%Y%m%d}",
            due_at=start,
        )

    assert _periods(scratch, scope) == sorted((start, end) for _, start, end in cases)


# --------------------------------------------------------------------------
# P5, P5b: precedence of the account value and the default
# --------------------------------------------------------------------------
def test_p5_the_default_is_used_when_the_account_period_is_null_and_the_account_wins(
    scratch,
):
    scope, (none_, null, quarterly), source, ports = _fixture(scratch, accounts=3)
    _terms(scratch, scope, null, None)  # a version that overrides nothing
    _terms(scratch, scope, quarterly, "quarterly")
    for index, account in enumerate((none_, null, quarterly)):
        _discharged(
            scratch,
            scope,
            account,
            source,
            ports,
            f"r{index}",
            due_at=BASE,
            service_ref=f"service:{index}",
        )

    ends = sorted(end for _, end in _periods(scratch, scope))
    assert ends == [
        add_months_local(BASE, 1),
        add_months_local(BASE, 1),
        add_months_local(BASE, 3),
    ]


def test_p5b_a_type_only_re_record_with_period_none_falls_back_to_the_default(
    scratch,
):
    scope, (account,), source, ports = _fixture(scratch)
    _terms(scratch, scope, account, "annually")
    restated = at(YEAR, 3, 1, 0)
    _terms(scratch, scope, account, None, effective_from=restated)  # the trap
    before = at(YEAR, 2, 10, 10)
    after = restated + timedelta(days=10)
    _discharged(
        scratch, scope, account, source, ports, "old", due_at=before, service_ref="a"
    )
    _discharged(
        scratch, scope, account, source, ports, "new", due_at=after, service_ref="b"
    )
    # A source still building the account's old annual period is now wrong.
    stale = source.add(scope, account, "stale", due_at=after, service_ref="c")
    source.mutators[stale] = lambda command: _with_period(
        command, ends=add_months_local(after, 12)
    )
    funds = unrestricted(scratch, scope, account)
    outcome = assembly_start(scratch, scope, stale, ports)

    assert _periods(scratch, scope) == [
        (before, add_months_local(before, 12)),  # terms at its own start: annual
        (after, add_months_local(after, 1)),  # the default after the restatement
    ]
    assert outcome.state is FundedChargeState.ATTENTION_REQUIRED
    assert outcome.reason_code == MISMATCH
    assert unrestricted(scratch, scope, account) == funds


# --------------------------------------------------------------------------
# P6, P8, P13: what F2 refuses
# --------------------------------------------------------------------------
def test_p6_a_source_with_the_wrong_length_is_a_mismatch_with_nothing_posted(scratch):
    scope, (account,), source, ports = _fixture(scratch)
    start = at(YEAR, 1, 31, 10)
    key = source.add(scope, account, "r1", due_at=start)
    source.mutators[key] = lambda command: _with_period(
        command, ends=start + timedelta(days=30)
    )
    before = unrestricted(scratch, scope, account)

    outcome = assembly_start(scratch, scope, key, ports)

    _refused_with_nothing_posted(scratch, scope, account, outcome, before)


def _arrears(command):
    return replace(
        command, obligation=replace(command.obligation, collection_timing="arrears")
    )


def _due_later(command):
    return replace(command, due_at=command.due_at + timedelta(days=1))


@pytest.mark.parametrize("mutator", [_arrears, _due_later], ids=["arrears", "due_at"])
def test_p8_an_arrears_command_or_a_due_date_after_issue_is_a_mismatch(
    scratch, mutator
):
    scope, (account,), source, ports = _fixture(scratch)
    key = source.add(scope, account, "r1", due_at=BASE)
    source.mutators[key] = mutator
    before = unrestricted(scratch, scope, account)

    outcome = assembly_start(scratch, scope, key, ports)

    _refused_with_nothing_posted(scratch, scope, account, outcome, before)


def test_p13_an_unknown_policy_zone_is_a_mismatch(scratch):
    scope, (account,), source, ports = _fixture(scratch, timezone="Mars/Olympus")
    key = source.add(scope, account, "r1", due_at=BASE)
    before = unrestricted(scratch, scope, account)

    outcome = assembly_start(scratch, scope, key, ports)

    _refused_with_nothing_posted(scratch, scope, account, outcome, before)


def test_p13_a_command_zone_that_is_not_the_policys_is_a_mismatch(scratch):
    scope, (account,), source, ports = _fixture(scratch, timezone="UTC")
    source.timezone = LAGOS  # the policy says UTC
    key = source.add(scope, account, "r1", due_at=BASE)
    before = unrestricted(scratch, scope, account)

    outcome = assembly_start(scratch, scope, key, ports)

    _refused_with_nothing_posted(scratch, scope, account, outcome, before)


# --------------------------------------------------------------------------
# P7a-P7f: a prorated charge co-terminates with the enclosing period
# --------------------------------------------------------------------------
def _prorated(source, scope, account, fact="up", **kw):
    kw.setdefault("due_at", at(YEAR, 4, 15, 10))
    kw.setdefault("period_starts_at", APRIL[0])
    kw.setdefault("period_ends_at", APRIL[1])
    return source.add(scope, account, fact, total="300.00", **kw)


def test_p7a_a_prorated_upgrade_pays_16_of_30_days_and_ends_with_the_period(scratch):
    scope, (account,), source, ports = _fixture(scratch)
    key = _prorated(source, scope, account)

    outcome = assembly_start(scratch, scope, key, ports)

    assert outcome.state is FundedChargeState.DISCHARGED
    ((document, obligation),) = _invoices(scratch, scope)
    assert document.grand_total == Decimal("160.00")  # 300.00 x 16 / 30
    assert (obligation.period_start, obligation.period_end) == (
        at(YEAR, 4, 15, 10),
        APRIL[1],
    )
    assert unrestricted(scratch, scope, account) == Decimal("5000.00") - 160
    assert _draws(scratch, scope) == 1


def test_p7b_the_same_charge_with_proration_off_is_a_mismatch(scratch):
    scope, (account,), source, ports = _fixture(scratch, proration="none")
    key = _prorated(source, scope, account)
    before = unrestricted(scratch, scope, account)

    outcome = assembly_start(scratch, scope, key, ports)

    _refused_with_nothing_posted(scratch, scope, account, outcome, before)


def test_p7c_a_wrong_amount_is_a_mismatch(scratch):
    scope, (account,), source, ports = _fixture(scratch)
    key = _prorated(source, scope, account)

    def wrong(command):
        amount = b._money("170.00")
        return replace(
            command,
            obligation=replace(
                command.obligation,
                pre_tax_amount=amount,
                tax_amount=b._money("0"),
                total_amount=amount,
            ),
        )

    source.mutators[key] = wrong
    before = unrestricted(scratch, scope, account)

    outcome = assembly_start(scratch, scope, key, ports)

    _refused_with_nothing_posted(scratch, scope, account, outcome, before)


def test_p7d_an_enclosing_period_that_disagrees_with_the_terms_is_a_mismatch(scratch):
    scope, (account,), source, ports = _fixture(scratch)
    # Monthly terms: a period from 1 April ends 1 May, not 1 June.
    key = _prorated(source, scope, account, period_ends_at=at(YEAR, 6, 1, 0))
    before = unrestricted(scratch, scope, account)

    outcome = assembly_start(scratch, scope, key, ports)

    _refused_with_nothing_posted(scratch, scope, account, outcome, before)


@pytest.mark.parametrize("one_sided", ["starts", "ends"])
def test_p7d_an_enclosing_period_with_one_end_only_is_a_mismatch(scratch, one_sided):
    scope, (account,), source, ports = _fixture(scratch)
    key = _prorated(source, scope, account)
    source.charges[key] = replace(
        source.charges[key],
        **{f"period_{'ends' if one_sided == 'starts' else 'starts'}_at": None},
    )
    before = unrestricted(scratch, scope, account)

    outcome = assembly_start(scratch, scope, key, ports)

    _refused_with_nothing_posted(scratch, scope, account, outcome, before)


def test_p7e_a_pending_prorated_charge_paid_on_20_april_pays_11_of_30_days(scratch):
    scope, (account,), source, ports = _fixture(scratch, funded=None)
    key = _prorated(source, scope, account)
    deposit(scratch, scope, account, "100.00")  # less than the 160.00 due at the order
    assert assembly_start(scratch, scope, key, ports).state is FundedChargeState.PENDING

    paid = at(YEAR, 4, 20, 10)
    cash = deposit(scratch, scope, account, "300.00", observed_at=paid)
    assembly_settled(scratch, scope, SettledFundsV1(account, cash.settlement_id), ports)

    ((document, obligation),) = _invoices(scratch, scope)
    assert document.grand_total == Decimal("110.00")  # 300.00 x 11 / 30
    assert document.due_at == paid
    assert (obligation.period_start, obligation.period_end) == (paid, APRIL[1])
    assert source.issues == [(key, paid)]
    assert unrestricted(scratch, scope, account) == Decimal("400.00") - 110
    (row,) = _rows(scratch, scope)
    assert row.state == "discharged"


@pytest.mark.parametrize(
    ("period", "paid"),
    [
        (APRIL, APRIL[1]),
        (APRIL, APRIL[1] + timedelta(days=3)),
        # Time is left (09:00) but no calendar day: the end's own local date.
        ((at(YEAR, 4, 1, 12), at(YEAR, 5, 1, 12)), at(YEAR, 5, 1, 9)),
    ],
    ids=["at_end", "after_end", "no_whole_day_left"],
)
def test_p7f_paid_when_no_days_are_left_the_charge_is_withdrawn_period_elapsed(
    scratch, period, paid
):
    scope, (account,), source, ports = _fixture(scratch, funded=None)
    key = _prorated(
        source, scope, account, period_starts_at=period[0], period_ends_at=period[1]
    )
    deposit(scratch, scope, account, "100.00")
    assert assembly_start(scratch, scope, key, ports).state is FundedChargeState.PENDING

    cash = deposit(scratch, scope, account, "300.00", observed_at=paid)
    assembly_settled(scratch, scope, SettledFundsV1(account, cash.settlement_id), ports)

    (row,) = _rows(scratch, scope)
    assert (row.state, row.reason_code) == ("withdrawn", "period_elapsed")
    assert _documents(scratch, scope) == 0
    assert _draws(scratch, scope) == 0
    assert unrestricted(scratch, scope, account) == Decimal("400.00")
    assert source.issues == []
    assert [event["active"] for event in _access(scratch, scope)] == [True, False]


def test_p7f_starting_a_charge_whose_period_is_over_is_refused_and_writes_nothing(
    scratch,
):
    scope, (account,), source, ports = _fixture(scratch)
    key = _prorated(source, scope, account, due_at=APRIL[1])

    with session(scratch, scope) as db:
        with pytest.raises(FundedChargeRefused) as refused:
            start_funded_charge(
                db, scope=scope, command=StartFundedChargeV1(key), ports=ports
            )
        db.rollback()

    assert refused.value.code == "period_elapsed"
    assert _rows(scratch, scope) == []
    assert _documents(scratch, scope) == 0


# --------------------------------------------------------------------------
# P9-P12: a terms change, the instants and replay
# --------------------------------------------------------------------------
def test_p9_a_period_change_while_pending_supersedes_withdraws_and_pays_the_new_length(
    scratch,
):
    scope, (account,), source, ports = _fixture(scratch, funded=None)
    old = source.add(scope, account, "old", total="300.00", due_at=at(YEAR, 3, 1, 10))
    assert assembly_start(scratch, scope, old, ports).state is FundedChargeState.PENDING
    switch = at(YEAR, 3, 5, 10)
    _terms(scratch, scope, account, "quarterly", effective_from=switch)

    # Sub-thin, in one transaction: supersede, then re-check; D3 withdraws.
    source.withdraw(old)
    assembly_recheck(scratch, scope, _request(account, switch), ports)
    new = source.add(scope, account, "new", total="900.00", due_at=switch)
    assert assembly_start(scratch, scope, new, ports).state is FundedChargeState.PENDING
    cash = deposit(scratch, scope, account, "1000.00", observed_at=switch)
    assembly_settled(scratch, scope, SettledFundsV1(account, cash.settlement_id), ports)

    states = [(row.state, row.reason_code) for row in _rows(scratch, scope)]
    assert states == [("withdrawn", "charge_withdrawn"), ("discharged", None)]
    assert _periods(scratch, scope) == [(switch, add_months_local(switch, 3))]
    assert unrestricted(scratch, scope, account) == Decimal("100.00")


def test_p10_a_paid_up_monthly_account_switches_to_quarterly_at_the_period_end(
    scratch,
):
    scope, (account,), source, ports = _fixture(scratch)
    first = at(YEAR, 3, 10, 10)
    _discharged(scratch, scope, account, source, ports, "r1", due_at=first)
    boundary = add_months_local(first, 1)
    _terms(scratch, scope, account, "quarterly", effective_from=boundary)

    _discharged(scratch, scope, account, source, ports, "r2", due_at=boundary)

    assert _periods(scratch, scope) == [
        (first, boundary),
        (boundary, add_months_local(boundary, 3)),
    ]


def _quarterly_from_mid_march(scratch, scope, account):
    started = at(YEAR, 3, 1, 10)
    _terms(scratch, scope, account, "quarterly", effective_from=at(YEAR, 3, 10, 0))
    return started, at(YEAR, 3, 12, 10)


def test_p11_the_settlement_path_reads_the_terms_at_the_cash_instant(scratch):
    scope, (account,), source, ports = _fixture(scratch, funded=None)
    due, paid = _quarterly_from_mid_march(scratch, scope, account)
    key = source.add(
        scope, account, "r1", total="300.00", due_at=due
    )  # monthly at its due time
    assert assembly_start(scratch, scope, key, ports).state is FundedChargeState.PENDING

    cash = deposit(scratch, scope, account, "500.00", observed_at=paid)
    assembly_settled(scratch, scope, SettledFundsV1(account, cash.settlement_id), ports)

    assert _periods(scratch, scope) == [(paid, add_months_local(paid, 3))]
    assert source.issues == [(key, paid)]


def test_p11_the_retry_path_reads_the_terms_at_the_cash_instant(scratch):
    scope, (account,), source, ports = _fixture(scratch, funded=None)
    due, paid = _quarterly_from_mid_march(scratch, scope, account)
    key = source.add(scope, account, "r1", total="300.00", due_at=due)
    assert assembly_start(scratch, scope, key, ports).state is FundedChargeState.PENDING
    source.mutators[key] = lambda command: _with_period(
        command, ends=add_months_local(paid, 1)
    )
    cash = deposit(scratch, scope, account, "500.00", observed_at=paid)
    assembly_settled(scratch, scope, SettledFundsV1(account, cash.settlement_id), ports)
    (row,) = _rows(scratch, scope)
    assert (row.state, row.reason_code) == ("attention_required", MISMATCH)
    assert _documents(scratch, scope) == 0

    del source.mutators[key]
    with session(scratch, scope) as db:
        outcome = retry_funded_charge(
            db,
            scope=scope,
            instance_id=row.id,
            expected_version=row.version,
            actor_ref="ops:example",
            ports=ports,
        )
        db.commit()

    assert outcome.state is FundedChargeState.DISCHARGED
    assert _periods(scratch, scope) == [(paid, add_months_local(paid, 3))]


def test_p12_replay_and_redelivery_give_one_obligation_and_the_same_period(scratch):
    scope, (account,), source, ports = _fixture(scratch)
    start = at(YEAR, 1, 31, 10)
    key = _discharged(scratch, scope, account, source, ports, "r1", due_at=start)
    replay = assembly_start(scratch, scope, key, ports)

    assert replay.replayed is True
    assert _periods(scratch, scope) == [(start, at(YEAR, 2, 28, 10))]
    assert (_documents(scratch, scope), _draws(scratch, scope)) == (1, 1)


def test_p12_a_redelivered_settlement_event_changes_nothing_twice(scratch):
    scope, (account,), source, ports = _fixture(scratch, funded=None)
    key = source.add(scope, account, "r1", total="300.00", due_at=BASE)
    assert assembly_start(scratch, scope, key, ports).state is FundedChargeState.PENDING
    cash = deposit(scratch, scope, account, "500.00", observed_at=BASE)
    event = SettledFundsV1(account, cash.settlement_id)
    assembly_settled(scratch, scope, event, ports)
    first = _periods(scratch, scope)
    assembly_settled(scratch, scope, event, ports)

    assert _periods(scratch, scope) == first == [(BASE, add_months_local(BASE, 1))]
    assert (_documents(scratch, scope), _draws(scratch, scope)) == (1, 1)


# --------------------------------------------------------------------------
# P14-P17: actual period days, replay and the GAP-L1 credit re-check
# --------------------------------------------------------------------------
def test_p14_a_31_day_and_a_28_day_period_give_different_daily_values(scratch):
    scope, (account,), source, ports = _fixture(scratch)
    feb = (at(YEAR, 2, 1, 0), at(YEAR, 3, 1, 0))
    mar = (at(YEAR, 3, 1, 0), at(YEAR, 4, 1, 0))
    for fact, (start, end), paid in (
        ("feb", feb, at(YEAR, 2, 28, 10)),
        ("mar", mar, at(YEAR, 3, 31, 10)),
    ):
        _discharged(
            scratch,
            scope,
            account,
            source,
            ports,
            fact,
            total="300.00",
            due_at=paid,
            period_starts_at=start,
            period_ends_at=end,
            service_ref=f"service:{fact}",
        )

    totals = [document.grand_total for document, _ in _invoices(scratch, scope)]
    assert totals == [Decimal("10.71"), Decimal("9.68")]  # 300/28 and 300/31, never 10


def test_p15_a_quarterly_enclosing_period_uses_its_real_day_count(scratch):
    scope, (account,), source, ports = _fixture(scratch)
    _terms(scratch, scope, account, "quarterly")
    cases = [  # (start, end, the day before the end, expected, days)
        (at(YEAR, 1, 1, 0), at(YEAR, 4, 1, 0), at(YEAR, 3, 31, 10), "10.22", 90),
        (at(YEAR, 7, 1, 0), at(YEAR, 10, 1, 0), at(YEAR, 9, 30, 10), "10.00", 92),
        (at(LEAP, 1, 1, 0), at(LEAP, 4, 1, 0), at(LEAP, 3, 31, 10), "10.11", 91),
    ]
    for index, (start, end, paid, _, days) in enumerate(cases):
        assert local_days(start, end) == days
        _discharged(
            scratch,
            scope,
            account,
            source,
            ports,
            f"q{index}",
            total="920.00",
            due_at=paid,
            period_starts_at=start,
            period_ends_at=end,
            service_ref=f"service:q{index}",
        )

    by_start = sorted(_invoices(scratch, scope), key=lambda pair: pair[1].period_start)
    assert [d.grand_total for d, _ in by_start] == [
        Decimal(c[3]) for c in sorted(cases, key=lambda c: c[2])
    ]


def test_p16_replay_of_a_prorated_discharge_is_one_obligation_at_the_same_amount(
    scratch,
):
    scope, (account,), source, ports = _fixture(scratch)
    key = _prorated(source, scope, account)
    first = assembly_start(scratch, scope, key, ports)
    again = assembly_start(scratch, scope, key, ports)

    assert first.state is FundedChargeState.DISCHARGED and again.replayed is True
    ((document, obligation),) = _invoices(scratch, scope)
    assert document.grand_total == Decimal("160.00")
    assert obligation.period_end == APRIL[1]
    assert (_documents(scratch, scope), _draws(scratch, scope)) == (1, 1)
    with session(scratch, scope) as db:
        view = get_funded_charge_instance(db, scope=scope, key=key)
    assert view is not None and view.document_id == document.id


def test_p17_the_credit_recheck_pays_a_prorated_charge_at_the_credit_instant(scratch):
    scope, (account,), source, ports = _fixture(scratch, funded=None)
    cash_in = deposit(scratch, scope, account, "200.00")
    with session(scratch, scope) as db:
        invoice = seed_invoice(db, scope, SimpleNamespace(id=account))
        group = allocate_settlement(
            db,
            scope=scope,
            command=AllocationCommand(
                cash_in.settlement_id, invoice.id, b._money("120.00"), BASE, "p17-a"
            ),
        )
        allocation_id = db.execute(
            select(billing_models.AllocationEffect.id).where(
                billing_models.AllocationEffect.posting_group_id == group.id
            )
        ).scalar_one()
        db.commit()
    key = _prorated(source, scope, account)  # 160.00 due at the order, 80.00 spendable
    assert assembly_start(scratch, scope, key, ports).state is FundedChargeState.PENDING
    freed = at(YEAR, 4, 20, 10)
    with session(scratch, scope) as db:
        deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(
                allocation_id, b._money("120.00"), freed, "p17-d"
            ),
        )
        db.commit()

    outcome = assembly_credit(scratch, scope, AccountCreditChangedV1(account), ports)

    assert outcome is not None and len(outcome.changed) == 1
    by_key = [(d, o) for d, o in _invoices(scratch, scope) if o.period_start == freed]
    ((document, obligation),) = by_key
    assert document.grand_total == Decimal("110.00")  # 11 days left, not 16
    assert (obligation.period_start, obligation.period_end) == (freed, APRIL[1])
    assert source.issues == [(key, freed)]
