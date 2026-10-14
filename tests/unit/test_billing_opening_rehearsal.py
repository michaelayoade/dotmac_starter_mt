"""Candidate reports consume genuine V2 preflight outputs, never cash guesses."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from dotmac_billing.opening_contracts import (
    OpeningBlockerV1,
    OpeningEvidenceError,
    OpeningPopulationCountsV1,
    OpeningVerificationUnavailable,
)
from dotmac_billing.opening_receivable_validation import (
    preview_opening_account_import_v2,
)
from dotmac_billing.opening_rehearsal import (
    compare_opening_rehearsals,
    report_opening_rehearsal,
)
from dotmac_billing.opening_rehearsal_contracts import (
    OpeningObservedControlV1,
    OpeningObservedInvoiceV1,
    OpeningObservedLaneV1,
    OpeningObservedPopulationV1,
    OpeningObservedPrecisionV1,
    OpeningObservedSettlementV1,
    OpeningRehearsalBindingV1,
    OpeningRehearsalDischargeMappingV1,
    OpeningRehearsalInvoiceMappingV1,
    OpeningRehearsalMappingV1,
    OpeningRehearsalRunV1,
    OpeningSubObservationV2,
)

from tests.unit import test_billing_opening_receivable_contracts as ar

CASH_TABLES = (
    "payments",
    "payment_settlements",
    "payment_provider_events",
    "integration_inbox",
    "payment_refunds",
    "payment_reversals",
    "payment_proofs",
    "payment_proof_corrections",
    "topup_intents",
)


def _run(index=0, *, verifier=True):
    command = ar._candidate()
    # Cash-free AR retains documentary totals and exact historic WHT ownership.
    # Remove payment discharge and transfer that amount to outstanding AR.
    items = tuple(
        replace(
            item,
            historical_discharges=tuple(
                x for x in item.historical_discharges if x.kind != "payment"
            ),
            residual_minor=item.residual_minor
            + sum(
                x.amount_minor
                for x in item.historical_discharges
                if x.kind == "payment"
            ),
        )
        for item in command.manifest.receivables
    )
    total = sum(x.residual_minor for x in items)
    identities = tuple(x.source_identity for x in items)
    dispositions = tuple(
        replace(
            x,
            residual_minor=next(
                i.residual_minor for i in items if i.source_identity == x.identity
            ),
        )
        for x in command.manifest.dispositions
        if x.identity in identities
    )
    capture = replace(
        command.manifest.capture,
        installation_id=str(UUID(int=50)),
        snapshot_id=f"snapshot:{index}",
        economic_cutoff=ar.NOW + timedelta(days=index),
        recorded_cutoff=ar.NOW + timedelta(days=index),
        captured_at=ar.NOW + timedelta(days=index),
    )
    manifest = replace(
        command.manifest,
        capture=capture,
        receivables=items,
        cash_lots=(),
        population=identities,
        dispositions=dispositions,
        counts=OpeningPopulationCountsV1(2, 2, 0, 0),
        controls=(
            replace(
                command.manifest.controls[0],
                available_cash_credit_minor=0,
                collectible_receivable_minor=total,
                open_ar_minor=total,
            ),
        ),
    )
    command = replace(command, manifest=manifest)
    preview = preview_opening_account_import_v2(
        command, verifier=ar.TrustedVerifier() if verifier else None
    )
    source = OpeningSubObservationV2(
        capture.source_repository_sha,
        UUID(capture.installation_id),
        capture.schema_heads,
        capture.snapshot_id,
        capture.captured_at,
        capture.economic_cutoff,
        "repeatable read",
        capture.population_version,
        True,
        (OpeningObservedPrecisionV1("NGN", 2),),
        "actual-export-content:fixture",
        (UUID(int=1), UUID(int=2)),
        (UUID(int=1), UUID(int=2)),
        (
            OpeningObservedControlV1(
                UUID(int=10),
                "NGN",
                2,
                2,
                0,
                0,
                Decimal("200"),
                Decimal("20"),
                Decimal("180"),
                Decimal(total).scaleb(-2),
                0,
            ),
        ),
        (
            OpeningObservedLaneV1(
                UUID(int=10),
                "NGN",
                "actual:lane-fingerprint",
                "authoritative",
                UUID(int=500),
                0,
                Decimal(total).scaleb(-2),
                *(Decimal("0"),) * 6,
            ),
        ),
        tuple(OpeningObservedPopulationV1(table, 0, ()) for table in CASH_TABLES),
        (),
        (),
    )
    # Actual V2 has credit-note settlements, not a WHT settlement kind. The
    # intentionally incompatible WHT manifest must remain blocked.
    source = replace(
        source,
        invoices=tuple(
            OpeningObservedInvoiceV1(
                UUID(int=i + 1),
                UUID(int=10),
                "NGN",
                2,
                item.original.document_number,
                item.original.issued_at,
                item.original.grand_total.amount,
                Decimal("10"),
                Decimal(item.residual_minor).scaleb(-2),
                (UUID(int=1000 + i),),
            )
            for i, item in enumerate(items)
        ),
        settlements=tuple(
            OpeningObservedSettlementV1(
                "credit_note",
                UUID(int=1000 + i),
                UUID(int=i + 1),
                UUID(int=2000 + i),
                Decimal("10"),
                True,
            )
            for i in range(2)
        ),
    )
    return OpeningRehearsalRunV1(
        source,
        manifest,
        (OpeningRehearsalMappingV1(UUID(int=10), command.partition),),
        (command,),
        (preview,),
        OpeningRehearsalBindingV1(
            UUID(int=100), "artifact:actual-fixture", "policy:actual-fixture"
        ),
        invoice_mappings=tuple(
            OpeningRehearsalInvoiceMappingV1(
                UUID(int=i + 1), item.source_identity, item.source_invoice_key
            )
            for i, item in enumerate(items)
        ),
        discharge_mappings=tuple(
            OpeningRehearsalDischargeMappingV1(
                "credit_note",
                UUID(int=1000 + i),
                f"withholding:{i}",
                UUID(int=2000 + i),
            )
            for i in range(2)
        ),
    )


def _empty_run(index=0):
    run = _run(index)
    manifest = replace(
        run.manifest,
        population=(),
        dispositions=(),
        counts=OpeningPopulationCountsV1(0, 0, 0, 0),
        controls=(),
        receivables=(),
    )
    source = replace(
        run.source,
        invoice_ids=(),
        eligible_invoice_ids=(),
        controls=(),
        lanes=(),
        invoices=(),
        settlements=(),
    )
    return replace(
        run,
        source=source,
        manifest=manifest,
        mappings=(),
        commands=(),
        previews=(),
        invoice_mappings=(),
        discharge_mappings=(),
    )


def _codes(run):
    return {x.code for x in report_opening_rehearsal(run).blockers}


def test_three_complete_exact_runs_and_no_activation_authority():
    runs = tuple(_empty_run(i) for i in range(3))
    for run in runs:
        report = report_opening_rehearsal(run)
        assert report.complete_exact_reconciliation
        assert report.blockers == ()
        assert report.deltas == ()
        assert all(x.unclassified_delta_minor == 0 for x in report.deltas)
        assert not report.activation_ready
    result = compare_opening_rehearsals(runs)
    assert result.candidate_evidence_complete
    assert not result.activation_ready


def test_missing_verifier_remains_blocked():
    assert "verifier_unavailable" in _codes(_run(verifier=False))


def test_real_verifier_unavailable_remains_blocked():
    class Unavailable:
        def verify_import_v2(self, request):
            raise OpeningVerificationUnavailable("not installed")

    run = _run()
    preview = preview_opening_account_import_v2(run.commands[0], verifier=Unavailable())
    assert "verification_unavailable" in _codes(replace(run, previews=(preview,)))


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda r: replace(r, previews=()), "partition_population_mismatch"),
        (lambda r: replace(r, previews=r.previews * 2), "duplicate_evidence"),
        (lambda r: replace(r, mappings=()), "partition_population_mismatch"),
        (
            lambda r: replace(r, source=replace(r.source, population_complete=False)),
            "source_population_incomplete",
        ),
        (
            lambda r: replace(
                r, source=replace(r.source, controls=r.source.controls * 2)
            ),
            "duplicate_evidence",
        ),
        (
            lambda r: replace(
                r, source=replace(r.source, invoice_ids=r.source.invoice_ids * 2)
            ),
            "duplicate_evidence",
        ),
        (
            lambda r: replace(r, source=replace(r.source, cash_populations=())),
            "source_population_incomplete",
        ),
        (
            lambda r: replace(
                r,
                source=replace(
                    r.source,
                    currency_precisions=(OpeningObservedPrecisionV1("NGN", 3),),
                ),
            ),
            "currency_precision_mismatch",
        ),
        (
            lambda r: replace(
                r,
                source=replace(
                    r.source, controls=(replace(r.source.controls[0], currency="USD"),)
                ),
            ),
            "partition_population_mismatch",
        ),
        (
            lambda r: replace(
                r,
                source=replace(
                    r.source,
                    controls=(replace(r.source.controls[0], account_id=UUID(int=999)),),
                ),
            ),
            "partition_population_mismatch",
        ),
        (
            lambda r: replace(
                r, previews=(replace(r.previews[0], manifest_fingerprint="other"),)
            ),
            "preview_binding_mismatch",
        ),
        (
            lambda r: replace(
                r,
                previews=(
                    replace(
                        r.previews[0],
                        target=replace(r.previews[0].target, posting_watermark="other"),
                    ),
                ),
            ),
            "preview_binding_mismatch",
        ),
        (
            lambda r: replace(
                r, previews=(replace(r.previews[0], receivable_residual_minor=1),)
            ),
            "preview_residual_binding_mismatch",
        ),
    ],
)
def test_population_and_binding_fail_closed(mutation, code):
    assert code in _codes(mutation(_run()))


@pytest.mark.parametrize(
    "amount", [Decimal("180.01"), Decimal("179.99"), Decimal("180.001"), Decimal("NaN")]
)
def test_exact_minor_delta_and_precision_refuse_even_with_manual_hold(amount):
    run = _run()
    run = replace(
        run,
        source=replace(
            run.source,
            controls=(replace(run.source.controls[0], eligible_residual=amount),),
        ),
        accepted_manual_holds=("reviewed:manual-collection",),
    )
    report = report_opening_rehearsal(run)
    assert "unclassified_money_delta" in {x.code for x in report.blockers}
    assert not report.complete_exact_reconciliation
    assert report.accepted_manual_holds == ("reviewed:manual-collection",)


def test_unqualified_cash_is_not_a_source_residual():
    run = _run()
    population = replace(
        run.source.cash_populations[0], count=1, identities=(UUID(int=7),)
    )
    run = replace(
        run,
        source=replace(
            run.source, cash_populations=(population, *run.source.cash_populations[1:])
        ),
    )
    report = report_opening_rehearsal(run)
    assert "cash_residual_evidence_unavailable" in {x.code for x in report.blockers}
    cash = next(x for x in report.deltas if x.lane == "cash")
    assert cash.source_minor is None and cash.unclassified_delta_minor is None


def test_external_blocker_and_unsupported_lane_survive():
    run = _run()
    lane = replace(run.source.lanes[0], unapplied_customer_credit=Decimal("1"))
    run = replace(
        run,
        source=replace(
            run.source,
            lanes=(lane,),
            blockers=(OpeningBlockerV1("source_exception", 1, ("source:1",)),),
        ),
    )
    assert {"source_exception", "unsupported_lane"}.issubset(_codes(run))


def test_three_copies_cannot_be_three_successive_reconciliations():
    runs = tuple(_empty_run(i) for i in range(3))
    assert compare_opening_rehearsals(runs).candidate_evidence_complete
    run = runs[0]
    result = compare_opening_rehearsals((run, run, run))
    assert not result.candidate_evidence_complete
    assert "duplicate_evidence_capture" in {x.code for x in result.blockers}


def test_snapshot_repeatability_ignores_validated_capture_clock_only():
    run = _empty_run()
    later_time = run.source.captured_at + timedelta(hours=1)
    later = replace(
        run,
        source=replace(run.source, captured_at=later_time),
        manifest=replace(
            run.manifest, capture=replace(run.manifest.capture, captured_at=later_time)
        ),
    )
    assert (
        report_opening_rehearsal(run).content_digest
        == report_opening_rehearsal(later).content_digest
    )
    assert compare_opening_rehearsals(
        (run, later, run), mode="snapshot_repeatability"
    ).candidate_evidence_complete
    changed = replace(run, source=replace(run.source, content_fingerprint="different"))
    assert not compare_opening_rehearsals(
        (run, changed, run), mode="snapshot_repeatability"
    ).candidate_evidence_complete


@pytest.mark.parametrize(
    "field", ["artifact_identity", "policy_identity", "target_installation_id"]
)
def test_changed_artifact_policy_or_target_binding_refuses(field):
    runs = tuple(_empty_run(i) for i in range(3))
    assert compare_opening_rehearsals(runs).candidate_evidence_complete
    value = UUID(int=101) if field == "target_installation_id" else "changed"
    changed = replace(runs[1], binding=replace(runs[1].binding, **{field: value}))
    result = compare_opening_rehearsals((runs[0], changed, runs[2]))
    assert not result.candidate_evidence_complete
    assert {x.code for x in result.blockers} == {"incompatible_run_bindings"}


def test_reordered_invalid_and_incomplete_sequences_refuse():
    runs = tuple(_empty_run(i) for i in range(3))
    assert compare_opening_rehearsals(runs).candidate_evidence_complete
    reordered = compare_opening_rehearsals((runs[1], runs[0], runs[2]))
    assert not reordered.candidate_evidence_complete
    assert {x.code for x in reordered.blockers} == {"run_cutoff_order_invalid"}
    incomplete = compare_opening_rehearsals(runs[:2])
    assert not incomplete.candidate_evidence_complete
    assert {x.code for x in incomplete.blockers} == {"three_complete_runs_required"}
    blocked = replace(
        runs[1], source=replace(runs[1].source, population_complete=False)
    )
    result = compare_opening_rehearsals((runs[0], blocked, runs[2]))
    assert not result.candidate_evidence_complete
    assert {x.code for x in result.blockers} == {"run_blocked"}


def test_opaque_snapshot_names_are_not_lexically_ordered():
    runs = tuple(_empty_run(i) for i in range(3))
    renamed = tuple(
        replace(
            run,
            source=replace(run.source, snapshot_id=name),
            manifest=replace(
                run.manifest, capture=replace(run.manifest.capture, snapshot_id=name)
            ),
        )
        for run, name in zip(runs, ("z", "a", "m"), strict=True)
    )
    assert compare_opening_rehearsals(renamed).candidate_evidence_complete


def test_closed_frozen_boundary_rejects_bags_and_mutation():
    run = _run()
    with pytest.raises(OpeningEvidenceError):
        replace(run, previews=[])
    with pytest.raises(FrozenInstanceError):
        run.source.population_complete = False


def test_available_v2_total_fields_cannot_replace_missing_documentary_or_wht_evidence():
    run = _run()
    assert run.previews[0].admissible
    codes = _codes(run)
    assert "original_documentary_evidence_unavailable" in codes
    assert "historical_discharge_evidence_unavailable" in codes
    assert not report_opening_rehearsal(run).complete_exact_reconciliation
    assert not compare_opening_rehearsals(
        tuple(_run(i) for i in range(3))
    ).candidate_evidence_complete


def test_source_original_and_discharge_cancellation_is_not_hidden_by_residual():
    run = _run()
    invoice = replace(
        run.source.invoices[0],
        total=Decimal("120.01"),
        canonical_discharge=Decimal("10.01"),
    )
    control = replace(
        run.source.controls[0],
        original_total=Decimal("200.01"),
        canonical_discharge=Decimal("20.01"),
    )
    settlement = replace(run.source.settlements[0], amount=Decimal("10.01"))
    changed = replace(
        run,
        source=replace(
            run.source,
            invoices=(invoice, run.source.invoices[1]),
            controls=(control,),
            settlements=(settlement, run.source.settlements[1]),
        ),
    )
    assert {
        "source_original_total_mismatch",
        "source_discharge_total_mismatch",
        "source_discharge_amount_mismatch",
    }.issubset(_codes(changed))
    assert all(
        x.unclassified_delta_minor == 0
        for x in report_opening_rehearsal(changed).deltas
    )
    assert not report_opening_rehearsal(changed).complete_exact_reconciliation


def test_manifest_one_minor_original_and_discharge_cancellation_with_real_preview():
    run = _run()
    item = run.manifest.receivables[0]
    original = replace(
        item.original, subtotal=ar._money(11001), grand_total=ar._money(12001)
    )
    effect = replace(item.historical_discharges[0], amount_minor=1001)
    item = replace(item, original=original, historical_discharges=(effect,))
    manifest = replace(run.manifest, receivables=(item, run.manifest.receivables[1]))
    command = replace(run.commands[0], manifest=manifest)
    preview = preview_opening_account_import_v2(command, verifier=ar.TrustedVerifier())
    assert preview.admissible
    changed = replace(run, manifest=manifest, commands=(command,), previews=(preview,))
    assert {
        "source_original_total_mismatch",
        "source_discharge_total_mismatch",
    }.issubset(_codes(changed))


@pytest.mark.parametrize(
    "mutation,code",
    [
        (
            lambda r: replace(r, invoice_mappings=r.invoice_mappings[:1]),
            "invoice_mapping_coverage_mismatch",
        ),
        (
            lambda r: replace(
                r,
                invoice_mappings=(
                    r.invoice_mappings[0],
                    replace(
                        r.invoice_mappings[1],
                        source_invoice_key=r.invoice_mappings[0].source_invoice_key,
                    ),
                ),
            ),
            "duplicate_evidence",
        ),
        (
            lambda r: replace(
                r,
                invoice_mappings=(
                    replace(r.invoice_mappings[0], source_invoice_id=UUID(int=999)),
                    r.invoice_mappings[1],
                ),
            ),
            "invoice_mapping_coverage_mismatch",
        ),
        (
            lambda r: replace(
                r,
                source=replace(
                    r.source,
                    invoices=(
                        replace(r.source.invoices[0], account_id=UUID(int=999)),
                        r.source.invoices[1],
                    ),
                ),
            ),
            "invoice_partition_binding_mismatch",
        ),
    ],
)
def test_identity_substitution_merge_omission_and_partition_association_refuse(
    mutation, code
):
    assert code in _codes(mutation(_run()))


def test_individual_discharge_cancellation_is_detected_without_aggregate_drift():
    run = _run()
    settlements = (
        replace(run.source.settlements[0], amount=Decimal("10.01")),
        replace(run.source.settlements[1], amount=Decimal("9.99")),
    )
    changed = replace(run, source=replace(run.source, settlements=settlements))
    assert "source_discharge_amount_mismatch" in _codes(changed)
    assert changed.source.controls == run.source.controls


def test_closed_excluded_documentary_history_is_not_compared_to_eligible_manifest():
    run = _run()
    closed = OpeningObservedInvoiceV1(
        UUID(int=3),
        UUID(int=10),
        "NGN",
        2,
        "CLOSED-3",
        ar.NOW,
        Decimal("50"),
        Decimal("50"),
        Decimal("0"),
        (UUID(int=3001),),
    )
    settlement = OpeningObservedSettlementV1(
        "credit_note", UUID(int=3001), UUID(int=3), UUID(int=3000), Decimal("50"), True
    )
    control = replace(
        run.source.controls[0],
        invoice_count=3,
        excluded_count=1,
        original_total=Decimal("250"),
        canonical_discharge=Decimal("70"),
    )
    changed = replace(
        run,
        source=replace(
            run.source,
            invoice_ids=(*run.source.invoice_ids, UUID(int=3)),
            invoices=(*run.source.invoices, closed),
            settlements=(*run.source.settlements, settlement),
            controls=(control,),
        ),
    )
    codes = _codes(changed)
    assert "source_control_population_mismatch" not in codes
    assert "source_original_total_mismatch" not in codes
    assert "source_discharge_total_mismatch" not in codes
    assert "excluded_money_unresolved" not in codes
    assert "original_documentary_evidence_unavailable" in codes


def test_capture_clock_is_bound_before_it_is_ignored_in_content_digest():
    run = _run()
    changed = replace(
        run,
        source=replace(
            run.source, captured_at=run.source.captured_at + timedelta(seconds=1)
        ),
    )
    assert "source_binding_mismatch" in _codes(changed)
    manifest = replace(
        run.manifest,
        capture=replace(
            run.manifest.capture,
            recorded_cutoff=run.source.captured_at + timedelta(seconds=1),
        ),
    )
    changed = replace(run, manifest=manifest)
    assert "source_capture_incomplete" in _codes(changed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("cash_lots", lambda r: ar._candidate().manifest.cash_lots[:1]),
        ("population", lambda r: _run().manifest.population[:1]),
        ("dispositions", lambda r: _run().manifest.dispositions[:1]),
        ("counts", lambda r: OpeningPopulationCountsV1(1, 0, 0, 0)),
        ("counts", lambda r: OpeningPopulationCountsV1(0, 1, 0, 0)),
        ("counts", lambda r: OpeningPopulationCountsV1(0, 0, 1, 0)),
        ("counts", lambda r: OpeningPopulationCountsV1(0, 0, 0, 1)),
    ],
)
def test_command_free_positive_requires_a_genuinely_empty_manifest(field, value):
    run = _empty_run()
    assert report_opening_rehearsal(run).complete_exact_reconciliation
    changed = replace(run, manifest=replace(run.manifest, **{field: value(run)}))
    report = report_opening_rehearsal(changed)
    assert not report.complete_exact_reconciliation
    assert "command_free_population_not_empty" in {x.code for x in report.blockers}


def test_command_free_manual_hold_cannot_hide_a_blocked_disposition():
    run = _empty_run()
    assert report_opening_rehearsal(run).complete_exact_reconciliation
    disposition = replace(_run().manifest.dispositions[0], disposition="blocking")
    changed = replace(
        run,
        manifest=replace(run.manifest, dispositions=(disposition,)),
        accepted_manual_holds=("accepted:manual-review",),
    )
    report = report_opening_rehearsal(changed)
    assert not report.complete_exact_reconciliation
    assert "command_free_population_not_empty" in {x.code for x in report.blockers}
    assert report.accepted_manual_holds == ("accepted:manual-review",)


def test_command_free_positive_requires_actual_source_population_empty():
    run = _empty_run()
    assert report_opening_rehearsal(run).complete_exact_reconciliation
    changed = replace(
        run, source=replace(run.source, settlements=_run().source.settlements)
    )
    report = report_opening_rehearsal(changed)
    assert not report.complete_exact_reconciliation
    assert "command_free_population_not_empty" in {x.code for x in report.blockers}
