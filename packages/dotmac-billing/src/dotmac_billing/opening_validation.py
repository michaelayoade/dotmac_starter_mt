"""Pure evidence preflight. No writer, storage, funds or authority activation."""

from __future__ import annotations

from collections import Counter
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.fingerprints import fingerprint_of

from dotmac_billing.opening_contracts import (
    OpeningBlockerV1,
    OpeningEvidenceError,
    OpeningEvidenceVerifier,
    OpeningImportPreviewV1,
    OpeningManifestV1,
    OpeningPartitionV1,
    OpeningPreviewRequestV1,
    OpeningSourceIdentityV1,
    OpeningVerificationUnavailable,
    OpeningVerificationV1,
)

_OPERATION = "billing.opening.preview.v1"


def _payload(value: object) -> object:
    # Shape mapping only: the qualified Kernel helper owns wire encoding.
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__name__,
            "fields": {
                field.name: _payload(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, tuple):
        return [_payload(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def opening_manifest_fingerprint(manifest: OpeningManifestV1) -> str:
    """Versioned candidate-content identity, never proof that content is true.

    All identities, provenance, counts, mappings, cutoffs and ordered evidence
    participate. No sorting away evidence order, balances-only digest or seal.
    """
    if type(manifest) is not OpeningManifestV1:
        raise OpeningEvidenceError("expected OpeningManifestV1")
    return fingerprint_of(
        {"domain": "billing.opening.manifest.v1", "candidate": _payload(manifest)}
    )


def preview_opening_account_import(
    request: OpeningPreviewRequestV1,
    *,
    verifier: OpeningEvidenceVerifier | None = None,
) -> OpeningImportPreviewV1:
    """Refuse unsupported evidence without mutating or creating financial facts.

    Verifier injection belongs to trusted application assembly, never request
    deserialization. Successful evidence preflight is not import authority or
    available funding. A future atomic writer must recheck the current target,
    claim the shared live cash-source namespace and reverify authority.
    """
    if type(request) is not OpeningPreviewRequestV1:
        raise OpeningEvidenceError("expected OpeningPreviewRequestV1")
    manifest = request.manifest
    capture = manifest.capture
    selected = request.partition
    fingerprint = opening_manifest_fingerprint(manifest)
    failures: dict[str, list[str]] = {}

    def block(code: str, reference: str) -> None:
        failures.setdefault(code, []).append(reference)

    if (
        request.target.scope != manifest.scope
        or request.target.account_id != selected.account_id
        or request.target.currency != selected.currency
        or request.target.precision != selected.precision
    ):
        block("target_binding_mismatch", "target")
    if (
        not capture.schema_heads
        or capture.isolation not in {"repeatable_read", "serializable"}
        or capture.economic_cutoff > capture.captured_at
        or capture.recorded_cutoff > capture.captured_at
    ):
        block("capture_incomplete", capture.snapshot_id)

    population = Counter(manifest.population)
    dispositions = Counter(item.identity for item in manifest.dispositions)
    if population != dispositions:
        block("population_disposition_mismatch", "population")
    for identity, count in population.items():
        if count != 1:
            block("source_identity_duplicate", f"{identity.kind}:{identity.key}")
    observed = Counter(item.disposition for item in manifest.dispositions)
    counts = manifest.counts
    if (
        counts.population != len(manifest.population)
        or counts.eligible != observed["eligible"]
        or counts.excluded != observed["excluded"]
        or counts.blocking != observed["blocking"]
        or counts.population != counts.eligible + counts.excluded + counts.blocking
        or min(counts.population, counts.eligible, counts.excluded, counts.blocking) < 0
    ):
        block("population_counts_mismatch", "counts")

    partitions = Counter(item.partition for item in manifest.controls)
    if partitions[selected] != 1:
        block("partition_missing", selected.source_account_key)
    target_coordinates = Counter(
        (item.partition.account_id, item.partition.currency)
        for item in manifest.controls
    )
    for coordinate, count in target_coordinates.items():
        if count != 1:
            block("account_mapping_ambiguous", str(coordinate))
    target_mappings: dict[tuple[UUID, str], set[str]] = {}
    source_mappings: dict[tuple[str, str], set[UUID]] = {}
    for control in manifest.controls:
        part = control.partition
        if partitions[part] != 1:
            block("partition_duplicate", part.source_account_key)
        if (
            not 0 <= part.precision <= 18
            or len(part.currency) != 3
            or not part.currency.isascii()
            or not part.currency.isalpha()
            or not part.currency.isupper()
        ):
            block("currency_precision_invalid", part.source_account_key)
        target_mappings.setdefault((part.account_id, part.currency), set()).add(
            part.source_account_key
        )
        source_mappings.setdefault((part.source_account_key, part.currency), set()).add(
            part.account_id
        )
    for mapping_coordinate, mapped_values in (
        *target_mappings.items(),
        *source_mappings.items(),
    ):
        if len(mapped_values) != 1:
            block("account_mapping_ambiguous", str(mapping_coordinate))
    for item in manifest.dispositions:
        if item.partition not in partitions:
            block("partition_membership_missing", item.identity.key)
        if item.disposition not in {"eligible", "excluded", "blocking"}:
            block("disposition_unsupported", item.identity.key)
        if item.disposition == "excluded" and item.residual_minor != 0:
            block("excluded_nonzero_residue", item.identity.key)
        if item.disposition == "blocking":
            block("source_blocking_disposition", item.identity.key)

    source_keys: Counter[tuple[str, str, str]] = Counter()
    lot_ids: Counter[str] = Counter()
    lot_identities: Counter[OpeningSourceIdentityV1] = Counter()
    cash_totals: Counter[OpeningPartitionV1] = Counter()
    eligible = {
        item.identity: item.partition
        for item in manifest.dispositions
        if item.disposition == "eligible"
    }
    source_residuals = {
        item.identity: item.residual_minor for item in manifest.dispositions
    }
    consumption_refs: Counter[str] = Counter()
    for lot in manifest.cash_lots:
        source_keys[
            (capture.installation_id, lot.source_system, lot.source_settlement_key)
        ] += 1
        lot_ids[lot.lot_id] += 1
        lot_identities[lot.source_identity] += 1
        cash_totals[lot.partition] += lot.residual_minor
        if (
            lot.partition not in partitions
            or eligible.get(lot.source_identity) != lot.partition
        ):
            block("cash_source_membership_mismatch", lot.lot_id)
        if source_residuals.get(lot.source_identity) != lot.residual_minor:
            block("source_residual_mismatch", lot.lot_id)
        confirmation = lot.confirmation
        if confirmation is None:
            block("cash_confirmation_incomplete", lot.lot_id)
        else:
            if (
                confirmation.cash_amount_minor != lot.gross_minor
                or confirmation.currency != lot.partition.currency
                or confirmation.precision != lot.partition.precision
            ):
                block("cash_confirmation_binding_mismatch", lot.lot_id)
            if (
                confirmation.economic_at > capture.economic_cutoff
                or confirmation.recorded_at > capture.recorded_cutoff
            ):
                block("source_after_cutoff", lot.lot_id)
        if min(lot.gross_minor, lot.residual_minor) < 0:
            block("cash_negative", lot.lot_id)
        consumed = 0
        for effect in lot.consumptions:
            consumption_refs[effect.reference] += 1
            consumed += effect.amount_minor
            if (
                effect.kind not in {"allocation", "refund", "reservation"}
                or effect.ledger_ref is None
                or effect.target_ref is None
                or (effect.kind == "reversal" and effect.reverses_ref is None)
            ):
                block("cash_consumption_unresolved", effect.reference)
            if effect.kind == "reversal" or effect.reverses_ref is not None:
                block("cash_reversal_unsupported", effect.reference)
            if effect.amount_minor < 0:
                block("cash_negative", effect.reference)
            if (
                effect.economic_at > capture.economic_cutoff
                or effect.recorded_at > capture.recorded_cutoff
            ):
                block("source_after_cutoff", effect.reference)
        if lot.gross_minor - consumed != lot.residual_minor:
            block("cash_equation_mismatch", lot.lot_id)
    for source_key, source_count in source_keys.items():
        if source_count != 1:
            block("cash_source_duplicate", str(source_key))
    for lot_id, lot_count in lot_ids.items():
        if lot_count != 1:
            block("cash_lot_duplicate", lot_id)
    for source_identity, identity_count in lot_identities.items():
        if identity_count != 1:
            block("cash_identity_duplicate", str(source_identity))
    for consumption_ref, reference_count in consumption_refs.items():
        if reference_count != 1:
            block("cash_consumption_duplicate", consumption_ref)
    for control in manifest.controls:
        if control.available_cash_credit_minor != cash_totals[control.partition]:
            block("cash_control_mismatch", control.partition.source_account_key)
        if control.partition == selected:
            for field in fields(control):
                if field.name not in {"partition", "available_cash_credit_minor"}:
                    if getattr(control, field.name) != 0:
                        block("unsupported_lane", field.name)
    for identity, part in eligible.items():
        if part == selected and lot_identities[identity] != 1:
            block("eligible_source_unrepresented", identity.key)

    if verifier is None:
        block("verifier_unavailable", "assembly-owned verifier")
    else:
        try:
            verified = verifier.verify(request, fingerprint)
        except OpeningVerificationUnavailable:
            block("verification_unavailable", "assembly-owned verifier")
        else:
            if type(verified) is not OpeningVerificationV1:
                block("verification_unsupported", "source/cash/authority evidence")
            elif (
                verified.operation != _OPERATION
                or verified.scope != manifest.scope
                or verified.manifest_fingerprint != fingerprint
                or verified.partition != selected
                or verified.target != request.target
                or verified.migration_authority_ref != request.migration_authority_ref
                or verified.source_authority_ref != capture.source_authority_ref
            ):
                block("verification_binding_mismatch", "source/cash/authority evidence")
    blockers = tuple(
        OpeningBlockerV1(code, len(refs), tuple(refs))
        for code, refs in sorted(failures.items())
    )
    return OpeningImportPreviewV1(
        _OPERATION,
        fingerprint,
        selected,
        request.target,
        not blockers,
        cash_totals[selected],
        blockers,
    )
