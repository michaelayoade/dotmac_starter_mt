"""Turn a verified pair of v2 attestations into a checked `HostSource`.

`trusted_host_source.py` authenticates a candidate/installed attestation
PAIR and states plainly that a successful `verify_attestation_pair` creates
no caller-constructible authority. `host_source.py`'s `require_host_source`
binds a different, receipt-based half of the same subject and states, just as
plainly, that it is `HostSource`'s only constructor. Neither module closes the
gap between "these two attestations agree" and "here is a `HostSource`" — and
this module is the one place that gap is closed, by composing the two
existing seams rather than inventing a third verification path.

## Why this module exists rather than growing either of the two above

`trusted_host_source.py` promises **zero I/O** — it holds no private key,
discovers no file, and does no I/O of its own. Reading the interpreter's own
installed artifact (`host_source.read_installed_artifact`) is I/O, so it
cannot move into that module without breaking a promise other code already
relies on. `host_source.py`'s `require_host_source` is the OTHER established
constructor and is left byte-for-byte alone: it is still what a caller with
only a `CandidateReceipt` — no attestation pair — uses, and this module adds
a second, independently-checked path rather than replacing it.

## The double candidate-verification cost, and why it is paid anyway

`verify_attestation_pair` already verifies the candidate half internally, but
it returns `None` — "success creates no caller-constructible authority" is
the whole point of that signature. Constructing a `HostSource` needs the
*fields* of the authenticated candidate subject, not just the fact that
verification succeeded, and `trusted_host_source.py`'s private `_verify`
helper is exactly that: private, and part of a module that has earned the
right to keep its internals unexported. So this module calls the public pair
check first, and — on success — calls the public single-envelope seam
(`verify_candidate_attestation`) a second time against the exact same
envelope object, paying one redundant pure-crypto verification rather than
reaching past the module boundary. Both calls verify the identical
`candidate` object with the identical `verifier`/`trust_policy`/`now`, so the
second call cannot newly fail having just succeeded.

## Why the interpreter is read only after the pair check succeeds

Reading `read_installed_artifact()` is the one I/O this module performs. It
happens strictly after `verify_attestation_pair` succeeds, never before,
because there is nothing to gate the read against until the pair has been
authenticated — reading first and discarding the result on a later
attestation failure would still be an unconditional, ungated read of host
state on every call, authenticated or not.

## What this module does NOT do

It does not decide when a mutating executor is trusted to call it — the
`HostSourceAdmissionTrace` this module returns is consumed by nothing in this
revision, and neither `engine/run.py`'s `Executor` nor
`recovery_execution.py`'s `RecoveryExecutor` import this module. That wiring,
and the delivery-seam decision it depends on, is later, separate work.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from .digest import Digest
from .errors import PreconditionFailed
from .host_source import DISAGREES, HostSource, read_installed_artifact
from .trusted_host_source import (
    AttestationEnvelopeV2,
    AttestationTrustPolicy,
    AttestationVerifier,
    InstalledHostAttestationSubjectV2,
    candidate_subject_digest,
    verify_attestation_pair,
    verify_candidate_attestation,
)

__all__ = [
    "HostSourceAdmissionTrace",
    "admit_host_source",
]


@dataclasses.dataclass(frozen=True, slots=True)
class HostSourceAdmissionTrace:
    """What was actually checked, for a log line an operator can trust.

    Every field is read from an AUTHENTICATED envelope or subject — never
    from a caller-supplied value the admission itself was supposed to check.
    Nothing in this package consumes this type yet; it exists so the fields
    an eventual consumer will need are named now, not invented ad hoc later.
    """

    candidate_subject_digest: Digest
    host_observation_id: str
    host_identity: str
    candidate_signer_fingerprint: str
    candidate_trust_root_version: str
    installed_signer_fingerprint: str
    installed_trust_root_version: str


def admit_host_source(
    *,
    candidate: AttestationEnvelopeV2 | None,
    installed: AttestationEnvelopeV2 | None,
    verifier: AttestationVerifier,
    trust_policy: AttestationTrustPolicy,
    expected_host_identity: str,
    now: datetime,
) -> tuple[HostSource, HostSourceAdmissionTrace]:
    """Admit a `HostSource` from a verified v2 attestation pair, or refuse.

    Six parameters, all inputs the caller could not have forged into a
    result: there is no parameter for a receipt, a pre-parsed subject, an
    installed artifact reading, a metadata reader, a distribution selector, or
    any preverified outcome. Every `SpecError`/`PreconditionFailed` the called
    functions raise propagates unchanged — this function adds exactly one new
    refusal, reusing `host_source.DISAGREES`, for the one new check it
    performs: the real interpreter reading against the authenticated
    installed-host subject.
    """
    verify_attestation_pair(
        candidate=candidate,
        installed=installed,
        verifier=verifier,
        trust_policy=trust_policy,
        expected_host_identity=expected_host_identity,
        now=now,
    )
    # `verify_attestation_pair` raises unless both halves are present and
    # agree; past this point they are proven non-None.
    assert candidate is not None
    assert installed is not None

    authenticated_candidate = verify_candidate_attestation(
        candidate=candidate,
        verifier=verifier,
        trust_policy=trust_policy,
        now=now,
    )
    authenticated_installed = InstalledHostAttestationSubjectV2.from_mapping(
        installed.subject_mapping()
    )

    # Gated on the pair check above: nothing before this line touches the
    # interpreter.
    reading = read_installed_artifact()

    disagreements = [
        name
        for name, from_host, from_attestation in (
            ("distribution", reading.distribution, authenticated_installed.package),
            ("version", reading.version, authenticated_installed.version),
            (
                "artifact_digest",
                reading.artifact_digest,
                authenticated_installed.wheel_sha256,
            ),
        )
        if from_host != from_attestation
    ]
    if disagreements:
        raise PreconditionFailed(
            "the interpreter's own installed-artifact reading disagrees with "
            "the authenticated installed-host attestation in "
            f"{', '.join(disagreements)}: read {reading.distribution!r} "
            f"{reading.version!r} {reading.artifact_digest}, attested "
            f"{authenticated_installed.package!r} "
            f"{authenticated_installed.version!r} "
            f"{authenticated_installed.wheel_sha256}. One of the two is "
            "stale, and admitting a HostSource while they disagree would bind "
            "an unreviewed artifact to a host nobody expected it to describe",
            code=DISAGREES,
        )

    host_source = HostSource(
        distribution=authenticated_candidate.package,
        version=authenticated_candidate.version,
        artifact_digest=authenticated_candidate.wheel_sha256,
        source_revision=authenticated_candidate.source_revision,
        repository=authenticated_candidate.repository,
        run_id=authenticated_candidate.run_id,
        artifact_id=authenticated_candidate.artifact_id,
        read_from=reading.read_from,
    )
    trace = HostSourceAdmissionTrace(
        candidate_subject_digest=candidate_subject_digest(authenticated_candidate),
        host_observation_id=installed.observation_id,
        host_identity=authenticated_installed.host_identity,
        candidate_signer_fingerprint=candidate.public_key_fingerprint,
        candidate_trust_root_version=candidate.trust_root_version,
        installed_signer_fingerprint=installed.public_key_fingerprint,
        installed_trust_root_version=installed.trust_root_version,
    )
    return host_source, trace
