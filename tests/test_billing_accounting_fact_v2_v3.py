"""Hosted migrated-PostgreSQL proofs for the exact V2 and V3 accounting-fact reads."""

from uuid import uuid4

import pytest
from dotmac_billing import (
    AccountingFactV2,
    AccountingFactV3,
    get_accounting_fact_v1,
    get_accounting_fact_v2,
    get_accounting_fact_v3,
    models,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.service import accept_withholding_discharge, grant_non_cash_credit
from dotmac_kernel.cache import TenantScope
from sqlalchemy import select

from tests import test_billing_isolation as billing
from tests import test_billing_non_cash_credit as runtime
from tests.billing_frozen_fixtures import (
    withholding_command as frozen_withholding_command,
)

scratch = runtime.scratch


def _fact_id(db, scope, posting_group_id):
    model = (
        models.AccountingFact
        if isinstance(scope, TenantScope)
        else models.PlatformAccountingFact
    )
    return db.execute(
        select(model.id).where(model.posting_group_id == posting_group_id)
    ).scalar_one()


def _v2(db, scope, account, invoice):
    discharge = accept_withholding_discharge(
        db,
        scope=scope,
        command=frozen_withholding_command(
            db,
            account.id,
            invoice.id,
            scope=scope,
            suffix=str(uuid4()),
            accepted="90",
            claimed="90",
        ),
    )
    return discharge, _fact_id(db, scope, discharge.posting_group_id)


def _v3(db, scope, account):
    grant = grant_non_cash_credit(
        db, scope=scope, command=runtime._grant(scope, account.id)
    )
    effect_model = (
        models.NonCashCreditEffect
        if isinstance(scope, TenantScope)
        else models.PlatformNonCashCreditEffect
    )
    effect = db.scalars(
        select(effect_model).where(effect_model.grant_id == grant.grant_id)
    ).first()
    return grant, effect, _fact_id(db, scope, effect.posting_group_id)


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_v2_and_v3_round_trip_not_found_and_wrong_version(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    with billing._session(url, scope) as db:
        account, _, invoice = billing._seed_invoice(db, scope, suffix=str(uuid4()))
        discharge, v2_id = _v2(db, scope, account, invoice)
        grant, effect, v3_id = _v3(db, scope, account)
        db.commit()
        v2 = get_accounting_fact_v2(db, scope=scope, fact_id=v2_id)
        assert isinstance(v2, AccountingFactV2)
        assert (v2.fact_id, v2.fact_version, v2.billing_account_id) == (
            v2_id,
            2,
            account.id,
        )
        assert v2.posting_group_id == discharge.posting_group_id
        assert v2.effect_kind == "withholding_accepted"
        assert [d.discharge_id for d in v2.withholding_discharges] == [discharge.id]
        v3 = get_accounting_fact_v3(db, scope=scope, fact_id=v3_id)
        assert isinstance(v3, AccountingFactV3)
        assert (v3.fact_id, v3.fact_version, v3.billing_account_id) == (
            v3_id,
            3,
            account.id,
        )
        assert v3.non_cash_credit_grant is not None
        assert v3.non_cash_credit_grant.grant_id == grant.grant_id
        assert effect.id in {e.effect_id for e in v3.non_cash_credit_effects}
        # Unknown id.
        for reader in (get_accounting_fact_v2, get_accounting_fact_v3):
            with pytest.raises(BillingRuleViolation, match="not found"):
                reader(db, scope=scope, fact_id=uuid4())
        # Wrong version: each reader refuses the other's id, and V1 refuses both.
        with pytest.raises(BillingRuleViolation, match="not found"):
            get_accounting_fact_v2(db, scope=scope, fact_id=v3_id)
        with pytest.raises(BillingRuleViolation, match="not found"):
            get_accounting_fact_v3(db, scope=scope, fact_id=v2_id)
        with pytest.raises(BillingRuleViolation, match="not found"):
            get_accounting_fact_v1(db, scope=scope, fact_id=v2_id)


def test_v1_fact_through_v2_and_v3_readers_is_refused(scratch):
    from tests.test_billing_accounting_fact_v1 import _allocation

    scope, url = runtime._scope_url(scratch, "tenant")
    with billing._session(url, scope) as db:
        *_, row = _allocation(db, scope, "")
        v1_id = row.id
        assert get_accounting_fact_v1(db, scope=scope, fact_id=v1_id).fact_version == 1
        for reader in (get_accounting_fact_v2, get_accounting_fact_v3):
            with pytest.raises(BillingRuleViolation, match="not found"):
                reader(db, scope=scope, fact_id=v1_id)


def test_cross_tenant_v2_v3_reads_are_denied(scratch):
    scope, url = runtime._scope_url(scratch, "tenant")
    with billing._session(url, scope) as db:
        account, _, invoice = billing._seed_invoice(db, scope, suffix=str(uuid4()))
        _, v2_id = _v2(db, scope, account, invoice)
        *_, v3_id = _v3(db, scope, account)
        db.commit()
    other = TenantScope(billing._create_tenants(scratch[0], 1)[0])
    with billing._session(url, other) as db:
        with pytest.raises(BillingRuleViolation, match="not found"):
            get_accounting_fact_v2(db, scope=other, fact_id=v2_id)
        with pytest.raises(BillingRuleViolation, match="not found"):
            get_accounting_fact_v3(db, scope=other, fact_id=v3_id)
