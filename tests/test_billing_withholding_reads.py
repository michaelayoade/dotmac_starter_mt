"""Real owner-reader evidence; consumer never queries shared Billing tables."""

from datetime import timedelta
from uuid import uuid4

import pytest
from dotmac_billing.contracts import (
    GetBillingAccountByExternalRefV1,
    GetCurrentInvoiceReceivableV1,
)
from dotmac_billing.queries import (
    get_billing_account_by_external_ref_v1,
    get_current_invoice_receivable_v1,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Currency
from dotmac_kernel.planes import ModulePlane

from tests import test_billing_isolation as billing


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_native_invoice_read_is_document_grained_and_has_real_cash_effect_time(
    scratch, plane
):
    admin, tenant, platform = scratch
    scope = (
        TenantScope(billing._create_tenants(admin, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    with billing._session(tenant if plane == "tenant" else platform, scope) as db:
        suffix = f"native-read-{uuid4()}"
        account, _, invoice = billing._seed_invoice(db, scope, suffix=suffix)
        lookup = get_billing_account_by_external_ref_v1(
            db,
            scope=scope,
            request=GetBillingAccountByExternalRefV1(
                scope, f"customer-{suffix}", Currency("NGN", 2)
            ),
        )
        assert lookup.account_id == account.id
        before = get_current_invoice_receivable_v1(
            db, scope=scope, request=GetCurrentInvoiceReceivableV1(scope, invoice.id)
        )
        assert before.invoice_total == billing._money("120")
        assert before.outstanding == billing._money("120")
        assert before.cash_applied == before.withholding_applied == billing._money("0")
        assert before.settlement_effective_at is None
        cash = billing.accept_settlement(
            db,
            scope=scope,
            command=billing._settlement_command(
                account.id, scope=scope, source_key=f"cash:{uuid4()}", amount="30"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        applied_at = billing.NOW + timedelta(hours=2)
        billing.allocate_settlement(
            db,
            scope=scope,
            command=billing.AllocationCommand(
                settlement_id=cash.id,
                document_id=invoice.id,
                amount=billing._money("30"),
                occurred_at=applied_at,
                source_ref=f"applied:{uuid4()}",
            ),
        )
        after = get_current_invoice_receivable_v1(
            db, scope=scope, request=GetCurrentInvoiceReceivableV1(scope, invoice.id)
        )
        assert after.active
        assert after.fact_version == before.fact_version
        assert after.source_version > before.source_version
        assert after.outstanding == billing._money("90")
        assert after.cash_applied == billing._money("30")
        assert after.withholding_applied == billing._money("0")
        assert after.settlement_effective_at == applied_at
        assert len(after.cash_allocation_ids) == 1
        assert not after.withholding_discharge_ids
        assert not after.opening_cash_effect_ids


def test_real_tax_determination_and_native_cash_owner_settle_two_invoices(
    scratch, monkeypatch
):
    """No hand-written rv1 seals and no gross/cash substitution in this proof."""
    from datetime import date
    from decimal import Decimal
    from pathlib import Path

    from alembic.config import Config
    from dotmac_billing.commands import (
        AcceptWithholdingDischarge,
        ApplyAvailableCreditCommand,
        RefundCommand,
    )
    from dotmac_billing.contracts import (
        WithholdingCashRequirementV1,
        WithholdingTaxEvidenceV1,
        WithholdingTaxLineV1,
    )
    from dotmac_billing.errors import BillingRuleViolation
    from dotmac_billing.service import (
        accept_withholding_discharge,
        apply_available_credit,
        refund_settlement,
    )
    from dotmac_kernel.planes import ModulePlaneSelection
    from dotmac_tax.contracts import (
        TaxAuthorityInput,
        TaxFact,
        TaxJurisdictionInput,
        TaxRuleInput,
    )
    from dotmac_tax.service import (
        create_tax_authority,
        create_tax_code,
        create_tax_jurisdiction,
        determine_tax_set,
        publish_tax_rule,
    )

    from alembic import command

    admin, tenant, _ = scratch
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option(
        "version_locations",
        " ".join(
            str(path)
            for path in (
                billing.KERNEL_VERSIONS,
                billing.ASSEMBLY_VERSIONS,
                billing.BILLING_VERSIONS,
                root / "packages/dotmac-tax/src/dotmac_tax/migrations/versions",
            )
        ),
    )
    config.attributes["module_plane_selections"] = (
        ModulePlaneSelection(
            module="billing", planes=(ModulePlane.TENANT, ModulePlane.PLATFORM)
        ),
        ModulePlaneSelection(module="tax", planes=(ModulePlane.TENANT,)),
    )
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin)
    command.upgrade(config, "heads")
    scope = TenantScope(billing._create_tenants(admin, 1)[0])
    with billing._session(tenant, scope) as db:
        account, _, first = billing._seed_invoice(
            db, scope, suffix=f"real-tax:{uuid4()}"
        )
        second_draft = billing._additional_credit_invoice(
            db, scope, account.id, str(uuid4())
        )
        second = billing.issue_document(
            db,
            scope=scope,
            command=billing._issue_command(second_draft.id),
            numbering=billing.NUMBERING,
        )
        authority = create_tax_authority(
            db,
            tenant_id=scope.tenant_id,
            command=TaxAuthorityInput("FIXTURE-AUTH", "Explicit test policy authority"),
        )
        jurisdiction = create_tax_jurisdiction(
            db,
            tenant_id=scope.tenant_id,
            command=TaxJurisdictionInput(
                authority.id,
                "FIXTURE-NG",
                "Explicit test jurisdiction",
                "NG",
                Currency("NGN", 2),
            ),
        )
        code = create_tax_code(
            db,
            tenant_id=scope.tenant_id,
            jurisdiction_id=jurisdiction.id,
            code="FIXTURE-WHT",
            name="Explicit withholding test rule",
            tax_kind_code="withholding",
        )
        publish_tax_rule(
            db,
            tenant_id=scope.tenant_id,
            command=TaxRuleInput(
                tax_code_id=code.id,
                version=1,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                priority=1,
                fact_kind="invoice",
                recognition_basis_code="invoice_issued",
                transaction_side="withholding",
                calculation_method="percentage",
                rate=Decimal("0.05"),
                fixed_amount=None,
                inclusive=False,
                recoverable_rate=Decimal("0"),
                treatment_code="standard_rated",
            ),
            published_at=billing.NOW - timedelta(days=1),
        )
        cash = billing.accept_settlement(
            db,
            scope=scope,
            command=billing._settlement_command(
                account.id,
                scope=scope,
                source_key=f"one-bank-deposit:{uuid4()}",
                amount="230",
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        for invoice in (first, second):
            result = determine_tax_set(
                db,
                tenant_id=scope.tenant_id,
                fact=TaxFact(
                    jurisdiction_id=jurisdiction.id,
                    occurred_on=billing.NOW.date(),
                    fact_kind="invoice",
                    recognition_basis_code="invoice_issued",
                    transaction_side="withholding",
                    base_amount=billing._money("100"),
                    source_ref=f"invoice:{invoice.id}",
                    source_version="1",
                    evidence_ref="actual-test-rule",
                ),
                determined_at=billing.NOW,
            )
            assert result.result_fingerprint.startswith("rv1:")
            component = result.components[0]
            evidence = WithholdingTaxEvidenceV1(
                source_owner="dotmac-tax",
                determination_ref=str(result.determination_set_id),
                source_version=result.source_version,
                source_fingerprint=result.source_fingerprint,
                result_fingerprint=result.result_fingerprint,
                evidence_ref=result.evidence_ref,
                source_ref=result.source_ref,
                component_ref=str(component.determination_id),
                rule_ref=str(component.rule_id),
                rule_version=str(component.rule_version),
                treatment_code=component.treatment_code,
                calculation_base_code=component.calculation_base_code,
                inclusive=component.inclusive,
                taxable_basis=component.base_amount,
                amount=component.tax_amount,
                lines=tuple(
                    WithholdingTaxLineV1(
                        row.sequence, row.taxable_amount, row.rate, row.tax_amount
                    )
                    for row in component.lines
                ),
                scope=scope,
                billing_account_id=account.id,
                document_id=invoice.id,
                recognition_basis_code=result.recognition_basis_code,
                fact_kind=result.fact_kind,
                transaction_side=result.transaction_side,
                determined_at=result.determined_at,
            )
            quote = WithholdingCashRequirementV1(
                billing._money("120"), component.tax_amount
            )
            assert quote.cash_required == billing._money("115")
            discharge = accept_withholding_discharge(
                db,
                scope=scope,
                command=AcceptWithholdingDischarge(
                    scope=scope,
                    billing_account_id=account.id,
                    document_id=invoice.id,
                    idempotency_key=f"wht:{invoice.id}",
                    source_owner="test-assembly",
                    source_ref=f"claim:{invoice.id}",
                    claim_ref=f"declaration:{invoice.id}",
                    claimed_amount=component.tax_amount,
                    accepted_amount=component.tax_amount,
                    tax_evidence=evidence,
                    authorization_actor_ref="user:fixture",
                    authorization_evidence_ref="verified-test-declaration",
                    occurred_at=billing.NOW,
                ),
            )
            # A genuine noncash discharge is not a refundable cash settlement.
            with pytest.raises(BillingRuleViolation) as rejected_refund:
                refund_settlement(
                    db,
                    scope=scope,
                    command=RefundCommand(
                        discharge.id,
                        component.tax_amount,
                        billing.NOW,
                        f"not-cash:{invoice.id}",
                    ),
                )
            assert rejected_refund.value.code == "billing.not_found"
            apply_available_credit(
                db,
                scope=scope,
                command=ApplyAvailableCreditCommand(
                    account.id,
                    invoice.id,
                    quote.cash_required,
                    billing.NOW,
                    f"cash-draw:{invoice.id}",
                ),
            )
            current = get_current_invoice_receivable_v1(
                db,
                scope=scope,
                request=GetCurrentInvoiceReceivableV1(scope, invoice.id),
            )
            assert current.cash_and_withholding_settled
            assert current.cash_applied == billing._money("115")
            assert current.withholding_applied == billing._money("5")
            assert current.outstanding == billing._money("0")
            assert current.settlement_effective_at == billing.NOW
        position = billing.rebuild_receivable_position(
            db,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        )
        assert (
            position.available_credit == position.collectible_receivable == Decimal("0")
        )
        assert cash.amount == Decimal("230")
