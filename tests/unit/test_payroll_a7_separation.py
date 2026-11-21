"""a7 (D-9): separation of duties on payroll runs is an optional tenant policy.

The parameter is keyword-only and defaults to ``True``, so a caller that does not
name it keeps a6 behaviour. Under ``False`` one operator can create, approve,
finalize and reverse; no synthetic second actor is needed.
"""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from dotmac_kernel.money import Money
from dotmac_payroll import (
    PayrollRuleViolation,
    approve_payroll_run,
    finalize_payroll_run,
    record_liability_settlement,
    reverse_liability_settlement,
)

from tests.unit.test_payroll_a4 import _calc, _liabilities, _run, _setup
from tests.unit.test_payroll_a5 import db as _db_fixture
from tests.unit.test_payroll_service import NGN, NOW, _tenant

db = _db_fixture  # the shared in-memory payroll database fixture

ONE = uuid4()


def _draft(db):
    tenant = _tenant(db)
    base, tax, loan = _setup(db, tenant.id, loan_mode=None)
    run = _run(db, tenant.id, creator=ONE)
    _calc(db, tenant.id, run, base, tax, loan)
    return tenant, run


def _approve(db, tenant, run, *, by, sod):
    return approve_payroll_run(
        db,
        tenant_id=tenant.id,
        run_id=run.id,
        approved_by_id=by,
        approved_at=NOW,
        separation_of_duties=sod,
    )


def _finalize(db, tenant, run, *, by, sod):
    return finalize_payroll_run(
        db,
        tenant_id=tenant.id,
        run_id=run.id,
        finalized_by_id=by,
        finalized_at=NOW,
        separation_of_duties=sod,
    )


@pytest.mark.parametrize(
    "func", [approve_payroll_run, finalize_payroll_run, reverse_liability_settlement]
)
def test_parameter_is_keyword_only_and_defaults_to_enforced(func) -> None:
    parameter = inspect.signature(func).parameters["separation_of_duties"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is True


def test_on_refuses_creator_approval_and_approver_finalization(db) -> None:
    tenant, run = _draft(db)
    with pytest.raises(PayrollRuleViolation, match="creator cannot approve"):
        _approve(db, tenant, run, by=ONE, sod=True)
    assert run.status == "draft"
    _approve(db, tenant, run, by=uuid4(), sod=True)
    with pytest.raises(PayrollRuleViolation, match="approver cannot finalize"):
        _finalize(db, tenant, run, by=run.approved_by_id, sod=True)
    assert run.status == "approved"


def test_omitting_the_parameter_keeps_the_a6_refusals(db) -> None:
    tenant, run = _draft(db)
    with pytest.raises(PayrollRuleViolation, match="creator cannot approve"):
        approve_payroll_run(
            db,
            tenant_id=tenant.id,
            run_id=run.id,
            approved_by_id=ONE,
            approved_at=NOW,
        )


def test_off_lets_one_operator_create_approve_and_finalize(db) -> None:
    tenant, run = _draft(db)
    _approve(db, tenant, run, by=ONE, sod=False)
    _finalize(db, tenant, run, by=ONE, sod=False)
    assert run.status == "finalized"
    assert run.created_by_id == run.approved_by_id == run.finalized_by_id == ONE


def _settled(db):
    tenant, run = _draft(db)
    _approve(db, tenant, run, by=ONE, sod=False)
    _finalize(db, tenant, run, by=ONE, sod=False)
    net = next(
        x for x in _liabilities(db, run) if x.liability_key == "employee-net-pay"
    )
    settlement = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=net.id,
        amount=Money.of(str(net.amount), NGN),
        settlement_ref="PAY-1",
        evidence_ref="bank:1",
        recorded_by_id=ONE,
        recorded_at=NOW,
    )
    return tenant, settlement


def _reverse(db, tenant, settlement, *, sod):
    return reverse_liability_settlement(
        db,
        tenant_id=tenant.id,
        settlement_id=settlement.id,
        return_ref="RET-1",
        reason="account closed",
        reversed_by_id=ONE,
        reversed_at=NOW,
        separation_of_duties=sod,
    )


def test_on_refuses_the_recorder_reversing_their_own_settlement(db) -> None:
    tenant, settlement = _settled(db)
    with pytest.raises(PayrollRuleViolation, match="different person"):
        _reverse(db, tenant, settlement, sod=True)


def test_off_lets_the_recorder_reverse_their_own_settlement(db) -> None:
    tenant, settlement = _settled(db)
    reversal = _reverse(db, tenant, settlement, sod=False)
    assert reversal.reversed_by_id == settlement.recorded_by_id == ONE


def test_no_column_or_migration_carries_the_policy() -> None:
    from dotmac_payroll import models

    for model in models.TENANT_MODELS:
        assert "separation_enforced" not in model.__table__.c


@pytest.mark.parametrize("bad", [None, 0, "", 1, "false"])
def test_a_non_bool_policy_is_refused_and_changes_nothing(db, bad) -> None:
    tenant, run = _draft(db)
    with pytest.raises(TypeError, match="must be bool"):
        _approve(db, tenant, run, by=ONE, sod=bad)
    assert run.status == "draft" and run.approved_by_id is None
    _approve(db, tenant, run, by=uuid4(), sod=True)
    with pytest.raises(TypeError, match="must be bool"):
        _finalize(db, tenant, run, by=run.approved_by_id, sod=bad)
    assert run.status == "approved" and run.finalized_by_id is None
    _finalize(db, tenant, run, by=uuid4(), sod=True)
    net = next(
        x for x in _liabilities(db, run) if x.liability_key == "employee-net-pay"
    )
    settlement = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=net.id,
        amount=Money.of(str(net.amount), NGN),
        settlement_ref="PAY-1",
        evidence_ref="bank:1",
        recorded_by_id=ONE,
        recorded_at=NOW,
    )
    with pytest.raises(TypeError, match="must be bool"):
        _reverse(db, tenant, settlement, sod=bad)
    assert net.status == "settled"
