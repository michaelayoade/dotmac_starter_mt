"""Optional separation of duties on supplier payments (payables a7, D-9).

Separation of duties is a per-tenant policy, never compulsory. The policy in force
is stamped on each payment at preparation (``separation_enforced``) and the later
steps enforce ``stamp or parameter`` (the stricter value wins).
"""

from __future__ import annotations

import inspect
from datetime import timedelta

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_payables import models, service
from dotmac_payables.contracts import (
    Conflict,
    InvalidTransition,
    SupplierPaymentStatus,
)
from dotmac_payables.service import (
    approve_supplier_payment,
    create_supplier_payment,
    record_supplier_payment,
    reverse_supplier_payment,
)
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.unit.test_payables_supplier_payments import (
    NOW,
    TENANT,
    _command,
    _obligation,
)
from tests.unit.test_payables_supplier_payments import db as _db_fixture

db = _db_fixture  # the shared in-memory payables database fixture

ONE = "user:operator"


def _prepare(
    db: Session, *, sod: bool, key: str = "a7:create", number: str = "SPAY-0001"
):
    ob = _obligation(db)
    payment = create_supplier_payment(
        db,
        scope=TenantScope(TENANT),
        command=_command([(ob, "1000")], prepared_by=ONE, number=number),
        idempotency_key=key,
        idempotency_expires_at=None,
        prepared_at=NOW,
        separation_of_duties=sod,
    )
    return payment, ob


def _approve(db: Session, payment, *, by: str, sod: bool, key: str = "a7:approve"):
    return approve_supplier_payment(
        db,
        scope=TenantScope(TENANT),
        payment_id=payment.id,
        approval_reference="approval:a7",
        approved_by=by,
        idempotency_key=key,
        idempotency_expires_at=None,
        approved_at=NOW + timedelta(hours=1),
        separation_of_duties=sod,
    )


def _record(db: Session, payment, *, by: str, sod: bool, key: str = "a7:record"):
    return record_supplier_payment(
        db,
        scope=TenantScope(TENANT),
        payment_id=payment.id,
        bank_reference="BANK-A7",
        recorded_by=by,
        idempotency_key=key,
        idempotency_expires_at=None,
        recorded_at=NOW + timedelta(hours=2),
        separation_of_duties=sod,
    )


def _reverse(db: Session, payment, *, by: str, sod: bool, key: str = "a7:reverse"):
    return reverse_supplier_payment(
        db,
        scope=TenantScope(TENANT),
        payment_id=payment.id,
        reason="bounced",
        reversal_date=payment.value_date,
        reversed_by=by,
        idempotency_key=key,
        idempotency_expires_at=None,
        reversed_at=NOW + timedelta(hours=3),
        separation_of_duties=sod,
    )


def _ledger_receipt(db: Session, payment) -> None:
    db.add(
        models.AccountingReceipt(
            tenant_id=TENANT,
            document_kind="supplier_payment",
            document_id=payment.id,
            consequence_fingerprint="a" * 64,
            accounting_reference="journal:a7",
            accounting_evidence_fingerprint="b" * 64,
            recorded_at=NOW,
        )
    )
    db.flush()


# --- the parameter ------------------------------------------------------------


@pytest.mark.parametrize(
    "func",
    [
        service.create_supplier_payment,
        service.approve_supplier_payment,
        service.record_supplier_payment,
        service.reverse_supplier_payment,
    ],
)
def test_parameter_is_keyword_only_and_defaults_to_off(func) -> None:
    parameter = inspect.signature(func).parameters["separation_of_duties"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is False


# --- policy ON: the same person is refused ------------------------------------


def test_on_stamps_true_and_refuses_the_same_person_at_every_step(db: Session) -> None:
    payment, _ = _prepare(db, sod=True)
    assert payment.separation_enforced is True
    with pytest.raises(InvalidTransition, match="preparer"):
        _approve(db, payment, by=ONE, sod=True)
    _approve(db, payment, by="user:approver", sod=True)
    with pytest.raises(InvalidTransition, match="preparer and approver"):
        _record(db, payment, by=ONE, sod=True)
    with pytest.raises(InvalidTransition, match="preparer and approver"):
        _record(db, payment, by="user:approver", sod=True, key="a7:record2")
    assert payment.status == SupplierPaymentStatus.APPROVED


def test_on_refuses_the_same_person_on_reversal(db: Session) -> None:
    payment, _ = _prepare(db, sod=True)
    _approve(db, payment, by="user:approver", sod=True)
    _record(db, payment, by="user:recorder", sod=True)
    _ledger_receipt(db, payment)
    with pytest.raises(InvalidTransition, match="recorder and approver"):
        _reverse(db, payment, by="user:recorder", sod=True)
    assert payment.status == SupplierPaymentStatus.RECORDED


# --- policy OFF: the same person passes ---------------------------------------


def test_off_stamps_false_and_one_person_runs_the_whole_lifecycle(db: Session) -> None:
    payment, ob = _prepare(db, sod=False)
    assert payment.separation_enforced is False
    _approve(db, payment, by=ONE, sod=False)
    _record(db, payment, by=ONE, sod=False)
    assert payment.status == SupplierPaymentStatus.RECORDED
    assert payment.prepared_by == payment.approved_by == payment.recorded_by == ONE
    _ledger_receipt(db, payment)
    _reverse(db, payment, by=ONE, sod=False)
    assert payment.status == SupplierPaymentStatus.REVERSED
    assert payment.reversed_by == ONE


# --- the stricter value wins --------------------------------------------------


def test_a_strict_stamp_beats_a_relaxed_parameter(db: Session) -> None:
    payment, _ = _prepare(db, sod=True)
    with pytest.raises(InvalidTransition, match="preparer"):
        _approve(db, payment, by=ONE, sod=False)
    _approve(db, payment, by="user:approver", sod=False)
    with pytest.raises(InvalidTransition, match="preparer and approver"):
        _record(db, payment, by=ONE, sod=False)


def test_a_strict_parameter_beats_a_relaxed_stamp(db: Session) -> None:
    payment, _ = _prepare(db, sod=False)
    with pytest.raises(InvalidTransition, match="preparer"):
        _approve(db, payment, by=ONE, sod=True)
    _approve(db, payment, by=ONE, sod=False)
    with pytest.raises(InvalidTransition, match="preparer and approver"):
        _record(db, payment, by=ONE, sod=True)


# --- the flag is part of the idempotency identity -----------------------------


def test_the_flag_is_in_the_idempotency_fingerprint(db: Session) -> None:
    payment, _ = _prepare(db, sod=False)
    _approve(db, payment, by=ONE, sod=False)
    # Same key, same command, different policy: a conflict, not a silent replay.
    with pytest.raises(IdempotencyConflict):
        _approve(db, payment, by=ONE, sod=True)
    # Same key and policy replays.
    again = _approve(db, payment, by=ONE, sod=False)
    assert again.id == payment.id


def test_create_replay_under_a_different_policy_is_a_conflict(db: Session) -> None:
    payment, ob = _prepare(db, sod=False)
    with pytest.raises(IdempotencyConflict):
        create_supplier_payment(
            db,
            scope=TenantScope(TENANT),
            command=_command([(ob, "1000")], prepared_by=ONE),
            idempotency_key="a7:create",
            idempotency_expires_at=None,
            prepared_at=NOW,
            separation_of_duties=True,
        )
    with pytest.raises(Conflict, match="separation-of-duties"):
        create_supplier_payment(
            db,
            scope=TenantScope(TENANT),
            command=_command([(ob, "1000")], prepared_by=ONE),
            idempotency_key="a7:create:other-key",
            idempotency_expires_at=None,
            prepared_at=NOW,
            separation_of_duties=True,
        )


# --- the database: relaxed per row, only when the stamp is false --------------


def _force(db: Session, payment, **values) -> None:
    db.execute(
        update(models.SupplierPayment)
        .where(models.SupplierPayment.id == payment.id)
        .values(**values)
    )


def test_database_still_refuses_a_forged_same_actor_row_when_stamped_on(
    db: Session,
) -> None:
    payment, _ = _prepare(db, sod=True)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            _force(
                db,
                payment,
                status=SupplierPaymentStatus.APPROVED,
                approved_by=ONE,
                approved_at=NOW,
                approval_reference="x",
            )


def test_database_accepts_the_same_actor_row_only_when_stamped_off(
    db: Session,
) -> None:
    payment, _ = _prepare(db, sod=False)
    with db.begin_nested():
        _force(
            db,
            payment,
            status=SupplierPaymentStatus.APPROVED,
            approved_by=ONE,
            approved_at=NOW,
            approval_reference="x",
        )


def test_sod_check_text_is_conditional_on_the_stamp() -> None:
    check = models.SUPPLIER_PAYMENT_SOD_CHECK
    assert check.startswith("NOT separation_enforced OR (")
    assert check.endswith(")")


def test_the_stamp_is_not_null_and_defaults_to_enforced() -> None:
    column = models.SupplierPayment.__table__.c.separation_enforced
    assert column.nullable is False
    assert "true" in str(column.server_default.arg).lower()


# --- review fixes: a non-bool value must not relax, fingerprint bytes ----------


@pytest.mark.parametrize("bad", [None, 0, "", 1, "false"])
def test_a_non_bool_policy_is_refused_and_nothing_is_written(db: Session, bad) -> None:
    from sqlalchemy import func, select

    ob = _obligation(db)
    before = db.scalar(select(func.count()).select_from(models.SupplierPayment))
    with pytest.raises(TypeError, match="must be bool"):
        create_supplier_payment(
            db,
            scope=TenantScope(TENANT),
            command=_command([(ob, "1000")], prepared_by=ONE),
            idempotency_key="a7:bad",
            idempotency_expires_at=None,
            prepared_at=NOW,
            separation_of_duties=bad,
        )
    assert db.scalar(select(func.count()).select_from(models.SupplierPayment)) == before
    payment, _ = _prepare(db, sod=True)
    with pytest.raises(TypeError, match="must be bool"):
        _approve(db, payment, by=ONE, sod=bad)
    assert payment.status == SupplierPaymentStatus.DRAFT
    _approve(db, payment, by="user:approver", sod=True)
    with pytest.raises(TypeError, match="must be bool"):
        _record(db, payment, by=ONE, sod=bad)
    _record(db, payment, by="user:recorder", sod=True)
    _ledger_receipt(db, payment)
    with pytest.raises(TypeError, match="must be bool"):
        _reverse(db, payment, by=ONE, sod=bad)
    assert payment.status == SupplierPaymentStatus.RECORDED
    assert (
        db.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.key.like("a7:bad%"))
        )
        == 0
    )


def test_the_flag_joins_the_fingerprint_only_when_relaxed(db: Session) -> None:
    from dotmac_kernel.idempotency import fingerprint_of
    from sqlalchemy import select

    payment, _ = _prepare(db, sod=True, key="a7:fp:strict")
    _approve(db, payment, by="user:approver", sod=True, key="a7:fp:approve")
    record = db.scalar(
        select(IdempotencyRecord).where(IdempotencyRecord.key == "a7:fp:approve")
    )
    a6_payload = {
        "payment_id": str(payment.id),
        "approval_reference": "approval:a7",
        "approved_by": "user:approver",
        "approved_at": (NOW + timedelta(hours=1)).isoformat(),
    }
    assert record.fingerprint == fingerprint_of(a6_payload)
    relaxed, _ = _prepare(db, sod=False, key="a7:fp:relaxed", number="SPAY-0002")
    _approve(db, relaxed, by=ONE, sod=False, key="a7:fp:approve2")
    record = db.scalar(
        select(IdempotencyRecord).where(IdempotencyRecord.key == "a7:fp:approve2")
    )
    assert record.fingerprint != fingerprint_of(
        {**a6_payload, "payment_id": str(relaxed.id), "approved_by": ONE}
    )


# --- default is OFF (Michael's no-compulsory-two-person ruling) ----------------


def test_omitting_the_parameter_lets_one_person_complete_the_flow(db: Session) -> None:
    ob = _obligation(db)
    scope = TenantScope(TENANT)
    payment = create_supplier_payment(
        db,
        scope=scope,
        command=_command([(ob, "1000")], prepared_by=ONE),
        idempotency_key="a7:def:create",
        idempotency_expires_at=None,
        prepared_at=NOW,
    )
    assert payment.separation_enforced is False
    approve_supplier_payment(
        db,
        scope=scope,
        payment_id=payment.id,
        approval_reference="approval:a7",
        approved_by=ONE,
        idempotency_key="a7:def:approve",
        idempotency_expires_at=None,
        approved_at=NOW + timedelta(hours=1),
    )
    record_supplier_payment(
        db,
        scope=scope,
        payment_id=payment.id,
        bank_reference="BANK-DEF",
        recorded_by=ONE,
        idempotency_key="a7:def:record",
        idempotency_expires_at=None,
        recorded_at=NOW + timedelta(hours=2),
    )
    assert payment.status == SupplierPaymentStatus.RECORDED
    _ledger_receipt(db, payment)
    reverse_supplier_payment(
        db,
        scope=scope,
        payment_id=payment.id,
        reason="bounced",
        reversal_date=payment.value_date,
        reversed_by=ONE,
        idempotency_key="a7:def:reverse",
        idempotency_expires_at=None,
        reversed_at=NOW + timedelta(hours=3),
    )
    assert payment.status == SupplierPaymentStatus.REVERSED


def test_explicit_true_enforces_even_though_the_default_is_off(db: Session) -> None:
    payment, _ = _prepare(db, sod=True, key="a7:def:true", number="SPAY-0009")
    assert payment.separation_enforced is True
    with pytest.raises(InvalidTransition, match="preparer"):
        approve_supplier_payment(
            db,
            scope=TenantScope(TENANT),
            payment_id=payment.id,
            approval_reference="approval:a7",
            approved_by=ONE,
            idempotency_key="a7:def:true:approve",
            idempotency_expires_at=None,
            approved_at=NOW,
        )  # default off cannot loosen a strict stamp
