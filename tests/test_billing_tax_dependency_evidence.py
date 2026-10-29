"""Hosted PostgreSQL canaries for dependency-aware Tax evidence (Billing a10)."""

# SQL identifiers below are fixed Billing-owned table names.
# ruff: noqa: S608
from dataclasses import fields, replace
from uuid import uuid4

import pytest
from dotmac_billing import (
    AcceptRatedObligationV3,
    GetInvoiceDocumentFactV1,
    InvoiceTaxEvidenceV2,
    IssueRatedInvoiceV3,
    TaxBaseDependencyPolicyEvidenceV1,
    TaxCommercialSourceEvidenceV1,
    TaxDependencyComponentEvidenceV1,
    TaxDeterminationComponentEvidenceV2,
    TaxDeterminationSetEvidenceV2,
    TaxFactSignatureEvidenceV1,
    TaxPriorResultReferenceEvidenceV1,
    WithholdingTaxEvidenceV2,
    WithholdingTaxLineV1,
    get_invoice_document_fact,
    get_invoice_document_fact_v2,
    get_invoice_document_fact_v3,
    get_withholding_discharge,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.service import (
    accept_withholding_discharge,
    create_billing_account,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests import test_billing_isolation as billing
from tests import test_billing_public_invoice_authoring as authoring
from tests import test_billing_tax_determination_evidence as predecessor

_money = predecessor._money


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _v2(old):
    result = old.determination
    return TaxDeterminationSetEvidenceV2(
        **{
            field.name: getattr(result, field.name)
            for field in fields(result)
            if field.name not in {"components", "result_fingerprint"}
        },
        result_fingerprint="rv2:" + "b" * 64,
        commercial_source=TaxCommercialSourceEvidenceV1("commercial:original", "1"),
        prior_results=(),
        components=tuple(
            TaxDeterminationComponentEvidenceV2(
                **{
                    field.name: getattr(component, field.name)
                    for field in fields(component)
                }
            )
            for component in result.components
        ),
    )


def _evidence(scope):
    vat = _v2(predecessor._evidence(scope))
    raw = _v2(predecessor._evidence(scope, taxes=("10",)))
    component = raw.components[0]
    component = replace(
        component,
        calculation_base_code="source_plus_dependency_tax",
        base_amount=_money("107.50"),
        lines=(replace(component.lines[0], taxable_amount=_money("107.50")),),
        selected_dependency_policy=TaxBaseDependencyPolicyEvidenceV1(
            TaxFactSignatureEvidenceV1(
                vat.jurisdiction_id,
                vat.fact_kind,
                vat.recognition_basis_code,
                vat.transaction_side,
            ),
            "all_prior",
        ),
        dependency_components=(
            TaxDependencyComponentEvidenceV1(
                vat.determination_set_id,
                vat.components[0].determination_id,
                vat.components[0].tax_code_id,
                vat.components[0].rule_id,
                vat.components[0].rule_version,
                vat.components[0].component_sequence,
                vat.components[0].tax_amount,
            ),
        ),
    )
    wht = replace(
        raw,
        transaction_side="withholding",
        components=(component,),
        prior_results=(
            TaxPriorResultReferenceEvidenceV1(
                vat.determination_set_id,
                vat.source_ref,
                vat.source_version,
                vat.source_fingerprint,
                vat.result_fingerprint,
            ),
        ),
    )
    return (
        InvoiceTaxEvidenceV2("invoice_tax", vat),
        InvoiceTaxEvidenceV2("expected_withholding", wht, (vat,)),
    )


def _command(account_id, scope, evidence):
    old = predecessor._command(account_id, scope, evidence)
    return IssueRatedInvoiceV3(
        **{
            field.name: getattr(old, field.name)
            for field in fields(old)
            if field.name != "obligation"
        },
        obligation=AcceptRatedObligationV3(
            **{
                field.name: getattr(old.obligation, field.name)
                for field in fields(old.obligation)
                if field.name != "tax_evidence"
            },
            tax_evidence=evidence,
            commercial_sources=tuple(
                dict.fromkeys(item.determination.commercial_source for item in evidence)
            ),
        ),
    )


def _account(session, scope):
    return create_billing_account(
        session,
        scope=scope,
        external_account_ref=str(uuid4()),
        currency="NGN",
        minor_units=2,
    )


def test_dependency_evidence_cross_tenant_canary(scratch):
    admin, tenant_url, _ = scratch
    left, right = billing._create_tenants(admin)
    scope = TenantScope(left)
    with billing._session(tenant_url, scope) as session:
        account = _account(session, scope)
        with pytest.raises(BillingRuleViolation, match="scope"):
            authoring._issue(
                session,
                scope,
                _command(account.id, scope, _evidence(TenantScope(right))),
            )


def test_dependency_roundtrip_retry_and_no_double_count(scratch):
    admin, tenant_url, _ = scratch
    left, right = billing._create_tenants(admin)
    scope = TenantScope(left)
    evidence = _evidence(scope)
    with billing._session(tenant_url, scope) as session:
        account = _account(session, scope)
        command = _command(account.id, scope, evidence)
        issued = authoring._issue(session, scope, command)
        session.commit()
    with billing._session(tenant_url, scope) as session:
        request = GetInvoiceDocumentFactV1(scope, issued.invoice_id, 1)
        stored = get_invoice_document_fact_v3(session, scope=scope, request=request)
        assert stored.contract_version == "billing.invoice.document.fact.v3"
        assert stored.fact.tax_evidence == evidence
        assert stored.fact.commercial_sources == command.obligation.commercial_sources
        assert stored.fact.tax_total == _money("7.50")
        assert stored.fact.expected_withholding == _money("10")
        assert stored.fact.grand_total == _money("107.50")
        assert authoring._issue(session, scope, command) == issued
        for reader in (get_invoice_document_fact, get_invoice_document_fact_v2):
            with pytest.raises(BillingRuleViolation, match="unsupported"):
                reader(session, scope=scope, request=request)
        changed = replace(
            evidence[1],
            determination=replace(
                evidence[1].determination, result_fingerprint="rv2:" + "c" * 64
            ),
        )
        with pytest.raises(IdempotencyConflict):
            authoring._issue(
                session,
                scope,
                replace(
                    command,
                    obligation=replace(
                        command.obligation, tax_evidence=(evidence[0], changed)
                    ),
                ),
            )
    other = TenantScope(right)
    with billing._session(tenant_url, other) as session:
        with pytest.raises(BillingRuleViolation, match="unavailable"):
            get_invoice_document_fact_v3(
                session,
                scope=other,
                request=GetInvoiceDocumentFactV1(other, issued.invoice_id, 1),
            )
    with billing._session(admin, scope) as session:
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(
                text(
                    "UPDATE mod_billing.invoice_document_facts "
                    "SET fact_payload='{}' WHERE id=:id"
                ),
                {"id": stored.fact_id},
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_empty_v3_and_legacy_original_facts_keep_version(scratch, plane):
    admin, tenant_url, platform_url = scratch
    scope = (
        TenantScope(billing._create_tenants(admin, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    with billing._session(
        tenant_url if plane == "tenant" else platform_url, scope
    ) as session:
        account = _account(session, scope)
        old = authoring._command(account.id, scope)
        legacy = authoring._issue(session, scope, old)
        request = GetInvoiceDocumentFactV1(scope, legacy.invoice_id, 1)
        assert get_invoice_document_fact_v3(
            session, scope=scope, request=request
        ) == get_invoice_document_fact(session, scope=scope, request=request)
        command = _command(account.id, scope, ())
        # Give the separate original obligation an independent commercial identity.
        command = replace(
            command,
            correlation_id=str(uuid4()),
            obligation=replace(
                command.obligation,
                contract_line_ref=str(uuid4()),
                source_fact_id=str(uuid4()),
            ),
        )
        issued = authoring._issue(session, scope, command)
        stored = get_invoice_document_fact_v3(
            session,
            scope=scope,
            request=GetInvoiceDocumentFactV1(scope, issued.invoice_id, 1),
        )
        assert stored.contract_version == "billing.invoice.document.fact.v3"
        assert stored.fact.tax_evidence == ()
        if plane == "tenant":
            old_v2 = predecessor._command(
                account.id, scope, (predecessor._evidence(scope),)
            )
            old_v2 = replace(
                old_v2,
                correlation_id=str(uuid4()),
                obligation=replace(
                    old_v2.obligation,
                    contract_line_ref=str(uuid4()),
                    source_fact_id=str(uuid4()),
                ),
            )
            issued_v2 = authoring._issue(session, scope, old_v2)
            request = GetInvoiceDocumentFactV1(scope, issued_v2.invoice_id, 1)
            assert get_invoice_document_fact_v3(
                session, scope=scope, request=request
            ) == get_invoice_document_fact_v2(session, scope=scope, request=request)


def test_platform_refuses_nonempty_tenant_tax_v2(scratch):
    _, _, platform_url = scratch
    scope = PlatformScope()
    with billing._session(platform_url, scope) as session:
        account = _account(session, scope)
        with pytest.raises(BillingRuleViolation, match="scope"):
            authoring._issue(
                session,
                scope,
                _command(account.id, scope, _evidence(TenantScope(uuid4()))),
            )


@pytest.mark.parametrize(
    "change", ["tenant", "commercial", "signature", "fingerprint", "snapshot", "base"]
)
def test_dependency_bindings_refuse_tampering(change):
    vat, wht = _evidence(TenantScope(uuid4()))
    result = wht.determination
    component = result.components[0]
    with pytest.raises(ValueError):
        if change == "tenant":
            result = replace(result, tenant_id=uuid4())
        elif change == "commercial":
            result = replace(
                result, commercial_source=TaxCommercialSourceEvidenceV1("another", "1")
            )
        elif change == "signature":
            component = replace(
                component,
                selected_dependency_policy=replace(
                    component.selected_dependency_policy,
                    prior_signature=replace(
                        component.selected_dependency_policy.prior_signature,
                        fact_kind="unrelated",
                    ),
                ),
            )
            result = replace(result, components=(component,))
        elif change == "fingerprint":
            result = replace(
                result,
                prior_results=(
                    replace(
                        result.prior_results[0], result_fingerprint="rv2:" + "c" * 64
                    ),
                ),
            )
        elif change == "snapshot":
            component = replace(
                component,
                dependency_components=(
                    replace(component.dependency_components[0], rule_id=uuid4()),
                ),
            )
            result = replace(result, components=(component,))
        else:
            result = replace(
                result, components=(replace(component, base_amount=_money("108")),)
            )
        replace(wht, determination=result)


def _discharge_evidence(scope, account_id, invoice_id, item):
    result = item.determination
    component = result.components[0]
    return WithholdingTaxEvidenceV2(
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
                line.sequence, line.taxable_amount, line.rate, line.tax_amount
            )
            for line in component.lines
        ),
        scope=scope,
        billing_account_id=account_id,
        document_id=invoice_id,
        recognition_basis_code=result.recognition_basis_code,
        fact_kind=result.fact_kind,
        transaction_side=result.transaction_side,
        determined_at=result.determined_at,
        determination=result,
        prior_determinations=item.prior_determinations,
    )


def test_withholding_discharge_retains_complete_dependency_lineage(scratch):
    admin, tenant_url, _ = scratch
    scope = TenantScope(billing._create_tenants(admin, 1)[0])
    evidence = _evidence(scope)
    with billing._session(tenant_url, scope) as session:
        account = _account(session, scope)
        issued = authoring._issue(session, scope, _command(account.id, scope, evidence))
        tax = _discharge_evidence(scope, account.id, issued.invoice_id, evidence[1])
        old = billing._withholding_command(
            account.id, issued.invoice_id, scope=scope, suffix=str(uuid4())
        )
        command = replace(
            old,
            tax_evidence=tax,
            claimed_amount=_money("10"),
            accepted_amount=_money("10"),
        )
        discharge = accept_withholding_discharge(session, scope=scope, command=command)
        discharge_id = discharge.id
        session.commit()
    with billing._session(tenant_url, scope) as session:
        stored = get_withholding_discharge(
            session, scope=scope, discharge_id=discharge_id
        )
        assert stored.tax_evidence == tax
        assert stored.accepted_delta == _money("10")
        assert (
            accept_withholding_discharge(session, scope=scope, command=command).id
            == discharge_id
        )
        with pytest.raises(ValueError):
            replace(tax, source_fingerprint="a" * 63 + "c")


def test_commercial_source_identity_is_exact():
    source = TaxCommercialSourceEvidenceV1("invoice:1", "1")
    with pytest.raises(ValueError):
        replace(source, source_ref=" invoice:1")


@pytest.mark.parametrize(
    "change", ["prefix", "currency", "missing_prior", "extra_prior"]
)
def test_dependency_wire_shape_and_complete_lineage_refuse_tampering(change):
    vat, wht = _evidence(TenantScope(uuid4()))
    with pytest.raises(ValueError):
        if change == "prefix":
            replace(wht.determination, result_fingerprint="rv1:" + "b" * 64)
        elif change == "currency":
            from dotmac_kernel.money import Currency, Money

            component = wht.determination.components[0]
            replace(
                component,
                dependency_components=(
                    replace(
                        component.dependency_components[0],
                        tax_amount=Money(
                            component.dependency_components[0].tax_amount.amount,
                            Currency("USD", 2),
                        ),
                    ),
                ),
            )
        elif change == "missing_prior":
            replace(wht, prior_determinations=())
        else:
            unrelated = _evidence(TenantScope(uuid4()))[0].determination
            replace(wht, prior_determinations=(vat.determination, unrelated))


@pytest.mark.parametrize("missing", [False, True])
def test_declared_commercial_source_is_refused_before_write(scratch, missing):
    admin, tenant_url, _ = scratch
    scope = TenantScope(billing._create_tenants(admin, 1)[0])
    with billing._session(tenant_url, scope) as session:
        account = _account(session, scope)
        command = _command(account.id, scope, _evidence(scope))
        changed_source = (
            () if missing else (TaxCommercialSourceEvidenceV1("unrelated", "2"),)
        )
        with pytest.raises(ValueError, match="commercial source"):
            replace(command.obligation, commercial_sources=changed_source)
        # The public owner also revalidates caller objects before accepting rows.
        forged = replace(command.obligation)
        object.__setattr__(forged, "commercial_sources", changed_source)
        with pytest.raises(BillingRuleViolation, match="commercial source"):
            authoring._issue(session, scope, replace(command, obligation=forged))
        assert (
            session.execute(
                text(
                    "SELECT count(*) FROM mod_billing.rated_obligations "
                    "WHERE billing_account_id=:account"
                ),
                {"account": account.id},
            ).scalar_one()
            == 0
        )


def test_changed_declared_commercial_source_conflicts_on_retry(scratch):
    admin, tenant_url, _ = scratch
    scope = TenantScope(billing._create_tenants(admin, 1)[0])
    vat, wht = _evidence(scope)
    with billing._session(tenant_url, scope) as session:
        account = _account(session, scope)
        command = _command(account.id, scope, (vat, wht))
        issued = authoring._issue(session, scope, command)
        session.commit()
        other = TaxCommercialSourceEvidenceV1("another-commercial-source", "2")
        prior = replace(vat.determination, commercial_source=other)
        changed_evidence = (
            replace(vat, determination=prior),
            replace(
                wht,
                determination=replace(wht.determination, commercial_source=other),
                prior_determinations=(prior,),
            ),
        )
        changed = replace(
            command,
            obligation=replace(
                command.obligation,
                commercial_sources=(other,),
                tax_evidence=changed_evidence,
            ),
        )
        with pytest.raises(IdempotencyConflict):
            authoring._issue(session, scope, changed)
        original = get_invoice_document_fact_v3(
            session,
            scope=scope,
            request=GetInvoiceDocumentFactV1(scope, issued.invoice_id, 1),
        )
        assert original.fact.commercial_sources == command.obligation.commercial_sources


def _line_evidence(scope, *, factor, source_ref):
    """Different original line amounts keep separate commercial source identities."""
    vat, wht = _evidence(scope)
    source = TaxCommercialSourceEvidenceV1(source_ref, "1")

    def money(value):
        return type(value)(value.amount * factor, value.currency)

    def component(value):
        return replace(
            value,
            base_amount=money(value.base_amount),
            tax_amount=money(value.tax_amount),
            recoverable_amount=money(value.recoverable_amount),
            non_recoverable_amount=money(value.non_recoverable_amount),
            lines=tuple(
                replace(
                    line,
                    taxable_amount=money(line.taxable_amount),
                    tax_amount=money(line.tax_amount),
                )
                for line in value.lines
            ),
            dependency_components=tuple(
                replace(item, tax_amount=money(item.tax_amount))
                for item in value.dependency_components
            ),
        )

    def result(value):
        return replace(
            value,
            commercial_source=source,
            source_amount=money(value.source_amount),
            net_amount=money(value.net_amount),
            tax_amount=money(value.tax_amount),
            gross_amount=money(value.gross_amount),
            components=tuple(component(item) for item in value.components),
        )

    prior = result(vat.determination)
    return (
        replace(vat, determination=prior),
        replace(
            wht, determination=result(wht.determination), prior_determinations=(prior,)
        ),
    )


@pytest.mark.parametrize("collapsed", [False, True])
def test_multiline_commercial_sources_bind_unequal_original_amounts(scratch, collapsed):
    from decimal import Decimal

    from dotmac_billing.service import (
        accept_rated_obligation,
        create_multi_line_draft_document,
        issue_document,
    )

    admin, tenant_url, _ = scratch
    scope = TenantScope(billing._create_tenants(admin, 1)[0])
    first = _line_evidence(scope, factor=Decimal("1"), source_ref="original:line:one")
    second = _line_evidence(
        scope,
        factor=Decimal("2.5"),
        source_ref="original:line:one" if collapsed else "original:line:two",
    )
    with billing._session(tenant_url, scope) as session:
        account = _account(session, scope)
        commands = tuple(
            replace(
                _command(account.id, scope, evidence).obligation,
                pre_tax_amount=evidence[0].determination.source_amount,
                total_amount=_money(
                    str(
                        evidence[0].determination.source_amount.amount
                        + evidence[0].determination.tax_amount.amount
                    )
                ),
            )
            for evidence in (first, second)
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=command,
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for command in commands
        )
        draft_command = billing._multiline_command(account.id, obligations)
        draft_command = replace(
            draft_command,
            lines=tuple(
                replace(line, quantity=Decimal("1"), unit_amount=command.pre_tax_amount)
                for line, command in zip(draft_command.lines, commands, strict=True)
            ),
        )
        draft = create_multi_line_draft_document(
            session, scope=scope, command=draft_command
        )
        draft_id = draft.id
        session.commit()
        if collapsed:
            with pytest.raises(BillingRuleViolation, match="different original facts"):
                issue_document(
                    session,
                    scope=scope,
                    command=billing._issue_command(draft_id),
                    numbering=billing.NUMBERING,
                )
            # Acceptance and drafting succeeded separately. Failed issuance must
            # retain the draft and publish neither an original fact nor postings.
            assert (
                session.execute(
                    text("SELECT lifecycle FROM mod_billing.documents WHERE id=:id"),
                    {"id": draft_id},
                ).scalar_one()
                == "draft"
            )
            assert (
                session.execute(
                    text(
                        "SELECT count(*) FROM mod_billing.invoice_document_facts "
                        "WHERE document_id=:id"
                    ),
                    {"id": draft_id},
                ).scalar_one()
                == 0
            )
            assert (
                session.execute(
                    text(
                        "SELECT count(*) FROM mod_billing.posting_groups "
                        "WHERE source_ref=:source"
                    ),
                    {"source": f"document:{draft_id}:issue"},
                ).scalar_one()
                == 0
            )
            return
        issued = issue_document(
            session,
            scope=scope,
            command=billing._issue_command(draft_id),
            numbering=billing.NUMBERING,
        )
        stored = get_invoice_document_fact_v3(
            session, scope=scope, request=GetInvoiceDocumentFactV1(scope, issued.id, 1)
        )
        assert stored.fact.commercial_sources == tuple(
            command.commercial_sources[0] for command in commands
        )
        assert stored.fact.tax_evidence == (*first, *second)
        assert tuple(line.pre_tax_amount for line in stored.fact.lines) == (
            _money("100"),
            _money("250"),
        )
        assert stored.fact.subtotal == _money("350")
        assert stored.fact.tax_total == _money("26.25")
        assert stored.fact.expected_withholding == _money("35")


@pytest.mark.parametrize("fault", ["missing", "extra", "duplicate"])
def test_declared_commercial_sources_are_an_exact_unique_set(fault):
    from decimal import Decimal

    scope = TenantScope(uuid4())
    first = _line_evidence(scope, factor=Decimal("1"), source_ref="line:one")
    second = _line_evidence(scope, factor=Decimal("2"), source_ref="line:two")
    command = _command(uuid4(), scope, (*first, *second))
    sources = command.obligation.commercial_sources
    invalid = (
        sources[:1]
        if fault == "missing"
        else (*sources, sources[0])
        if fault == "duplicate"
        else (*sources, TaxCommercialSourceEvidenceV1("surplus", "1"))
    )
    with pytest.raises(ValueError, match="commercial sources"):
        replace(command.obligation, commercial_sources=invalid)


@pytest.mark.parametrize("successor", ["set", "component"])
def test_standalone_legacy_acceptance_refuses_successor_evidence_before_insert(
    scratch, successor
):
    from dotmac_billing.service import accept_rated_obligation

    admin, tenant_url, _ = scratch
    scope = TenantScope(billing._create_tenants(admin, 1)[0])
    with billing._session(tenant_url, scope) as session:
        account = _account(session, scope)
        legacy = predecessor._evidence(scope)
        if successor == "set":
            with pytest.raises(ValueError):
                replace(legacy, determination=_evidence(scope)[0].determination)
            object.__setattr__(
                legacy, "determination", _evidence(scope)[0].determination
            )
        else:
            result = legacy.determination
            component = TaxDeterminationComponentEvidenceV2(
                **{
                    field.name: getattr(result.components[0], field.name)
                    for field in fields(result.components[0])
                }
            )
            with pytest.raises(ValueError):
                replace(result, components=(component,))
            object.__setattr__(result, "components", (component,))
        command = predecessor._command(account.id, scope, (legacy,)).obligation
        with pytest.raises(BillingRuleViolation, match="typed"):
            accept_rated_obligation(
                session,
                scope=scope,
                command=command,
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
        assert (
            session.execute(
                text(
                    "SELECT count(*) FROM mod_billing.rated_obligations "
                    "WHERE billing_account_id=:account"
                ),
                {"account": account.id},
            ).scalar_one()
            == 0
        )


def test_unequal_original_line_amounts_cannot_collapse_onto_one_commercial_identity():
    from decimal import Decimal

    scope = TenantScope(uuid4())
    first = _line_evidence(scope, factor=Decimal("1"), source_ref="collapsed:line")
    second = _line_evidence(scope, factor=Decimal("2"), source_ref="collapsed:line")
    with pytest.raises(ValueError, match="different original facts"):
        _command(uuid4(), scope, (*first, *second))
