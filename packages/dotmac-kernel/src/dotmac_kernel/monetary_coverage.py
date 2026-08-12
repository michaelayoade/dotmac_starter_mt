"""Payment coverage is derived arithmetic, never lifecycle state.

This is the shared ADR-0016 owner ported from ERP's accepted
``app.services.finance.coverage`` implementation and its PostgreSQL parity
proof.  A monetary document has three persisted numbers—``total_amount``,
``amount_paid``, and database-generated ``balance_due``—while the coverage
classification remains a read-time decision over those facts and a caller-
supplied tolerance.

Settings storage is deliberately outside this module.  A product declares and
reads its own ``payments.payment_dust`` setting, then passes the parsed Decimal
to ``coverage_of`` or ``coverage_case``.  ``parse_payment_dust`` preserves ERP's
malformed-setting fallback without importing an organization- or tenant-
specific resolver.

The Python and SQL functions are twins rather than separate policy owners.
Their required PostgreSQL parity matrix lives in
``tests/test_monetary_coverage_parity.py``.
"""

from __future__ import annotations

import enum
from decimal import Decimal, InvalidOperation

from sqlalchemy import Case, ColumnElement, Computed, Numeric, case, literal
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

PAYMENT_DUST_DEFAULT = Decimal("0.01")
PAYMENT_DUST_KEY = "payment_dust"


class PaymentCoverage(str, enum.Enum):
    """The closed arithmetic vocabulary for payment coverage."""

    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"
    OVERPAID = "overpaid"


class MonetaryCoverageMixin:
    """The ADR-0016 persistence shape for a monetary document.

    ``NUMERIC(20, 6)`` is the qualifying ERP source's deployed shape.  The
    generated column contains pure subtraction only; tolerance policy remains
    in ``coverage_of``/``coverage_case`` so changing it never requires DDL.
    ``balance_due`` has no value on an unflushed new instance and is refreshed
    by SQLAlchemy after a database flush.
    """

    @declared_attr
    def total_amount(cls) -> Mapped[Decimal]:
        return mapped_column(Numeric(20, 6), nullable=False)

    @declared_attr
    def amount_paid(cls) -> Mapped[Decimal]:
        return mapped_column(
            Numeric(20, 6),
            nullable=False,
            default=Decimal("0"),
        )

    @declared_attr
    def balance_due(cls) -> Mapped[Decimal]:
        return mapped_column(
            Numeric(20, 6),
            Computed("total_amount - amount_paid", persisted=True),
            nullable=False,
        )


def parse_payment_dust(
    raw: object,
    *,
    default: Decimal = PAYMENT_DUST_DEFAULT,
) -> Decimal:
    """Parse a product setting without making this module its storage owner.

    ERP's established read behavior is fail-safe: malformed stored input falls
    back to the declared default rather than taking every aging/dunning read
    down.  Validation remains loud on the product's settings write path.
    """
    if isinstance(raw, Decimal):
        return raw
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return default


def coverage_of(
    *,
    total_amount: Decimal,
    amount_paid: Decimal,
    dust: Decimal = PAYMENT_DUST_DEFAULT,
) -> PaymentCoverage:
    """Classify loaded values using live subtraction, with no I/O or clock."""
    balance = total_amount - amount_paid

    if balance < -dust:
        return PaymentCoverage.OVERPAID
    if balance <= dust:
        return PaymentCoverage.PAID
    if amount_paid > dust:
        return PaymentCoverage.PARTIAL
    return PaymentCoverage.UNPAID


def coverage_case(
    balance_due: ColumnElement[Decimal],
    amount_paid: ColumnElement[Decimal],
    *,
    dust: Decimal = PAYMENT_DUST_DEFAULT,
) -> Case:
    """Return the query-usable SQL twin of ``coverage_of``.

    SQL receives the database-generated balance rather than recomputing it.
    Python subtracts live because a loaded generated column does not track an
    in-flight assignment to ``amount_paid`` until the row is flushed.
    """
    return case(
        (balance_due < literal(-dust), literal(PaymentCoverage.OVERPAID.value)),
        (balance_due <= literal(dust), literal(PaymentCoverage.PAID.value)),
        (amount_paid > literal(dust), literal(PaymentCoverage.PARTIAL.value)),
        else_=literal(PaymentCoverage.UNPAID.value),
    )


__all__ = [
    "PAYMENT_DUST_DEFAULT",
    "PAYMENT_DUST_KEY",
    "MonetaryCoverageMixin",
    "PaymentCoverage",
    "coverage_case",
    "coverage_of",
    "parse_payment_dust",
]
