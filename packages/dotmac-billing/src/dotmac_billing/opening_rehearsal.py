"""Pure candidate reports over Sub observations and genuine Starter preflights.

Fingerprints identify supplied content, never its truth. This module has no
verifier, cash qualification, import, seal or activation authority.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal, DecimalException, localcontext
from itertools import pairwise
from typing import Literal
from uuid import UUID

from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.money import Currency, Money, MoneyError

from dotmac_billing._opening_values import cash_decimal_context, minor_units_of
from dotmac_billing.opening_contracts import OpeningBlockerV1, OpeningEvidenceError
from dotmac_billing.opening_receivable_validation import opening_manifest_fingerprint_v2
from dotmac_billing.opening_rehearsal_contracts import (
    OpeningRehearsalComparisonV1,
    OpeningRehearsalDeltaV1,
    OpeningRehearsalReportV1,
    OpeningRehearsalRunV1,
)
from dotmac_billing.opening_validation import _payload

__all__ = ["report_opening_rehearsal", "compare_opening_rehearsals"]

_CASH_TABLES = frozenset(
    {
        "payments",
        "payment_settlements",
        "payment_provider_events",
        "integration_inbox",
        "payment_refunds",
        "payment_reversals",
        "payment_proofs",
        "payment_proof_corrections",
        "topup_intents",
    }
)


def _digest(domain: str, value: object) -> str:
    def wire(item: object) -> object:
        if isinstance(item, dict):
            return {key: wire(member) for key, member in item.items()}
        if isinstance(item, tuple):
            return [wire(member) for member in item]
        return _payload(item)

    return fingerprint_of({"domain": domain, "value": wire(value)})


def _minor(amount: Decimal, currency: str, precision: int) -> int:
    if not amount.is_finite() or amount.adjusted() >= 14:
        raise OpeningEvidenceError("invalid observed amount")
    with localcontext(cash_decimal_context()):
        money = Money(amount, Currency(currency, precision))
    if money.amount != amount:
        raise OpeningEvidenceError("observed amount needs rounding")
    return minor_units_of(money)


def _blocks(failures: dict[str, list[str]]) -> tuple[OpeningBlockerV1, ...]:
    return tuple(
        OpeningBlockerV1(code, len(refs), tuple(sorted(refs)))
        for code, refs in sorted(failures.items())
    )


def report_opening_rehearsal(run: OpeningRehearsalRunV1) -> OpeningRehearsalReportV1:
    """Reconcile all supplied partitions; preflight blockers always survive."""
    if type(run) is not OpeningRehearsalRunV1:
        raise OpeningEvidenceError("expected OpeningRehearsalRunV1")
    source, manifest = run.source, run.manifest
    failures: dict[str, list[str]] = {}

    def block(code: str, ref: str) -> None:
        failures.setdefault(code, []).append(ref)

    # Without a real per-partition command/preview there is no existing
    # preflight to validate hidden population, dispositions or cash lots. Only
    # genuinely empty complete source and manifest populations can take this
    # command-free path; manual holds never waive this condition.
    if not run.commands:
        manifest_empty = not any(
            (
                manifest.population,
                manifest.dispositions,
                manifest.controls,
                manifest.cash_lots,
                manifest.receivables,
            )
        ) and all(
            getattr(manifest.counts, f.name) == 0 for f in fields(manifest.counts)
        )
        source_empty = not any(
            (
                source.invoice_ids,
                source.eligible_invoice_ids,
                source.invoices,
                source.settlements,
                source.controls,
                source.lanes,
                source.qualified_payment_ids,
                source.blockers,
            )
        ) and all(x.count == 0 and not x.identities for x in source.cash_populations)
        if (
            not manifest_empty
            or not source_empty
            or not source.population_complete
            or run.previews
            or run.mappings
            or run.invoice_mappings
            or run.discharge_mappings
        ):
            block("command_free_population_not_empty", "complete_population")
    for item in source.blockers:
        failures.setdefault(item.code, []).extend(item.references)
        if item.count != len(item.references) or item.count <= 0:
            block("malformed_blocker", item.code)
    if source.economic_cutoff > source.captured_at:
        block("source_capture_incomplete", "economic_cutoff")
    if not source.population_complete:
        block("source_population_incomplete", "capture")
    capture = manifest.capture
    if capture.recorded_cutoff > source.captured_at:
        block("source_capture_incomplete", "recorded_cutoff")
    for name in (
        "source_repository_sha",
        "schema_heads",
        "snapshot_id",
        "captured_at",
        "economic_cutoff",
        "population_version",
    ):
        if getattr(source, name) != getattr(capture, name):
            block("source_binding_mismatch", name)
    if str(source.installation_id) != capture.installation_id:
        block("source_binding_mismatch", "installation_id")
    if (
        source.isolation not in {"repeatable read", "serializable"}
        or source.isolation.replace(" ", "_") != capture.isolation
    ):
        block("source_snapshot_unproven", "isolation")
    if (
        len(set(source.schema_heads)) != len(source.schema_heads)
        or not source.schema_heads
    ):
        block("source_binding_mismatch", "schema_heads")
    precision = {x.currency: x.precision for x in source.currency_precisions}
    if len(precision) != len(source.currency_precisions):
        block("duplicate_evidence", "currency_precisions")
    for values, ref in (
        (source.invoice_ids, "invoices"),
        (source.eligible_invoice_ids, "eligible_invoices"),
    ):
        if len(set(values)) != len(values):
            block("duplicate_evidence", ref)
    if not set(source.eligible_invoice_ids).issubset(source.invoice_ids):
        block("source_population_incomplete", "eligible_invoices")
    tables = {x.table: x for x in source.cash_populations}
    if len(tables) != len(source.cash_populations):
        block("duplicate_evidence", "cash_populations")
    if set(tables) != _CASH_TABLES:
        block("source_population_incomplete", "cash_populations")
    for population in source.cash_populations:
        if population.count < 0 or population.count != len(population.identities):
            block("source_population_incomplete", population.table)
        if len(set(population.identities)) != len(population.identities):
            block("duplicate_evidence", population.table)
        # V2 explicitly supplies no qualified cash residual. Raw rows cannot be
        # reinterpreted as available credit, even if their gross amounts net.
        if population.count:
            block("cash_residual_evidence_unavailable", population.table)
    if source.qualified_payment_ids:
        block("unsupported_cash_qualification", "qualified_payment_ids")
    controls = {(x.account_id, x.currency): x for x in source.controls}
    if len(controls) != len(source.controls):
        block("duplicate_evidence", "source_controls")
    if sum(x.invoice_count for x in source.controls) != len(source.invoice_ids):
        block("source_population_incomplete", "invoice_count")
    if sum(x.eligible_count for x in source.controls) != len(
        source.eligible_invoice_ids
    ):
        block("source_population_incomplete", "eligible_count")
    for control in source.controls:
        amounts = (
            control.original_total,
            control.canonical_discharge,
            control.residual,
            control.eligible_residual,
        )
        if not all(amount.is_finite() and amount.adjusted() < 14 for amount in amounts):
            block("source_money_invalid", str(control.account_id))
        else:
            with localcontext(cash_decimal_context()):
                if (
                    control.original_total - control.canonical_discharge
                    != control.residual
                ):
                    block("source_control_equation_mismatch", str(control.account_id))
        if (
            any(
                getattr(control, name) < 0
                for name in (
                    "invoice_count",
                    "eligible_count",
                    "excluded_count",
                    "exception_count",
                    "source_count",
                )
            )
            or control.eligible_count + control.excluded_count > control.invoice_count
        ):
            block("source_population_incomplete", str(control.account_id))
        if control.currency is not None:
            try:
                for amount in amounts:
                    _minor(
                        amount, control.currency, precision.get(control.currency, -1)
                    )
            except (OpeningEvidenceError, MoneyError, DecimalException, ValueError):
                block("currency_precision_mismatch", str(control.account_id))
        if control.exception_count:
            block("source_exceptions_present", str(control.account_id))
        if control.account_id is None or control.currency is None:
            block("source_partition_unresolved", str(control.account_id))
    lane_keys = [(x.account_id, x.currency) for x in source.lanes]
    if len(set(lane_keys)) != len(lane_keys):
        block("duplicate_evidence", "source_lanes")
    if set(lane_keys) != set(controls):
        block("source_population_incomplete", "source_lanes")
    for lane in source.lanes:
        control = controls.get((lane.account_id, lane.currency))
        if lane.authority != "authoritative" or lane.authority_cutover_id is None:
            block("source_authority_unavailable", str(lane.account_id))
        if lane.posting_group_count < 0:
            block("source_population_incomplete", "posting_group_count")
        if not lane.collectible_receivable.is_finite():
            block("source_money_invalid", str(lane.account_id))
        elif (
            control is not None
            and control.eligible_residual.is_finite()
            and lane.collectible_receivable != control.eligible_residual
        ):
            block(
                "unclassified_money_delta", f"{lane.account_id}:source_receivable_lane"
            )
        if (lane.account_id, lane.currency) not in controls:
            block("source_partition_unresolved", str(lane.account_id))
        for field in fields(lane):
            if field.name in {
                "account_id",
                "currency",
                "fingerprint",
                "authority",
                "authority_cutover_id",
                "posting_group_count",
                "collectible_receivable",
            }:
                continue
            amount = getattr(lane, field.name)
            if not amount.is_finite():
                block("source_money_invalid", f"{lane.account_id}:{field.name}")
            elif amount != 0:
                block("unsupported_lane", f"{lane.account_id}:{field.name}")
    mappings = {
        (x.source_account_id, x.partition.currency): x.partition for x in run.mappings
    }
    partitions = [x.partition for x in run.mappings]
    if len(mappings) != len(run.mappings) or len(set(partitions)) != len(partitions):
        block("duplicate_evidence", "mappings")
    if set(mappings) != set(controls):
        block("partition_population_mismatch", "source_controls")
    if set(partitions) != {x.partition for x in manifest.controls}:
        block("partition_population_mismatch", "manifest_controls")
    commands = {x.partition: x for x in run.commands}
    if len(commands) != len(run.commands):
        block("duplicate_evidence", "commands")
    if set(commands) != set(partitions):
        block("partition_population_mismatch", "commands")
    for command in run.commands:
        if command.manifest != manifest or command.scope != manifest.scope:
            block("command_binding_mismatch", command.partition.source_account_key)
    invoices = {x.invoice_id: x for x in source.invoices}
    if len(invoices) != len(source.invoices):
        block("duplicate_evidence", "source_invoices")
    if set(invoices) != set(source.invoice_ids):
        block("source_population_incomplete", "invoice_observations")
    for key, control in controls.items():
        cohort = tuple(x for x in source.invoices if (x.account_id, x.currency) == key)
        eligible_cohort = tuple(
            x for x in cohort if x.invoice_id in source.eligible_invoice_ids
        )
        if (
            len(cohort) != control.invoice_count
            or len(eligible_cohort) != control.eligible_count
        ):
            block("source_population_incomplete", f"{key[0]}:invoice_partition_counts")
        try:
            if key[1] is None:
                raise OpeningEvidenceError("unresolved currency")
            units = precision.get(key[1], -1)
            for field, expected in (
                ("total", control.original_total),
                ("canonical_discharge", control.canonical_discharge),
                ("residual", control.residual),
            ):
                if sum(
                    _minor(getattr(x, field), key[1], units) for x in cohort
                ) != _minor(expected, key[1], units):
                    block("source_control_population_mismatch", f"{key[0]}:{field}")
            if sum(
                _minor(x.residual, key[1], units) for x in eligible_cohort
            ) != _minor(control.eligible_residual, key[1], units):
                block(
                    "source_control_population_mismatch", f"{key[0]}:eligible_residual"
                )
        except (OpeningEvidenceError, MoneyError, DecimalException, ValueError):
            block("currency_precision_mismatch", str(key[0]))
    for invoice in source.invoices:
        if (invoice.account_id, invoice.currency) not in controls:
            block("source_partition_unresolved", str(invoice.invoice_id))
    invoice_mappings = {x.source_invoice_id: x for x in run.invoice_mappings}
    if len(invoice_mappings) != len(run.invoice_mappings):
        block("duplicate_evidence", "invoice_mappings")
    if set(invoice_mappings) != set(source.eligible_invoice_ids):
        block("invoice_mapping_coverage_mismatch", "eligible_invoices")
    receivables = {x.source_invoice_key: x for x in manifest.receivables}
    mapped_keys = [x.source_invoice_key for x in run.invoice_mappings]
    if len(set(mapped_keys)) != len(mapped_keys) or len(receivables) != len(
        manifest.receivables
    ):
        block("duplicate_evidence", "mapped_receivables")
    if set(mapped_keys) != set(receivables):
        block("invoice_mapping_coverage_mismatch", "manifest_receivables")
    settlements = {(x.kind, x.id): x for x in source.settlements}
    if len(settlements) != len(source.settlements):
        block("duplicate_evidence", "source_settlements")
    discharge_mappings = {
        (x.settlement_kind, x.settlement_id): x.historical_reference
        for x in run.discharge_mappings
    }
    if len(discharge_mappings) != len(run.discharge_mappings):
        block("duplicate_evidence", "discharge_mappings")
    for settlement in source.settlements:
        if settlement.invoice_id not in invoices:
            block("source_population_incomplete", f"{settlement.id}:invoice")
    used_settlements: set[tuple[str, UUID]] = set()
    expected_source_ids = {
        (x.settlement_kind, x.settlement_id): x.source_id
        for x in run.discharge_mappings
    }
    for source_id, mapping in invoice_mappings.items():
        invoice = invoices.get(source_id)
        item = receivables.get(mapping.source_invoice_key)
        if invoice is None or item is None:
            block("invoice_mapping_coverage_mismatch", str(source_id))
            continue
        partition = mappings.get((invoice.account_id, invoice.currency))
        if (
            item.partition != partition
            or item.source_identity != mapping.source_identity
            or invoice.precision != item.partition.precision
        ):
            block("invoice_partition_binding_mismatch", str(source_id))
        # The actual V2 observation has no frozen subtotal, tax, FX, party or
        # documentary snapshots. Total-only equality cannot attest the complete
        # imported original. Keep this explicitly unavailable until Sub exports
        # the documentary evidence through its own read-only contract.
        block("original_documentary_evidence_unavailable", str(source_id))
        if item.original is None:
            block(
                "original_documentary_evidence_unavailable", mapping.source_invoice_key
            )
        else:
            try:
                original_minor = minor_units_of(item.original.grand_total)
                observed_minor = _minor(
                    invoice.total, invoice.currency, item.partition.precision
                )
                if original_minor != observed_minor:
                    block("source_original_total_mismatch", str(source_id))
                if (
                    item.original.document_number != invoice.invoice_number
                    or item.original.issued_at != invoice.issued_at
                ):
                    block("source_original_document_binding_mismatch", str(source_id))
                discharged_minor = _minor(
                    invoice.canonical_discharge,
                    invoice.currency,
                    item.partition.precision,
                )
                if (
                    sum(x.amount_minor for x in item.historical_discharges)
                    != discharged_minor
                ):
                    block("source_discharge_total_mismatch", str(source_id))
                if item.residual_minor != _minor(
                    invoice.residual, invoice.currency, item.partition.precision
                ):
                    block("source_invoice_residual_mismatch", str(source_id))
            except (OpeningEvidenceError, MoneyError, DecimalException, ValueError):
                block("currency_precision_mismatch", str(source_id))
        observed = {
            key: value
            for key, value in settlements.items()
            if value.invoice_id == source_id and value.active
        }
        all_observed_ids = [
            value.id for value in settlements.values() if value.invoice_id == source_id
        ]
        if len(set(invoice.settlement_ids)) != len(invoice.settlement_ids) or sorted(
            invoice.settlement_ids
        ) != sorted(all_observed_ids):
            block("source_population_incomplete", f"{source_id}:settlements")
        history = {x.reference: x for x in item.historical_discharges}
        references = [
            discharge_mappings[key] for key in observed if key in discharge_mappings
        ]
        if len(references) != len(set(references)) or set(references) != set(history):
            block("historical_discharge_evidence_unavailable", str(source_id))
        for key, settlement in observed.items():
            used_settlements.add(key)
            effect = history.get(discharge_mappings.get(key, ""))
            if effect is None or settlement.kind not in {"payment", "credit_note"}:
                block("historical_discharge_evidence_unavailable", str(settlement.id))
                continue
            if effect.kind not in {"payment", "credit_note"}:
                block("historical_discharge_evidence_unavailable", effect.reference)
            if expected_source_ids.get(key) != settlement.source_id:
                block("source_discharge_binding_mismatch", str(settlement.id))
            if effect.kind != settlement.kind:
                block("source_discharge_binding_mismatch", str(settlement.id))
            try:
                if effect.amount_minor != _minor(
                    settlement.amount, invoice.currency, item.partition.precision
                ):
                    block("source_discharge_amount_mismatch", str(settlement.id))
            except (OpeningEvidenceError, MoneyError, DecimalException, ValueError):
                block("currency_precision_mismatch", str(settlement.id))
    if set(discharge_mappings) != used_settlements:
        block("discharge_mapping_coverage_mismatch", "settlements")
    previews = {x.partition: x for x in run.previews}
    if len(previews) != len(run.previews):
        block("duplicate_evidence", "previews")
    if set(previews) != set(partitions):
        block("partition_population_mismatch", "previews")
    manifest_digest = opening_manifest_fingerprint_v2(manifest)
    deltas: list[OpeningRehearsalDeltaV1] = []
    for key, partition in sorted(
        mappings.items(), key=lambda x: (str(x[0][0]), x[0][1])
    ):
        ref = f"{key[0]}:{key[1]}"
        preview = previews.get(partition)
        control = controls.get(key)
        command = commands.get(partition)
        if preview is None or control is None or command is None:
            continue
        if precision.get(partition.currency) != partition.precision:
            block("currency_precision_mismatch", ref)
        if (
            preview.target != command.target
            or preview.manifest_fingerprint != manifest_digest
            or preview.operation != "billing.opening.import.v2"
            or preview.target.scope != manifest.scope
            or preview.target.account_id != partition.account_id
            or preview.target.currency != partition.currency
            or preview.target.precision != partition.precision
        ):
            block("preview_binding_mismatch", ref)
        for item in preview.blockers:
            failures.setdefault(item.code, []).extend(
                f"{ref}:{x}" for x in item.references
            )
            if item.count != len(item.references) or item.count <= 0:
                block("malformed_blocker", item.code)
        if not preview.admissible and not preview.blockers:
            block("preview_incomplete", ref)
        if preview.admissible and preview.blockers:
            block("preview_binding_mismatch", ref)
        expected_ar = sum(
            x.residual_minor for x in manifest.receivables if x.partition == partition
        )
        expected_cash = sum(
            x.residual_minor for x in manifest.cash_lots if x.partition == partition
        )
        if (preview.cash_residual_minor, preview.receivable_residual_minor) != (
            expected_cash,
            expected_ar,
        ):
            block("preview_residual_binding_mismatch", ref)
        source_minor = None
        try:
            source_minor = _minor(
                control.eligible_residual, partition.currency, partition.precision
            )
        except (OpeningEvidenceError, MoneyError, DecimalException, ValueError):
            block("currency_precision_mismatch", ref)
        delta = (
            None
            if source_minor is None
            else preview.receivable_residual_minor - source_minor
        )
        if delta != 0:
            block("unclassified_money_delta", f"{ref}:receivable")
        deltas.append(
            OpeningRehearsalDeltaV1(
                partition,
                "receivable",
                source_minor,
                preview.receivable_residual_minor,
                delta,
            )
        )
        cash_absent = not any(x.count for x in source.cash_populations)
        cash_minor = 0 if cash_absent else None
        cash_delta = preview.cash_residual_minor if cash_absent else None
        if cash_delta != 0:
            block("unclassified_money_delta", f"{ref}:cash")
        deltas.append(
            OpeningRehearsalDeltaV1(
                partition, "cash", cash_minor, preview.cash_residual_minor, cash_delta
            )
        )
    blockers = _blocks(failures)
    # Capture clock and snapshot token identify evidence captures, not content.
    source_payload = {
        f.name: getattr(source, f.name)
        for f in fields(source)
        if f.name not in {"captured_at", "snapshot_id"}
    }
    manifest_payload = {
        f.name: getattr(manifest, f.name)
        for f in fields(manifest)
        if f.name != "capture"
    }
    capture_payload = {
        f.name: getattr(capture, f.name)
        for f in fields(capture)
        if f.name not in {"captured_at", "snapshot_id"}
    }
    binding_digest = _digest(
        "billing.opening.rehearsal.binding.v1",
        {
            "source": source.source_repository_sha,
            "installation": source.installation_id,
            "schema": source.schema_heads,
            "population": source.population_version,
            "assembly": run.binding,
            "mappings": run.mappings,
            "invoice_mapping_policy": capture.account_mapping_version,
            "source_authority": capture.source_authority_ref,
            "account_mapping": capture.account_mapping_version,
            "cash_mapping": capture.cash_mapping_version,
            "receivable_policies": tuple(
                sorted(
                    {
                        (
                            x.receivable_mapping_version,
                            x.tax_mapping_version,
                            x.source_resolver_version,
                            x.collection.mapping_version,
                        )
                        for x in manifest.receivables
                    }
                )
            ),
            "source_lane_authorities": tuple(
                (x.account_id, x.currency, x.authority, x.authority_cutover_id)
                for x in source.lanes
            ),
            "migration_authority": tuple(
                x.migration_authority_ref for x in run.commands
            ),
            "targets": tuple(
                (
                    x.target.scope,
                    x.target.account_id,
                    x.target.currency,
                    x.target.precision,
                )
                for x in run.previews
            ),
        },
    )
    content_digest = _digest(
        "billing.opening.rehearsal.report.v1",
        {
            "source": source_payload,
            "manifest": manifest_payload,
            "invoice_mappings": run.invoice_mappings,
            "discharge_mappings": run.discharge_mappings,
            "capture": capture_payload,
            "binding": binding_digest,
            "deltas": tuple(deltas),
            "blockers": blockers,
            "targets": tuple(x.target for x in run.previews),
            "holds": run.accepted_manual_holds,
        },
    )
    return OpeningRehearsalReportV1(
        content_digest,
        binding_digest,
        source.snapshot_id,
        source.installation_id,
        source.economic_cutoff,
        capture.recorded_cutoff,
        tuple(x.target.posting_watermark for x in run.previews),
        not blockers,
        blockers,
        tuple(deltas),
        run.accepted_manual_holds,
    )


def compare_opening_rehearsals(
    runs: tuple[OpeningRehearsalRunV1, ...],
    *,
    mode: Literal[
        "snapshot_repeatability", "successive_reconciliations"
    ] = "successive_reconciliations",
) -> OpeningRehearsalComparisonV1:
    """Evaluate ordered evidence without treating opaque IDs as clocks."""
    if type(runs) is not tuple or mode not in {
        "snapshot_repeatability",
        "successive_reconciliations",
    }:
        raise OpeningEvidenceError("unsupported rehearsal comparison")
    reports = tuple(report_opening_rehearsal(run) for run in runs)
    failures: dict[str, list[str]] = {}
    if len(reports) != 3:
        failures["three_complete_runs_required"] = ["runs"]
    for index, report in enumerate(reports):
        if not report.complete_exact_reconciliation:
            failures.setdefault("run_blocked", []).append(str(index))
    if len({r.binding_digest for r in reports}) != 1:
        failures["incompatible_run_bindings"] = ["runs"]
    if mode == "snapshot_repeatability":
        if len({r.content_digest for r in reports}) != 1:
            failures["snapshot_content_changed"] = ["runs"]
        if (
            len({(r.source_installation_id, r.source_snapshot_id) for r in reports})
            != 1
        ):
            failures["snapshot_identity_changed"] = ["runs"]
    else:
        captures = {(r.source_installation_id, r.source_snapshot_id) for r in reports}
        if len(captures) != len(reports):
            failures["duplicate_evidence_capture"] = ["runs"]
        for before, after in pairwise(reports):
            if (
                after.economic_cutoff < before.economic_cutoff
                or after.recorded_cutoff < before.recorded_cutoff
            ):
                failures.setdefault("run_cutoff_order_invalid", []).append(
                    after.source_snapshot_id
                )
    blockers = _blocks(failures)
    digests = tuple(r.content_digest for r in reports)
    return OpeningRehearsalComparisonV1(
        _digest(
            "billing.opening.rehearsal.comparison.v1",
            {
                "mode": mode,
                "reports": digests,
                "captures": tuple(
                    (r.source_installation_id, r.source_snapshot_id) for r in reports
                ),
                "blockers": blockers,
            },
        ),
        mode,
        not blockers,
        blockers,
        digests,
    )
