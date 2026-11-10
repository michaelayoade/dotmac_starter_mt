"""D4: Lane 3's own ``HostSourceAdmissionProvider``.

``Executor.run`` verifies the host source before its first effect, through the
``admission_provider`` it is constructed with. The installed CLI hard-codes
``RefusingHostSourceAdmissionProvider``, which is why D4 drives the
``Executor`` in-process (roadmap Q1, answered 2026-10-06). The provider it
hands in is this one: Starter-owned execution tooling, so it can grow without
touching Foundation ``src/`` and without spending the candidate.

## Two states, both deliberate

Constructed with no inputs — the only construction that exists in production
today — this provider refuses exactly as the reference implementation does:
same codes, zero effects. Nothing about today's behavior changes.

Constructed with the complete typed input set, it delegates once to
Foundation's public ``admit_host_source`` and forwards its result or refusal
unchanged. The inputs are the ones that function's contract names, and none
of them is discovered here: trusted composition (CP/Control) must supply the
candidate and installed ``AttestationEnvelopeV2`` pair, an
``AttestationTrustPolicy`` pinned to Gate-0 trust state, the expected host
identity and observation, and Control's ``verification_context_digest`` for
the dispatch. A partially wired provider is a construction error, not a
silent permanent refusal: naming some inputs and omitting others is a wiring
mistake a composition root should see at startup, in its own traceback.

The verifier is Ed25519 only, matching the algorithm the attestation
producer declares (`scripts/candidate_attestation_signer.py` signs no other).
It returns ``False`` for every malformed key, signature or algorithm rather
than raising, because `trusted_host_source` already turns a false verify
into its typed `SIGNATURE_INVALID` refusal and a second vocabulary here
would only obscure which layer refused.

What still does not exist: the Gate-0 attester, its trust-state records, and
a Control API that hands a composition these inputs. Until trusted
composition supplies them, every real construction remains the no-input one
and admission remains refused. This module makes the accepting path
implementable and testable; it does not create its inputs.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from dotmac_deployment_foundation.host_source import HostSource
from dotmac_deployment_foundation.host_source_admission import (
    HostSourceAdmissionTrace,
    RefusingHostSourceAdmissionProvider,
)
from dotmac_deployment_foundation.host_source_admission import (
    admit_host_source as _foundation_admit_host_source,
)
from dotmac_deployment_foundation.trusted_host_source import (
    AttestationEnvelopeV2,
    AttestationTrustPolicy,
    AttestationVerifier,
)

#: The package whose installed artifact this lane admits. Fixed rather than a
#: parameter default a caller could widen: admitting some other distribution
#: under this lane's provider is a different act with a different owner.
EXPECTED_PACKAGE = "dotmac-deployment-foundation"

_ED25519_ALGORITHM = "ed25519"
_ED25519_PUBLIC_KEY_BYTES = 32
_ED25519_SIGNATURE_BYTES = 64


class Ed25519AttestationVerifier:
    """Verify Ed25519 attestation signatures, and nothing else."""

    def verify(
        self,
        *,
        public_key: bytes,
        algorithm: str,
        message: bytes,
        signature: str,
    ) -> bool:
        if algorithm != _ED25519_ALGORITHM:
            return False
        if len(public_key) != _ED25519_PUBLIC_KEY_BYTES:
            return False
        try:
            raw_signature = base64.b64decode(signature, validate=True)
        except (binascii.Error, ValueError):
            return False
        if len(raw_signature) != _ED25519_SIGNATURE_BYTES:
            return False
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                raw_signature, message
            )
        except InvalidSignature:
            return False
        return True


class Lane3HostSourceAdmissionProvider:
    """The provider D4's ``Executor`` is constructed with.

    No inputs: the reference refusal, byte-for-byte. Complete inputs: one
    delegation to Foundation's public ``admit_host_source``. Partial inputs:
    ``ValueError`` at construction.
    """

    def __init__(
        self,
        *,
        candidate: AttestationEnvelopeV2 | None = None,
        installed: AttestationEnvelopeV2 | None = None,
        trust_policy: AttestationTrustPolicy | None = None,
        expected_host_identity: str = "",
        expected_observation_id: str = "",
        verification_context_digest: str = "",
        verifier: AttestationVerifier | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        supplied = {
            "candidate": candidate is not None,
            "installed": installed is not None,
            "trust_policy": trust_policy is not None,
            "expected_host_identity": bool(expected_host_identity),
            "expected_observation_id": bool(expected_observation_id),
            "verification_context_digest": bool(verification_context_digest),
        }
        if any(supplied.values()) and not all(supplied.values()):
            missing = [name for name, present in supplied.items() if not present]
            raise ValueError(
                "host-source admission inputs are all-or-nothing; missing: "
                + ", ".join(missing)
            )
        self._wired = all(supplied.values())
        self._candidate = candidate
        self._installed = installed
        self._trust_policy = trust_policy
        self._expected_host_identity = expected_host_identity
        self._expected_observation_id = expected_observation_id
        self._verification_context_digest = verification_context_digest
        self._verifier = (
            verifier if verifier is not None else Ed25519AttestationVerifier()
        )
        self._clock = clock if clock is not None else (lambda: datetime.now(UTC))

    def admit_host_source(self) -> tuple[HostSource, HostSourceAdmissionTrace]:
        if not self._wired:
            # Delegation, not a new refusal vocabulary: until trusted
            # composition supplies the attested inputs there is nothing to
            # admit, and Foundation's reference refusal already says that
            # precisely.
            return RefusingHostSourceAdmissionProvider().admit_host_source()
        # `all-or-nothing` construction proves these non-None when wired; the
        # cast states that construction invariant where the checker reads it.
        return _foundation_admit_host_source(
            candidate=self._candidate,
            installed=self._installed,
            verifier=self._verifier,
            trust_policy=cast("AttestationTrustPolicy", self._trust_policy),
            expected_host_identity=self._expected_host_identity,
            expected_observation_id=self._expected_observation_id,
            expected_package=EXPECTED_PACKAGE,
            verification_context_digest=self._verification_context_digest,
            now=self._clock(),
        )
