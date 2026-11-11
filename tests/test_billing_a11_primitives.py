"""Hosted PostgreSQL proofs for the Billing a11 primitives (ST-2).

Numbering document identity, discount snapshots, write-off as policy data, the
per-document exposure and withholding queries, the ordered accounting-fact
query, and refusal of mixed-service collectible documents.
"""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_billing import (
    AcceptRatedObligationV3,
    GetInvoiceDocumentFactV1,
    ListAccountingFactsV1,
    SummarizeAccountingFactWindowV1,
    get_document_exposure,
    get_document_withholding_evidence,
    get_invoice_document_fact_v3,
    list_accounting_facts,
    summarize_accounting_fact_window,
)
from dotmac_billing import service as svc
from dotmac_billing.commands import (
    AllocationCommand,
    DraftDocumentLine,
    GrantNonCashCredit,
    IssueCreditNote,
)
from dotmac_billing.contracts import (
    DiscountSnapshotV1,
    NonCashCreditClassificationV1,
    NonCashCreditPolicyParameterV1,
    ReceivableExposureV1,
)
from dotmac_billing.document_queries import (
    AccountingFactCursorV1,
    fact_index_digest_seed,
    fact_index_line,
)
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.service import (
    accept_rated_obligation,
    accept_settlement,
    accept_withholding_discharge,
    allocate_settlement,
    create_billing_account,
    create_multi_line_draft_document,
    grant_non_cash_credit,
    issue_credit_note,
    issue_document,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.planes import ModulePlane

from tests import test_billing_isolation as b
from tests import test_billing_public_invoice_authoring as authoring
from tests import test_billing_tax_dependency_evidence as dep
from tests.billing_frozen_fixtures import (
    withholding_command as frozen_withholding_command,
)

CONFIRMED = frozenset({"provider_settled"})


@pytest.fixture(scope="module")
def scratch():
    with b._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _routing(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(b._create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    return scope, tenant_url if plane == "tenant" else platform_url


class Recording(b._Numbering):
    def __init__(self, *, fail=False):
        self.references: list[str] = []
        self.keys: list[str] = []
        self.fail = fail

    def allocate(self, db, **kwargs):
        self.references.append(kwargs["document_reference"])
        self.keys.append(kwargs["idempotency_key"])
        if self.fail:
            raise ValueError("numbering unavailable")
        return super().allocate(db, **kwargs)


def _credit(invoice_id, *, amount="20", correlation=None):
    return IssueCreditNote(
        original_document_id=invoice_id,
        pre_tax_amount=b._money(amount),
        tax_amount=b._money("0"),
        total_amount=b._money(amount),
        reason="service correction",
        series_code="credit",
        reference_date=b.NOW.date(),
        actor_ref="billing-test",
        correlation_id=correlation or f"credit:{invoice_id}",
        occurred_at=b.NOW,
        document_profile_code="credit.standard",
        document_profile_version="1",
    )


# --- (7) numbering document identity -------------------------------------


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_numbering_gets_a_stable_document_reference_for_invoice_and_credit(
    scratch, plane
):
    scope, url = _routing(scratch, plane)
    with b._session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        obligation = accept_rated_obligation(
            session,
            scope=scope,
            command=b._obligation_command(account.id, scope=scope, source_fact_id="n1"),
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
        draft = b.create_draft_document(
            session, scope=scope, command=b._draft_command(obligation.id)
        )
        numbering = Recording()
        issued = issue_document(
            session,
            scope=scope,
            command=b._issue_command(draft.id),
            numbering=numbering,
        )
        assert numbering.references == [f"billing:{draft.id}"]
        # Exact replay never re-allocates.
        issue_document(
            session,
            scope=scope,
            command=b._issue_command(draft.id),
            numbering=numbering,
        )
        assert len(numbering.references) == 1

        command = _credit(issued.id)
        failing = Recording(fail=True)
        with pytest.raises(ValueError, match="unavailable"), session.begin_nested():
            issue_credit_note(session, scope=scope, command=command, numbering=failing)
        retry = Recording()
        credit = issue_credit_note(
            session, scope=scope, command=command, numbering=retry
        )
        # The failed attempt had its own throw-away document id; the identity
        # handed to Numbering is the command-stable one on every attempt.
        assert failing.references == retry.references
        assert retry.references == [f"billing:credit:{command.correlation_id}"]
        assert credit.document_number is not None
        issue_credit_note(session, scope=scope, command=command, numbering=retry)
        assert len(retry.references) == 1


# --- (1) credit-note receivable bounds, pinned ---------------------------


def test_credit_over_receivable_and_unissued_target_stay_refused(scratch):
    scope, url = _routing(scratch, "tenant")
    with b._session(url, scope) as session:
        _, _, invoice = b._seed_invoice(session, scope, suffix=str(uuid4()))
        with pytest.raises(BillingRuleViolation, match="open receivable"):
            issue_credit_note(
                session,
                scope=scope,
                command=_credit(invoice.id, amount="120.01"),
                numbering=b.NUMBERING,
            )
        # A credit always needs an issued invoice to link to; there is no
        # unlinked credit-note path to carry tax.
        account_id = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        ).id
        obligation = accept_rated_obligation(
            session,
            scope=scope,
            command=b._obligation_command(account_id, scope=scope, source_fact_id="d1"),
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
        draft = b.create_draft_document(
            session, scope=scope, command=b._draft_command(obligation.id)
        )
        with pytest.raises(BillingRuleViolation, match="issued invoice"):
            issue_credit_note(
                session,
                scope=scope,
                command=_credit(draft.id),
                numbering=b.NUMBERING,
            )


# --- (2) discounts -------------------------------------------------------


def _discount(code="loyalty", amount="20.00"):
    return DiscountSnapshotV1(code, "Loyalty discount", b._money(amount), "policy:1")


def _discounted(account_id, scope, *discounts):
    command = dep._command(account_id, scope, ())
    return replace(
        command,
        correlation_id=str(uuid4()),
        obligation=replace(
            command.obligation,
            contract_line_ref=str(uuid4()),
            source_fact_id=str(uuid4()),
            discounts=tuple(discounts),
        ),
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_discount_snapshot_is_issued_into_the_fact_and_replays_exactly(scratch, plane):
    scope, url = _routing(scratch, plane)
    with b._session(url, scope) as session:
        account = dep._account(session, scope)
        command = _discounted(account.id, scope, _discount(), _discount("promo", "5"))
        issued = authoring._issue(session, scope, command)
        stored = get_invoice_document_fact_v3(
            session,
            scope=scope,
            request=GetInvoiceDocumentFactV1(scope, issued.invoice_id, 1),
        )
        line = stored.fact.lines[0]
        # pre_tax is the NET taxable base; gross = unit price before discounts.
        assert line.discount_total == b._money("25")
        assert line.discounts == command.obligation.discounts
        assert line.pre_tax_amount == b._money("100")
        assert line.unit_amount == b._money("125")
        assert stored.fact.subtotal == b._money("100")
        assert stored.fact.grand_total == b._money("100")
        # Exact replay returns the original result; changed discounts conflict.
        assert authoring._issue(session, scope, command) == issued
        changed = replace(
            command,
            obligation=replace(
                command.obligation, discounts=(_discount(), _discount("promo", "6"))
            ),
        )
        with pytest.raises(IdempotencyConflict):
            authoring._issue(session, scope, changed)


def test_undiscounted_v3_keeps_zero_discount_and_a10_fingerprint(scratch):
    scope, url = _routing(scratch, "tenant")
    with b._session(url, scope) as session:
        account = dep._account(session, scope)
        command = _discounted(account.id, scope)
        payload = svc._rated_fingerprint_payload(command)
        assert "discounts" not in payload and "discounts" not in payload["obligation"]
        with_discount = _discounted(account.id, scope, _discount())
        assert (
            "discounts" in svc._rated_fingerprint_payload(with_discount)["obligation"]
        )
        issued = authoring._issue(session, scope, command)
        fact = get_invoice_document_fact_v3(
            session,
            scope=scope,
            request=GetInvoiceDocumentFactV1(scope, issued.invoice_id, 1),
        ).fact
        assert fact.lines[0].discount_total == b._money("0")
        assert fact.lines[0].discounts == ()
        assert fact.lines[0].unit_amount == fact.lines[0].pre_tax_amount


@pytest.mark.parametrize(
    "fault",
    ["zero", "negative", "duplicate", "blank_code", "currency"],
)
def test_malformed_discounts_are_refused_before_any_write(fault):
    scope = TenantScope(uuid4())
    command = _discounted(uuid4(), scope, _discount()).obligation
    bad = {
        "zero": (_discount(amount="0"),),
        "negative": (_discount(amount="-1"),),
        "duplicate": (_discount(), _discount()),
        "blank_code": (replace(_discount(), discount_code=" "),),
        "currency": (replace(_discount(), amount=b._money("5", "USD")),),
    }[fault]
    with pytest.raises(BillingRuleViolation) as raised:
        svc._require_rated_discounts(replace(command, discounts=bad))
    assert raised.value.code in {
        "billing.discount_invalid",
        "billing.discount_duplicate",
        "billing.mixed_currency",
    }


def test_tax_determined_on_the_undiscounted_gross_is_refused():
    scope = TenantScope(uuid4())
    evidence = dep._evidence(scope)
    base = dep._command(uuid4(), scope, evidence).obligation
    net_base = evidence[0].determination.net_amount.amount
    ok = replace(
        base,
        pre_tax_amount=b._money(str(net_base)),
        discounts=(_discount(),),
    )
    svc._require_rated_discounts(ok)
    # The discount lowered the charge below the base Tax used: the tax was
    # determined on the gross, so the discount did not reduce the taxable base.
    gross_taxed = replace(ok, pre_tax_amount=b._money(str(net_base - Decimal("20"))))
    with pytest.raises(BillingRuleViolation) as raised:
        svc._require_rated_discounts(gross_taxed)
    assert raised.value.code == "billing.discount_base_not_reduced"
    assert isinstance(ok, AcceptRatedObligationV3)


def test_explicit_line_price_includes_the_discounts():
    payload = [
        {
            "contract_version": "billing.tax.evidence.v2",
            "commercial_sources": [],
            "evidence": [],
            "discounts": [
                {
                    "discount_code": "loyalty",
                    "description": "Loyalty",
                    "amount": {"amount": "20.00", "currency": "NGN", "minor_units": 2},
                    "source_version": "policy:1",
                }
            ],
        }
    ]
    obligation = SimpleNamespace(
        tax_evidence=payload,
        currency="NGN",
        minor_units=2,
        pre_tax_amount=Decimal("100.00"),
        total_amount=Decimal("100.00"),
    )
    line = DraftDocumentLine(uuid4(), "Access", Decimal("2"), "month", b._money("60"))
    svc._require_line_input(line, obligation)  # 2 x 60 = 100 net + 20 discount
    with pytest.raises(BillingRuleViolation, match="plus its discounts"):
        svc._require_line_input(replace(line, unit_amount=b._money("50")), obligation)


# --- (3) write-off is a policy classification, not a command -------------


def _write_off(scope, account_id, invoice_id, *, amount="120"):
    return GrantNonCashCredit(
        scope,
        account_id,
        b._money(amount),
        NonCashCreditClassificationV1(
            "write_off",
            "finance-policy",
            "policy:write-off",
            "1",
            "c" * 64,
            (NonCashCreditPolicyParameterV1("route", "grant"),),
        ),
        str(uuid4()),
        "finance",
        str(uuid4()),
        "v1",
        "finance:approver",
        "decision:1",
        b.NOW,
        restricted_document_id=invoice_id,
        apply_on_issue=True,
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_write_off_is_a_policy_classified_grant_applied_on_issue(scratch, plane):
    scope, url = _routing(scratch, plane)
    with b._session(url, scope) as session:
        account, _, invoice = b._seed_invoice(session, scope, suffix=str(uuid4()))
        before = get_document_exposure(session, scope=scope, document_id=invoice.id)
        assert before.collectible_receivable == b._money("120")
        command = _write_off(scope, account.id, invoice.id)
        grant = grant_non_cash_credit(session, scope=scope, command=command)
        assert grant.classification.category == "write_off"
        assert grant_non_cash_credit(session, scope=scope, command=command) == grant
        after = get_document_exposure(session, scope=scope, document_id=invoice.id)
        assert after.collectible_receivable == b._money("0")
        assert after.financial_state == "resolved"
        assert after.source_version > before.source_version
        with pytest.raises((BillingConflict, BillingRuleViolation)):
            grant_non_cash_credit(
                session,
                scope=scope,
                command=replace(command, amount=b._money("119"), idempotency_key="x"),
            )


# --- (4) per-document exposure and withholding evidence ------------------


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_document_exposure_tracks_settlement_and_is_scope_isolated(scratch, plane):
    scope, url = _routing(scratch, plane)
    with b._session(url, scope) as session:
        account, _, invoice = b._seed_invoice(session, scope, suffix=str(uuid4()))
        opened = get_document_exposure(session, scope=scope, document_id=invoice.id)
        assert isinstance(opened, ReceivableExposureV1)
        assert opened.exposure_ref == f"document:{invoice.id}"
        assert opened.service_ref == "service:example"
        assert opened.financial_state == "open"
        assert opened.collectible_receivable == b._money("120")
        settlement = accept_settlement(
            session,
            scope=scope,
            command=b._settlement_command(
                account.id, scope=scope, source_key=str(uuid4())
            ),
            accepted_confirmation_evidence=CONFIRMED,
        )
        allocate_settlement(
            session,
            scope=scope,
            command=AllocationCommand(
                settlement.id, invoice.id, b._money("50"), b.NOW, str(uuid4())
            ),
        )
        partial = get_document_exposure(session, scope=scope, document_id=invoice.id)
        assert partial.financial_state == "partially_resolved"
        assert partial.collectible_receivable == b._money("70")
        assert partial.source_version > opened.source_version
        assert (
            get_document_exposure(session, scope=scope, document_id=invoice.id)
            == partial
        )
        with pytest.raises(BillingRuleViolation) as missing:
            get_document_exposure(session, scope=scope, document_id=uuid4())
        assert missing.value.code == "billing.not_found"
    if plane == "tenant":
        other = TenantScope(b._create_tenants(scratch[0], 1)[0])
        with b._session(url, other) as session:
            with pytest.raises(BillingRuleViolation) as hidden:
                get_document_exposure(session, scope=other, document_id=invoice.id)
            assert hidden.value.code == "billing.not_found"


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_document_withholding_evidence_lists_only_that_document(scratch, plane):
    scope, url = _routing(scratch, plane)
    with b._session(url, scope) as session:
        account, _, invoice = b._seed_invoice(session, scope, suffix=str(uuid4()))
        _, _, other = b._seed_invoice(session, scope, suffix=str(uuid4()))
        empty = get_document_withholding_evidence(
            session, scope=scope, document_id=invoice.id
        )
        assert empty.effects == () and empty.net_accepted == b._money("0")
        accept_withholding_discharge(
            session,
            scope=scope,
            command=frozen_withholding_command(
                session,
                account.id,
                invoice.id,
                scope=scope,
                suffix=str(uuid4()),
                accepted="90",
                claimed="90",
            ),
        )
        evidence = get_document_withholding_evidence(
            session, scope=scope, document_id=invoice.id
        )
        assert len(evidence.effects) == 1
        assert evidence.effects[0].document_id == invoice.id
        assert evidence.net_accepted == b._money("90")
        untouched = get_document_withholding_evidence(
            session, scope=scope, document_id=other.id
        )
        assert untouched.effects == ()
        with pytest.raises(BillingRuleViolation) as missing:
            get_document_withholding_evidence(session, scope=scope, document_id=uuid4())
        assert missing.value.code == "billing.not_found"


# --- (5) ordered accounting-fact query -----------------------------------


def _walk(session, scope, start, end, limit):
    """Page the whole window; return every entry in page order."""
    seen, cursor = [], None
    while True:
        page = list_accounting_facts(
            session,
            scope=scope,
            request=ListAccountingFactsV1(scope, start, end, cursor, limit),
        )
        seen.extend(page.entries)
        if page.next_cursor is None:
            return seen
        cursor = page.next_cursor


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_ordered_fact_query_is_total_complete_and_digest_checkable(scratch, plane):
    scope, url = _routing(scratch, plane)
    start, end = b.NOW.replace(hour=0), b.NOW.replace(hour=23)
    with b._session(url, scope) as session:
        account, _, invoice = b._seed_invoice(session, scope, suffix=str(uuid4()))
        settlement = accept_settlement(
            session,
            scope=scope,
            command=b._settlement_command(
                account.id, scope=scope, source_key=str(uuid4())
            ),
            accepted_confirmation_evidence=CONFIRMED,
        )
        allocate_settlement(
            session,
            scope=scope,
            command=AllocationCommand(
                settlement.id, invoice.id, b._money("50"), b.NOW, str(uuid4())
            ),
        )
        session.commit()
    with b._session(url, scope) as session:
        summary = summarize_accounting_fact_window(
            session,
            scope=scope,
            request=SummarizeAccountingFactWindowV1(scope, start, end),
        )
        whole = _walk(session, scope, start, end, 1000)
        assert summary.count >= 3 and len(whole) == summary.count
        if plane == "tenant":
            assert summary.count == 3
        # Paging one at a time visits the same total order with no gap or repeat.
        assert _walk(session, scope, start, end, 1) == whole
        assert _walk(session, scope, start, end, 2) == whole
        keys = [
            (
                e.cursor.occurred_at,
                str(e.cursor.billing_account_id),
                e.cursor.source_version,
                e.cursor.fact_version,
                str(e.cursor.fact_id),
            )
            for e in whole
        ]
        assert keys == sorted(keys)
        # A consumer proves completeness with its own running chain.
        chain = fact_index_digest_seed()
        for entry in whole:
            chain.update(
                fact_index_line(
                    entry.cursor.fact_id, entry.cursor.fact_version, entry.fact_digest
                )
            )
        assert chain.hexdigest() == summary.digest
        again = summarize_accounting_fact_window(
            session,
            scope=scope,
            request=SummarizeAccountingFactWindowV1(scope, start, end),
        )
        assert again == summary
        later = summarize_accounting_fact_window(
            session,
            scope=scope,
            request=SummarizeAccountingFactWindowV1(
                scope, b.NOW.replace(year=2030), b.NOW.replace(year=2031)
            ),
        )
        assert later.count == 0
        for bad in (
            ListAccountingFactsV1(scope, end, start),
            ListAccountingFactsV1(scope, start.replace(tzinfo=None), end),
            ListAccountingFactsV1(scope, start, end, None, 0),
            ListAccountingFactsV1(scope, start, end, None, 1001),
            ListAccountingFactsV1(TenantScope(uuid4()), start, end),
        ):
            with pytest.raises(BillingRuleViolation):
                list_accounting_facts(session, scope=scope, request=bad)
        with pytest.raises(BillingRuleViolation):
            summarize_accounting_fact_window(
                session,
                scope=scope,
                request=SummarizeAccountingFactWindowV1(scope, end, start),
            )
    assert isinstance(AccountingFactCursorV1, type)


def test_ordered_fact_query_does_not_leak_across_tenants(scratch):
    scope, url = _routing(scratch, "tenant")
    start, end = b.NOW.replace(hour=0), b.NOW.replace(hour=23)
    with b._session(url, scope) as session:
        b._seed_invoice(session, scope, suffix=str(uuid4()))
    other = TenantScope(b._create_tenants(scratch[0], 1)[0])
    with b._session(url, other) as session:
        page = list_accounting_facts(
            session, scope=other, request=ListAccountingFactsV1(other, start, end)
        )
        assert page.entries == ()


# --- (6) one service per collectible document ----------------------------


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_mixed_service_ref_multiline_document_is_refused(scratch, plane):
    scope, url = _routing(scratch, plane)
    with b._session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        rows = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=replace(
                    b._obligation_command(
                        account.id, scope=scope, source_fact_id=str(uuid4())
                    ),
                    service_ref=service,
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for service in ("service:a", "service:b")
        )
        with pytest.raises(BillingRuleViolation) as raised:
            create_multi_line_draft_document(
                session, scope=scope, command=b._multiline_command(account.id, rows)
            )
        assert raised.value.code == "billing.exposure_service_ambiguous"
        # A missing service is also a different service, never "matches anything".
        none_row = accept_rated_obligation(
            session,
            scope=scope,
            command=replace(
                b._obligation_command(
                    account.id, scope=scope, source_fact_id=str(uuid4())
                ),
                service_ref=None,
            ),
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
        with pytest.raises(BillingRuleViolation) as raised:
            create_multi_line_draft_document(
                session,
                scope=scope,
                command=b._multiline_command(account.id, (rows[0], none_row)),
            )
        assert raised.value.code == "billing.exposure_service_ambiguous"
