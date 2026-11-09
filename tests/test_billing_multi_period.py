"""Real-migration PostgreSQL evidence for the explicit multi-period contract."""

from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from uuid import uuid4

import pytest
from dotmac_billing import (
    GetInvoiceServicePeriodsV1,
    create_multi_period_draft_document_v1,
    get_invoice_service_periods_v1,
)
from dotmac_billing.commands import CreateMultiPeriodDraftDocumentV1
from dotmac_billing.contracts import ServicePeriodEvidenceV1, ServicePeriodStatus
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.service import (
    accept_rated_obligation,
    create_billing_account,
    create_multi_line_draft_document,
    issue_document,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import select

from tests import test_billing_isolation as billing


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_multi_period_issue_keeps_exact_original_lines_and_unknown_aggregate(
    scratch, plane
):
    admin, tenant, platform = scratch
    scope = (
        TenantScope(billing._create_tenants(admin, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    with billing._session(tenant if plane == "tenant" else platform, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"multi-period:{uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        dates = [datetime(2026, month, 2, tzinfo=UTC) for month in (1, 2, 3)]
        obligations = []
        for start, end in pairwise(dates):
            command = replace(
                billing._obligation_command(
                    account.id, scope=scope, source_fact_id=str(uuid4())
                ),
                service_period=ServicePeriodEvidenceV1(
                    status=ServicePeriodStatus.VERIFIED, starts_at=start, ends_at=end
                ),
            )
            obligations.append(
                accept_rated_obligation(
                    session,
                    scope=scope,
                    command=command,
                    accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
                )
            )
        original = billing._multiline_command(account.id, obligations)
        with pytest.raises(BillingRuleViolation, match="share account"):
            create_multi_line_draft_document(session, scope=scope, command=original)
        command = CreateMultiPeriodDraftDocumentV1(
            **{field.name: getattr(original, field.name) for field in fields(original)}
        )
        draft = create_multi_period_draft_document_v1(
            session, scope=scope, command=command
        )
        assert draft.authoring_mode == "explicit_multiperiod"
        issued = issue_document(
            session,
            scope=scope,
            command=billing._issue_command(draft.id),
            numbering=billing.NUMBERING,
        )
        session.commit()
        replay = create_multi_period_draft_document_v1(
            session, scope=scope, command=command
        )
        assert replay.id == issued.id
        facts = get_invoice_service_periods_v1(
            session,
            scope=scope,
            request=GetInvoiceServicePeriodsV1(scope=scope, invoice_id=issued.id),
        )
        assert [
            (row.period.starts_at, row.period.ends_at) for row in facts.periods
        ] == list(pairwise(dates))
        assert facts.scope == scope
        assert facts.billing_account_id == account.id
        assert facts.settlement_grain == "document"
        assert len({row.line_id for row in facts.periods}) == 2
        assert len({row.obligation_id for row in facts.periods}) == 2
        assert (
            sum((row.total_amount.amount for row in facts.periods), Decimal(0))
            == issued.grand_total
        )
        model = (
            billing.models.ReceivableExposureFact
            if plane == "tenant"
            else billing.models.PlatformReceivableExposureFact
        )
        exposure = session.execute(
            select(model).where(model.document_id == issued.id)
        ).scalar_one()
        assert exposure.service_period == {
            "status": "unknown_unverified",
            "starts_at": None,
            "ends_at": None,
        }
        assert exposure.collectible_receivable == issued.grand_total
        settlement = billing.accept_settlement(
            session,
            scope=scope,
            command=billing._settlement_command(
                account.id, scope=scope, source_key=f"partial:{uuid4()}", amount="60.00"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        billing.allocate_settlement(
            session,
            scope=scope,
            command=billing.AllocationCommand(
                settlement_id=settlement.id,
                document_id=issued.id,
                amount=billing._money("60.00"),
                occurred_at=billing.NOW,
                source_ref=f"partial:{issued.id}",
            ),
        )
        after_payment = get_invoice_service_periods_v1(
            session,
            scope=scope,
            request=GetInvoiceServicePeriodsV1(scope=scope, invoice_id=issued.id),
        )
        assert after_payment == facts
        assert after_payment.settlement_grain == "document"
        assert all(not hasattr(row, "paid_amount") for row in after_payment.periods)


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_bi12_predecessor_upgrade_preserves_issued_invoice_and_original_fact(plane):
    import os

    from alembic.config import Config

    from alembic import command

    planes = (ModulePlane.TENANT, ModulePlane.PLATFORM)
    with billing._scratch_database(
        planes, target_revision="bi_0012_tax_evidence"
    ) as urls:
        admin, tenant, platform = urls
        scope = (
            TenantScope(billing._create_tenants(admin, 1)[0])
            if plane == "tenant"
            else PlatformScope()
        )
        url = tenant if plane == "tenant" else platform
        model = (
            billing.models.InvoiceDocumentFact
            if plane == "tenant"
            else billing.models.PlatformInvoiceDocumentFact
        )
        with billing._session(url, scope) as session:
            account = create_billing_account(
                session,
                scope=scope,
                external_account_ref=f"predecessor:{uuid4()}",
                currency="NGN",
                minor_units=2,
            )
            obligation = accept_rated_obligation(
                session,
                scope=scope,
                command=billing._obligation_command(
                    account.id, scope=scope, source_fact_id=str(uuid4())
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            draft = billing.create_draft_document(
                session, scope=scope, command=billing._draft_command(obligation.id)
            )
            issued = issue_document(
                session,
                scope=scope,
                command=billing._issue_command(draft.id),
                numbering=billing.NUMBERING,
            )
            identity = issued.id
            fact = session.execute(
                select(model).where(model.document_id == identity)
            ).scalar_one()
            original = (
                fact.presentation_model_digest,
                fact.fact_payload,
                issued.document_number,
                issued.grand_total,
            )
            session.commit()
        config = Config(str(billing.REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(billing.REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{billing.KERNEL_VERSIONS} {billing.ASSEMBLY_VERSIONS} "
            f"{billing.BILLING_VERSIONS}",
        )
        from dotmac_kernel.planes import ModulePlaneSelection

        config.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="billing", planes=planes),
        )
        assert os.environ["MIGRATION_DATABASE_URL"] == admin
        command.upgrade(config, "bi_0013_multi_period")
        with billing._session(url, scope) as session:
            fact = session.execute(
                select(model).where(model.document_id == identity)
            ).scalar_one()
            document_model = (
                billing.models.BillingDocument
                if plane == "tenant"
                else billing.models.PlatformBillingDocument
            )
            issued = session.get(document_model, identity)
            assert (
                fact.presentation_model_digest,
                fact.fact_payload,
                issued.document_number,
                issued.grand_total,
            ) == original
            assert issued.authoring_mode == "legacy_single"


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", ["mixed", "quantity"])
def test_explicit_one_time_line_has_no_invented_service_or_period(scratch, plane, kind):
    admin, tenant, platform = scratch
    scope = (
        TenantScope(billing._create_tenants(admin, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    with billing._session(tenant if plane == "tenant" else platform, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"one-time:{uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        sources = []
        if kind == "mixed":
            sources.append(
                billing._obligation_command(
                    account.id, scope=scope, source_fact_id=str(uuid4())
                )
            )
        sources.append(
            replace(
                billing._obligation_command(
                    account.id, scope=scope, source_fact_id=str(uuid4())
                ),
                service_ref=None,
                service_period=ServicePeriodEvidenceV1(
                    status=ServicePeriodStatus.NOT_APPLICABLE
                ),
            )
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=source,
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for source in sources
        )
        original = billing._multiline_command(account.id, obligations)
        command = CreateMultiPeriodDraftDocumentV1(
            **{field.name: getattr(original, field.name) for field in fields(original)}
        )
        draft = create_multi_period_draft_document_v1(
            session, scope=scope, command=command
        )
        issued = issue_document(
            session,
            scope=scope,
            command=billing._issue_command(draft.id),
            numbering=billing.NUMBERING,
        )
        facts = get_invoice_service_periods_v1(
            session,
            scope=scope,
            request=GetInvoiceServicePeriodsV1(scope=scope, invoice_id=issued.id),
        )
        assert len(facts.periods) == len(obligations)
        assert facts.periods[-1].service_ref is None
        assert facts.periods[-1].period.status is ServicePeriodStatus.NOT_APPLICABLE
        assert facts.periods[-1].period.starts_at is None
        assert facts.periods[-1].period.ends_at is None
        line_model = (
            billing.models.DocumentLine
            if plane == "tenant"
            else billing.models.PlatformDocumentLine
        )
        actual = (
            session.execute(
                select(line_model)
                .where(line_model.document_id == issued.id)
                .order_by(line_model.line_number)
            )
            .scalars()
            .all()
        )
        assert actual[-1].quantity == Decimal("2")
        assert actual[-1].unit_amount == Decimal("50")
        model = (
            billing.models.ReceivableExposureFact
            if plane == "tenant"
            else billing.models.PlatformReceivableExposureFact
        )
        exposure = session.execute(
            select(model).where(model.document_id == issued.id)
        ).scalar_one()
        assert exposure.service_ref is None
        assert exposure.service_period["status"] == "unknown_unverified"
        assert exposure.collectible_receivable == issued.grand_total
