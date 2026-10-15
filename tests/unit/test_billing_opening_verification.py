"""Real Ed25519 canaries; ephemeral fixture keys are hosted-test-only custody."""

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotmac_billing.opening_contracts import OpeningVerificationUnavailable
from dotmac_billing.opening_qualification import qualify_opening_account_import_v2
from dotmac_billing.opening_verification import (
    SignedOpeningImportGrantProviderV2,
    SignedOpeningSourceEvidenceProviderV2,
    opening_import_grant_digest_v1,
    opening_import_grant_signing_input_v1,
    opening_source_attestation_digest_v1,
    opening_source_attestation_signing_input_v1,
    required_opening_evidence_v1,
)
from dotmac_billing.opening_verification_contracts import (
    BillingOpeningCommandGrantV1,
    BillingOpeningSourceAttestationV1,
    OpeningAuthorityPolicyV1,
    OpeningEvidenceBindingV1,
    OpeningGrantIssuanceV1,
    OpeningImmutableEvidenceV1,
    OpeningRevocationStateV1,
    OpeningSignatureTermsV1,
    OpeningTrustKeyV1,
    OpeningWindowIdentityV1,
    SignedOpeningCommandGrantV1,
    SignedOpeningSourceAttestationV1,
)
from dotmac_kernel.cache import TenantScope

from tests.unit import test_billing_opening_qualification as qualification
from tests.unit import test_billing_opening_receivable_contracts as ar


class TrustedPorts:
    """Independent held objects; requests never choose paths or trust keys."""

    def __init__(self):
        self.command = qualification._with_other_partition(ar._candidate())
        self.now_value = self.command.occurred_at
        self.source_private = Ed25519PrivateKey.generate()
        self.grant_private = Ed25519PrivateKey.generate()
        start = self.now_value - timedelta(hours=1)
        end = self.now_value + timedelta(hours=1)

        def key(private, purpose, name):
            public = private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            return OpeningTrustKeyV1(
                name,
                name + "-issuer",
                purpose,
                public.hex(),
                sha256(public).hexdigest(),
                start,
                end,
            )

        self.policy = OpeningAuthorityPolicyV1(
            "policy-v1",
            self.command.scope,
            self.command.manifest.capture.installation_id,
            "billing-test-audience",
            key(self.source_private, "billing.opening.source.attest.v1", "source"),
            key(self.grant_private, "billing.opening.import.v2", "grant"),
            start,
            end,
            7200,
            60,
        )
        self.revocation = OpeningRevocationStateV1(
            "policy-v1",
            self.command.scope,
            self.policy.installation_id,
            self.now_value,
            (),
            (),
            (),
        )
        self.window = OpeningWindowIdentityV1(
            self.command.scope,
            uuid4(),
            uuid4(),
            qualification._request(self.command).manifest_fingerprint,
        )
        self.calls = []
        self.error = None
        self.objects = {}
        header = self.terms("source", "billing.opening.source.attest.v1")
        provisional = BillingOpeningSourceAttestationV1(
            header,
            self.command.manifest,
            "source-proof",
            "cash-proof",
            "ar-proof",
            "tax-proof",
            "discharge-proof",
            "fence-proof",
            "control-proof",
            (),
        )
        bindings = []
        for index, required in enumerate(required_opening_evidence_v1(provisional)):
            content = ("retained-object-" + str(index)).encode()
            binding = OpeningEvidenceBindingV1(
                required,
                "object-" + str(index),
                "immutable-version-1",
                sha256(content).hexdigest(),
                "custody-provenance-" + str(index),
            )
            bindings.append(binding)
            self.objects[binding.object_id] = OpeningImmutableEvidenceV1(
                binding.object_id,
                binding.version_id,
                binding.provenance_ref,
                content,
                "issue_time_original"
                if required.original_issued_at
                else "retained_source",
                required.original_issued_at,
            )
        self.source_payload = replace(provisional, bindings=tuple(bindings))
        self.sign_source()
        request = qualification._request(self.command)
        self.grant_payload = BillingOpeningCommandGrantV1(
            self.terms("grant", "billing.opening.import.v2"),
            "grant-1",
            "decision-1",
            self.command,
            request.command_digest,
            request.manifest_fingerprint,
            opening_source_attestation_digest_v1(self.source_payload),
            self.window.window_id,
            self.window.epoch,
            "grant-proof",
        )
        self.sign_grant()
        self.source_provider = SignedOpeningSourceEvidenceProviderV2(
            authority=self, source=self, custody=self, clock=self
        )
        self.grant_provider = SignedOpeningImportGrantProviderV2(
            authority=self, source=self, grants=self, windows=self, clock=self
        )

    def terms(self, key, purpose):
        return OpeningSignatureTermsV1(
            1,
            purpose,
            key + "-issuer",
            key,
            "policy-v1",
            self.command.scope,
            self.command.manifest.capture.installation_id,
            "billing-test-audience",
            self.now_value,
            self.now_value,
            self.now_value + timedelta(minutes=30),
        )

    def sign_source(self):
        self.signed_source = SignedOpeningSourceAttestationV1(
            self.source_payload,
            self.source_private.sign(
                opening_source_attestation_signing_input_v1(self.source_payload)
            ).hex(),
        )

    def sign_grant(self):
        self.signed_grant = SignedOpeningCommandGrantV1(
            self.grant_payload,
            self.grant_private.sign(
                opening_import_grant_signing_input_v1(self.grant_payload)
            ).hex(),
        )
        self.issuance = OpeningGrantIssuanceV1(
            self.grant_payload.grant_id,
            opening_import_grant_digest_v1(self.grant_payload),
            self.grant_payload.command_digest,
            self.command.idempotency_key,
            self.command.scope,
            self.policy.installation_id,
        )

    def now(self):
        self.calls.append("time")
        return self.now_value

    def current_policy(self, *, scope, installation_id):
        self.calls.append("policy")
        return self.policy

    def current_revocations(self, *, scope, installation_id, policy_id):
        self.calls.append("revocations")
        return self.revocation

    def load_attestation(self, *, scope, capture):
        self.calls.append("source")
        if self.error:
            raise self.error
        return self.signed_source

    def authenticate_immutable(self, binding, *, scope, capture):
        self.calls.append("custody")
        return self.objects.get(binding.object_id)

    def load_grant(self, request):
        self.calls.append("grant")
        return self.signed_grant

    def load_issuance(self, *, scope, installation_id, grant_id):
        self.calls.append("issuance")
        return self.issuance

    def current_window(self, *, scope):
        self.calls.append("window")
        return self.window

    def qualify(self, command=None):
        return qualify_opening_account_import_v2(
            qualification._request(command or self.command),
            source_provider=self.source_provider,
            grant_provider=self.grant_provider,
        )


def test_real_signatures_independent_custody_and_full_mixed_population():
    ports = TrustedPorts()
    result = ports.qualify()
    assert result is not None
    assert result.source_binding_proof_ref == "source-proof"
    assert ports.calls.index("grant") > ports.calls.index("custody")
    assert ports.qualify() is not None
    assert ports.calls.count("issuance") == 2
    assert ports.calls.count("source") == 6


@pytest.mark.parametrize("which", ["source", "grant"])
def test_invalid_signature_is_authoritative_denial(which):
    ports = TrustedPorts()
    value = getattr(ports, "signed_" + which)
    setattr(ports, "signed_" + which, replace(value, signature_hex="00" * 64))
    assert ports.qualify() is None


@pytest.mark.parametrize("which", ["source", "grant"])
@pytest.mark.parametrize(
    "field", ["issuer", "key_id", "policy_id", "audience", "installation_id", "scope"]
)
def test_validly_signed_wrong_authority_terms_are_denied(which, field):
    ports = TrustedPorts()
    payload = getattr(ports, which + "_payload")
    changed = TenantScope(uuid4()) if field == "scope" else "wrong"
    setattr(
        ports,
        which + "_payload",
        replace(payload, terms=replace(payload.terms, **{field: changed})),
    )
    getattr(ports, "sign_" + which)()
    assert ports.qualify() is None


@pytest.mark.parametrize(
    "case",
    ["unrelated_partition", "zero_residual_lot", "document", "discharge", "cash"],
)
def test_retained_attestation_refuses_rehashed_mutated_complete_manifest(case):
    ports = TrustedPorts()
    assert ports.qualify(qualification._tampered(ports.command, case)) is None


@pytest.mark.parametrize(
    "mutation", ["bytes", "version", "provenance", "regenerated", "missing"]
)
def test_original_objects_require_authenticated_original_provenance(mutation):
    ports = TrustedPorts()
    binding = next(
        b for b in ports.source_payload.bindings if b.requirement.original_issued_at
    )
    original = ports.objects[binding.object_id]
    if mutation == "missing":
        del ports.objects[binding.object_id]
    else:
        field, value = {
            "bytes": ("content", b"current PDF"),
            "version": ("version_id", "current"),
            "provenance": ("provenance_ref", "invented"),
            "regenerated": ("provenance", "regenerated"),
        }[mutation]
        ports.objects[binding.object_id] = replace(original, **{field: value})
    assert ports.qualify() is None


def test_signed_missing_population_binding_is_denied():
    ports = TrustedPorts()
    population_binding = next(
        b for b in ports.source_payload.bindings if b.requirement.role == "population"
    )
    ports.source_payload = replace(
        ports.source_payload,
        bindings=tuple(
            b for b in ports.source_payload.bindings if b != population_binding
        ),
    )
    ports.sign_source()
    assert ports.qualify() is None


@pytest.mark.parametrize(
    "field",
    [
        "source_attestation_digest",
        "command_digest",
        "manifest_fingerprint",
        "grant_id",
        "window_id",
        "window_epoch",
    ],
)
def test_exact_grant_bindings_and_immutable_issuance_cannot_be_repurposed(field):
    ports = TrustedPorts()
    changed = uuid4() if field.startswith("window") else "wrong"
    ports.grant_payload = replace(ports.grant_payload, **{field: changed})
    old_issuance = ports.issuance
    ports.sign_grant()
    if field == "grant_id":
        ports.issuance = old_issuance
    assert ports.qualify() is None


@pytest.mark.parametrize("which", ["source", "grant"])
@pytest.mark.parametrize(
    "wrong",
    [
        "billing.opening.import.reverse.v2",
        "billing.opening.window.provision.v1",
        "billing.opening.window.seal.v1",
    ],
)
def test_wrong_purpose_is_closed_before_signing(which, wrong):
    from dotmac_billing.opening_contracts import OpeningEvidenceError

    ports = TrustedPorts()
    payload = getattr(ports, which + "_payload")
    with pytest.raises(OpeningEvidenceError):
        replace(payload, terms=replace(payload.terms, purpose=wrong))


@pytest.mark.parametrize("revoked", ["key", "issuer", "grant"])
def test_retry_rechecks_revocation(revoked):
    ports = TrustedPorts()
    assert ports.qualify() is not None
    field, values = {
        "key": ("revoked_key_ids", ("source",)),
        "issuer": ("revoked_issuers", ("grant-issuer",)),
        "grant": ("revoked_grant_ids", ("grant-1",)),
    }[revoked]
    ports.revocation = replace(ports.revocation, **{field: values})
    assert ports.qualify() is None


def test_stale_or_missing_revocations_remain_unavailable():
    ports = TrustedPorts()
    ports.revocation = replace(
        ports.revocation, checked_at=ports.now_value - timedelta(seconds=61)
    )
    with pytest.raises(OpeningVerificationUnavailable):
        ports.qualify()
    ports.revocation = None
    with pytest.raises(OpeningVerificationUnavailable):
        ports.qualify()


def test_exact_validity_boundaries_and_grant_is_fresh_after_source_io():
    ports = TrustedPorts()
    ports.now_value = ports.source_payload.terms.expires_at
    with pytest.raises(OpeningVerificationUnavailable):
        ports.qualify()  # Revocation cannot be silently treated as fresh.
    ports.revocation = replace(ports.revocation, checked_at=ports.now_value)
    assert ports.qualify() is None


def test_grant_id_reuse_for_changed_idempotency_is_denied():
    ports = TrustedPorts()
    ports.issuance = replace(ports.issuance, idempotency_key="other-command")
    assert ports.qualify() is None


def test_current_window_epoch_mismatch_denied_and_no_seal_flag_is_requested():
    ports = TrustedPorts()
    assert ports.qualify() is not None
    ports.window = replace(ports.window, epoch=uuid4())
    assert ports.qualify() is None
    # Port exposes immutable identity only: sealing does not revoke an exact retry.


def test_missing_policy_is_unavailable_and_programmer_errors_escape():
    ports = TrustedPorts()
    ports.policy = None
    with pytest.raises(OpeningVerificationUnavailable):
        ports.qualify()
    ports = TrustedPorts()
    failure = RuntimeError("programmer bug")
    ports.error = failure
    with pytest.raises(RuntimeError) as raised:
        ports.qualify()
    assert raised.value is failure


def test_real_providers_reach_existing_preview_with_separate_inverse_delegate():
    from dotmac_billing.opening_receivable_validation import (
        preview_opening_account_import_v2,
    )

    ports = TrustedPorts()
    result = preview_opening_account_import_v2(
        ports.command,
        verifier=qualification.PreviewAdapter(
            ports.source_provider,
            ports.grant_provider,
            qualification.SeparateInverseStub(),
        ),
    )
    assert result.admissible, result.blockers
    assert result.cash_residual_minor == 6000
    # The second partition is authenticated, but only the selected one is imported.
    assert result.receivable_residual_minor == 7000


@pytest.mark.parametrize("which", ["source", "grant"])
def test_correctly_signed_other_opening_purpose_is_denied(which):
    ports = TrustedPorts()
    payload = getattr(ports, which + "_payload")
    other = (
        "billing.opening.import.v2"
        if which == "source"
        else "billing.opening.source.attest.v1"
    )
    setattr(
        ports,
        which + "_payload",
        replace(payload, terms=replace(payload.terms, purpose=other)),
    )
    getattr(ports, "sign_" + which)()
    assert ports.qualify() is None


@pytest.mark.parametrize("which", ["source", "grant"])
def test_actual_wrong_key_signature_is_denied(which):
    ports = TrustedPorts()
    payload = getattr(ports, which + "_payload")
    wrong = ports.grant_private if which == "source" else ports.source_private
    signing_input = (
        opening_source_attestation_signing_input_v1(payload)
        if which == "source"
        else opening_import_grant_signing_input_v1(payload)
    )
    setattr(
        ports,
        "signed_" + which,
        replace(
            getattr(ports, "signed_" + which),
            signature_hex=wrong.sign(signing_input).hex(),
        ),
    )
    assert ports.qualify() is None


def test_same_source_and_grant_key_is_a_composition_error():
    from dotmac_billing.opening_contracts import OpeningEvidenceError

    ports = TrustedPorts()
    ports.policy = replace(
        ports.policy,
        grant_key=replace(
            ports.policy.grant_key,
            public_key_hex=ports.policy.source_key.public_key_hex,
            public_key_fingerprint=ports.policy.source_key.public_key_fingerprint,
        ),
    )
    with pytest.raises(OpeningEvidenceError):
        ports.qualify()


@pytest.mark.parametrize("field", ["original", "confirmation"])
def test_signed_missing_original_or_cash_confirmation_is_denied(field):
    ports = TrustedPorts()
    manifest = ports.source_payload.manifest
    if field == "original":
        manifest = replace(
            manifest,
            receivables=(
                replace(manifest.receivables[0], original=None),
                *manifest.receivables[1:],
            ),
        )
    else:
        manifest = replace(
            manifest,
            cash_lots=(
                replace(manifest.cash_lots[0], confirmation=None),
                *manifest.cash_lots[1:],
            ),
        )
    ports.source_payload = replace(ports.source_payload, manifest=manifest)
    ports.sign_source()
    assert (
        ports.source_provider.load_source(
            scope=manifest.scope, capture=manifest.capture
        )
        is None
    )


def test_custody_io_cannot_leave_a_stale_grant_admissible():
    ports = TrustedPorts()
    original = ports.authenticate_immutable

    def slow_custody(binding, *, scope, capture):
        result = original(binding, scope=scope, capture=capture)
        # Grant expires before source. Authenticated source I/O advances current time.
        ports.now_value = ports.grant_payload.terms.expires_at
        ports.revocation = replace(ports.revocation, checked_at=ports.now_value)
        return result

    ports.source_payload = replace(
        ports.source_payload,
        terms=replace(
            ports.source_payload.terms,
            expires_at=ports.now_value + timedelta(minutes=45),
        ),
    )
    ports.sign_source()
    ports.grant_payload = replace(
        ports.grant_payload,
        source_attestation_digest=opening_source_attestation_digest_v1(
            ports.source_payload
        ),
    )
    ports.sign_grant()
    ports.source_provider = SignedOpeningSourceEvidenceProviderV2(
        authority=ports,
        source=ports,
        custody=type(
            "Custody", (), {"authenticate_immutable": staticmethod(slow_custody)}
        )(),
        clock=ports,
    )
    assert ports.qualify() is None


def test_binding_order_is_unordered_but_mutation_changes_signature_input():
    ports = TrustedPorts()
    original = opening_source_attestation_signing_input_v1(ports.source_payload)
    reversed_value = replace(
        ports.source_payload, bindings=tuple(reversed(ports.source_payload.bindings))
    )
    assert opening_source_attestation_signing_input_v1(reversed_value) == original
    changed = replace(ports.source_payload.bindings[0], version_id="other")
    changed_value = replace(
        ports.source_payload, bindings=(changed, *ports.source_payload.bindings[1:])
    )
    assert opening_source_attestation_signing_input_v1(changed_value) != original


def test_terms_and_ascii_signing_input_golden_vectors(monkeypatch):
    """Literal cv1 header frames are independent of the Kernel encoder calls."""
    from datetime import UTC, datetime

    from dotmac_billing import opening_verification as verification
    from dotmac_kernel.cache import PlatformScope

    instant = datetime(2000, 1, 1, tzinfo=UTC)
    terms = OpeningSignatureTermsV1(
        1,
        "billing.opening.source.attest.v1",
        "i",
        "k",
        "p",
        PlatformScope(),
        "s",
        "a",
        instant,
        instant,
        instant,
    )
    body = (
        b"s14:schema_versioni1:1"
        b"s7:purposes32:billing.opening.source.attest.v1"
        b"s6:issuers1:i"
        b"s6:key_ids1:k"
        b"s9:policy_ids1:p"
        b"s10:scope_kinds8:platform"
        b"s8:scope_idz0:"
        b"s15:installation_ids1:s"
        b"s8:audiences1:a"
        b"s9:issued_ats32:2000-01-01T00:00:00.000000+00:00"
        b"s10:not_befores32:2000-01-01T00:00:00.000000+00:00"
        b"s10:expires_ats32:2000-01-01T00:00:00.000000+00:00"
    )
    assert (
        verification._terms_bytes(terms) == b"o" + str(len(body)).encode() + b":" + body
    )
    vector_digest = "cv1:" + "0123456789abcdef" * 4
    monkeypatch.setattr(
        verification,
        "opening_source_attestation_digest_v1",
        lambda value: vector_digest,
    )
    monkeypatch.setattr(
        verification, "opening_import_grant_digest_v1", lambda value: vector_digest
    )
    # Purpose and ASCII framing are pinned independently of digest computation.
    ports = TrustedPorts()
    assert verification.opening_source_attestation_signing_input_v1(
        ports.source_payload
    ) == (
        b"dotmac-billing:billing.opening.source.attest.v1:cv1:"
        b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )
    assert verification.opening_import_grant_signing_input_v1(ports.grant_payload) == (
        b"dotmac-billing:billing.opening.command.grant.v1:cv1:"
        b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )


def test_revocation_freshness_boundary_and_grant_not_before_boundary():
    ports = TrustedPorts()
    ports.revocation = replace(
        ports.revocation, checked_at=ports.now_value - timedelta(seconds=60)
    )
    assert ports.qualify() is not None
    ports.grant_payload = replace(
        ports.grant_payload,
        terms=replace(
            ports.grant_payload.terms, not_before=ports.now_value + timedelta(seconds=1)
        ),
    )
    ports.sign_grant()
    assert ports.qualify() is None
    ports.now_value += timedelta(seconds=1)
    ports.revocation = replace(ports.revocation, checked_at=ports.now_value)
    assert ports.qualify() is not None


def test_custody_unavailability_propagates_without_authoritative_denial():
    ports = TrustedPorts()
    failure = OpeningVerificationUnavailable("custody transport unavailable")

    class UnavailableCustody:
        def authenticate_immutable(self, binding, *, scope, capture):
            raise failure

    ports.source_provider = SignedOpeningSourceEvidenceProviderV2(
        authority=ports,
        source=ports,
        custody=UnavailableCustody(),
        clock=ports,
    )
    with pytest.raises(OpeningVerificationUnavailable) as raised:
        ports.qualify()
    assert raised.value is failure
    assert "grant" not in ports.calls


def test_signed_label_and_hash_without_original_provenance_are_denied():
    ports = TrustedPorts()
    binding = next(
        b for b in ports.source_payload.bindings if b.requirement.original_issued_at
    )
    original = ports.objects[binding.object_id]
    ports.objects[binding.object_id] = replace(original, provenance="retained_source")
    assert ports.qualify() is None


def test_source_refresh_between_providers_requires_matching_attestation_digest():
    ports = TrustedPorts()
    original_load = ports.load_grant

    def refresh_during_grant(request):
        ports.source_payload = replace(
            ports.source_payload, fence_proof_ref="new-fence"
        )
        ports.sign_source()
        ports.grant_payload = replace(
            ports.grant_payload,
            source_attestation_digest=opening_source_attestation_digest_v1(
                ports.source_payload
            ),
        )
        ports.sign_grant()
        return original_load(request)

    class RefreshingGrants:
        load_grant = staticmethod(refresh_during_grant)
        load_issuance = staticmethod(ports.load_issuance)

    ports.grant_provider = SignedOpeningImportGrantProviderV2(
        authority=ports,
        source=ports,
        grants=RefreshingGrants(),
        windows=ports,
        clock=ports,
    )
    # The refreshed source has not been authenticated by the first provider.
    assert ports.qualify() is None


@pytest.mark.parametrize("during", ["load_issuance", "current_window"])
@pytest.mark.parametrize("withdrawal", ["expiry", "key", "issuer"])
def test_slow_issuance_or_window_read_cannot_leave_stale_source_admissible(
    monkeypatch, during, withdrawal
):
    ports = TrustedPorts()
    # Source expires before the grant, so checking only final grant validity
    # would incorrectly admit the expiry case. Revocation cases keep both valid.
    ports.source_payload = replace(
        ports.source_payload,
        terms=replace(
            ports.source_payload.terms,
            expires_at=ports.now_value + timedelta(minutes=10),
        ),
    )
    ports.sign_source()
    ports.grant_payload = replace(
        ports.grant_payload,
        source_attestation_digest=opening_source_attestation_digest_v1(
            ports.source_payload
        ),
    )
    ports.sign_grant()
    assert ports.qualify() is not None
    original_read = getattr(ports, during)

    def withdrawing_read(**kwargs):
        result = original_read(**kwargs)
        if withdrawal == "expiry":
            ports.now_value = ports.source_payload.terms.expires_at
            ports.revocation = replace(ports.revocation, checked_at=ports.now_value)
            assert ports.now_value < ports.grant_payload.terms.expires_at
        elif withdrawal == "key":
            ports.revocation = replace(ports.revocation, revoked_key_ids=("source",))
        else:
            ports.revocation = replace(
                ports.revocation, revoked_issuers=("source-issuer",)
            )
        return result

    monkeypatch.setattr(ports, during, withdrawing_read)
    assert ports.qualify() is None


@pytest.mark.parametrize("during", ["load_issuance", "current_window"])
def test_source_replacement_during_issuance_or_window_read_is_crossbound(
    monkeypatch, during
):
    ports = TrustedPorts()
    original_read = getattr(ports, during)

    def replacing_read(**kwargs):
        result = original_read(**kwargs)
        ports.source_payload = replace(
            ports.source_payload,
            terms=replace(
                ports.source_payload.terms,
                expires_at=ports.now_value + timedelta(minutes=40),
            ),
        )
        ports.sign_source()
        return result

    monkeypatch.setattr(ports, during, replacing_read)
    assert ports.qualify() is None


def test_zero_residual_other_partition_population_requires_retained_evidence():
    ports = TrustedPorts()
    zero_lot = next(
        lot for lot in ports.command.manifest.cash_lots if lot.residual_minor == 0
    )
    assert zero_lot.partition != ports.command.partition
    missing = next(
        b
        for b in ports.source_payload.bindings
        if b.requirement.role == "population"
        and b.requirement.subject == zero_lot.source_identity
    )
    ports.source_payload = replace(
        ports.source_payload,
        bindings=tuple(b for b in ports.source_payload.bindings if b != missing),
    )
    ports.sign_source()
    # Even a correctly signed full financial manifest needs the independent
    # retained population object for this zero-residual, unrelated partition.
    assert (
        ports.source_provider.load_source(
            scope=ports.command.scope, capture=ports.command.manifest.capture
        )
        is None
    )


@pytest.mark.parametrize("withdrawal", ["expiry", "key", "issuer"])
def test_final_grant_revocation_read_also_qualifies_source(monkeypatch, withdrawal):
    ports = TrustedPorts()
    ports.source_payload = replace(
        ports.source_payload,
        terms=replace(
            ports.source_payload.terms,
            expires_at=ports.now_value + timedelta(minutes=10),
        ),
    )
    ports.sign_source()
    ports.grant_payload = replace(
        ports.grant_payload,
        source_attestation_digest=opening_source_attestation_digest_v1(
            ports.source_payload
        ),
    )
    ports.sign_grant()
    original_revocations = ports.current_revocations
    original_source = ports.load_attestation
    source_reads = 0
    final_source_authenticated = False

    def mark_final_source(**kwargs):
        nonlocal source_reads
        source_reads += 1
        return original_source(**kwargs)

    def withdraw_on_final_grant_read(**kwargs):
        nonlocal final_source_authenticated
        # The final source reload is read 3. Its first revocation read
        # authenticates source; the following read is final grant authority.
        if source_reads == 3:
            if not final_source_authenticated:
                final_source_authenticated = True
            else:
                if withdrawal == "expiry":
                    ports.now_value = ports.source_payload.terms.expires_at
                    ports.revocation = replace(
                        ports.revocation, checked_at=ports.now_value
                    )
                    assert ports.now_value < ports.grant_payload.terms.expires_at
                elif withdrawal == "key":
                    ports.revocation = replace(
                        ports.revocation, revoked_key_ids=("source",)
                    )
                else:
                    ports.revocation = replace(
                        ports.revocation, revoked_issuers=("source-issuer",)
                    )
        return original_revocations(**kwargs)

    monkeypatch.setattr(ports, "load_attestation", mark_final_source)
    monkeypatch.setattr(ports, "current_revocations", withdraw_on_final_grant_read)
    assert ports.qualify() is None
    assert final_source_authenticated
