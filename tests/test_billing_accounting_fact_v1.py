"""Hosted migrated-PostgreSQL proofs for exact immutable V1 accounting reads."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from dotmac_billing import (
    get_accounting_fact_v1,
    get_allocation_accounting_fact_v1,
    models,
    service,
)
from dotmac_billing.commands import AllocationCommand, DeallocationCommand
from dotmac_billing.errors import BillingRuleViolation
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from sqlalchemy import select

from tests import test_billing_isolation as billing

scratch = billing.scratch


def _routing(urls, plane):
    if plane == "tenant":
        return TenantScope(billing._create_tenants(urls[0], 1)[0]), urls[1], ""
    return PlatformScope(), urls[2], "Platform"


def _allocation(db, scope, prefix):
    account, _, invoice = billing._seed_invoice(db, scope, suffix=str(uuid4()))
    settlement = service.accept_settlement(
        db,
        scope=scope,
        command=billing._settlement_command(
            account.id, scope=scope, source_key=str(uuid4())
        ),
        accepted_confirmation_evidence=frozenset({"provider_settled"}),
    )
    offered = AllocationCommand(
        settlement.id, invoice.id, billing._money("50"), billing.NOW, str(uuid4())
    )
    group = service.allocate_settlement(db, scope=scope, command=offered)
    model = getattr(models, prefix + "AccountingFact")
    row = db.execute(
        select(model).where(model.posting_group_id == group.id)
    ).scalar_one()
    db.commit()
    return account.id, settlement.id, invoice.id, offered, row


def test_cross_tenant_exact_accounting_fact_canary(scratch):
    scope, url, prefix = _routing(scratch, "tenant")
    with billing._session(url, scope) as db:
        *_, row = _allocation(db, scope, prefix)
        fact_id, posting_group_id = row.id, row.posting_group_id
    other = TenantScope(billing._create_tenants(scratch[0], 1)[0])
    with billing._session(url, other) as db:
        with pytest.raises(BillingRuleViolation, match="not found"):
            get_accounting_fact_v1(db, scope=other, fact_id=fact_id)
        with pytest.raises(BillingRuleViolation, match="not found"):
            get_allocation_accounting_fact_v1(
                db, scope=other, posting_group_id=posting_group_id
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_original_allocation_read_survives_full_deallocation_and_ignores_orm(
    scratch, plane
):
    scope, url, prefix = _routing(scratch, plane)
    with billing._session(url, scope) as db:
        account, settlement, invoice, offered, row = _allocation(db, scope, prefix)
        original = get_accounting_fact_v1(db, scope=scope, fact_id=row.id)
        assert (
            get_allocation_accounting_fact_v1(
                db, scope=scope, posting_group_id=row.posting_group_id
            )
            == original
        )
        assert original.fact_id == row.id
        assert original.fact_version == 1
        assert original.billing_account_id == account
        assert original.posting_group_id == row.posting_group_id
        assert original.source_ref == offered.source_ref
        assert original.effect_kind == "allocation"
        assert len(original.allocations) == 1
        allocation = original.allocations[0]
        assert (
            allocation.settlement_id,
            allocation.document_id,
            allocation.effect_kind,
            allocation.amount_delta,
            allocation.offsets_allocation_id,
        ) == (settlement, invoice, "allocation", billing._money("50"), None)
        with pytest.raises(FrozenInstanceError):
            original.source_ref = "changed"
        # Dirty identity-map objects must neither override nor autoflush the read.
        row.fact_payload = {"dirty": "not persisted"}
        row.fact_digest = "f" * 64
        assert get_accounting_fact_v1(db, scope=scope, fact_id=row.id) == original
        assert (
            get_allocation_accounting_fact_v1(
                db, scope=scope, posting_group_id=original.posting_group_id
            )
            == original
        )
        assert row in db.dirty
        db.rollback()
        allocation_model = getattr(models, prefix + "AllocationEffect")
        allocated = db.execute(
            select(allocation_model).where(
                allocation_model.posting_group_id == original.posting_group_id
            )
        ).scalar_one()
        service.deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(
                allocated.id, billing._money("50"), billing.NOW, str(uuid4())
            ),
        )
        db.commit()
        assert (
            service.allocation_remaining(db, scope=scope, allocation_id=allocated.id)
            == 0
        )
        assert (
            get_accounting_fact_v1(db, scope=scope, fact_id=original.fact_id)
            == original
        )
        assert (
            service.allocate_settlement(db, scope=scope, command=offered).id
            == original.posting_group_id
        )
    with billing._session(url, scope) as db:
        assert (
            get_accounting_fact_v1(db, scope=scope, fact_id=original.fact_id)
            == original
        )
        with pytest.raises(BillingRuleViolation, match="not found"):
            get_accounting_fact_v1(db, scope=scope, fact_id=uuid4())
        assert (
            get_allocation_accounting_fact_v1(
                db, scope=scope, posting_group_id=original.posting_group_id
            )
            == original
        )
        with pytest.raises(BillingRuleViolation, match="not found"):
            get_allocation_accounting_fact_v1(db, scope=scope, posting_group_id=uuid4())
        group_model = getattr(models, prefix + "PostingGroup")
        invoice_group = db.execute(
            select(group_model.id).where(
                group_model.billing_account_id == account,
                group_model.group_kind == "invoice_issued",
            )
        ).scalar_one()
        with pytest.raises(BillingRuleViolation, match="original allocation"):
            get_allocation_accounting_fact_v1(
                db, scope=scope, posting_group_id=invoice_group
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "version",
        "digest",
        "shape",
        "scope",
        "identity",
        "group",
        "amount",
        "allocation",
        "source",
        "occurred_at",
    ],
)
def test_appended_bad_fact_is_refused_against_real_posting_evidence(
    scratch, plane, fault
):
    scope, url, prefix = _routing(scratch, plane)
    with billing._session(url, scope) as db:
        *_, source = _allocation(db, scope, prefix)
        group_model = getattr(models, prefix + "PostingGroup")
        group = db.get(group_model, source.posting_group_id)
        group_id, fact_id = uuid4(), uuid4()
        values = {
            column.name: getattr(group, column.name)
            for column in group_model.__table__.columns
            if column.name not in {"id", "created_at"}
        }
        values.update(id=group_id, source_version=group.source_version + 1)
        db.add(group_model(**values))
        db.flush()
        # Copy the original immutable backing so a correctly sealed append is
        # readable; each fault below changes only its named evidence dimension.
        for suffix in ("PostingEffect", "AllocationEffect"):
            model = getattr(models, prefix + suffix)
            for original in db.execute(
                select(model).where(model.posting_group_id == source.posting_group_id)
            ).scalars():
                copied = {
                    column.name: getattr(original, column.name)
                    for column in model.__table__.columns
                    if column.name not in {"id", "created_at"}
                }
                copied.update(id=uuid4(), posting_group_id=group_id)
                db.add(model(**copied))
        db.flush()
        payload = deepcopy(source.fact_payload)
        payload.update(fact_id=str(fact_id), posting_group_id=str(group_id))
        version = 2 if fault == "version" else 1
        if fault == "shape":
            del payload["allocations"]
        elif fault == "scope":
            payload["scope"] = {} if plane == "tenant" else {"tenant_id": str(uuid4())}
        elif fault == "identity":
            payload["fact_id"] = str(uuid4())
        elif fault == "group":
            payload["posting_group_id"] = str(source.posting_group_id)
        elif fault == "amount":
            payload["effects"][0]["amount_delta"]["amount"] = "51.00"
        elif fault == "allocation":
            payload["allocations"][0]["document_id"] = str(uuid4())
        elif fault == "source":
            payload["source_ref"] = "different-source"
        elif fault == "occurred_at":
            payload["occurred_at"] = "2026-01-01T00:00:00+00:00"
        row_values = {
            column.name: getattr(source, column.name)
            for column in source.__table__.columns
            if column.name not in {"id", "created_at"}
        }
        row_values.update(
            id=fact_id,
            posting_group_id=group_id,
            fact_version=version,
            fact_payload=payload,
            fact_digest="f" * 64 if fault == "digest" else fingerprint_of(payload),
        )
        db.add(type(source)(**row_values))
        db.flush()
        if fault == "valid":
            assert (
                get_accounting_fact_v1(db, scope=scope, fact_id=fact_id).fact_id
                == fact_id
            )
            db.rollback()
            return
        with pytest.raises(BillingRuleViolation) as error:
            get_accounting_fact_v1(db, scope=scope, fact_id=fact_id)
        assert error.value.code == (
            "billing.not_found"
            if fault == "version"
            else "billing.accounting_fact_integrity"
        )
        db.rollback()
