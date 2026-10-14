"""Pure complete-partition checks; trust is supplied only by an assembly port."""

from __future__ import annotations

from collections import Counter
from dataclasses import fields

from dotmac_kernel.fingerprints import fingerprint_of

from dotmac_billing._opening_values import minor_units_of, money_from_minor
from dotmac_billing.opening_contracts import (
    OpeningBlockerV1,
    OpeningEvidenceError,
    OpeningManifestV1,
    OpeningPreviewRequestV1,
    OpeningVerificationUnavailable,
)
from dotmac_billing.opening_receivable_contracts import (
    ImportOpeningAccountV2,
    OpeningAccountImportVerifierV2,
    OpeningHistoricalDischargeV1,
    OpeningImportPreviewV2,
    OpeningImportVerificationRequestV2,
    OpeningImportVerificationV2,
    OpeningManifestV2,
    OpeningReplayBindingV1,
)
from dotmac_billing.opening_validation import _payload, preview_opening_account_import


def opening_manifest_fingerprint_v2(manifest: OpeningManifestV2) -> str:
    if type(manifest) is not OpeningManifestV2:
        raise OpeningEvidenceError("expected OpeningManifestV2")
    return fingerprint_of(
        {"domain": "billing.opening.manifest.v2", "candidate": _payload(manifest)}
    )


def opening_account_command_digest_v2(command: ImportOpeningAccountV2) -> str:
    if type(command) is not ImportOpeningAccountV2:
        raise OpeningEvidenceError("expected ImportOpeningAccountV2")
    return fingerprint_of(
        {"domain": "billing.opening.account.command.v2", "command": _payload(command)}
    )


def opening_withholding_component_identity(
    *, tax_source_owner: str, determination_ref: str, component_ref: str
) -> str:
    """Exact current Tax uniqueness identity; no concatenation ambiguity."""
    return fingerprint_of(
        {
            "domain": "billing.withholding.tax_component.v1",
            "tax_source_owner": tax_source_owner,
            "determination_ref": determination_ref,
            "component_ref": component_ref,
        }
    )


def _opening_replay_identity(
    binding: OpeningReplayBindingV1,
) -> tuple[str, str, str, str]:
    namespace = {
        "billing.withholding.accept": "withholding.source",
        "billing.withholding.tax_component": "withholding.tax_component",
        "billing.non_cash_credit.grant": "non_cash.grant",
        "billing.non_cash_credit.apply": "non_cash.effect",
        "billing.credit_note.issue": "credit_note.issue",
    }[binding.operation]
    if binding.operation == "billing.withholding.tax_component":
        if binding.determination_ref is None or binding.component_ref is None:
            raise OpeningEvidenceError(
                "historical Tax identity needs its raw component tuple"
            )
        return (
            namespace,
            binding.source_owner,
            binding.determination_ref,
            binding.component_ref,
        )
    return namespace, binding.source_owner, binding.source_ref, ""


def verify_opening_account_import_v2(
    command: ImportOpeningAccountV2,
    verifier: OpeningAccountImportVerifierV2 | None,
) -> OpeningImportVerificationV2 | None:
    if verifier is None:
        return None
    digest = opening_account_command_digest_v2(command)
    manifest_digest = opening_manifest_fingerprint_v2(command.manifest)
    result = verifier.verify_import_v2(
        OpeningImportVerificationRequestV2(command, digest, manifest_digest)
    )
    if type(result) is not OpeningImportVerificationV2:
        return None
    if (
        result.operation != "billing.opening.import.v2"
        or result.scope != command.scope
        or result.command_digest != digest
        or result.manifest_fingerprint != manifest_digest
        or result.partition != command.partition
        or result.target != command.target
        or result.installation_id != command.manifest.capture.installation_id
        or result.migration_authority_ref != command.migration_authority_ref
        or result.source_authority_ref != command.manifest.capture.source_authority_ref
    ):
        return None
    return result


def opening_account_import_blockers_v2(
    command: ImportOpeningAccountV2,
) -> tuple[OpeningBlockerV1, ...]:
    """Validate the complete manifest, including partitions not selected here."""
    manifest = command.manifest
    failures: dict[str, list[str]] = {}

    def block(code: str, reference: str) -> None:
        failures.setdefault(code, []).append(reference)

    if (
        len(command.idempotency_key) > 255
        or len(command.migration_authority_ref) > 255
        or len(command.target.posting_watermark) > 255
        or len(command.target.expected_position_fingerprint) > 64
    ):
        block("opening_command_storage_invalid", "command")

    ar_keys = {item.source_identity.key for item in manifest.receivables}
    # V1's population/capture/cash checks remain the source of truth. Its two
    # cash-only admission restrictions are superseded by the AR checks below.
    cash = OpeningManifestV1(
        manifest.scope,
        manifest.capture,
        manifest.population,
        manifest.dispositions,
        manifest.counts,
        manifest.controls,
        manifest.cash_lots,
    )
    result = preview_opening_account_import(
        OpeningPreviewRequestV1(
            cash, command.partition, command.target, command.migration_authority_ref
        )
    )
    for cash_blocker in result.blockers:
        if cash_blocker.code == "verifier_unavailable":
            continue
        for reference in cash_blocker.references:
            if (
                cash_blocker.code == "eligible_source_unrepresented"
                and reference in ar_keys
            ):
                continue
            if cash_blocker.code == "unsupported_lane" and reference in {
                "collectible_receivable_minor",
                "open_ar_minor",
            }:
                continue
            block(cash_blocker.code, reference)
    if command.scope != manifest.scope or command.scope != command.target.scope:
        block("target_binding_mismatch", "scope")
    eligible = {
        item.identity: item
        for item in manifest.dispositions
        if item.disposition == "eligible"
    }
    cash_keys = {
        (lot.source_system, lot.source_settlement_key): lot
        for lot in manifest.cash_lots
    }
    identities: Counter[object] = Counter()
    invoice_keys: Counter[str] = Counter()
    history_keys: Counter[str] = Counter()
    replay_keys: dict[tuple[str, str, str, str], str] = {}
    replay_partitions: dict[tuple[str, str, str, str], object] = {}
    ar_totals: Counter[object] = Counter()
    for cash_lot in manifest.cash_lots:
        if (
            len(cash_lot.source_system) > 120
            or len(cash_lot.source_settlement_key) > 255
        ):
            block("cash_source_identity_invalid", cash_lot.lot_id)
        try:
            for amount in (
                cash_lot.gross_minor,
                cash_lot.residual_minor,
                sum(x.amount_minor for x in cash_lot.consumptions),
            ):
                money_from_minor(
                    amount, cash_lot.partition.currency, cash_lot.partition.precision
                )
        except OpeningEvidenceError:
            block("cash_storage_precision_invalid", cash_lot.lot_id)
    for item in manifest.receivables:
        key = item.source_invoice_key
        if (
            len(key) > 255
            or len(manifest.capture.installation_id) > 255
            or len(manifest.capture.source_system) > 120
        ):
            block("receivable_source_identity_invalid", key)
        identities[item.source_identity] += 1
        invoice_keys[key] += 1
        ar_totals[item.partition] += item.residual_minor
        disposition = eligible.get(item.source_identity)
        if (
            disposition is None
            or disposition.partition != item.partition
            or disposition.residual_minor != item.residual_minor
        ):
            block("receivable_source_membership_mismatch", key)
        if item.residual_minor <= 0:
            block("receivable_residual_nonpositive", key)
        original = item.original
        if original is None:
            block("original_documentary_evidence_missing", key)
            continue
        if (
            len(original.document_number) > 255
            or len(original.document_profile_code) > 120
            or len(original.document_profile_version) > 120
            or len(original.locale) > 40
            or len(original.timezone) > 80
        ):
            block("original_documentary_evidence_invalid", key)
        currency = (item.partition.currency, item.partition.precision)
        amounts = (original.subtotal, original.tax_total, original.grand_total)
        if any((x.currency.code, x.currency.minor_units) != currency for x in amounts):
            block("receivable_currency_mismatch", key)
        subtotal, tax, total = (minor_units_of(x) for x in amounts)
        if min(subtotal, tax, total) < 0 or subtotal + tax != total:
            block("original_document_total_mismatch", key)
        if sum(minor_units_of(x.tax_amount) for x in original.tax_snapshots) != tax:
            block("original_tax_evidence_mismatch", key)
        for snapshot in original.tax_snapshots:
            if (
                not snapshot.rate.is_finite()
                or snapshot.rate < 0
                or (
                    snapshot.tax_amount.currency.code,
                    snapshot.tax_amount.currency.minor_units,
                )
                != currency
                or snapshot.tax_amount.currency != snapshot.taxable_basis.currency
            ):
                block("original_tax_evidence_mismatch", key)
        if original.fx_snapshot is not None and (
            not original.fx_snapshot.rate.is_finite() or original.fx_snapshot.rate <= 0
        ):
            block("original_fx_evidence_mismatch", key)
        if original.issued_at > manifest.capture.economic_cutoff:
            block("source_after_cutoff", key)
        discharged = 0
        prior: dict[str, OpeningHistoricalDischargeV1] = {}
        inverted: Counter[str] = Counter()
        for effect in item.historical_discharges:
            history_keys[effect.reference] += 1
            discharged += effect.amount_minor
            if (
                effect.economic_at > manifest.capture.economic_cutoff
                or effect.recorded_at > manifest.capture.recorded_cutoff
            ):
                block("source_after_cutoff", effect.reference)
            if effect.amount_minor == 0:
                block("historical_discharge_zero", effect.reference)
            if effect.kind == "withholding" and not effect.tax_snapshots:
                block("historical_tax_evidence_missing", effect.reference)
            for snapshot in effect.tax_snapshots:
                if (
                    not snapshot.rate.is_finite()
                    or snapshot.rate < 0
                    or snapshot.tax_amount.currency != original.grand_total.currency
                    or snapshot.taxable_basis.currency != original.grand_total.currency
                ):
                    block("historical_tax_evidence_mismatch", effect.reference)
            if effect.amount_minor < 0:
                original_effect = (
                    prior.get(effect.reverses_ref)
                    if effect.reverses_ref is not None
                    else None
                )
                if (
                    original_effect is None
                    or original_effect.amount_minor <= 0
                    or effect.kind not in {"refund", "reversal"}
                    or (effect.source_system, effect.source_key)
                    != (original_effect.source_system, original_effect.source_key)
                ):
                    block("historical_discharge_inverse_unresolved", effect.reference)
                else:
                    inverted[original_effect.reference] -= effect.amount_minor
                    if (
                        inverted[original_effect.reference]
                        > original_effect.amount_minor
                    ):
                        block("historical_discharge_inverse_excess", effect.reference)
            elif effect.reverses_ref is not None:
                block("historical_discharge_inverse_unresolved", effect.reference)
            prior[effect.reference] = effect
            if effect.kind == "adjustment":
                block("historical_discharge_unsupported", effect.reference)
            expected_bindings = {
                "withholding": {
                    "billing.withholding.accept",
                    "billing.withholding.tax_component",
                },
                "credit_note": {"billing.credit_note.issue"},
                "non_cash_credit": {
                    "billing.non_cash_credit.grant",
                    "billing.non_cash_credit.apply",
                },
            }.get(effect.kind, set())
            if not expected_bindings.issubset(
                {x.operation for x in effect.replay_bindings}
            ):
                block("historical_replay_mapping_incomplete", effect.reference)
            for binding in effect.replay_bindings:
                if (
                    len(binding.source_owner) > 120
                    or len(binding.source_ref)
                    > (512 if binding.operation == "billing.credit_note.issue" else 255)
                    or (
                        binding.determination_ref is not None
                        and len(binding.determination_ref) > 255
                    )
                    or (
                        binding.component_ref is not None
                        and len(binding.component_ref) > 255
                    )
                ):
                    block("historical_replay_mapping_incomplete", effect.reference)
                if (
                    binding.operation == "billing.credit_note.issue"
                    and binding.source_owner != "dotmac-billing"
                ):
                    block("historical_replay_mapping_incomplete", effect.reference)
                if binding.operation == "billing.withholding.tax_component":
                    if (
                        binding.determination_ref is None
                        or binding.component_ref is None
                        or binding.source_ref
                        != opening_withholding_component_identity(
                            tax_source_owner=binding.source_owner,
                            determination_ref=binding.determination_ref,
                            component_ref=binding.component_ref,
                        )
                    ):
                        block("historical_replay_mapping_incomplete", effect.reference)
                elif (
                    binding.determination_ref is not None
                    or binding.component_ref is not None
                ):
                    block("historical_replay_mapping_incomplete", effect.reference)
                try:
                    binding_key = _opening_replay_identity(binding)
                except OpeningEvidenceError:
                    block("historical_replay_mapping_incomplete", effect.reference)
                    continue
                previous = replay_keys.setdefault(binding_key, binding.evidence_digest)
                if previous != binding.evidence_digest:
                    block("historical_replay_mapping_drift", effect.reference)
                previous_partition = replay_partitions.setdefault(
                    binding_key, item.partition
                )
                if previous_partition != item.partition:
                    block("historical_replay_partition_mismatch", effect.reference)
            if effect.kind == "payment":
                lot = cash_keys.get((effect.source_system, effect.source_key))
                if (
                    lot is None
                    or lot.partition != item.partition
                    or not any(
                        use.reference == effect.reference
                        and use.target_ref == key
                        and use.amount_minor == effect.amount_minor
                        for use in lot.consumptions
                    )
                ):
                    block("historical_cash_source_unclaimed", effect.reference)
        if discharged < 0 or total - discharged != item.residual_minor:
            block("receivable_equation_mismatch", key)
        collection = item.collection
        if (
            collection.status == "verified"
            and not collection.automated_collection_allowed
        ):
            block("collection_evidence_incomplete", key)
    for key, count in identities.items():
        if count != 1:
            block("receivable_identity_duplicate", str(key))
    for key, count in invoice_keys.items():
        if count != 1:
            block("receivable_invoice_duplicate", key)
    represented = Counter(x.source_identity for x in manifest.cash_lots)
    represented.update(x.source_identity for x in manifest.receivables)
    for identity in eligible:
        if represented[identity] != 1:
            block("eligible_source_unrepresented", identity.key)
    for key, count in history_keys.items():
        if count != 1:
            block("historical_discharge_duplicate", str(key))
    for control in manifest.controls:
        key = control.partition.source_account_key
        if (
            control.collectible_receivable_minor != ar_totals[control.partition]
            or control.open_ar_minor != ar_totals[control.partition]
        ):
            block("receivable_control_mismatch", key)
        for field in fields(control):
            if (
                field.name
                not in {
                    "partition",
                    "available_cash_credit_minor",
                    "collectible_receivable_minor",
                    "open_ar_minor",
                }
                and getattr(control, field.name) != 0
            ):
                block("unsupported_lane", f"{key}:{field.name}")
        try:
            money_from_minor(
                control.open_ar_minor,
                control.partition.currency,
                control.partition.precision,
            )
            money_from_minor(
                control.available_cash_credit_minor,
                control.partition.currency,
                control.partition.precision,
            )
        except OpeningEvidenceError:
            block("currency_precision_invalid", key)
    return tuple(
        OpeningBlockerV1(code, len(refs), tuple(refs))
        for code, refs in sorted(failures.items())
    )


def preview_opening_account_import_v2(
    command: ImportOpeningAccountV2,
    *,
    verifier: OpeningAccountImportVerifierV2 | None = None,
) -> OpeningImportPreviewV2:
    if type(command) is not ImportOpeningAccountV2:
        raise OpeningEvidenceError("expected ImportOpeningAccountV2")
    blockers = list(opening_account_import_blockers_v2(command))
    try:
        verified = verify_opening_account_import_v2(command, verifier)
    except OpeningVerificationUnavailable:
        blockers.append(OpeningBlockerV1("verification_unavailable", 1, ("assembly",)))
    else:
        if verified is None:
            code = (
                "verifier_unavailable"
                if verifier is None
                else "verification_binding_mismatch"
            )
            blockers.append(OpeningBlockerV1(code, 1, ("assembly",)))
    return OpeningImportPreviewV2(
        "billing.opening.import.v2",
        opening_manifest_fingerprint_v2(command.manifest),
        command.partition,
        command.target,
        not blockers,
        sum(
            x.residual_minor
            for x in command.manifest.cash_lots
            if x.partition == command.partition
        ),
        sum(
            x.residual_minor
            for x in command.manifest.receivables
            if x.partition == command.partition
        ),
        tuple(blockers),
    )
