"""Hosted-test-only retained signed evidence and narrow Session-bound wiring.

Reuses ephemeral real Ed25519 fixture keys. This models independent retention,
never proves live custody or original-document provenance. No production signer
or trust enrollment is installed. Inverse approval is an explicit, separate
exact-request fixture authority with no dependency on the import signer.
"""

from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

from dotmac_billing.opening_qualification import qualify_opening_account_import_v2
from dotmac_billing.opening_receivable_contracts import (
    OpeningImportReversalVerificationV2,
)
from dotmac_billing.opening_verification import (
    SignedOpeningImportGrantProviderV2,
    SignedOpeningSourceEvidenceProviderV2,
    opening_source_attestation_digest_v1,
    required_opening_evidence_v1,
)
from dotmac_billing.opening_verification_contracts import (
    OpeningEvidenceBindingV1,
    OpeningImmutableEvidenceV1,
    OpeningWindowIdentityV1,
)
from dotmac_billing.opening_window_queries import get_opening_window

from tests.unit import test_billing_opening_qualification as qualification
from tests.unit.test_billing_opening_verification import TrustedPorts


class SessionWindowReader:
    """Use the actual caller Session and existing owner read, including seals."""

    def __init__(self, session):
        self.session = session
        self.calls = []

    def current_window(self, *, scope):
        self.calls.append(scope)
        window = get_opening_window(self.session, scope=scope)
        if window is None:
            return None
        return OpeningWindowIdentityV1(
            window.scope, window.window_id, window.epoch, window.manifest_fingerprint
        )


class ExactInverseFixtureAuthority:
    """Separate exact-request test decision; no import permission is inherited.

    The positive response is issued only by approve_exact in the test. This is
    an injected fixture authority, not a production inverse verifier or signer.
    Its returned response is forwarded unchanged by SignedOpeningFixture.
    """

    def __init__(self):
        self.requests = []
        self.approved = {}
        self.approved_requests = {}
        self.returned = None

    def approve_exact(self, request):
        command = request.command
        assert request.command_digest not in self.approved
        self.approved_requests[request.command_digest] = request
        self.approved[request.command_digest] = OpeningImportReversalVerificationV2(
            "billing.opening.import.reverse.v2",
            command.scope,
            request.command_digest,
            command.receipt_id,
            request.receipt_evidence_digest,
            request.original_posting_group_id,
            request.billing_account_id,
            request.currency,
            request.precision,
            command.migration_authority_ref,
            "fixture:separate-exact-inverse-decision",
        )

    def verify_import_reversal_v2(self, request):
        self.requests.append(request)
        if self.approved_requests.get(request.command_digest) != request:
            return None
        self.returned = self.approved[request.command_digest]
        return self.returned


class SignedOpeningFixture:
    """Retained source and immutable issued grants for one real DB command.

    Each issued grant ID is permanently bound to one immutable issuance value.
    Preview and retries only read these records. Reissuing a changed command
    explicitly creates another ID; it cannot overwrite an existing issuance.
    """

    request = staticmethod(qualification._request)

    def __init__(self, command, window, session):
        self.command = command
        self.window = window
        self.ports = ports = TrustedPorts()
        ports.command = command
        ports.policy = replace(
            ports.policy,
            scope=command.scope,
            installation_id=command.manifest.capture.installation_id,
        )
        ports.revocation = replace(
            ports.revocation,
            scope=command.scope,
            installation_id=command.manifest.capture.installation_id,
        )
        provisional = replace(
            ports.source_payload,
            terms=ports.terms("source", "billing.opening.source.attest.v1"),
            manifest=command.manifest,
            bindings=(),
        )
        ports.objects = {}
        bindings = []
        for index, requirement in enumerate(required_opening_evidence_v1(provisional)):
            # Bytes are held independently from the caller's command and retained
            # at these exact version/provenance coordinates for subsequent reads.
            content = f"retained-hosted-object:{index}:{requirement.reference}".encode()
            binding = OpeningEvidenceBindingV1(
                requirement,
                f"hosted-object:{index}",
                "immutable-version:1",
                sha256(content).hexdigest(),
                f"hosted-custody-provenance:{index}",
            )
            bindings.append(binding)
            ports.objects[binding.object_id] = OpeningImmutableEvidenceV1(
                binding.object_id,
                binding.version_id,
                binding.provenance_ref,
                content,
                "issue_time_original"
                if requirement.original_issued_at
                else "retained_source",
                requirement.original_issued_at,
            )
        ports.source_payload = replace(provisional, bindings=tuple(bindings))
        ports.sign_source()
        self.issued_grants = {}
        self.issuance_records = {}
        self.issue_exact_import_grant(command)
        self.windows = SessionWindowReader(session)
        self.source = SignedOpeningSourceEvidenceProviderV2(
            authority=ports, source=ports, custody=ports, clock=ports
        )
        self.grants = SignedOpeningImportGrantProviderV2(
            authority=ports,
            source=ports,
            grants=self,
            windows=self.windows,
            clock=ports,
        )
        self.inverse = ExactInverseFixtureAuthority()

    def issue_exact_import_grant(self, command, *, epoch=None):
        ports = self.ports
        request = self.request(command)
        grant_id = f"hosted-import-grant:{uuid4()}"
        assert grant_id not in self.issuance_records
        ports.grant_payload = replace(
            ports.grant_payload,
            terms=ports.terms("grant", "billing.opening.import.v2"),
            grant_id=grant_id,
            approval_decision_ref=f"hosted-exact-decision:{uuid4()}",
            command=command,
            command_digest=request.command_digest,
            manifest_fingerprint=request.manifest_fingerprint,
            source_attestation_digest=opening_source_attestation_digest_v1(
                ports.source_payload
            ),
            window_id=self.window.window_id,
            window_epoch=self.window.epoch if epoch is None else epoch,
        )
        ports.sign_grant()
        # The reused unit signer records its baseline command identity. This
        # issued record must instead bind the exact command approved here.
        issuance = replace(ports.issuance, idempotency_key=command.idempotency_key)
        self.issued_grants[request.command_digest] = ports.signed_grant
        self.issuance_records[grant_id] = issuance

    def load_grant(self, request):
        self.ports.calls.append("hosted-grant")
        return self.issued_grants.get(request.command_digest)

    def load_issuance(self, *, scope, installation_id, grant_id):
        self.ports.calls.append("hosted-issuance")
        issuance = self.issuance_records.get(grant_id)
        if issuance is None or (issuance.scope, issuance.installation_id) != (
            scope,
            installation_id,
        ):
            return None
        return issuance

    def verify_import_v2(self, request):
        return qualify_opening_account_import_v2(
            request, source_provider=self.source, grant_provider=self.grants
        )

    def verify_import_reversal_v2(self, request):
        return self.inverse.verify_import_reversal_v2(request)


class ImportProofForOtherPurpose:
    """Deliberate wrong-operation port offers an authentic import proof unchanged."""

    def __init__(self, verifier, command):
        self.verifier, self.command = verifier, command

    def import_proof(self):
        return self.verifier.verify_import_v2(self.verifier.request(self.command))

    def verify_provision(self, request):
        return self.import_proof()

    def verify_seal(self, request):
        return self.import_proof()

    def verify_import_reversal_v2(self, request):
        return self.import_proof()
