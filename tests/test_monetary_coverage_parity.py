"""The Python and PostgreSQL monetary-coverage decisions must stay identical.

Ported from ``dotmac_erp:tests/integration/test_coverage_parity.py`` with only
the import boundary changed.  The expression deliberately runs on PostgreSQL:
SQLite's looser NUMERIC behavior is not evidence for the deployed SQL rule.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from dotmac_kernel.monetary_coverage import (
    PAYMENT_DUST_DEFAULT,
    PaymentCoverage,
    coverage_case,
    coverage_of,
)
from sqlalchemy import literal, select
from sqlalchemy.orm import Session

CASES = [
    ("100.00", "0.00"),
    ("100.00", "0.01"),
    ("100.00", "0.02"),
    ("100.00", "40.00"),
    ("100.00", "99.98"),
    ("100.00", "99.99"),
    ("100.00", "100.00"),
    ("100.00", "100.01"),
    ("100.00", "100.02"),
    ("100.00", "150.00"),
    ("0.00", "0.00"),
    ("-100.00", "0.00"),
]

TOLERANCES = ["0.01", "0", "1.00"]


@pytest.mark.parametrize("dust", TOLERANCES)
@pytest.mark.parametrize(("total", "paid"), CASES)
def test_sql_agrees_with_python(
    admin_session: Session,
    total: str,
    paid: str,
    dust: str,
) -> None:
    total_amount = Decimal(total)
    amount_paid = Decimal(paid)
    tolerance = Decimal(dust)

    in_python = coverage_of(
        total_amount=total_amount,
        amount_paid=amount_paid,
        dust=tolerance,
    )
    in_sql = admin_session.scalar(
        select(
            coverage_case(
                literal(total_amount - amount_paid),
                literal(amount_paid),
                dust=tolerance,
            )
        )
    )

    assert in_sql == in_python.value, (
        f"total={total} paid={paid} dust={dust}: SQL said {in_sql!r}, "
        f"Python said {in_python.value!r}; the two coverage decisions diverged"
    )


def test_the_matrix_reaches_every_member() -> None:
    """Sensitivity: agreement over one accidental outcome proves nothing."""
    reached = {
        coverage_of(
            total_amount=Decimal(total),
            amount_paid=Decimal(paid),
            dust=PAYMENT_DUST_DEFAULT,
        )
        for total, paid in CASES
    }
    assert reached == set(PaymentCoverage)
