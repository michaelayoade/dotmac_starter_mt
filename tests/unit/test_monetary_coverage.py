"""ADR-0016's shared monetary-coverage owner, ported product-first from ERP.

These are the DB-free proofs.  PostgreSQL executes the SQL twin separately in
``tests/test_monetary_coverage_parity.py``; keeping that proof out of SQLite is
intentional because NUMERIC comparison semantics are part of the contract.
"""

from __future__ import annotations

from decimal import Decimal

from dotmac_kernel import (
    PAYMENT_DUST_DEFAULT,
    PAYMENT_DUST_KEY,
    MonetaryCoverageMixin,
    PaymentCoverage,
    coverage_of,
    parse_payment_dust,
)
from sqlalchemy import Integer, Numeric
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class _Base(DeclarativeBase):
    pass


class _MonetaryDocument(MonetaryCoverageMixin, _Base):
    __tablename__ = "monetary_coverage_probe"

    document_id: Mapped[int] = mapped_column(Integer, primary_key=True)


def test_payment_coverage_is_the_closed_arithmetic_vocabulary() -> None:
    assert {member.value for member in PaymentCoverage} == {
        "unpaid",
        "partial",
        "paid",
        "overpaid",
    }


def test_coverage_boundaries_match_the_erp_reference_owner() -> None:
    def classify(total: str, paid: str) -> PaymentCoverage:
        return coverage_of(
            total_amount=Decimal(total),
            amount_paid=Decimal(paid),
        )

    assert classify("100.00", "0.00") is PaymentCoverage.UNPAID
    assert classify("100.00", "0.01") is PaymentCoverage.UNPAID
    assert classify("100.00", "0.02") is PaymentCoverage.PARTIAL
    assert classify("100.00", "99.98") is PaymentCoverage.PARTIAL
    assert classify("100.00", "99.99") is PaymentCoverage.PAID
    assert classify("100.00", "100.01") is PaymentCoverage.PAID
    assert classify("100.00", "100.02") is PaymentCoverage.OVERPAID
    assert classify("0.00", "0.00") is PaymentCoverage.PAID
    assert classify("-100.00", "0.00") is PaymentCoverage.OVERPAID


def test_dust_parser_preserves_erp_fallback_without_owning_settings_storage() -> None:
    assert PAYMENT_DUST_KEY == "payment_dust"
    assert PAYMENT_DUST_DEFAULT == Decimal("0.01")
    assert parse_payment_dust("0.125") == Decimal("0.125")
    assert parse_payment_dust(Decimal("0.5")) == Decimal("0.5")
    assert parse_payment_dust(None) == PAYMENT_DUST_DEFAULT
    assert parse_payment_dust("not-a-decimal") == PAYMENT_DUST_DEFAULT


def test_mixin_supplies_the_three_adr_0016_columns() -> None:
    columns = _MonetaryDocument.__table__.c

    assert {"total_amount", "amount_paid", "balance_due"} <= set(columns.keys())
    for name in ("total_amount", "amount_paid", "balance_due"):
        column = columns[name]
        assert isinstance(column.type, Numeric)
        assert column.type.precision == 20
        assert column.type.scale == 6
        assert column.nullable is False

    assert columns.amount_paid.default is not None
    assert columns.amount_paid.default.arg == Decimal("0")
    assert columns.balance_due.computed is not None
    assert str(columns.balance_due.computed.sql) == "total_amount - amount_paid"
    assert columns.balance_due.computed.persisted is True


def test_mixin_does_not_store_a_coverage_cache() -> None:
    assert "coverage" not in _MonetaryDocument.__table__.c
