"""Unit proofs for the V2/V3 accounting-fact read surface (no database)."""

from uuid import uuid4

import dotmac_billing
import pytest
from dotmac_billing import queries
from dotmac_billing.errors import BillingRuleViolation
from dotmac_kernel.cache import TenantScope


@pytest.mark.parametrize("name", ["get_accounting_fact_v2", "get_accounting_fact_v3"])
def test_public_surface_exports_reader(name):
    assert name in dotmac_billing.__all__
    assert getattr(dotmac_billing, name) is getattr(queries, name)
    assert name in queries.__all__


@pytest.mark.parametrize(
    "reader", [queries.get_accounting_fact_v2, queries.get_accounting_fact_v3]
)
def test_scope_and_id_are_validated_before_any_query(reader):
    with pytest.raises(BillingRuleViolation, match="explicit scope"):
        reader(None, scope=object(), fact_id=uuid4())  # type: ignore[arg-type]
    with pytest.raises(BillingRuleViolation, match="must be a UUID"):
        reader(None, scope=TenantScope(uuid4()), fact_id="x")  # type: ignore[arg-type]
