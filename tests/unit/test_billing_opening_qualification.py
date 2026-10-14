"""Provider fixtures are independent held test evidence, never production issuers."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from typing import cast
from uuid import uuid4

import pytest
from dotmac_billing.opening_contracts import (
    OpeningCaptureV1,
    OpeningEvidenceError,
    OpeningVerificationUnavailable,
)
from dotmac_billing.opening_qualification import (
    OpeningImportGrantV2,
    OpeningSourceEvidenceV2,
    qualify_opening_account_import_v2,
)
from dotmac_billing.opening_receivable_contracts import (
    ImportOpeningAccountV2,
    OpeningImportReversalVerificationRequestV2,
    OpeningImportReversalVerificationV2,
    OpeningImportVerificationRequestV2,
    OpeningImportVerificationV2,
    OpeningManifestV2,
)
from dotmac_billing.opening_receivable_validation import (
    opening_account_command_digest_v2,
    opening_account_import_blockers_v2,
    opening_manifest_fingerprint_v2,
    preview_opening_account_import_v2,
)
from dotmac_kernel.cache import Scope, TenantScope

from tests.unit import test_billing_opening_receivable_contracts as ar


def _request(command: ImportOpeningAccountV2) -> OpeningImportVerificationRequestV2:
    return OpeningImportVerificationRequestV2(
        command,
        opening_account_command_digest_v2(command),
        opening_manifest_fingerprint_v2(command.manifest),
    )


def _evidence(manifest: OpeningManifestV2) -> OpeningSourceEvidenceV2:
    return OpeningSourceEvidenceV2(
        manifest,
        "provider:source-binding",
        "provider:cash-lifecycle",
        "provider:ar-lifecycle",
        "provider:documentary-tax",
        "provider:historical-discharge",
    )


class HeldSource:
    def __init__(self, evidence: OpeningSourceEvidenceV2 | None) -> None:
        self.evidence = evidence
        self.calls: list[tuple[Scope, OpeningCaptureV1]] = []
        self.error: Exception | None = None

    def load_source(
        self, *, scope: Scope, capture: OpeningCaptureV1
    ) -> OpeningSourceEvidenceV2 | None:
        self.calls.append((scope, capture))
        if self.error is not None:
            raise self.error
        return self.evidence


class HeldGrant:
    def __init__(self, grant: OpeningImportGrantV2 | None) -> None:
        self.grant = grant
        self.calls: list[OpeningImportVerificationRequestV2] = []
        self.error: Exception | None = None

    def verify_grant(
        self, request: OpeningImportVerificationRequestV2
    ) -> OpeningImportGrantV2 | None:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.grant


def _providers(command: ImportOpeningAccountV2) -> tuple[HeldSource, HeldGrant]:
    return HeldSource(_evidence(command.manifest)), HeldGrant(
        OpeningImportGrantV2(
            "billing.opening.import.v2", command, "provider:fresh-migration-grant"
        )
    )


class SeparateInverseStub:
    """Preview must not ask this separately owned test-only inverse authority."""

    def verify_import_reversal_v2(
        self, request: OpeningImportReversalVerificationRequestV2
    ) -> OpeningImportReversalVerificationV2 | None:
        raise AssertionError("import qualification cannot authorize an inverse")


class PreviewAdapter:
    def __init__(
        self, source: HeldSource, grant: HeldGrant, inverse: SeparateInverseStub
    ) -> None:
        self.source = source
        self.grant = grant
        self.inverse = inverse

    def verify_import_v2(
        self, request: OpeningImportVerificationRequestV2
    ) -> OpeningImportVerificationV2 | None:
        return qualify_opening_account_import_v2(
            request, source_provider=self.source, grant_provider=self.grant
        )

    def verify_import_reversal_v2(
        self, request: OpeningImportReversalVerificationRequestV2
    ) -> OpeningImportReversalVerificationV2 | None:
        return self.inverse.verify_import_reversal_v2(request)


def test_qualified_mixed_cash_and_ar_reaches_existing_preview() -> None:
    command = ar._candidate()
    source, grant = _providers(command)
    result = preview_opening_account_import_v2(
        command, verifier=PreviewAdapter(source, grant, SeparateInverseStub())
    )
    assert result.admissible, result.blockers
    assert result.cash_residual_minor == 6000
    assert result.receivable_residual_minor == 12000
    assert source.calls == [(command.scope, command.manifest.capture)]
    assert grant.calls == [_request(command)]


def test_verification_uses_provider_proofs_and_exact_existing_bindings() -> None:
    command = ar._candidate()
    source, grant = _providers(command)
    request = _request(command)
    result = qualify_opening_account_import_v2(
        request, source_provider=source, grant_provider=grant
    )
    assert result == OpeningImportVerificationV2(
        "billing.opening.import.v2",
        command.scope,
        request.command_digest,
        request.manifest_fingerprint,
        command.partition,
        command.target,
        command.manifest.capture.installation_id,
        command.migration_authority_ref,
        command.manifest.capture.source_authority_ref,
        "provider:source-binding",
        "provider:cash-lifecycle",
        "provider:ar-lifecycle",
        "provider:documentary-tax",
        "provider:historical-discharge",
        "provider:fresh-migration-grant",
    )


@pytest.mark.parametrize("field", ["command_digest", "manifest_fingerprint"])
def test_request_digest_drift_never_reaches_providers(field: str) -> None:
    command = ar._candidate()
    source, grant = _providers(command)
    assert (
        qualify_opening_account_import_v2(
            replace(_request(command), **{field: "untrusted-digest"}),
            source_provider=source,
            grant_provider=grant,
        )
        is None
    )
    assert not source.calls and not grant.calls


@pytest.mark.parametrize(
    "field",
    [
        "idempotency_key",
        "migration_authority_ref",
        "target",
        "partition",
        "occurred_at",
    ],
)
def test_grant_must_bind_exact_command(field: str) -> None:
    command = ar._candidate()
    if field == "target":
        granted = replace(
            command, target=replace(command.target, posting_watermark="other")
        )
    elif field == "partition":
        granted = replace(
            command, partition=replace(command.partition, source_account_key="other")
        )
    elif field == "occurred_at":
        granted = replace(
            command, occurred_at=command.occurred_at + timedelta(seconds=1)
        )
    else:
        granted = replace(command, **{field: "different"})
    source, grant = _providers(granted)
    source.evidence = _evidence(command.manifest)
    assert (
        qualify_opening_account_import_v2(
            _request(command), source_provider=source, grant_provider=grant
        )
        is None
    )


@pytest.mark.parametrize("authority", ["grant", "source"])
def test_cross_scope_authority_is_refused(authority: str) -> None:
    command = ar._candidate()
    source, grant = _providers(command)
    foreign_scope = TenantScope(uuid4())
    foreign_manifest = replace(command.manifest, scope=foreign_scope)
    if authority == "grant":
        grant.grant = OpeningImportGrantV2(
            "billing.opening.import.v2",
            replace(command, scope=foreign_scope, manifest=foreign_manifest),
            "foreign:grant",
        )
    else:
        source.evidence = _evidence(foreign_manifest)
    assert (
        qualify_opening_account_import_v2(
            _request(command), source_provider=source, grant_provider=grant
        )
        is None
    )


@pytest.mark.parametrize(
    "field", ["snapshot_id", "installation_id", "source_authority_ref"]
)
def test_source_capture_affinity_is_explicit(field: str) -> None:
    command = ar._candidate()
    source, grant = _providers(command)
    source.evidence = _evidence(
        replace(
            command.manifest,
            capture=replace(command.manifest.capture, **{field: "foreign"}),
        )
    )
    assert (
        qualify_opening_account_import_v2(
            _request(command), source_provider=source, grant_provider=grant
        )
        is None
    )


def _tampered(command: ImportOpeningAccountV2, case: str) -> ImportOpeningAccountV2:
    manifest = command.manifest
    if case == "unrelated_partition":
        # Alter only a document in the other complete, financially valid partition.
        item = manifest.receivables[1]
        assert item.partition != command.partition
        assert item.original is not None
        manifest = replace(
            manifest,
            receivables=(
                manifest.receivables[0],
                replace(
                    item, original=replace(item.original, document_number="ALTERED")
                ),
            ),
        )
    elif case in ("cash", "zero_residual_lot"):
        index = 0 if case == "cash" else 1
        lots = list(manifest.cash_lots)
        assert lots[1].residual_minor == 0
        confirmation = lots[index].confirmation
        assert confirmation is not None
        lots[index] = replace(
            lots[index], confirmation=replace(confirmation, evidence_digest="d" * 64)
        )
        manifest = replace(manifest, cash_lots=tuple(lots))
    else:
        item = manifest.receivables[0]
        assert item.original is not None
        if case == "document":
            item = replace(
                item, original=replace(item.original, document_number="ALTERED")
            )
        else:
            item = replace(
                item,
                historical_discharges=(
                    replace(item.historical_discharges[0], ledger_ref="altered-ledger"),
                    *item.historical_discharges[1:],
                ),
            )
        manifest = replace(manifest, receivables=(item, *manifest.receivables[1:]))
    return replace(command, manifest=manifest)


def _with_other_partition(command: ImportOpeningAccountV2) -> ImportOpeningAccountV2:
    """Relocate the fixture's second complete cash/AR group without new maths."""
    manifest = command.manifest
    other = replace(
        command.partition, source_account_key="other-account", account_id=uuid4()
    )
    first_item, second_item = manifest.receivables
    first_lot, second_lot = manifest.cash_lots
    moved = {second_item.source_identity, second_lot.source_identity}
    local_control = replace(
        manifest.controls[0],
        collectible_receivable_minor=first_item.residual_minor,
        open_ar_minor=first_item.residual_minor,
    )
    other_control = replace(
        manifest.controls[0],
        partition=other,
        collectible_receivable_minor=second_item.residual_minor,
        open_ar_minor=second_item.residual_minor,
        available_cash_credit_minor=second_lot.residual_minor,
    )
    return replace(
        command,
        manifest=replace(
            manifest,
            receivables=(first_item, replace(second_item, partition=other)),
            cash_lots=(first_lot, replace(second_lot, partition=other)),
            dispositions=tuple(
                replace(row, partition=other) if row.identity in moved else row
                for row in manifest.dispositions
            ),
            controls=(local_control, other_control),
        ),
    )


@pytest.mark.parametrize(
    "case",
    ["unrelated_partition", "zero_residual_lot", "document", "discharge", "cash"],
)
def test_independently_retained_source_refuses_rehashed_candidate(case: str) -> None:
    original = _with_other_partition(ar._candidate())
    changed = _tampered(original, case)
    source = HeldSource(_evidence(original.manifest))
    _, grant = _providers(changed)
    assert not opening_account_import_blockers_v2(original)
    assert not opening_account_import_blockers_v2(changed)
    assert (
        _request(changed).manifest_fingerprint
        != _request(original).manifest_fingerprint
    )
    assert (
        qualify_opening_account_import_v2(
            _request(changed), source_provider=source, grant_provider=grant
        )
        is None
    )


def test_echoed_inconsistent_manifest_still_fails_canonical_preflight() -> None:
    command = ar._candidate()
    manifest = replace(
        command.manifest,
        controls=(replace(command.manifest.controls[0], open_ar_minor=1),),
    )
    inconsistent = replace(command, manifest=manifest)
    source, grant = _providers(inconsistent)
    assert opening_account_import_blockers_v2(inconsistent)
    assert (
        qualify_opening_account_import_v2(
            _request(inconsistent), source_provider=source, grant_provider=grant
        )
        is None
    )
    assert source.calls and grant.calls


@pytest.mark.parametrize("missing", ["source", "grant", "both"])
def test_missing_provider_is_unavailable(missing: str) -> None:
    command = ar._candidate()
    source, grant = _providers(command)
    with pytest.raises(OpeningVerificationUnavailable):
        qualify_opening_account_import_v2(
            _request(command),
            source_provider=None if missing in ("source", "both") else source,
            grant_provider=None if missing in ("grant", "both") else grant,
        )


@pytest.mark.parametrize("denied", ["source", "grant"])
def test_provider_none_is_authoritative_denial(denied: str) -> None:
    command = ar._candidate()
    source, grant = _providers(command)
    if denied == "source":
        source.evidence = None
    else:
        grant.grant = None
    assert (
        qualify_opening_account_import_v2(
            _request(command), source_provider=source, grant_provider=grant
        )
        is None
    )


@pytest.mark.parametrize("provider", ["source", "grant"])
@pytest.mark.parametrize("error", [OpeningVerificationUnavailable, RuntimeError])
def test_provider_errors_propagate(provider: str, error: type[Exception]) -> None:
    command = ar._candidate()
    source, grant = _providers(command)
    failure = error("provider failure")
    if provider == "source":
        source.error = failure
    else:
        grant.error = failure
    with pytest.raises(error) as raised:
        qualify_opening_account_import_v2(
            _request(command), source_provider=source, grant_provider=grant
        )
    assert raised.value is failure


@pytest.mark.parametrize("revoked", ["source", "grant"])
def test_identical_retry_obtains_fresh_authority(revoked: str) -> None:
    command = ar._candidate()
    source, grant = _providers(command)
    request = _request(command)
    assert (
        qualify_opening_account_import_v2(
            request, source_provider=source, grant_provider=grant
        )
        is not None
    )
    if revoked == "source":
        source.evidence = None
    else:
        grant.grant = None
    assert (
        qualify_opening_account_import_v2(
            request, source_provider=source, grant_provider=grant
        )
        is None
    )
    assert grant.calls == [request, request]
    if revoked == "source":
        assert source.calls == [(command.scope, command.manifest.capture)] * 2


def test_request_and_provider_results_require_exact_closed_types() -> None:
    command = ar._candidate()
    source, grant = _providers(command)
    with pytest.raises(OpeningEvidenceError):
        qualify_opening_account_import_v2(
            cast(OpeningImportVerificationRequestV2, object()),
            source_provider=source,
            grant_provider=grant,
        )
    source.evidence = cast(OpeningSourceEvidenceV2, object())
    assert (
        qualify_opening_account_import_v2(
            _request(command), source_provider=source, grant_provider=grant
        )
        is None
    )
    source.evidence = _evidence(command.manifest)
    grant.grant = cast(OpeningImportGrantV2, object())
    assert (
        qualify_opening_account_import_v2(
            _request(command), source_provider=source, grant_provider=grant
        )
        is None
    )


def test_request_and_provider_subclasses_are_not_closed_authority() -> None:
    class RequestSubclass(OpeningImportVerificationRequestV2):
        pass

    class SourceSubclass(OpeningSourceEvidenceV2):
        pass

    class GrantSubclass(OpeningImportGrantV2):
        pass

    command = ar._candidate()
    source, grant = _providers(command)
    request = _request(command)
    with pytest.raises(OpeningEvidenceError):
        qualify_opening_account_import_v2(
            RequestSubclass(
                command, request.command_digest, request.manifest_fingerprint
            ),
            source_provider=source,
            grant_provider=grant,
        )
    source.evidence = SourceSubclass(
        command.manifest, "source", "cash", "ar", "tax", "discharge"
    )
    assert (
        qualify_opening_account_import_v2(
            request, source_provider=source, grant_provider=grant
        )
        is None
    )
    source.evidence = _evidence(command.manifest)
    grant.grant = GrantSubclass("billing.opening.import.v2", command, "grant")
    assert (
        qualify_opening_account_import_v2(
            request, source_provider=source, grant_provider=grant
        )
        is None
    )


def test_identical_retry_uses_new_provider_proofs() -> None:
    command = ar._candidate()
    source, grant = _providers(command)
    request = _request(command)
    first = qualify_opening_account_import_v2(
        request, source_provider=source, grant_provider=grant
    )
    assert first is not None
    source.evidence = replace(
        _evidence(command.manifest), source_binding_proof_ref="provider:renewed-source"
    )
    grant.grant = OpeningImportGrantV2(
        "billing.opening.import.v2", command, "provider:renewed-grant"
    )
    second = qualify_opening_account_import_v2(
        request, source_provider=source, grant_provider=grant
    )
    assert second is not None
    assert second.source_binding_proof_ref == "provider:renewed-source"
    assert second.migration_authority_proof_ref == "provider:renewed-grant"
    assert first.command_digest == second.command_digest
    assert first.manifest_fingerprint == second.manifest_fingerprint
    assert len(source.calls) == len(grant.calls) == 2


def test_envelopes_are_frozen_closed_values() -> None:
    command = ar._candidate()
    evidence = _evidence(command.manifest)
    grant = OpeningImportGrantV2("billing.opening.import.v2", command, "provider:grant")
    with pytest.raises(FrozenInstanceError):
        evidence.source_binding_proof_ref = "caller-proof"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        grant.migration_authority_proof_ref = "caller-proof"  # type: ignore[misc]
    with pytest.raises(OpeningEvidenceError):
        replace(evidence, cash_lifecycle_proof_ref=" ")
    with pytest.raises(OpeningEvidenceError):
        replace(grant, operation="billing.opening.import.reverse.v2")
