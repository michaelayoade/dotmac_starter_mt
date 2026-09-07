"""The fail-closed verifier for TWO independently authored host-source attestations.

`host_source.py` answers "which Foundation is installed here" from a single
local reading (PEP 610 `direct_url.json`) bound to a single committed
document (`CandidateArtifact.v1`). This module is step 3 of the same chain:
it defines the SIDECAR CONTRACT two genuinely independent authorities must
each sign, and the verifier that admits only when BOTH sign, BOTH verify
against their OWN declared trust root, and neither could have produced the
other's half.

## Why this is a separate module, and why it does not touch `Executor`

Two prior attempts at admission both failed the SAME way: a caller could
supply both halves of the comparison itself — a plain, caller-constructible
`CandidateReceipt` alongside a caller-authored `InstalledMetadata.read_text`
return value (attempt 1), or a caller-named directory holding a caller-authored
file (attempt 2). Neither ever required the caller to possess something they
could not simply write down. `CandidateReceipt`/`InstalledMetadata` remain, by
design, PARSING interfaces — useful for turning bytes into a typed value, never
authority-bearing on their own (see `host_source.py`'s own module docstring).

This module changes what "supplying a value" requires:
:class:`SignedAttestationEnvelope` is still just a parsed shape — document,
signature, key id — and still proves nothing by itself. What proves
something is :func:`verify_trusted_host_source`
refusing unless EACH envelope's signature verifies against a key its OWN,
independently declared :class:`AttestationTrustRoot` accepts, AND the two
trust roots share no accepted key (:class:`DistinctTrustRoots`), AND the two
envelopes were not signed by the same key. A caller who cannot produce a valid
signature for a key a trust root accepts cannot pass this gate by writing a
more convincing JSON document — the exact property neither prior attempt had.

**This module is NOT wired into `Executor` or `RecoveryExecutor`.** Doing so
today would require the two things a companion investigation (see this
package's `CHANGELOG.md`, "Trusted provenance was investigated (step 3)...")
found do not exist yet anywhere reachable by a `pip install`ed consumer: a
real signer producing a genuine `CandidateArtifactAttestation` for the
distribution actually being installed, and a real, independent signer
producing a genuine `InstalledHostObservation` for the actual host. Landing
this verifier now — fail-closed, with the sidecar contract fixed — is what lets
those two producers and the rehearsal that composes them (future work) be
built against a stable target rather than each inventing their own shape.

## The sidecar contract

Two JSON documents, each wrapped in the SAME generic envelope shape:

    {
      "document": { ... the attested facts, see below ... },
      "signature": "<opaque, whatever the verifier understands>",
      "key_id": "<which key signed it>"
    }

`document` for a **candidate attestation** is exactly a `CandidateArtifact.v1`
document (`host_source.candidate_receipt_from_mapping`'s schema — unchanged,
reused rather than duplicated). `document` for an **installed-host
observation** is `InstalledHostObservation.v1`:

    {
      "schema": "InstalledHostObservation.v1",
      "distribution": "dotmac-deployment-foundation",
      "version": "0.4.0a2",
      "sha256": "<64 lower-case hex, the wheel artifact digest>",
      "observed_at": "2026-09-08T00:00:00Z"
    }

`sha256` is deliberately the same field name and the same subject
`CandidateReceipt.artifact_digest` is — the wheel FILE's digest, never a
source-tree or installed-content digest (see `host_source.py`'s `WRONG_KIND`
discussion; that discrimination is unchanged and still lives there, applied
by whatever future caller binds this module's result to the live host via
`require_host_source`).

## What "distinct trust roots" means here, mechanically

`DistinctTrustRoots` refuses at CONSTRUCTION if the candidate root's and the
installed root's `accepted_key_ids` overlap at all. Two roots that could both
accept the same key are one root wearing two names — exactly the "two fields
or two files supplied by one caller remain one reading" failure this module
exists to close. A caller cannot construct an admitting `DistinctTrustRoots`
by pointing two policies at the same signer, however the two documents were
shaped.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping
from typing import Any, Final

from .digest import Digest
from .errors import PreconditionFailed, SpecError
from .evidence import SignatureVerifier
from .host_source import CandidateReceipt, candidate_receipt_from_mapping

__all__ = [
    "ATTESTATIONS_DISAGREE",
    "CANDIDATE_ATTESTATION_PURPOSE",
    "INSTALLED_OBSERVATION_PURPOSE",
    "INSTALLED_OBSERVATION_SCHEMA",
    "KEY_NOT_TRUSTED",
    "OBSERVATION_ABSENT",
    "OBSERVATION_MALFORMED",
    "ROOT_PURPOSE_MISMATCH",
    "SAME_KEY_SIGNED_BOTH",
    "SIGNATURE_INVALID",
    "TRUST_ROOTS_NOT_DISTINCT",
    "AttestationTrustRoot",
    "DistinctTrustRoots",
    "InstalledObservation",
    "SignedAttestationEnvelope",
    "TrustedAttestationBinding",
    "installed_observation_from_mapping",
    "verify_trusted_host_source",
]

#: The ONLY purpose a trust root binding the candidate-attestation slot may
#: declare. Not interchangeable with :data:`INSTALLED_OBSERVATION_PURPOSE` —
#: `DistinctTrustRoots.__post_init__` refuses a root in the wrong slot, the
#: same discipline `evidence.ReleaseEvidenceVerificationIdentity` already
#: applies to its own single purpose.
CANDIDATE_ATTESTATION_PURPOSE: Final = (
    "dotmac_deployment_foundation.host_source.candidate_attestation"
)

#: The ONLY purpose a trust root binding the installed-observation slot may
#: declare.
INSTALLED_OBSERVATION_PURPOSE: Final = (
    "dotmac_deployment_foundation.host_source.installed_observation"
)

INSTALLED_OBSERVATION_SCHEMA: Final = "InstalledHostObservation.v1"

# ── stable refusal codes — assert the code, read the prose ─────────────────

#: Either envelope was `None`. The repair names WHICH one, in the message; the
#: code is shared because the repair family ("go get the missing attestation")
#: is the same either way.
OBSERVATION_ABSENT: Final = "trusted-host-source-attestation-absent"

#: An envelope's `document` does not parse as its declared schema.
OBSERVATION_MALFORMED: Final = "trusted-host-source-attestation-malformed"

#: A `DistinctTrustRoots` was constructed with a root in the wrong slot.
ROOT_PURPOSE_MISMATCH: Final = "trusted-host-source-root-purpose-mismatch"

#: `DistinctTrustRoots` was constructed with overlapping accepted key sets —
#: the two roots are not distinct, whatever their `purpose` fields say.
TRUST_ROOTS_NOT_DISTINCT: Final = "trusted-host-source-trust-roots-not-distinct"

#: An envelope's `key_id` is not in its own slot's `accepted_key_ids`. A valid
#: signature from a stranger is still a stranger — `evidence.accept_release_
#: evidence` states the identical rule for the same reason.
KEY_NOT_TRUSTED: Final = "trusted-host-source-key-not-trusted"

#: The two envelopes named the SAME `key_id`. Checked directly, in addition to
#: (never instead of) `TRUST_ROOTS_NOT_DISTINCT`: a misconfigured pair of
#: roots that individually validate but happen to share a signer must not be
#: rescued by this check alone, but this check still refuses it even if a
#: caller found some way to construct overlapping roots this module's own
#: `__post_init__` should already have refused.
SAME_KEY_SIGNED_BOTH: Final = "trusted-host-source-same-key-signed-both"

#: A signature did not verify over its envelope's canonical bytes.
SIGNATURE_INVALID: Final = "trusted-host-source-signature-invalid"

#: Both envelopes verified, independently, against distinct roots — and still
#: describe different bytes. One of the two attestations is stale, forged in
#: a way its own signature does not detect (an honestly-signed lie), or about
#: a different Foundation entirely.
ATTESTATIONS_DISAGREE: Final = "trusted-host-source-attestations-disagree"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


# ── the generic envelope — a parsed shape, never authority-bearing alone ────


@dataclasses.dataclass(frozen=True, slots=True)
class SignedAttestationEnvelope:
    """``{document, signature, key_id}`` — the ONE shape both attestation kinds
    share. Mirrors `evidence.SignedEvidenceEnvelope` deliberately: the same
    corruption that type's docstring recounts (a document silently
    restringified between disk and verifier, so a signature is checked over a
    restatement rather than the thing signed) is possible here too, and the
    fix is the same — hold the parsed mapping, never the caller's string.

    Holding one of these proves NOTHING was verified. It is exactly as
    authority-free as `host_source.CandidateReceipt` — a value a test or an
    attacker can construct by hand just as easily as a real signer can. The
    verification happens only in :func:`verify_trusted_host_source`, against a
    verifier and a trust root this type never sees.
    """

    document: Mapping[str, Any]
    signature: str
    key_id: str

    def __post_init__(self) -> None:
        if isinstance(self.document, str) or not isinstance(self.document, Mapping):
            raise SpecError(
                "SignedAttestationEnvelope.document must be the parsed JSON "
                f"object the signature covers, got {type(self.document).__name__}. "
                "A stringified document is a restatement, and a signature "
                "verified over a restatement verifies nothing",
                code=OBSERVATION_MALFORMED,
            )
        if not str(self.signature).strip():
            raise SpecError(
                "SignedAttestationEnvelope.signature is empty. An unsigned "
                "envelope proves only that somebody could write a file",
                code=OBSERVATION_MALFORMED,
            )
        if not str(self.key_id).strip():
            raise SpecError(
                "SignedAttestationEnvelope.key_id is empty, so no trust root "
                "can accept or refuse it",
                code=OBSERVATION_MALFORMED,
            )

    @classmethod
    def from_payload(cls, payload: Any, *, where: str) -> SignedAttestationEnvelope:
        if not isinstance(payload, Mapping):
            raise SpecError(
                f"{where}: envelope must be a JSON object", code=OBSERVATION_MALFORMED
            )
        unknown = sorted(set(payload) - {"document", "signature", "key_id"})
        if unknown:
            raise SpecError(
                f"{where}: envelope has unknown member(s) {unknown}. An "
                "envelope carrying more than its document, signature and key "
                "id may have been produced by something this version cannot "
                "judge",
                code=OBSERVATION_MALFORMED,
            )
        return cls(
            document=payload.get("document"),  # type: ignore[arg-type]
            signature=str(payload.get("signature") or ""),
            key_id=str(payload.get("key_id") or ""),
        )

    def canonical_bytes(self) -> bytes:
        """The exact bytes a signature covers — sorted keys, tight separators.

        Same rule `evidence.ReleaseEvidenceV1.canonical_bytes` and
        `document.py`'s descriptor canonicalisation already apply, for the
        identical reason: a signature over raw file bytes lets a harmless
        re-serialization look like tampering.
        """
        return json.dumps(self.document, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )


# ── the installed-observation half of the contract ──────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class InstalledObservation:
    """The `InstalledHostObservation.v1` facts, and only those.

    Deliberately narrow, the same way `host_source.CandidateReceipt` is
    deliberately narrow: this needs four fields to make the binding, and a
    type that carried more would invite a later comparison against a field
    that is not part of it.
    """

    distribution: str
    version: str
    artifact_digest: Digest
    observed_at: str


def installed_observation_from_mapping(
    document: Mapping[str, Any], *, where: str = "installed-host observation"
) -> InstalledObservation:
    """Read a committed-shape `InstalledHostObservation.v1`, refusing anything else.

    `SpecError`, not `PreconditionFailed`: a malformed observation is a
    document a producer got wrong, the same split
    `host_source.candidate_receipt_from_mapping` draws for the candidate side.
    """
    schema = str(document.get("schema", ""))
    if schema != INSTALLED_OBSERVATION_SCHEMA:
        raise SpecError(
            f"{where}: declares schema {schema!r}, expected "
            f"{INSTALLED_OBSERVATION_SCHEMA!r}. A mapping with a 'sha256' key "
            "is not an installed-host observation; accepting one would make "
            "this half of the gate satisfiable by any JSON file whose keys "
            "happen to line up",
            code=OBSERVATION_MALFORMED,
        )
    for field in ("distribution", "version", "sha256", "observed_at"):
        if not str(document.get(field, "")).strip():
            raise SpecError(
                f"{where}: carries no {field!r}. Every one of the four is "
                "load-bearing — without it the observation cannot say which "
                "distribution, which version, which bytes, or when",
                code=OBSERVATION_MALFORMED,
            )
    return InstalledObservation(
        distribution=str(document["distribution"]),
        version=str(document["version"]),
        artifact_digest=Digest.parse(str(document["sha256"]), where=f"{where}.sha256"),
        observed_at=str(document["observed_at"]),
    )


# ── trust roots — declared, disjoint, never inferred ────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class AttestationTrustRoot:
    """Who may sign for ONE slot of this contract, and what purpose they sign for.

    Same shape and same refusal `evidence.TrustPolicy` already applies to
    `accepted_key_ids`: empty must never mean "any signer accepted", because
    that is the most permissive setting a missing configuration could produce.
    """

    purpose: str
    accepted_key_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not str(self.purpose).strip():
            raise SpecError(
                "AttestationTrustRoot.purpose is empty", code=OBSERVATION_MALFORMED
            )
        if not self.accepted_key_ids:
            raise SpecError(
                "AttestationTrustRoot.accepted_key_ids is empty. An empty "
                "signer set must not mean 'anyone' — configure the signers, "
                "or this module cannot tell an authority from a stranger",
                code=OBSERVATION_MALFORMED,
            )


@dataclasses.dataclass(frozen=True, slots=True)
class DistinctTrustRoots:
    """The pair of roots :func:`verify_trusted_host_source` checks against.

    Refuses at construction — never at call time — if the two roots are not
    actually distinct. That is the mechanical meaning of "distinct trust
    roots, not one root twice" in this module: a `DistinctTrustRoots` that
    admits construction is, by construction, two roots that share no key.
    """

    candidate: AttestationTrustRoot
    installed: AttestationTrustRoot

    def __post_init__(self) -> None:
        if self.candidate.purpose != CANDIDATE_ATTESTATION_PURPOSE:
            raise SpecError(
                f"DistinctTrustRoots.candidate declares purpose "
                f"{self.candidate.purpose!r}, not "
                f"{CANDIDATE_ATTESTATION_PURPOSE!r}. A root minted for the "
                "installed-observation slot must not be usable in the "
                "candidate slot — the only way to make that unrepresentable "
                "is to refuse it here",
                code=ROOT_PURPOSE_MISMATCH,
            )
        if self.installed.purpose != INSTALLED_OBSERVATION_PURPOSE:
            raise SpecError(
                f"DistinctTrustRoots.installed declares purpose "
                f"{self.installed.purpose!r}, not "
                f"{INSTALLED_OBSERVATION_PURPOSE!r}",
                code=ROOT_PURPOSE_MISMATCH,
            )
        shared = self.candidate.accepted_key_ids & self.installed.accepted_key_ids
        if shared:
            raise SpecError(
                "DistinctTrustRoots.candidate and .installed accept a shared "
                f"key id {sorted(shared)}. Two policies that can both be "
                "satisfied by the same signer are one trust root wearing two "
                "names — a single party holding that key could author both "
                "attestations, which is exactly the 'one caller, two "
                "readings' shape this contract exists to refuse",
                code=TRUST_ROOTS_NOT_DISTINCT,
            )


# ── the result — carries only what was actually checked ─────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class TrustedAttestationBinding:
    """Two independently authored, signed attestations that agree.

    Constructible only through :func:`verify_trusted_host_source` in the same
    sense `host_source.HostSource` is constructible only through
    `require_host_source`: every field was checked before this value existed.

    This is NOT a `HostSource` and does not, by itself, prove anything about
    the process currently running — binding a `candidate_receipt` to the LIVE
    host remains `host_source.require_host_source`'s job, unchanged, called
    separately by whatever future caller composes the two (tracked as future
    work; not done by this module, which owns only the two-attestation
    agreement).
    """

    candidate_receipt: CandidateReceipt
    installed_observation: InstalledObservation
    candidate_key_id: str
    installed_key_id: str


def verify_trusted_host_source(
    *,
    candidate: SignedAttestationEnvelope | None,
    candidate_verifier: SignatureVerifier,
    installed: SignedAttestationEnvelope | None,
    installed_verifier: SignatureVerifier,
    trust_roots: DistinctTrustRoots,
) -> TrustedAttestationBinding:
    """Admit only if both attestations are present, each verifies against its
    OWN distinct trust root, neither could have produced the other's half, and
    the two agree — or refuse, naming exactly which property failed.

    Every refusal is `PreconditionFailed`: nothing has changed, and the
    identical call can be re-run once the stated cause is resolved. Ordering,
    deliberately: absence, then the same-key (one-caller) check, then each
    key's trust-root membership, then each signature, then agreement —
    signature verification never runs on a document whose presence was never
    established or that already failed a cheaper check, and content is
    compared only once both signatures are known-good, so a stranger cannot
    use this function to probe which content it would have accepted.
    """
    if candidate is None:
        raise PreconditionFailed(
            "no candidate attestation was supplied. There is nothing here "
            "that could bind these bytes to a source revision, signed by "
            "anyone",
            code=OBSERVATION_ABSENT,
        )
    if installed is None:
        raise PreconditionFailed(
            "no installed-host observation was supplied. A candidate "
            "attestation alone says what SHOULD be running somewhere; it "
            "says nothing about what actually is",
            code=OBSERVATION_ABSENT,
        )

    # THE ONE-CALLER CHECK, FIRST — before either key is checked against a
    # trust root, and deliberately not merely a restatement of what
    # `DistinctTrustRoots` already refuses at construction. That refusal
    # protects against a MISCONFIGURED pair of roots; this one protects
    # against a single party presenting two envelopes under one key
    # regardless of how the roots are configured, so it fires on its own
    # evidence rather than depending on `trust_roots` having been built
    # correctly. Checked before signature verification too: a stranger must
    # not be able to use signature failure output to probe past this one.
    if candidate.key_id == installed.key_id:
        raise PreconditionFailed(
            f"both attestations are signed by the same key "
            f"({candidate.key_id!r}). A candidate attestation and an "
            "installed-host observation signed by one key are one party's "
            "word twice, not two independent readings — the exact shape both "
            "prior attempts at this admission path had, in different "
            "clothing",
            code=SAME_KEY_SIGNED_BOTH,
        )

    if candidate.key_id not in trust_roots.candidate.accepted_key_ids:
        raise PreconditionFailed(
            f"the candidate attestation is signed by {candidate.key_id!r}, "
            "which is not an accepted candidate-attestation signer "
            f"{sorted(trust_roots.candidate.accepted_key_ids)}. A valid "
            "signature from a stranger is still a stranger",
            code=KEY_NOT_TRUSTED,
        )
    if installed.key_id not in trust_roots.installed.accepted_key_ids:
        raise PreconditionFailed(
            f"the installed-host observation is signed by "
            f"{installed.key_id!r}, which is not an accepted "
            f"installed-observation signer "
            f"{sorted(trust_roots.installed.accepted_key_ids)}",
            code=KEY_NOT_TRUSTED,
        )

    try:
        candidate_ok = candidate_verifier.verify(
            key_id=candidate.key_id,
            message=candidate.canonical_bytes(),
            signature=candidate.signature,
        )
    except Exception as exc:
        raise PreconditionFailed(
            f"candidate attestation signature verification failed: {exc}",
            code=SIGNATURE_INVALID,
        ) from exc
    if not candidate_ok:
        raise PreconditionFailed(
            "the candidate attestation signature does not verify over its "
            "canonical bytes. Either the document was edited after signing "
            "or it was signed by a different key than it claims",
            code=SIGNATURE_INVALID,
        )

    try:
        installed_ok = installed_verifier.verify(
            key_id=installed.key_id,
            message=installed.canonical_bytes(),
            signature=installed.signature,
        )
    except Exception as exc:
        raise PreconditionFailed(
            f"installed-host observation signature verification failed: {exc}",
            code=SIGNATURE_INVALID,
        ) from exc
    if not installed_ok:
        raise PreconditionFailed(
            "the installed-host observation signature does not verify over "
            "its canonical bytes. Either the document was edited after "
            "signing or it was signed by a different key than it claims",
            code=SIGNATURE_INVALID,
        )

    # Content is parsed and compared only AFTER both signatures verify,
    # deliberately: refusing on content first would let a caller probe which
    # values this function accepts using documents they never had to sign —
    # `evidence.accept_release_evidence` states the identical rule.
    candidate_receipt = candidate_receipt_from_mapping(
        candidate.document, where="candidate attestation document"
    )
    installed_observation = installed_observation_from_mapping(
        installed.document, where="installed-host observation document"
    )

    if candidate_receipt.facility != installed_observation.distribution:
        raise PreconditionFailed(
            f"the candidate attestation is about {candidate_receipt.facility!r} "
            f"and the installed-host observation is about "
            f"{installed_observation.distribution!r}. An attestation for "
            "another facility binds another facility's bytes",
            code=ATTESTATIONS_DISAGREE,
        )
    if candidate_receipt.version != installed_observation.version:
        raise PreconditionFailed(
            f"the candidate attestation is for version "
            f"{candidate_receipt.version!r} and the installed-host "
            f"observation is for version {installed_observation.version!r}. "
            "Two independently signed statements about two different "
            "versions do not bind each other",
            code=ATTESTATIONS_DISAGREE,
        )
    if candidate_receipt.artifact_digest != installed_observation.artifact_digest:
        raise PreconditionFailed(
            f"the candidate attestation binds artifact "
            f"{candidate_receipt.artifact_digest} and the installed-host "
            f"observation reports {installed_observation.artifact_digest}. "
            "One of the two independent authorities is describing bytes the "
            "other is not, and guessing which would bind a Foundation nobody "
            "actually attested to these bytes",
            code=ATTESTATIONS_DISAGREE,
        )

    return TrustedAttestationBinding(
        candidate_receipt=candidate_receipt,
        installed_observation=installed_observation,
        candidate_key_id=candidate.key_id,
        installed_key_id=installed.key_id,
    )
