"""Hosted PostgreSQL canaries for immutable Tax-authored document evidence."""

# SQL identifiers are only closed tenant/platform fixture prefixes.
# ruff: noqa: S608
from dataclasses import fields, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_billing import (
    AcceptRatedObligationV2,
    GetInvoiceDocumentFactV1,
    InvoiceTaxEvidenceV1,
    IssueRatedInvoiceV2,
    TaxDeterminationComponentEvidenceV1,
    TaxDeterminationLineEvidenceV1,
    TaxDeterminationSetEvidenceV1,
    get_invoice_document_fact_v2,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.service import create_billing_account
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.money import Currency, Money
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests import test_billing_isolation as billing
from tests import test_billing_public_invoice_authoring as authoring
from tests import test_billing_withholding_persistence as migrations
from tests.billing_predecessor_fixtures import predecessor_obligation_columns


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _money(value):
    return Money(Decimal(value), Currency("NGN", 2))


def _evidence(scope, *, purpose="invoice_tax", rates=(None,), taxes=("7.50",)):
    set_id = uuid4()
    components = tuple(
        TaxDeterminationComponentEvidenceV1(
            determination_id=uuid4(),
            determination_set_id=set_id,
            component_sequence=index,
            tax_code_id=uuid4(),
            rule_id=uuid4(),
            rule_version=1,
            treatment_code="standard",
            calculation_base_code="source_amount",
            inclusive=False,
            party_category=None,
            supply_category=None,
            place_code=None,
            party_classification_id=None,
            supply_classification_id=None,
            place_classification_id=None,
            base_amount=_money("100"),
            tax_amount=_money(tax),
            recoverable_amount=_money("0"),
            non_recoverable_amount=_money(tax),
            lines=(
                TaxDeterminationLineEvidenceV1(1, _money("100"), rate, _money(tax)),
            ),
        )
        for index, (rate, tax) in enumerate(zip(rates, taxes, strict=True), 1)
    )
    tax_total = sum(
        (component.tax_amount.amount for component in components), Decimal(0)
    )
    return InvoiceTaxEvidenceV1(
        purpose,
        TaxDeterminationSetEvidenceV1(
            tenant_id=scope.tenant_id,
            determination_set_id=set_id,
            jurisdiction_id=uuid4(),
            occurred_on=date(2026, 10, 28),
            fact_kind="invoice",
            recognition_basis_code="invoice_expectation",
            transaction_side="output",
            source_amount=_money("100"),
            net_amount=_money("100"),
            tax_amount=_money(str(tax_total)),
            gross_amount=_money(str(100 + tax_total)),
            source_ref="issued-source",
            source_version="1",
            source_fingerprint="a" * 64,
            result_fingerprint="rv1:" + "b" * 64,
            evidence_ref="tax-sealed-result",
            counterparty_ref=None,
            supply_ref=None,
            place_ref=None,
            determined_at=datetime(2026, 10, 28, tzinfo=UTC),
            components=components,
        ),
    )


def _command(account_id, scope, evidence):
    old = authoring._command(account_id, scope)
    tax = sum(
        (
            item.determination.tax_amount.amount
            for item in evidence
            if item.purpose == "invoice_tax"
        ),
        Decimal(0),
    )
    old = replace(
        old,
        obligation=replace(
            old.obligation,
            tax_amount=_money(str(tax)),
            total_amount=_money(str(100 + tax)),
        ),
    )
    obligation = AcceptRatedObligationV2(
        **{
            field.name: getattr(old.obligation, field.name)
            for field in fields(old.obligation)
            if field.name != "tax_snapshots"
        },
        tax_snapshots=(),
        tax_evidence=evidence,
    )
    return IssueRatedInvoiceV2(
        **{
            field.name: getattr(old, field.name)
            for field in fields(old)
            if field.name != "obligation"
        },
        obligation=obligation,
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_new_v1_issuance_after_bi12_retains_sql_null_evidence(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(billing._create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    prefix = "" if plane == "tenant" else "platform_"
    with billing._session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        result = authoring._issue(session, scope, authoring._command(account.id, scope))
        assert session.execute(
            text(
                "SELECT tax_evidence IS NULL "
                f"FROM mod_billing.{prefix}rated_obligations "
                "WHERE id=:id"
            ),
            {"id": result.obligation_id},
        ).scalar_one()


def test_tax_evidence_cross_tenant_canary(scratch):
    admin_url, tenant_url, _ = scratch
    left, right = billing._create_tenants(admin_url)
    scope = TenantScope(left)
    with billing._session(tenant_url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        command = _command(account.id, scope, (_evidence(TenantScope(right)),))
        with pytest.raises(BillingRuleViolation, match="scope"):
            authoring._issue(session, scope, command)


@pytest.mark.parametrize(
    "rates,taxes,progressive",
    [
        ((None,), ("7.50",), False),
        ((None,), ("7.50",), True),
        ((Decimal("0"),), ("0",), False),
        ((Decimal("0.025"), Decimal("0.05")), ("2.50", "5.00"), False),
        ((Decimal("0"), None), ("0", "7.50"), False),
    ],
)
def test_issued_tax_evidence_round_trip_and_retry(scratch, rates, taxes, progressive):
    admin_url, tenant_url, _ = scratch
    tenant_id, right = billing._create_tenants(admin_url)
    scope = TenantScope(tenant_id)
    vat = _evidence(scope, rates=rates, taxes=taxes)
    if progressive:
        component = replace(
            vat.determination.components[0],
            lines=(
                TaxDeterminationLineEvidenceV1(
                    1, _money("50"), Decimal("0.05"), _money("2.50")
                ),
                TaxDeterminationLineEvidenceV1(
                    2, _money("50"), Decimal("0.10"), _money("5.00")
                ),
            ),
        )
        vat = replace(
            vat, determination=replace(vat.determination, components=(component,))
        )
    wht = _evidence(scope, purpose="expected_withholding", taxes=("5",))
    with billing._session(tenant_url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        command = _command(account.id, scope, (vat, wht))
        result = authoring._issue(session, scope, command)
        session.commit()
    with billing._session(tenant_url, scope) as session:
        request = GetInvoiceDocumentFactV1(scope, result.invoice_id, 1)
        stored = get_invoice_document_fact_v2(session, scope=scope, request=request)
        assert stored.fact.tax_evidence == (vat, wht)
        assert stored.fact.expected_withholding == _money("5")
        assert stored.fact.grand_total == command.obligation.total_amount
        assert authoring._issue(session, scope, command) == result
        changed = replace(
            vat, determination=replace(vat.determination, source_version="2")
        )
        with pytest.raises(IdempotencyConflict):
            authoring._issue(
                session,
                scope,
                replace(
                    command,
                    obligation=replace(command.obligation, tax_evidence=(changed, wht)),
                ),
            )
        # No live policy lookup participates in the issued document read.
        assert (
            get_invoice_document_fact_v2(session, scope=scope, request=request)
            == stored
        )
    with billing._session(tenant_url, TenantScope(right)) as session:
        other = TenantScope(right)
        with pytest.raises(BillingRuleViolation, match="unavailable"):
            get_invoice_document_fact_v2(
                session,
                scope=other,
                request=GetInvoiceDocumentFactV1(other, result.invoice_id, 1),
            )
    with billing._session(admin_url, scope) as session:
        for sql, identity in (
            (
                "UPDATE mod_billing.rated_obligations "
                "SET tax_evidence = '[]' WHERE id = :id",
                result.obligation_id,
            ),
            (
                "DELETE FROM mod_billing.rated_obligations WHERE id = :id",
                result.obligation_id,
            ),
            (
                "UPDATE mod_billing.invoice_document_facts "
                "SET fact_payload = '{}' WHERE id = :id",
                stored.fact_id,
            ),
            (
                "DELETE FROM mod_billing.invoice_document_facts WHERE id = :id",
                stored.fact_id,
            ),
        ):
            with pytest.raises(DBAPIError), session.begin_nested():
                session.execute(text(sql), {"id": identity})


def test_component_line_sum_and_order_are_validated():
    scope = TenantScope(uuid4())
    component = _evidence(scope).determination.components[0]
    with pytest.raises(ValueError, match="total"):
        replace(component, lines=(replace(component.lines[0], tax_amount=_money("1")),))
    with pytest.raises(ValueError, match="order"):
        replace(component, lines=(component.lines[0], component.lines[0]))
    progressive = replace(
        component,
        lines=(
            TaxDeterminationLineEvidenceV1(
                1, _money("50"), Decimal("0.05"), _money("2.50")
            ),
            TaxDeterminationLineEvidenceV1(
                2, _money("50"), Decimal("0.10"), _money("5.00")
            ),
        ),
    )
    assert progressive.lines[1].rate == Decimal("0.10")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_actual_bi11_predecessor_preserves_v1_facts_and_rows(monkeypatch, plane):
    from alembic import command

    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    with billing._scratch_database(
        (selection,), target_revision="bi_0011_settlement_earmarks"
    ) as urls:
        scope = (
            TenantScope(billing._create_tenants(urls[0], 1)[0])
            if plane == "tenant"
            else PlatformScope()
        )
        url = urls[1] if plane == "tenant" else urls[2]
        prefix = "" if plane == "tenant" else "platform_"
        with predecessor_obligation_columns(), billing._session(url, scope) as session:
            account = create_billing_account(
                session,
                scope=scope,
                external_account_ref=str(uuid4()),
                currency="NGN",
                minor_units=2,
            )
            offered = authoring._command(account.id, scope)
            issued = authoring._issue(session, scope, offered)
            request = GetInvoiceDocumentFactV1(scope, issued.invoice_id, 1)
            original = get_invoice_document_fact_v2(
                session, scope=scope, request=request
            )
            before = session.execute(
                text(
                    f"SELECT to_jsonb(x) FROM mod_billing.{prefix}rated_obligations x "
                    "WHERE id=:id"
                ),
                {"id": issued.obligation_id},
            ).scalar_one()
            session.commit()
        monkeypatch.setenv("MIGRATION_DATABASE_URL", urls[0])
        config = migrations._config((selection,))
        command.upgrade(config, "bi_0012_tax_evidence")
        with billing._session(url, scope) as session:
            assert (
                get_invoice_document_fact_v2(session, scope=scope, request=request)
                == original
            )
            after = session.execute(
                text(
                    f"SELECT to_jsonb(x) FROM mod_billing.{prefix}rated_obligations x "
                    "WHERE id=:id"
                ),
                {"id": issued.obligation_id},
            ).scalar_one()
            assert after.pop("tax_evidence") is None
            assert after == before
        command.downgrade(config, "bi_0011_settlement_earmarks")
        command.upgrade(config, "bi_0012_tax_evidence")
        with billing._session(url, scope) as session:
            assert (
                get_invoice_document_fact_v2(session, scope=scope, request=request)
                == original
            )
