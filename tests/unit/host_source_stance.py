"""A synthetic ACCEPTING admission provider, handed to a test executor.

## Why this module used to exist, and why the thing it built was the defect

`Executor.run`/`Executor.rollback` call `require_host_source` themselves
(`engine/run.py::Executor._verify_host_source`) immediately after the
caller's lock is proven held and before everything else. This module used to
supply `valid_host_source_kwargs()` — a `host_source_receipt=` (a
`CandidateReceipt`, a plain frozen dataclass carrying `artifact_digest`
directly) paired with a `host_source_metadata=` (`FakeInstall`, whose
`read_text("direct_url.json")` returns a caller-chosen JSON string) built to
agree with each other byte for byte.

An independent review at `541cee5d` named this correctly:
`valid_host_source_kwargs()` **was the exploit** `host_source_installed=`
originally produced, respelled with a `json.dumps` in between. Supplying a
`CandidateReceipt` and an `InstalledMetadata` that agree is not "the same
trusted path a genuine artifact takes" — it is a caller stating the same
digest on both sides of the one comparison `require_host_source` makes.
`CandidateReceipt` and `InstalledMetadata` are PARSING interfaces (they turn
a document/a `.dist-info` reading into a typed value); neither is
authority-bearing evidence, and no combination of the two — however
carefully the fixture agrees with itself — turns caller-authored data into
proof that a trusted third party attested to it. `Executor` and
`RecoveryExecutor` were repaired to accept **no such parsing parameter of any
kind**, and stayed that way.

## What changed: an install-once PROVIDER seam, not a parsing parameter

Trusted host provenance (`host_source_admission.py`'s `admit_host_source()`)
landed as its own, separate piece of work, and PR-2 wires a
`HostSourceAdmissionProvider` seam into both mutating executors: a
constructor-supplied, argument-free `admit_host_source() -> tuple[HostSource,
HostSourceAdmissionTrace]`. This test seam is not intended to prove trusted
provenance: it is a HANDED-OVER object whose method supplies synthetic values
so callers can exercise post-gate sequencing. A production implementation may
obtain those values from Control and evidence, but that behavior is outside
this fixture's claim.

`valid_host_source_kwargs()` now returns `{"admission_provider": <a fresh
synthetic ACCEPTING provider>}` — a sequencing test double whose
`admit_host_source()` returns a valid, made-up-but-internally-consistent
`(HostSource, HostSourceAdmissionTrace)` pair, never anything that touched
`require_host_source` or a real artifact reading. It bypasses provenance only
to exercise code after the gate; it is not evidence of trusted admission.
Every call site in this
suite spreads the return value as `**valid_host_source_kwargs()` (or copies
its items with `setdefault`), so the fixture rename is invisible at each call
site: what used to let tests exercise `Executor`/`RecoveryExecutor` through
two caller-authored parsing values now lets them exercise the post-gate path
through one caller-supplied provider object, and every test that used to reach
`pytest.skip` through this fixture now drives the executor past the
host-source gate without asserting real provenance.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.host_source import HostSource
from dotmac_deployment_foundation.host_source_admission import (
    HostSourceAdmissionProvider,
    HostSourceAdmissionTrace,
)
from dotmac_deployment_foundation.trusted_host_source import (
    AttestationPairVerificationResultV1,
    AttestationVerifiedRootV1,
)

from tests.unit.foundation_v3_support import (
    HOST_ENROLMENT_REF,
    HOST_ID,
    HOST_INCARNATION,
)

#: A syntactically valid sha256 hex digest, reused wherever this module needs
#: a placeholder digest — the value is never compared against a real
#: artifact, so any correctly-shaped 64 hex character string does.
_DIGEST = "b" * 64


def _verified_root(*, fingerprint: str, version: str) -> AttestationVerifiedRootV1:
    return AttestationVerifiedRootV1(
        public_key_fingerprint=fingerprint,
        trust_root_version=version,
        key_id="synthetic-test-key",
        algorithm="ed25519",
        purpose="synthetic-test-purpose",
        custody_domain="synthetic-test-custody",
        issuer="synthetic-test-issuer",
    )


@dataclasses.dataclass(slots=True)
class AcceptingHostSourceAdmissionProvider:
    """A synthetic provider whose `admit_host_source()` always succeeds.

    Every field of the returned pair is internally consistent and
    self-contained — no real attestation, no real installed artifact, no
    call to `require_host_source` anywhere in this class. `calls` counts
    invocations, which is what `test_deployment_foundation_host_source_
    admission_provider.py`'s freshness proof reads to show the executor
    consults the provider again on every mutating entry point rather than
    caching one admission across calls.
    """

    host_source: HostSource = dataclasses.field(
        default_factory=lambda: HostSource(
            distribution="dotmac-deployment-foundation",
            version="0.1.0",
            artifact_digest=Digest.parse(_DIGEST, where="test fixture"),
            source_revision="a" * 40,
            repository="dotmac/dotmac-deployment-foundation",
            run_id="run-1",
            artifact_id="artifact-1",
            read_from="synthetic test fixture, not a real installer reading",
        )
    )
    trace: HostSourceAdmissionTrace = dataclasses.field(
        default_factory=lambda: HostSourceAdmissionTrace(
            candidate_subject_digest=Digest.parse(_DIGEST, where="test fixture"),
            host_observation_id="dispatch-test-1",
            host_identity=HOST_ID,
            candidate_signer_fingerprint="candidate-signer-1",
            candidate_trust_root_version="v1",
            installed_signer_fingerprint=HOST_INCARNATION,
            installed_trust_root_version=HOST_ENROLMENT_REF,
            pair_verification_result=AttestationPairVerificationResultV1(
                candidate_attestation_envelope_digest="sha256:" + "c" * 64,
                installed_attestation_envelope_digest="sha256:" + "d" * 64,
                verification_context_digest="sha256:" + "e" * 64,
                expected_host_identity=HOST_ID,
                expected_observation_id="dispatch-test-1",
                expected_package="dotmac-deployment-foundation",
                candidate_audience="synthetic-test-candidate",
                installed_audience=HOST_ID,
                candidate_root=_verified_root(
                    fingerprint="candidate-signer-1", version="v1"
                ),
                installed_root=_verified_root(
                    fingerprint=HOST_INCARNATION, version=HOST_ENROLMENT_REF
                ),
            ),
            opaque_finalization=object(),
        )
    )
    calls: int = 0

    def admit_host_source(self) -> tuple[HostSource, HostSourceAdmissionTrace]:
        self.calls += 1
        return self.host_source, self.trace


def accepting_admission_provider() -> AcceptingHostSourceAdmissionProvider:
    """A fresh synthetic accepting provider, isinstance-checkable as the
    Protocol it satisfies."""
    provider = AcceptingHostSourceAdmissionProvider()
    assert isinstance(provider, HostSourceAdmissionProvider), (
        "AcceptingHostSourceAdmissionProvider must satisfy the runtime-"
        "checkable HostSourceAdmissionProvider protocol"
    )
    return provider


def valid_host_source_kwargs() -> dict[str, Any]:
    """The kwargs that admit a test `Executor`/`RecoveryExecutor` past the
    host-source gate: a fresh synthetic ACCEPTING provider, handed over as
    `admission_provider=`.

    A FRESH provider on every call, never a shared instance — a caller
    counting invocations on the returned provider (the freshness proof)
    would otherwise see calls from unrelated tests accumulate on one shared
    object.
    """
    return {"admission_provider": accepting_admission_provider()}
