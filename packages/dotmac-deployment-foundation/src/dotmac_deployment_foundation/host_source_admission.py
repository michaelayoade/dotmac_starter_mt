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

`verify_attestation_pair` already verifies the candidate half internally and
returns non-authorizing verification evidence. Constructing a `HostSource` needs the
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

## The provider seam — install-once, handed over, never discovered

`engine/run.py`'s `Executor` and `recovery_execution.py`'s `RecoveryExecutor`
both mutate a host, and both need "which Foundation is asking?" answered
before their first effect. Neither imports Control's real attestation
context, and this module does not either — so the seam between them is a
`HostSourceAdmissionProvider`: one argument-free method, `admit_host_source()`,
returning exactly the pair this module's own `admit_host_source()` function
returns. Argument-free is deliberate: a real provider implementation reaches
Control and evidence itself, entirely inside its own method body, so there is
no request/context parameter schema to invent here before Control has one.

The provider is a REQUIRED CONSTRUCTOR parameter on both executors, with no
default — never an ambient registry, a module-level "installed provider"
global, or anything this package discovers on its own. `install_secret_source`
-style ambient installation is the wrong shape here on purpose: an executor
that goes looking for a provider cannot tell "no provider was installed" from
"the wrong provider was installed", and a mutating executor is exactly where
that distinction has to stay visible at the call site. Requiring the argument
turns "no real admission was wired in" into a visible per-call-site decision:
a caller that wants today's unconditional refusal constructs
:class:`RefusingHostSourceAdmissionProvider` below explicitly.

`RefusingHostSourceAdmissionProvider` is the ONLY implementation this package
ships. It delegates to `host_source.require_host_source(receipt=None)` —
byte-for-byte today's existing refusal, with the same typed codes
(`ABSENT`/`WRONG_KIND`/`NO_RECEIPT`) and zero effects — so a caller passing it
explicitly observes no behavior change. The protocol is a composition seam,
NOT an authority proof: any Python caller able to construct an executor can
hand it an object returning a fabricated success. The shipped CLI passes
`RefusingHostSourceAdmissionProvider()` explicitly at every call site, and
those refusal-only call sites are guarded by an architecture test — which
also checks how the name `RefusingHostSourceAdmissionProvider` itself is
bound in that file, not only the shape of each call, so an import that
rebinds the name to a different, real provider is caught too.
Before a production assembly may supply an accepting one, it must prove that
request input cannot select or replace the installed implementation and that
the provider uses this module's `admit_host_source()` against fresh
Control-resolved trust, host identity, and replay state. That provider and
proof are later cross-repo work; this seam alone cannot establish positive
admission.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Protocol, runtime_checkable

from .digest import Digest
from .errors import PreconditionFailed
from .host_source import (
    DISAGREES,
    HostSource,
    read_installed_artifact,
    require_host_source,
)
from .trusted_host_source import (
    AttestationEnvelopeV2,
    AttestationPairVerificationResultV1,
    AttestationTrustPolicy,
    AttestationVerifier,
    InstalledHostAttestationSubjectV2,
    candidate_subject_digest,
    verify_attestation_pair,
    verify_candidate_attestation,
)

__all__ = [
    "HostSourceAdmissionProvider",
    "HostSourceAdmissionTrace",
    "RefusingHostSourceAdmissionProvider",
    "admit_host_source",
]


@dataclasses.dataclass(frozen=True, slots=True)
class HostSourceAdmissionTrace:
    """Coordinates of the checks made by :func:`admit_host_source`.

    That function reads every field from an authenticated envelope or subject.
    The dataclass is publicly constructible, however, so its TYPE alone does
    not prove that the function ran. An executor handed an arbitrary provider
    cannot treat a returned trace as authenticated until trusted composition
    establishes the provider's implementation. The V3 executor compares its
    installed-host identity/root coordinates to the independently observed
    execution subject before effects; type alone is not authentication. The
    actual pair-verification result is retained for Control's later single
    finalizer. ``opaque_finalization`` is a transient CP continuation:
    Foundation checks only its presence and passes the trace through unchanged.
    It is excluded from repr and equality, and never becomes outcome evidence.
    """

    candidate_subject_digest: Digest
    host_observation_id: str
    host_identity: str
    candidate_signer_fingerprint: str
    candidate_trust_root_version: str
    installed_signer_fingerprint: str
    installed_trust_root_version: str
    pair_verification_result: AttestationPairVerificationResultV1 | None = None
    opaque_finalization: object | None = dataclasses.field(
        default=None, repr=False, compare=False
    )


def admit_host_source(
    *,
    candidate: AttestationEnvelopeV2 | None,
    installed: AttestationEnvelopeV2 | None,
    verifier: AttestationVerifier,
    trust_policy: AttestationTrustPolicy,
    expected_host_identity: str,
    expected_observation_id: str,
    expected_package: str,
    verification_context_digest: str,
    now: datetime,
) -> tuple[HostSource, HostSourceAdmissionTrace]:
    """Admit a `HostSource` from a verified v2 attestation pair, or refuse.

    Nine parameters, all inputs the caller could not have forged into a
    result: there is no parameter for a receipt, a pre-parsed subject, an
    installed artifact reading, a metadata reader, a distribution selector, or
    any preverified outcome. Every `SpecError`/`PreconditionFailed` the called
    functions raise propagates unchanged — this function adds exactly one new
    refusal, reusing `host_source.DISAGREES`, for the one new check it
    performs: the real interpreter reading against the authenticated
    installed-host subject. `verification_context_digest` is opaque here too —
    it is only ever forwarded to `verify_attestation_pair`, never inspected.
    """
    pair_result = verify_attestation_pair(
        candidate=candidate,
        installed=installed,
        verifier=verifier,
        trust_policy=trust_policy,
        expected_host_identity=expected_host_identity,
        expected_observation_id=expected_observation_id,
        expected_package=expected_package,
        verification_context_digest=verification_context_digest,
        now=now,
    )
    # `verify_attestation_pair` raises unless both halves are present and
    # agree; past this point they are proven non-None. An `assert` here would
    # be silently removed under Python's `-O` flag (bandit B101) and this
    # module does not get to rely on a narrowing that a runtime flag can
    # strip — a real, unstrippable branch instead of a suppressed lint.
    if candidate is None or installed is None:  # pragma: no cover - unreachable
        raise AssertionError(
            "unreachable: verify_attestation_pair already guarantees both "
            "attestations are present"
        )

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
        pair_verification_result=pair_result,
    )
    return host_source, trace


@runtime_checkable
class HostSourceAdmissionProvider(Protocol):
    """The whole seam a mutating executor is handed at construction.

    One argument-free method. No parameter for a receipt, an envelope, a
    subject, a metadata reader, a trust policy, a verifier, a root, an
    expected host identity, or a preverified result — a real implementation
    obtains current Control context and evidence itself, entirely inside its
    own method body. This is deliberate: Control has no request/context API
    for this yet, and defining one here would invent it rather than receive
    it.
    """

    def admit_host_source(self) -> tuple[HostSource, HostSourceAdmissionTrace]: ...


class RefusingHostSourceAdmissionProvider:
    """The reference implementation: delegates to today's existing refusal.

    `admission_provider` is a required constructor parameter with no
    default; a caller that wants today's behavior constructs this class
    explicitly, and the explicit-provider path produces EXACTLY today's
    behavior — the same `ABSENT`/`WRONG_KIND`/`NO_RECEIPT` codes, zero
    effects, in every environment. This class introduces no new refusal
    vocabulary; it is a pure delegation to `require_host_source(receipt=None)`.
    """

    def admit_host_source(self) -> tuple[HostSource, HostSourceAdmissionTrace]:
        require_host_source(receipt=None)
        # `require_host_source(receipt=None)` cannot return normally: `receipt`
        # is the literal constant `None` here, so either an earlier check
        # (ABSENT/WRONG_SUBJECT/WRONG_KIND) raises first, or execution reaches
        # the `if receipt is None:` branch and raises NO_RECEIPT. There is no
        # path through that function, fed a literal `None`, that returns a
        # value — this is a real, unstrippable branch stating that fact
        # rather than a comment trusting it as an invariant.
        raise AssertionError(  # pragma: no cover - unreachable, see above
            "unreachable: require_host_source(receipt=None) always refuses"
        )
