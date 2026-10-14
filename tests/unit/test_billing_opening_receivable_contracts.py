"""Complete source evidence fixtures; trusted stubs are never production proof."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
from typing import Union, get_origin, get_type_hints
from uuid import uuid4

import pytest
from dotmac_billing._opening_values import _validate_closed
from dotmac_billing.contracts import (
    AppliedTaxSnapshotV1,
    DueDateBasisStatus,
    DueDateBasisV1,
    PartyDocumentSnapshotV1,
    PartyTaxIdentitySnapshotV1,
    PaymentInstructionsSnapshotV1,
    PostalAddressSnapshotV1,
    PresentationAssetReferenceV1,
    ServicePeriodEvidenceV1,
    ServicePeriodStatus,
)
from dotmac_billing.opening_contracts import (
    OpeningEvidenceError,
    OpeningPopulationCountsV1,
    OpeningSourceIdentityV1,
)
from dotmac_billing.opening_receivable_contracts import (
    ImportOpeningAccountV2,
    OpeningCollectionEvidenceV1,
    OpeningHistoricalDischargeV1,
    OpeningImportReversalVerificationV2,
    OpeningImportVerificationV2,
    OpeningManifestV2,
    OpeningOriginalDocumentV1,
    OpeningReceivableItemV1,
    OpeningReplayBindingV1,
)
from dotmac_billing.opening_receivable_validation import (
    opening_manifest_fingerprint_v2,
    opening_withholding_component_identity,
    preview_opening_account_import_v2,
)
from dotmac_kernel.money import Currency, Money

from tests.unit import test_billing_opening_manifest as cash

NOW = cash.NOW


def _money(minor):
    return Money(Decimal(minor).scaleb(-2), Currency("NGN", 2))


def _candidate():
    base = cash._candidate()
    partition = base.partition
    items, lots, population, dispositions = [], [], [], []
    for index, (gross, paid, withheld, residual, cash_residual) in enumerate(
        ((12000, 4000, 1000, 7000, 6000), (8000, 2000, 1000, 5000, 0))
    ):
        identity = OpeningSourceIdentityV1("invoice", f"invoice:{index}")
        payment_identity = OpeningSourceIdentityV1("payment", f"payment:{index}")
        original = OpeningOriginalDocumentV1(
            f"LEGACY-{index}",
            NOW,
            f"archive:invoice:{index}",
            "a" * 64,
            _money(gross - 1000),
            _money(1000),
            _money(gross),
            PartyDocumentSnapshotV1(
                "Original Seller", PostalAddressSnapshotV1("Seller street", "NGA")
            ),
            PartyDocumentSnapshotV1(
                "Original Customer", PostalAddressSnapshotV1("Customer street", "NGA")
            ),
            (
                PartyTaxIdentitySnapshotV1(
                    "seller", "tax_id", "historic-tax-id", "NGA", "party", "7"
                ),
            ),
            (
                AppliedTaxSnapshotV1(
                    "vat",
                    "NGA",
                    "historic-policy",
                    "7",
                    Decimal("0.2"),
                    _money(5000),
                    _money(1000),
                ),
            ),
            None,
            f"tax-document:{index}",
            "fx:not-applicable:NGN",
            "invoice.standard",
            "1",
            PaymentInstructionsSnapshotV1(
                method_code="bank_transfer",
                bank_name="Original bank",
                account_name="Original Seller",
                account_reference="documentary-account",
            ),
            PresentationAssetReferenceV1.none(),
            "en-NG",
            "Africa/Lagos",
        )
        discharges = tuple(
            OpeningHistoricalDischargeV1(
                f"{kind}:{index}",
                kind,
                amount,
                NOW,
                NOW,
                owner,
                key,
                operation,
                key,
                "mapping:1",
                f"ledger:{kind}:{index}",
                "b" * 64,
                f"tax:{kind}:{index}",
                (
                    AppliedTaxSnapshotV1(
                        "withholding",
                        "NGA",
                        "historic-wht-policy",
                        "7",
                        Decimal("0.2"),
                        _money(5000),
                        _money(amount),
                    ),
                )
                if kind == "withholding"
                else (),
                None,
                replay_bindings=(
                    OpeningReplayBindingV1(
                        "billing.withholding.accept", owner, key, "b" * 64
                    ),
                    OpeningReplayBindingV1(
                        "billing.withholding.tax_component",
                        "dotmac-tax",
                        opening_withholding_component_identity(
                            tax_source_owner="dotmac-tax",
                            determination_ref=f"tax-set:{index}",
                            component_ref=f"component:{index}",
                        ),
                        "b" * 64,
                        determination_ref=f"tax-set:{index}",
                        component_ref=f"component:{index}",
                    ),
                )
                if kind == "withholding"
                else (),
            )
            for kind, amount, owner, key, operation in (
                (
                    "payment",
                    paid,
                    "legacy-payments",
                    f"payment:{index}",
                    "billing.settlement.accept",
                ),
                (
                    "withholding",
                    withheld,
                    "customer-remittance",
                    f"wht:{index}",
                    "billing.withholding.accept",
                ),
            )
        )
        collection = OpeningCollectionEvidenceV1(
            "unknown_unverified",
            None,
            None,
            None,
            ServicePeriodEvidenceV1(ServicePeriodStatus.UNKNOWN_UNVERIFIED),
            None,
            DueDateBasisV1.unknown_unverified(
                source_authority="legacy", evidence_ref=f"due:unknown:{index}"
            ),
            "legacy",
            f"collection:{index}",
            "collection-mapping:1",
        )
        items.append(
            OpeningReceivableItemV1(
                identity,
                partition,
                f"invoice:{index}",
                original,
                discharges,
                residual,
                collection,
                "ar-mapping:1",
                "tax-mapping:1",
                "resolver:1",
                "c" * 64,
            )
        )
        template = base.manifest.cash_lots[0]
        lots.append(
            replace(
                template,
                lot_id=f"lot:{index}",
                source_identity=payment_identity,
                source_system="legacy-payments",
                source_settlement_key=f"payment:{index}",
                gross_minor=paid + cash_residual,
                residual_minor=cash_residual,
                confirmation=replace(
                    template.confirmation, cash_amount_minor=paid + cash_residual
                ),
                consumptions=(
                    replace(
                        template.consumptions[0],
                        reference=f"payment:{index}",
                        amount_minor=paid,
                        target_ref=f"invoice:{index}",
                    ),
                ),
            )
        )
        population.extend((identity, payment_identity))
        dispositions.extend(
            (
                replace(
                    base.manifest.dispositions[0],
                    identity=identity,
                    residual_minor=residual,
                    reason="open invoice",
                ),
                replace(
                    base.manifest.dispositions[0],
                    identity=payment_identity,
                    residual_minor=cash_residual,
                ),
            )
        )
    manifest = OpeningManifestV2(
        base.manifest.scope,
        base.manifest.capture,
        tuple(population),
        tuple(dispositions),
        OpeningPopulationCountsV1(4, 4, 0, 0),
        (
            replace(
                base.manifest.controls[0],
                collectible_receivable_minor=12000,
                open_ar_minor=12000,
                available_cash_credit_minor=6000,
            ),
        ),
        tuple(lots),
        tuple(items),
    )
    return ImportOpeningAccountV2(
        base.manifest.scope,
        manifest,
        partition,
        base.target,
        "migration-authority:qualified",
        str(uuid4()),
        NOW,
    )


class TrustedVerifier:
    """Qualifies injected test evidence only; no production source attestation."""

    def __init__(self, mutate=None):
        self.mutate = mutate
        self.calls = []

    def verify_import_v2(self, request):
        self.calls.append(request)
        cmd = request.command
        result = OpeningImportVerificationV2(
            "billing.opening.import.v2",
            cmd.scope,
            request.command_digest,
            request.manifest_fingerprint,
            cmd.partition,
            cmd.target,
            cmd.manifest.capture.installation_id,
            cmd.migration_authority_ref,
            cmd.manifest.capture.source_authority_ref,
            "source-proof:qualified",
            "cash-proof:qualified",
            "ar-proof:qualified",
            "tax-mapping-proof:qualified",
            "discharge-mapping-proof:qualified",
            "migration-proof:qualified",
        )
        return self.mutate(result) if self.mutate else result

    def verify_import_reversal_v2(self, request):
        cmd = request.command
        value = OpeningImportReversalVerificationV2(
            "billing.opening.import.reverse.v2",
            cmd.scope,
            request.command_digest,
            cmd.receipt_id,
            request.receipt_evidence_digest,
            request.original_posting_group_id,
            request.billing_account_id,
            request.currency,
            request.precision,
            cmd.migration_authority_ref,
            "fresh-migration-reversal-proof:qualified",
        )
        return self.mutate(value) if self.mutate else value


def test_complete_multiinvoice_and_cash_controls_are_exact():
    cmd = _candidate()
    result = preview_opening_account_import_v2(cmd, verifier=TrustedVerifier())
    assert result.admissible, result.blockers
    assert result.receivable_residual_minor == 12000
    assert result.cash_residual_minor == 6000
    assert result.blocker_count == 0
    for field in (
        "open_ar_minor",
        "collectible_receivable_minor",
        "available_cash_credit_minor",
    ):
        control = cmd.manifest.controls[0]
        wrong = replace(control, **{field: getattr(control, field) + 1})
        changed = replace(cmd, manifest=replace(cmd.manifest, controls=(wrong,)))
        assert not preview_opening_account_import_v2(
            changed, verifier=TrustedVerifier()
        ).admissible


def test_unknown_collection_evidence_retains_financial_ar_without_invention():
    cmd = _candidate()
    assert preview_opening_account_import_v2(cmd, verifier=TrustedVerifier()).admissible
    for item in cmd.manifest.receivables:
        assert not item.collection.automated_collection_allowed
        assert item.collection.subject_ref is item.collection.service_ref is None
        assert item.collection.due_at is None
        assert item.collection.service_period.starts_at is None


@pytest.mark.parametrize(
    "field,value",
    [("original", None), ("residual_minor", 7001), ("historical_discharges", ())],
)
def test_missing_document_or_discharge_equation_is_counted(field, value):
    cmd = _candidate()
    first, second = cmd.manifest.receivables
    changed = replace(
        cmd,
        manifest=replace(
            cmd.manifest, receivables=(replace(first, **{field: value}), second)
        ),
    )
    result = preview_opening_account_import_v2(changed, verifier=TrustedVerifier())
    assert not result.admissible
    assert result.blocker_count > 0


def test_documentary_tax_and_exact_payment_mapping_are_required():
    cmd = _candidate()
    first, second = cmd.manifest.receivables
    variants = (
        replace(first, original=replace(first.original, tax_snapshots=())),
        replace(
            first,
            historical_discharges=(
                replace(first.historical_discharges[0], source_key="unmapped-payment"),
                first.historical_discharges[1],
            ),
        ),
    )
    for item in variants:
        result = preview_opening_account_import_v2(
            replace(cmd, manifest=replace(cmd.manifest, receivables=(item, second))),
            verifier=TrustedVerifier(),
        )
        assert not result.admissible
        assert result.blocker_count > 0


def test_documentary_provenance_and_order_change_digest_at_equal_balance():
    cmd = _candidate()
    first, second = cmd.manifest.receivables
    baseline = opening_manifest_fingerprint_v2(cmd.manifest)
    for item in (
        replace(first, historical_discharges=first.historical_discharges[::-1]),
        replace(first, original=replace(first.original, documentary_digest="d" * 64)),
        replace(first, tax_mapping_version="tax-mapping:2"),
        replace(
            first,
            collection=replace(
                first.collection, evidence_ref="different-unknown-evidence"
            ),
        ),
    ):
        changed = replace(cmd.manifest, receivables=(item, second))
        assert opening_manifest_fingerprint_v2(changed) != baseline
    assert opening_manifest_fingerprint_v2(cmd.manifest) == baseline


def test_closed_immutable_evidence_refuses_coercion():
    item = _candidate().manifest.receivables[0]
    with pytest.raises(FrozenInstanceError):
        item.residual_minor = 0
    for amount in (True, 1.0, "7000", Decimal("7000")):
        with pytest.raises(OpeningEvidenceError):
            replace(item, residual_minor=amount)
    with pytest.raises(OpeningEvidenceError):
        replace(item, historical_discharges=list(item.historical_discharges))


@pytest.mark.parametrize("field,value", [("residual_minor", -1), ("residual_minor", 0)])
def test_negative_and_closed_residue_are_not_clamped_into_open_items(field, value):
    cmd = _candidate()
    first, second = cmd.manifest.receivables
    changed = replace(
        cmd,
        manifest=replace(
            cmd.manifest, receivables=(replace(first, **{field: value}), second)
        ),
    )
    result = preview_opening_account_import_v2(changed, verifier=TrustedVerifier())
    assert not result.admissible
    assert result.blocker_count > 0


@pytest.mark.parametrize(
    "kind,operations",
    [
        ("credit_note", ("billing.credit_note.issue",)),
        (
            "non_cash_credit",
            ("billing.non_cash_credit.grant", "billing.non_cash_credit.apply"),
        ),
        (
            "withholding",
            ("billing.withholding.accept", "billing.withholding.tax_component"),
        ),
    ],
)
def test_supported_historical_origins_require_all_exact_replay_mappings(
    kind, operations
):
    cmd = _candidate()
    first, second = cmd.manifest.receivables
    payment, prior = first.historical_discharges
    bindings = tuple(
        OpeningReplayBindingV1(
            operation,
            "dotmac-billing"
            if operation == "billing.credit_note.issue"
            else "historic-owner",
            opening_withholding_component_identity(
                tax_source_owner="historic-owner",
                determination_ref="historical-determination",
                component_ref="historical-component",
            )
            if operation == "billing.withholding.tax_component"
            else f"source:{operation}",
            "d" * 64,
            determination_ref="historical-determination"
            if operation == "billing.withholding.tax_component"
            else None,
            component_ref="historical-component"
            if operation == "billing.withholding.tax_component"
            else None,
        )
        for operation in operations
    )
    historic = replace(prior, kind=kind, replay_bindings=bindings)
    changed = replace(
        cmd,
        manifest=replace(
            cmd.manifest,
            receivables=(
                replace(first, historical_discharges=(payment, historic)),
                second,
            ),
        ),
    )
    positive = preview_opening_account_import_v2(changed, verifier=TrustedVerifier())
    assert positive.admissible, positive.blockers
    for index in range(len(bindings)):
        incomplete = replace(
            historic, replay_bindings=bindings[:index] + bindings[index + 1 :]
        )
        bad = replace(
            changed,
            manifest=replace(
                changed.manifest,
                receivables=(
                    replace(first, historical_discharges=(payment, incomplete)),
                    second,
                ),
            ),
        )
        result = preview_opening_account_import_v2(bad, verifier=TrustedVerifier())
        assert not result.admissible
        assert "historical_replay_mapping_incomplete" in {
            block.code for block in result.blockers
        }


def test_shared_payment_is_one_cash_source_with_two_exact_invoice_components():
    cmd = _candidate()
    first, second = cmd.manifest.receivables
    first_lot, second_lot = cmd.manifest.cash_lots
    second_payment, second_wht = second.historical_discharges
    second_payment = replace(
        second_payment,
        source_system=first_lot.source_system,
        source_key=first_lot.source_settlement_key,
        live_identity=first_lot.source_settlement_key,
    )
    second = replace(second, historical_discharges=(second_payment, second_wht))
    combined = replace(
        first_lot,
        gross_minor=12000,
        confirmation=replace(first_lot.confirmation, cash_amount_minor=12000),
        consumptions=first_lot.consumptions + second_lot.consumptions,
    )
    population = tuple(
        value
        for value in cmd.manifest.population
        if value != second_lot.source_identity
    )
    dispositions = tuple(
        value
        for value in cmd.manifest.dispositions
        if value.identity != second_lot.source_identity
    )
    manifest = replace(
        cmd.manifest,
        cash_lots=(combined,),
        receivables=(first, second),
        population=population,
        dispositions=dispositions,
        counts=OpeningPopulationCountsV1(3, 3, 0, 0),
    )
    positive = preview_opening_account_import_v2(
        replace(cmd, manifest=manifest), verifier=TrustedVerifier()
    )
    assert positive.admissible, positive.blockers
    # A matching aggregate without the second exact target component is insufficient.
    unmatched = replace(
        combined, consumptions=(replace(combined.consumptions[0], amount_minor=6000),)
    )
    result = preview_opening_account_import_v2(
        replace(cmd, manifest=replace(manifest, cash_lots=(unmatched,))),
        verifier=TrustedVerifier(),
    )
    assert not result.admissible
    assert "historical_cash_source_unclaimed" in {
        block.code for block in result.blockers
    }


def test_linked_historical_refund_is_documentary_and_overinverse_is_refused():
    cmd = _candidate()
    first, second = cmd.manifest.receivables
    payment, wht = first.historical_discharges
    original_credit = replace(
        wht,
        reference="historic-credit",
        kind="credit_note",
        amount_minor=2000,
        source_system="dotmac-billing",
        source_key="original-credit",
        live_identity="original-credit",
        replay_bindings=(
            OpeningReplayBindingV1(
                "billing.credit_note.issue",
                "dotmac-billing",
                "original-credit",
                "e" * 64,
            ),
        ),
    )
    refund = replace(
        original_credit,
        reference="credit-reversal",
        kind="reversal",
        amount_minor=-2000,
        reverses_ref=original_credit.reference,
        replay_bindings=(),
    )
    manifest = replace(
        cmd.manifest,
        receivables=(
            replace(
                first, historical_discharges=(payment, wht, original_credit, refund)
            ),
            second,
        ),
    )
    positive = preview_opening_account_import_v2(
        replace(cmd, manifest=manifest), verifier=TrustedVerifier()
    )
    assert positive.admissible, positive.blockers
    for inverse in (
        replace(refund, reverses_ref="unresolved"),
        replace(refund, amount_minor=-2001),
        replace(refund, source_key="other-credit"),
    ):
        changed = replace(
            manifest,
            receivables=(
                replace(
                    first,
                    historical_discharges=(payment, wht, original_credit, inverse),
                ),
                second,
            ),
        )
        assert not preview_opening_account_import_v2(
            replace(cmd, manifest=changed), verifier=TrustedVerifier()
        ).admissible


def test_document_currency_precision_dates_and_tax_are_never_filled_from_target():
    cmd = _candidate()
    first, second = cmd.manifest.receivables
    original = first.original
    for documentary in (
        replace(original, grand_total=Money.of("120", Currency("USD", 2))),
        replace(original, subtotal=Money.of("110", Currency("NGN", 3))),
        replace(original, grand_total=_money(11999)),
        replace(original, subtotal=_money(-11000)),
        replace(original, issued_at=NOW + cash.timedelta(days=1)),
    ):
        changed = replace(
            cmd,
            manifest=replace(
                cmd.manifest, receivables=(replace(first, original=documentary), second)
            ),
        )
        result = preview_opening_account_import_v2(changed, verifier=TrustedVerifier())
        assert not result.admissible
        assert result.blocker_count > 0
    for field in ("issued_at", "fx_evidence_ref", "tax_evidence_ref"):
        with pytest.raises(OpeningEvidenceError):
            replace(first.original, **{field: None})


def test_complete_census_dispositions_and_duplicate_invoice_content_do_not_disappear():
    cmd = _candidate()
    first, second = cmd.manifest.receivables
    for manifest in (
        replace(cmd.manifest, dispositions=cmd.manifest.dispositions[:-1]),
        replace(cmd.manifest, counts=replace(cmd.manifest.counts, population=3)),
        replace(
            cmd.manifest,
            receivables=(
                first,
                replace(
                    second,
                    source_invoice_key=first.source_invoice_key,
                    original=replace(second.original, documentary_digest="f" * 64),
                ),
            ),
        ),
        replace(cmd.manifest, receivables=(first,)),
    ):
        result = preview_opening_account_import_v2(
            replace(cmd, manifest=manifest), verifier=TrustedVerifier()
        )
        assert not result.admissible
        assert result.blocker_count > 0


def test_tax_bearing_historical_discharge_cannot_omit_its_documentary_tax_result():
    cmd = _candidate()
    first, second = cmd.manifest.receivables
    payment, wht = first.historical_discharges
    assert wht.tax_snapshots
    missing_tax = replace(wht, tax_snapshots=())
    changed = replace(
        cmd,
        manifest=replace(
            cmd.manifest,
            receivables=(
                replace(first, historical_discharges=(payment, missing_tax)),
                second,
            ),
        ),
    )
    result = preview_opening_account_import_v2(changed, verifier=TrustedVerifier())
    assert not result.admissible
    assert result.blocker_count > 0


def test_same_payment_reference_in_another_currency_partition_cannot_back_ar():
    cmd = _candidate()
    assert preview_opening_account_import_v2(cmd, verifier=TrustedVerifier()).admissible
    original_lot, second_lot = cmd.manifest.cash_lots
    foreign_partition = replace(cmd.partition, currency="USD")
    foreign_lot = replace(
        original_lot,
        partition=foreign_partition,
        confirmation=replace(original_lot.confirmation, currency="USD"),
    )
    dispositions = tuple(
        replace(value, partition=foreign_partition)
        if value.identity == original_lot.source_identity
        else value
        for value in cmd.manifest.dispositions
    )
    local_control = replace(cmd.manifest.controls[0], available_cash_credit_minor=0)
    foreign_control = replace(
        local_control,
        partition=foreign_partition,
        collectible_receivable_minor=0,
        open_ar_minor=0,
        available_cash_credit_minor=6000,
    )
    changed = replace(
        cmd,
        manifest=replace(
            cmd.manifest,
            cash_lots=(foreign_lot, second_lot),
            dispositions=dispositions,
            controls=(local_control, foreign_control),
        ),
    )
    result = preview_opening_account_import_v2(changed, verifier=TrustedVerifier())
    assert not result.admissible
    assert "historical_cash_source_unclaimed" in {item.code for item in result.blockers}


def test_collection_timing_optional_literal_keeps_none_and_valid_members_closed():
    unknown = _candidate().manifest.receivables[0].collection
    assert unknown.collection_timing is None
    assert not unknown.automated_collection_allowed
    basis = DueDateBasisV1(
        status=DueDateBasisStatus.VERIFIED,
        source_authority="contract.v1",
        evidence_ref="contract:line:1:v1",
        payment_terms_code="net_30",
        payment_terms_version="1",
        issued_at=NOW,
        effective_at=NOW,
        timezone="Africa/Lagos",
        derivation_policy="calendar_days",
        derivation_version="1",
    )
    for timing in ("advance", "arrears"):
        verified = replace(
            unknown,
            status="verified",
            subject_ref="customer:1",
            service_ref="service:1",
            collection_timing=timing,
            due_at=NOW,
            due_date_basis=basis,
            service_period=ServicePeriodEvidenceV1(
                ServicePeriodStatus.VERIFIED, NOW - timedelta(days=30), NOW
            ),
        )
        assert verified.collection_timing == timing
        assert verified.automated_collection_allowed
        for malformed in ("unknown", "", True, 1, object()):
            with pytest.raises(OpeningEvidenceError):
                replace(verified, collection_timing=malformed)
    original = _candidate().manifest.receivables[0].original
    # Reused party DTOs are recursively closed at the real outer boundary.
    with pytest.raises(OpeningEvidenceError):
        replace(original, seller=replace(original.seller, registered_identifiers=[]))


def test_closed_union_validation_covers_both_python_union_representations():
    literal_optional = get_type_hints(OpeningCollectionEvidenceV1)["collection_timing"]
    assert get_origin(literal_optional) is Union
    for value in (None, "advance", "arrears"):
        _validate_closed(value, literal_optional)
    for value in ("unknown", "", True, 1, object()):
        with pytest.raises(OpeningEvidenceError):
            _validate_closed(value, literal_optional)
    for value in (None, 0, 1):
        _validate_closed(value, int | None)
    for value in (True, False, 1.0, "1", object()):
        with pytest.raises(OpeningEvidenceError):
            _validate_closed(value, int | None)


def test_real_v2_preflight_is_consumed_by_rehearsal_report():
    from dotmac_billing.opening_rehearsal import report_opening_rehearsal

    from tests.unit.test_billing_opening_rehearsal import _run

    run = _run(verifier=False)
    preview = preview_opening_account_import_v2(run.commands[0])
    report = report_opening_rehearsal(replace(run, previews=(preview,)))
    assert preview.blockers
    assert "verifier_unavailable" in {x.code for x in report.blockers}
    assert not report.complete_exact_reconciliation
    assert not report.activation_ready
