"""DRAFT purpose-bound public Ed25519 verification over trusted read ports.

No private keys, transport, cache, database or financial resolver lives here.
The ports are trusted dependencies, not request-selectable evidence sources.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.semantic_encoding import (
    ABSENT,
    canonical_instant,
    digest_of,
    encode_fields,
    encode_unordered,
)

from dotmac_billing._opening_values import _validate_closed
from dotmac_billing.opening_contracts import (
    OpeningCaptureV1,
    OpeningEvidenceError,
    OpeningSourceIdentityV1,
    OpeningVerificationUnavailable,
)
from dotmac_billing.opening_qualification import (
    OpeningImportGrantV2,
    OpeningSourceEvidenceV2,
)
from dotmac_billing.opening_receivable_contracts import (
    OpeningImportVerificationRequestV2,
)
from dotmac_billing.opening_receivable_validation import (
    opening_account_command_digest_v2,
    opening_manifest_fingerprint_v2,
)
from dotmac_billing.opening_verification_contracts import (
    BillingOpeningCommandGrantV1,
    BillingOpeningSourceAttestationV1,
    OpeningAuthorityPolicyV1,
    OpeningAuthorityReaderV1,
    OpeningEvidenceBindingV1,
    OpeningEvidenceRequirementV1,
    OpeningEvidenceRoleV1,
    OpeningGrantIssuanceV1,
    OpeningImmutableCustodyReaderV1,
    OpeningImmutableEvidenceV1,
    OpeningRevocationStateV1,
    OpeningSignaturePurposeV1,
    OpeningSignatureTermsV1,
    OpeningSignedGrantReaderV1,
    OpeningSignedSourceReaderV1,
    OpeningVerificationClockV1,
    OpeningWindowIdentityReaderV1,
    OpeningWindowIdentityV1,
    SignedOpeningCommandGrantV1,
    SignedOpeningSourceAttestationV1,
)

_SOURCE: Final = "billing.opening.source.attest.v1"
_IMPORT: Final = "billing.opening.import.v2"


def _scope_fields(scope: Scope) -> tuple[tuple[str, object], ...]:
    return (
        ("scope_kind", "tenant" if type(scope) is TenantScope else "platform"),
        ("scope_id", str(scope.tenant_id) if type(scope) is TenantScope else ABSENT),
    )


def _terms_bytes(terms: OpeningSignatureTermsV1) -> bytes:
    return encode_fields(
        (
            ("schema_version", terms.schema_version),
            ("purpose", terms.purpose),
            ("issuer", terms.issuer),
            ("key_id", terms.key_id),
            ("policy_id", terms.policy_id),
            *_scope_fields(terms.scope),
            ("installation_id", terms.installation_id),
            ("audience", terms.audience),
            ("issued_at", canonical_instant(terms.issued_at)),
            ("not_before", canonical_instant(terms.not_before)),
            ("expires_at", canonical_instant(terms.expires_at)),
        )
    )


def _binding_bytes(binding: OpeningEvidenceBindingV1) -> bytes:
    requirement = binding.requirement
    return encode_fields(
        (
            ("role", requirement.role),
            ("reference", requirement.reference),
            (
                "subject_kind",
                requirement.subject.kind if requirement.subject else ABSENT,
            ),
            ("subject_key", requirement.subject.key if requirement.subject else ABSENT),
            (
                "original_issued_at",
                canonical_instant(requirement.original_issued_at)
                if requirement.original_issued_at
                else ABSENT,
            ),
            ("object_id", binding.object_id),
            ("version_id", binding.version_id),
            ("content_sha256", binding.content_sha256),
            ("provenance_ref", binding.provenance_ref),
        )
    )


def opening_source_attestation_digest_v1(
    value: BillingOpeningSourceAttestationV1,
) -> str:
    """Frozen field order; whole-manifest equality retains Billing's V2 owner."""
    _validate_closed(value, BillingOpeningSourceAttestationV1)
    return digest_of(
        _SOURCE,
        encode_fields(
            (
                ("terms", _terms_bytes(value.terms)),
                (
                    "manifest_fingerprint",
                    opening_manifest_fingerprint_v2(value.manifest),
                ),
                ("source_binding_proof_ref", value.source_binding_proof_ref),
                ("cash_lifecycle_proof_ref", value.cash_lifecycle_proof_ref),
                (
                    "receivable_lifecycle_proof_ref",
                    value.receivable_lifecycle_proof_ref,
                ),
                (
                    "documentary_tax_mapping_proof_ref",
                    value.documentary_tax_mapping_proof_ref,
                ),
                (
                    "historical_discharge_mapping_proof_ref",
                    value.historical_discharge_mapping_proof_ref,
                ),
                ("fence_proof_ref", value.fence_proof_ref),
                ("controls_proof_ref", value.controls_proof_ref),
                (
                    "bindings",
                    encode_unordered(_binding_bytes(b) for b in value.bindings),
                ),
            )
        ),
    )


def opening_import_grant_digest_v1(value: BillingOpeningCommandGrantV1) -> str:
    """Frozen fields, existing exact command digest, and immutable issuance terms."""
    _validate_closed(value, BillingOpeningCommandGrantV1)
    return digest_of(
        "billing.opening.command.grant.v1",
        encode_fields(
            (
                ("terms", _terms_bytes(value.terms)),
                ("grant_id", value.grant_id),
                ("approval_decision_ref", value.approval_decision_ref),
                (
                    "exact_command_digest",
                    opening_account_command_digest_v2(value.command),
                ),
                ("command_digest", value.command_digest),
                ("manifest_fingerprint", value.manifest_fingerprint),
                ("source_attestation_digest", value.source_attestation_digest),
                ("window_id", str(value.window_id)),
                ("window_epoch", str(value.window_epoch)),
                ("migration_authority_proof_ref", value.migration_authority_proof_ref),
            )
        ),
    )


def _signing_input(purpose: str, digest: str) -> bytes:
    # ASCII digest text, purpose separated, with no newline or implicit JSON.
    return f"dotmac-billing:{purpose}:{digest}".encode("ascii")


def opening_source_attestation_signing_input_v1(
    value: BillingOpeningSourceAttestationV1,
) -> bytes:
    return _signing_input(_SOURCE, opening_source_attestation_digest_v1(value))


def opening_import_grant_signing_input_v1(value: BillingOpeningCommandGrantV1) -> bytes:
    return _signing_input(
        "billing.opening.command.grant.v1", opening_import_grant_digest_v1(value)
    )


def required_opening_evidence_v1(
    value: BillingOpeningSourceAttestationV1,
) -> tuple[OpeningEvidenceRequirementV1, ...]:
    """Coverage over the entire manifest; never filter to the requested partition."""
    _validate_closed(value, BillingOpeningSourceAttestationV1)
    requirements: list[OpeningEvidenceRequirementV1] = []

    def add(
        role: OpeningEvidenceRoleV1,
        reference: str,
        subject: OpeningSourceIdentityV1 | None = None,
        original_at: datetime | None = None,
    ) -> None:
        requirement = OpeningEvidenceRequirementV1(
            role, reference, subject, original_at
        )
        if requirement not in requirements:
            requirements.append(requirement)

    for role, reference in (
        ("source_binding", value.source_binding_proof_ref),
        ("cash_lifecycle", value.cash_lifecycle_proof_ref),
        ("receivable_lifecycle", value.receivable_lifecycle_proof_ref),
        ("documentary_tax_mapping", value.documentary_tax_mapping_proof_ref),
        ("historical_discharge_mapping", value.historical_discharge_mapping_proof_ref),
        ("fence", value.fence_proof_ref),
        ("controls", value.controls_proof_ref),
    ):
        add(role, reference)  # type: ignore[arg-type]
    for identity in value.manifest.population:
        add("population", identity.key, identity)
    for lot in value.manifest.cash_lots:
        identity = lot.source_identity
        if lot.confirmation is not None:
            add("confirmation", lot.confirmation.reference, identity)
        for consumption in lot.consumptions:
            add("consumption", consumption.reference, identity)
            for consumption_reference in (
                consumption.ledger_ref,
                consumption.target_ref,
                consumption.reverses_ref,
            ):
                if consumption_reference is not None:
                    add("consumption", consumption_reference, identity)
    for item in value.manifest.receivables:
        identity = item.source_identity
        add("receivable", item.source_invoice_key, identity)
        add("collection", item.collection.evidence_ref, identity)
        if item.original is not None:
            original = item.original
            add(
                "original_document",
                original.documentary_reference,
                identity,
                original.issued_at,
            )
            add("tax", original.tax_evidence_ref, identity)
            add("fx", original.fx_evidence_ref, identity)
        for discharge in item.historical_discharges:
            add("discharge", discharge.reference, identity)
            add("discharge", discharge.ledger_ref, identity)
            add("tax", discharge.tax_evidence_ref, identity)
            if discharge.reverses_ref is not None:
                add("discharge", discharge.reverses_ref, identity)
            for replay in discharge.replay_bindings:
                add("discharge", replay.source_ref, identity)
                for replay_reference in (
                    replay.determination_ref,
                    replay.component_ref,
                ):
                    if replay_reference is not None:
                        add("tax", replay_reference, identity)
    return tuple(requirements)


def _hex(value: str, length: int) -> bytes | None:
    if len(value) != length * 2 or any(c not in "0123456789abcdef" for c in value):
        return None
    return bytes.fromhex(value)


def _validate_policy(policy: OpeningAuthorityPolicyV1) -> None:
    _validate_closed(policy, OpeningAuthorityPolicyV1)
    if (
        policy.source_key.purpose != _SOURCE
        or policy.grant_key.purpose != _IMPORT
        or policy.source_key.key_id == policy.grant_key.key_id
        or policy.source_key.public_key_hex == policy.grant_key.public_key_hex
        or policy.max_validity_seconds <= 0
        or policy.max_revocation_age_seconds <= 0
        or policy.not_before >= policy.expires_at
    ):
        raise OpeningEvidenceError("invalid opening authority policy composition")
    for key in (policy.source_key, policy.grant_key):
        public = _hex(key.public_key_hex, 32)
        if (
            public is None
            or sha256(public).hexdigest() != key.public_key_fingerprint
            or key.not_before >= key.expires_at
        ):
            raise OpeningEvidenceError(
                "invalid opening authority public key configuration"
            )


def _authenticate(
    terms: OpeningSignatureTermsV1,
    signature_hex: str,
    signing_input: bytes,
    *,
    purpose: OpeningSignaturePurposeV1,
    scope: Scope,
    installation_id: str,
    authority: OpeningAuthorityReaderV1,
    clock: OpeningVerificationClockV1,
    grant_id: str | None = None,
    paired_source: SignedOpeningSourceAttestationV1 | None = None,
) -> bool:
    policy = authority.current_policy(scope=scope, installation_id=installation_id)
    if policy is None:
        raise OpeningVerificationUnavailable("opening authority policy unavailable")
    _validate_policy(policy)
    if policy.scope != scope or policy.installation_id != installation_id:
        raise OpeningEvidenceError(
            "opening authority reader returned foreign configuration"
        )
    revocations = authority.current_revocations(
        scope=scope, installation_id=installation_id, policy_id=policy.policy_id
    )
    if revocations is None:
        raise OpeningVerificationUnavailable("opening revocation state unavailable")
    _validate_closed(revocations, OpeningRevocationStateV1)
    now = clock.now()
    _validate_closed(now, datetime)
    if (
        revocations.scope != scope
        or revocations.installation_id != installation_id
        or revocations.policy_id != policy.policy_id
        or revocations.checked_at > now
        or now - revocations.checked_at
        > timedelta(seconds=policy.max_revocation_age_seconds)
    ):
        raise OpeningVerificationUnavailable("opening revocation state is not current")
    # Both purposes use the SAME final snapshot; no external read can advance
    # time or revocation between the two pure evaluations.
    if paired_source is not None:
        _validate_closed(paired_source, SignedOpeningSourceAttestationV1)
        if not _authenticate_snapshot(
            paired_source.attestation.terms,
            paired_source.signature_hex,
            opening_source_attestation_signing_input_v1(paired_source.attestation),
            purpose=_SOURCE,
            scope=scope,
            installation_id=installation_id,
            policy=policy,
            revocations=revocations,
            now=now,
        ):
            return False
    return _authenticate_snapshot(
        terms,
        signature_hex,
        signing_input,
        purpose=purpose,
        scope=scope,
        installation_id=installation_id,
        policy=policy,
        revocations=revocations,
        now=now,
        grant_id=grant_id,
    )


def _authenticate_snapshot(
    terms: OpeningSignatureTermsV1,
    signature_hex: str,
    signing_input: bytes,
    *,
    purpose: OpeningSignaturePurposeV1,
    scope: Scope,
    installation_id: str,
    policy: OpeningAuthorityPolicyV1,
    revocations: OpeningRevocationStateV1,
    now: datetime,
    grant_id: str | None = None,
) -> bool:
    key = policy.source_key if purpose == _SOURCE else policy.grant_key
    if (
        terms.purpose != purpose
        or terms.scope != scope
        or terms.installation_id != installation_id
        or terms.issuer != key.issuer
        or terms.key_id != key.key_id
        or terms.policy_id != policy.policy_id
        or terms.audience != policy.audience
        or not policy.not_before <= terms.issued_at <= now < policy.expires_at
        or not key.not_before <= terms.issued_at <= now < key.expires_at
        or not terms.issued_at <= terms.not_before <= now < terms.expires_at
        or terms.expires_at > min(key.expires_at, policy.expires_at)
        or terms.expires_at - terms.issued_at
        > timedelta(seconds=policy.max_validity_seconds)
        or key.key_id in revocations.revoked_key_ids
        or key.issuer in revocations.revoked_issuers
        or grant_id in revocations.revoked_grant_ids
    ):
        return False
    signature = _hex(signature_hex, 64)
    if signature is None:
        return False
    # Configuration was validated above; only actual invalid signatures are caught.
    public = bytes.fromhex(key.public_key_hex)
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, signing_input)
    except InvalidSignature:
        return False
    return True


def _source_is_authentic(
    signed: SignedOpeningSourceAttestationV1,
    *,
    scope: Scope,
    capture: OpeningCaptureV1,
    authority: OpeningAuthorityReaderV1,
    clock: OpeningVerificationClockV1,
) -> bool:
    _validate_closed(signed, SignedOpeningSourceAttestationV1)
    source = signed.attestation
    return (
        source.manifest.scope == scope
        and source.manifest.capture == capture
        and _authenticate(
            source.terms,
            signed.signature_hex,
            opening_source_attestation_signing_input_v1(source),
            purpose=_SOURCE,
            scope=scope,
            installation_id=capture.installation_id,
            authority=authority,
            clock=clock,
        )
    )


class SignedOpeningSourceEvidenceProviderV2:
    """Verify complete source and independently authenticated retained objects."""

    def __init__(
        self,
        *,
        authority: OpeningAuthorityReaderV1,
        source: OpeningSignedSourceReaderV1,
        custody: OpeningImmutableCustodyReaderV1,
        clock: OpeningVerificationClockV1,
    ) -> None:
        self._authority, self._source, self._custody, self._clock = (
            authority,
            source,
            custody,
            clock,
        )

    def load_source(
        self, *, scope: Scope, capture: OpeningCaptureV1
    ) -> OpeningSourceEvidenceV2 | None:
        signed = self._source.load_attestation(scope=scope, capture=capture)
        if signed is None:
            return None
        if not _source_is_authentic(
            signed,
            scope=scope,
            capture=capture,
            authority=self._authority,
            clock=self._clock,
        ):
            return None
        value = signed.attestation
        if (
            value.terms.issued_at < capture.captured_at
            or any(item.original is None for item in value.manifest.receivables)
            or any(lot.confirmation is None for lot in value.manifest.cash_lots)
        ):
            return None
        required = required_opening_evidence_v1(value)
        actual = tuple(b.requirement for b in value.bindings)
        if len(actual) != len(set(actual)) or set(actual) != set(required):
            return None
        for binding in value.bindings:
            content_digest = _hex(binding.content_sha256, 32)
            if content_digest is None:
                return None
            evidence = self._custody.authenticate_immutable(
                binding, scope=scope, capture=capture
            )
            if evidence is None:
                return None
            _validate_closed(evidence, OpeningImmutableEvidenceV1)
            if (
                evidence.object_id != binding.object_id
                or evidence.version_id != binding.version_id
                or evidence.provenance_ref != binding.provenance_ref
                or sha256(evidence.content).digest() != content_digest
                or evidence.provenance == "regenerated"
            ):
                return None
            issued = binding.requirement.original_issued_at
            if issued is not None and (
                evidence.provenance != "issue_time_original"
                or evidence.original_issued_at != issued
            ):
                return None
        # Recheck freshness after custody I/O; authority is never cached.
        if not _source_is_authentic(
            signed,
            scope=scope,
            capture=capture,
            authority=self._authority,
            clock=self._clock,
        ):
            return None
        return OpeningSourceEvidenceV2(
            value.manifest,
            value.source_binding_proof_ref,
            value.cash_lifecycle_proof_ref,
            value.receivable_lifecycle_proof_ref,
            value.documentary_tax_mapping_proof_ref,
            value.historical_discharge_mapping_proof_ref,
            opening_source_attestation_digest_v1(value),
        )


class SignedOpeningImportGrantProviderV2:
    """Current exact import approval; immutable issuance is distinct from usage."""

    def __init__(
        self,
        *,
        authority: OpeningAuthorityReaderV1,
        source: OpeningSignedSourceReaderV1,
        grants: OpeningSignedGrantReaderV1,
        windows: OpeningWindowIdentityReaderV1,
        clock: OpeningVerificationClockV1,
    ) -> None:
        self._authority, self._source, self._grants, self._windows, self._clock = (
            authority,
            source,
            grants,
            windows,
            clock,
        )

    def verify_grant(
        self, request: OpeningImportVerificationRequestV2
    ) -> OpeningImportGrantV2 | None:
        _validate_closed(request, OpeningImportVerificationRequestV2)
        command = request.command
        digest = opening_account_command_digest_v2(command)
        fingerprint = opening_manifest_fingerprint_v2(command.manifest)
        if (
            request.command_digest != digest
            or request.manifest_fingerprint != fingerprint
        ):
            return None
        signed = self._grants.load_grant(request)
        if signed is None:
            return None
        _validate_closed(signed, SignedOpeningCommandGrantV1)
        value = signed.grant
        installation = command.manifest.capture.installation_id
        source = self._source.load_attestation(
            scope=command.scope, capture=command.manifest.capture
        )
        if source is None or not _source_is_authentic(
            source,
            scope=command.scope,
            capture=command.manifest.capture,
            authority=self._authority,
            clock=self._clock,
        ):
            return None
        source_digest = opening_source_attestation_digest_v1(source.attestation)
        if (
            opening_manifest_fingerprint_v2(source.attestation.manifest) != fingerprint
            or opening_account_command_digest_v2(value.command) != digest
            or value.command.scope != command.scope
            or value.command_digest != digest
            or value.manifest_fingerprint != fingerprint
            or value.source_attestation_digest != source_digest
        ):
            return None
        issuance = self._grants.load_issuance(
            scope=command.scope, installation_id=installation, grant_id=value.grant_id
        )
        if issuance is None:
            return None
        _validate_closed(issuance, OpeningGrantIssuanceV1)
        if issuance != OpeningGrantIssuanceV1(
            value.grant_id,
            opening_import_grant_digest_v1(value),
            digest,
            command.idempotency_key,
            command.scope,
            installation,
        ):
            return None
        window = self._windows.current_window(scope=command.scope)
        if window is None:
            return None
        _validate_closed(window, OpeningWindowIdentityV1)
        if (
            window.scope != command.scope
            or window.window_id != value.window_id
            or window.epoch != value.window_epoch
            or window.manifest_fingerprint != fingerprint
        ):
            return None
        # Issuance/window I/O may outlive or revoke the retained source authority.
        # Reload and bind the current source before the final fresh grant check.
        fresh_source = self._source.load_attestation(
            scope=command.scope, capture=command.manifest.capture
        )
        if fresh_source is None or not _source_is_authentic(
            fresh_source,
            scope=command.scope,
            capture=command.manifest.capture,
            authority=self._authority,
            clock=self._clock,
        ):
            return None
        if (
            opening_source_attestation_digest_v1(fresh_source.attestation)
            != source_digest
            or value.source_attestation_digest != source_digest
        ):
            return None
        # Both source and grant use the last shared authority/time snapshot.
        if not _authenticate(
            value.terms,
            signed.signature_hex,
            opening_import_grant_signing_input_v1(value),
            purpose=_IMPORT,
            scope=command.scope,
            installation_id=installation,
            authority=self._authority,
            clock=self._clock,
            grant_id=value.grant_id,
            paired_source=fresh_source,
        ):
            return None
        return OpeningImportGrantV2(
            _IMPORT, command, value.migration_authority_proof_ref, source_digest
        )
