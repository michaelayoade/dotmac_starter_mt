"""The target-host attester's pure core: sign what was actually installed.

This module is the producer half of `trusted_host_source.py`'s v2 contract. It
holds no private key, does no network I/O, does not shell out to `pip`, and
declares no new runtime dependency — exactly the same constraint ADR-0070
places on the rest of this facility, and exactly the same reason `evidence.py`
gives for holding no cryptography of its own: "This facility declares ZERO
runtime dependencies, so it cannot import a signing library and must not ship
a weak stdlib substitute." The actual download, digest-verify-before-install,
`pip install <exact local file>` and Ed25519 signing live in a SEPARATE,
non-Foundation script — `scripts/host_attester_install.py` — because those
steps need a network client and a signing implementation Foundation is
constitutionally forbidden from carrying.

## What this module refuses to accept, structurally

The installed-host subject's `package`/`version`/`wheel_sha256` are read
EXCLUSIVELY from a `HostSource` value — `host_source.py`'s own docstring:
"Constructible only through `require_host_source`". This module takes no
`installed_digest`, `wheel_sha256`, `artifact_digest` or similarly-shaped
parameter of its own: there is structurally nowhere for a caller to hand this
function an answer instead of letting it read one off a value that was itself
produced by reading the installer's PEP 610 record. A caller that wants to lie
about what was installed has to lie to `require_host_source` instead, which is
a different, already-hardened attack surface with its own four refusals
(`ABSENT`, `WRONG_KIND`, `DISAGREES`, `NO_RECEIPT`), not a new one opened here.

## Where the candidate subject comes from

`HostSource` already carries every field `CandidateAttestationSubjectV2`
needs — `distribution`, `version`, `artifact_digest`, `source_revision`,
`repository`, `run_id`, `artifact_id` — because `require_host_source` copied
them from the SAME `CandidateArtifact.v1` receipt it bound the installed
digest against. Re-deriving the candidate subject from `HostSource` rather
than from a second, separately-supplied receipt closes a seam a careless
caller could otherwise open: passing `require_host_source` one receipt and
this module a DIFFERENT one would let the two disagree, and there is no way to
disagree with a value you were never given twice.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from typing import Final, Protocol, runtime_checkable

from .digest import Digest
from .errors import SpecError
from .host_source import HostSource
from .trusted_host_source import (
    ATTESTATION_SCHEMA,
    INSTALLED_OBSERVATION_PURPOSE,
    OBSERVATION_MALFORMED,
    AttestationEnvelopeV2,
    CandidateAttestationSubjectV2,
    InstalledHostAttestationSubjectV2,
)

__all__ = [
    "HostAttesterSigner",
    "build_installed_attestation",
    "candidate_subject_from_host_source",
]

#: Deliberately NOT imported from `trusted_host_source.py`: that module keeps
#: this computation PRIVATE (`_canonical`), and adding a new public export
#: there is a call for whoever owns that shared file's public surface, not a
#: decision this module makes unilaterally while a parallel lane is also
#: editing it. So the identical canonicalization — sorted keys, no incidental
#: whitespace, `allow_nan=False` — is restated here rather than imported. Any
#: drift between this and `verify_attestation_pair`'s inline computation would
#: surface immediately: this module's own tests build a candidate subject,
#: compute its digest with this function, and confirm
#: `verify_attestation_pair` (imported unmodified) accepts the resulting pair.
def _candidate_subject_digest(candidate: CandidateAttestationSubjectV2) -> Digest:
    if not isinstance(candidate, CandidateAttestationSubjectV2):
        raise SpecError(
            "_candidate_subject_digest requires a CandidateAttestationSubjectV2",
            code=OBSERVATION_MALFORMED,
        )
    canonical = json.dumps(
        candidate.canonical_document(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return Digest.of(canonical)

#: A placeholder the envelope constructor accepts (non-empty text) so its
#: `signed_bytes()` can be computed BEFORE a real signature exists — the same
#: build-then-replace idiom the test suite already uses. Never itself treated
#: as a signature: it is overwritten by `dataclasses.replace` before this
#: function returns, and a caller that saw this value would be looking at a
#: bug, not a signed envelope.
_UNSIGNED: Final = "unsigned"


def candidate_subject_from_host_source(
    host_source: HostSource,
) -> CandidateAttestationSubjectV2:
    """The candidate subject `HostSource` already transitively proves.

    Not a second reading of a receipt: every field here was already copied
    onto `host_source` by `require_host_source`, from the one committed
    `CandidateArtifact.v1` it bound the installed digest against.
    """
    if not isinstance(host_source, HostSource):
        raise SpecError(
            "candidate_subject_from_host_source requires a HostSource value "
            "produced by require_host_source, never a hand-built stand-in",
            code=OBSERVATION_MALFORMED,
        )
    return CandidateAttestationSubjectV2(
        host_source.distribution,
        host_source.version,
        host_source.artifact_digest,
        host_source.source_revision,
        host_source.repository,
        host_source.run_id,
        host_source.artifact_id,
    )


@runtime_checkable
class HostAttesterSigner(Protocol):
    """The enrolled per-incarnation host key. A pure crypto seam, the producer
    mirror of `trusted_host_source.AttestationVerifier`: this module holds no
    key material and performs no signing itself, exactly as that module holds
    no verification key material and performs no cryptography itself."""

    def sign(self, *, algorithm: str, message: bytes) -> str: ...


def _format_instant(value: datetime, *, where: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SpecError(
            f"{where} must be a timezone-aware datetime", code=OBSERVATION_MALFORMED
        )
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_installed_attestation(
    *,
    host_source: HostSource,
    expected_host_identity: str,
    signer: HostAttesterSigner,
    issuer: str,
    key_id: str,
    algorithm: str,
    public_key_fingerprint: str,
    custody_domain: str,
    trust_root_version: str,
    observation_id: str,
    issued_at: datetime,
    expires_at: datetime,
) -> AttestationEnvelopeV2:
    """Sign the INSTALLED-HOST observation `host_source` already proves.

    No parameter here can carry an installed digest, a package name or a
    version in place of `host_source` reading them off the installer's own
    record — see the module docstring. `expected_host_identity` is
    Control-resolved and supplied by the caller; this module does not resolve
    a Fleet `host_id` or an attester incarnation itself (ADR-0070's amendment,
    2026-09-08: "Foundation must never resolve host identity itself").
    """
    if not isinstance(host_source, HostSource):
        raise SpecError(
            "build_installed_attestation requires a HostSource produced by "
            "require_host_source",
            code=OBSERVATION_MALFORMED,
        )
    if not isinstance(signer, HostAttesterSigner):
        raise SpecError(
            "signer must implement HostAttesterSigner", code=OBSERVATION_MALFORMED
        )

    candidate_subject = candidate_subject_from_host_source(host_source)
    installed_subject = InstalledHostAttestationSubjectV2(
        expected_host_identity,
        host_source.distribution,
        host_source.version,
        host_source.artifact_digest,
        _candidate_subject_digest(candidate_subject),
    )

    envelope = AttestationEnvelopeV2(
        ATTESTATION_SCHEMA,
        INSTALLED_OBSERVATION_PURPOSE,
        issuer,
        key_id,
        algorithm,
        public_key_fingerprint,
        custody_domain,
        trust_root_version,
        _format_instant(issued_at, where="issued_at"),
        _format_instant(expires_at, where="expires_at"),
        expected_host_identity,
        observation_id,
        installed_subject.canonical_document(),
        _UNSIGNED,
    )
    signature = signer.sign(algorithm=algorithm, message=envelope.signed_bytes())
    if not isinstance(signature, str) or not signature.strip():
        raise SpecError(
            "the injected signer returned an empty signature",
            code=OBSERVATION_MALFORMED,
        )
    return dataclasses.replace(envelope, signature=signature)
