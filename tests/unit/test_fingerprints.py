"""The canonical digest helper stays persistence-free and compatibly owned."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from dotmac_kernel.fingerprints import fingerprint_of


def test_fingerprint_is_stable_for_order_and_non_json_scalar_values() -> None:
    first = {"id": UUID(int=1), "amount": Decimal("1.50")}
    second = {"amount": Decimal("1.50"), "id": UUID(int=1)}

    assert fingerprint_of(first) == fingerprint_of(second)
    assert len(fingerprint_of(first)) == 64


def test_idempotency_keeps_the_same_fingerprint_owner_as_a_reexport() -> None:
    from dotmac_kernel.idempotency import fingerprint_of as ledger_fingerprint

    payload = {"invoice_id": "invoice-1", "fact_version": 3}
    assert ledger_fingerprint(payload) == fingerprint_of(payload)
