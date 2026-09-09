"""The target-host attester's producer core: sign what was actually installed.

## Why this lives in `scripts/`, not inside `dotmac_deployment_foundation`

Foundation's own established posture is VERIFY, NEVER SIGN — `trusted_host_
source.py` defines `AttestationVerifier` (a pure crypto seam FOR CHECKING a
signature) and this repository has never defined a producer-side `Signer`
protocol anywhere under `packages/dotmac-deployment-foundation/`; `evidence.py`
states the reason directly: "This facility declares ZERO runtime dependencies,
so it cannot import a signing library and must not ship a weak stdlib
substitute" — restated by Michael, 2026-09-08: "whatever holds the
per-incarnation signing key must not be reachable from Foundation's execution
path. Foundation verifies; it does not sign."

This module WAS drafted as `dotmac_deployment_foundation.host_attester` and
was moved out for exactly that reason, worked through here so the move is not
unexamined:

* **Every consumer of `dotmac-deployment-foundation` would receive it.**
  ADR-0070 says the facility is "consumed by CI, build runners, provisioning
  workers and deployment hosts" — the overwhelming majority of which never
  attest anything. Shipping a `HostAttesterSigner` protocol and a
  `sign(...)`-calling function inside a package classified `universal-facility`
  (zero runtime dependencies, "does not admit an executor") widens every
  installation's surface to something signing-adjacent, for a capability only
  the target-host attestation workload ever exercises.
* **The wheel-substitution question, worked through rather than assumed.** If
  an attacker substitutes the candidate wheel, do they also substitute the
  code that would have detected it? The DOWNLOAD-and-VERIFY step in
  `host_attester_install.py` answers that for the DIGEST comparison: it hashes
  the downloaded FILE'S RAW BYTES with `hashlib` and compares against the
  committed `CandidateArtifact.v1` digest — a check that runs BEFORE `pip
  install` and needs no code FROM the candidate wheel to execute correctly, so
  a substituted wheel is caught as opaque bytes, never as trusted code. That
  half of the circularity is genuinely closed, not merely moved, PROVIDED the
  code performing that comparison is not itself sourced from the wheel under
  test.
  The SIGNING step is different in kind: it runs AFTER install, reads
  installer-authored PEP 610 metadata (files `pip`, not the package, writes —
  `read_installed_artifact` executes no code from the installed distribution),
  and then calls into whatever code is CURRENTLY resident to format and sign
  the result. If that code shipped INSIDE the artifact just verified and
  installed, the argument that "only a digest-matching wheel could reach this
  point" carries the load — sound in principle, but it makes the signing
  step's integrity depend entirely on the pre-install gate having been
  implemented with no gap, in every deployment, forever. Keeping the signer
  OUTSIDE the Foundation wheel removes the dependency on that argument being
  perfectly maintained: substituting the Foundation wheel no longer
  automatically substitutes the component that attests it, because that
  component is not part of the same artifact. An attacker would need to
  separately compromise THIS file's own delivery to the host — a distinct,
  and Control/Platform-owned, channel (ADR-0070 decision 6: "Platform's
  dependency on this seam stays in a thin deployment adapter").
* **Target-local placement is still correct — this file is not moved off the
  host.** It runs where the install happened, exactly as the ADR requires; it
  is only moved OUT of the artifact whose installation it is attesting.

This module still imports `dotmac_deployment_foundation` as an ordinary
dependency — reusing `HostSource`, `AttestationEnvelopeV2`, and the wire types
`trusted_host_source.py` already defines — the same way `release_facility.py`
imports installed console scripts and modules without being packaged into any
wheel itself. It is executed directly (from a checkout, or copied to the host
by a trusted composition), never `pip install`-ed as part of
`dotmac-deployment-foundation`.

Holds no private key, does no network I/O, does not shell out to `pip`. The
actual download / verify-before-install / `pip install <exact local file>`
sequence lives in the sibling `host_attester_install.py`.

## What this module refuses to accept, structurally

The installed-host subject's `package`/`version`/`wheel_sha256` are read
EXCLUSIVELY from a `HostSource` value — `host_source.py`'s own docstring:
"Constructible only through `require_host_source`". This module takes no
`installed_digest`, `wheel_sha256`, `artifact_digest` or similarly-shaped
parameter of its own: there is structurally nowhere for a caller to hand this
function an answer instead of letting it read one off a value that was itself
produced by reading the installer's PEP 610 record.

## Where the candidate subject comes from

`HostSource` already carries every field `CandidateAttestationSubjectV2`
needs, because `require_host_source` copied them from the SAME
`CandidateArtifact.v1` receipt it bound the installed digest against.
Re-deriving the candidate subject from `HostSource` rather than a
separately-supplied receipt closes a seam a careless caller could otherwise
open: passing `require_host_source` one receipt and this module a DIFFERENT
one would let the two disagree, and there is no way to disagree with a value
you were never given twice.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Final, Protocol, runtime_checkable

from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.host_source import HostSource
from dotmac_deployment_foundation.trusted_host_source import (
    ATTESTATION_SCHEMA,
    INSTALLED_OBSERVATION_PURPOSE,
    OBSERVATION_MALFORMED,
    AttestationEnvelopeV2,
    CandidateAttestationSubjectV2,
    InstalledHostAttestationSubjectV2,
    candidate_subject_digest,
)

__all__ = [
    "HostAttesterSigner",
    "build_installed_attestation",
    "candidate_subject_from_host_source",
]

#: The candidate-subject-binding digest is a document `trusted_host_source.py`
#: already owns a canonicalizer for: `verify_attestation_pair` recomputes this
#: exact value, over this exact document, to compare against what a host
#: signed. A producer-side restatement of `json.dumps(..., sort_keys=True,
#: ...)` here would be a second answer to the one question the package's own
#: `candidate_subject_digest` already answers — the same shape
#: `execution_plan.py`/`recovery_plan.py` refuse by routing through
#: `canonical_plan.canonical_plan_bytes` instead of canonicalizing for
#: themselves. This module calls the package's function directly.


#: A placeholder the envelope constructor accepts (non-empty text) so its
#: `signed_bytes()` can be computed BEFORE a real signature exists — the same
#: build-then-replace idiom `test_deployment_foundation_trusted_host_source.py`
#: already uses. Never itself treated as a signature: overwritten by
#: `dataclasses.replace` before this function returns.
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
    key material and performs no signing itself."""

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
        candidate_subject_digest(candidate_subject),
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
