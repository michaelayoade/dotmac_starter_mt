"""``ReleaseEvidence.v1`` — evidence must be signed, and it must be OURS.

The gate this replaces read a JSON file and checked that it was **non-empty**:

    evidence = self._effects.release_evidence(plan.source_revision)
    if not evidence:
        raise PreconditionFailed(...)

So "CI accepted this revision" was satisfied by any writable file containing
`{"<revision>": {"x": "y"}}`. Nothing established who wrote it, whether the run
it describes happened, or whether it happened *in this repository*. The step
was named `verify_release_evidence` and verified that a file existed.

## The fork-head check, which matters more than it looks

All seven Dotmac repositories are now `all_external_contributors`, which stops
fork code from **executing** in CI. It does nothing about evidence being
**admitted**: a fork's workflow run is real, green, and describes a real commit.
Unsigned evidence with no head check lets that run satisfy this gate for code
that never ran here at all.

GitHub gives the discriminator for free — a workflow run carries both
`repository_id` (where the workflow lives) and `head_repository_id` (where the
branch lives). They differ exactly when the run came from a fork. Comparing
them is one line and closes the whole class, which is why the absence of that
line was worth a finding.

`ref` is checked for the same reason at a different layer: a green run on
`refs/heads/some-branch` is not a green run on a protected ref, and only the
latter had the required checks enforced at merge.

## No cryptography here, and that is deliberate

This facility declares ZERO runtime dependencies (ADR-0070), so it cannot
import a signing library and must not ship a weak stdlib substitute — an
HMAC-with-shared-secret scheme would let every verifier forge, which for
release evidence is not a smaller version of the property, it is the absence of
it.

Instead the caller supplies a :class:`SignatureVerifier`, exactly as it already
supplies `AuthorizationReceipt`, `ProbeResult` and the ancestry observation.
This module owns the part that must be reviewable — what has to be true before
evidence counts — and the assembly owns the algorithm.

**No verifier means refuse.** Not "skip signature checking": an unverifiable
document is not a document that passed, and a facility that degrades to
trusting unsigned input when its verifier is missing has a bypass that anybody
can trigger by removing configuration.

## Canonical bytes, so the signature covers what it appears to cover

The signed message is the canonical serialization of the *whole* document —
sorted keys, no incidental whitespace — never the raw file bytes. A signature
over raw bytes lets a re-serialization with identical content fail, and, worse,
lets two different byte strings with the same meaning have different standings.
Same rule `document.py` already applies to descriptors, for the same reason.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping
from typing import Any, Final, Protocol, runtime_checkable

from .digest import Digest
from .errors import PreconditionFailed, SpecError

__all__ = [
    "IDENTITY_KEY_MISMATCH",
    "IDENTITY_MALFORMED",
    "PURPOSE_MISMATCH",
    "RELEASE_EVIDENCE_IDENTITY_SCHEMA",
    "RELEASE_EVIDENCE_PURPOSE",
    "ReleaseEvidenceVerificationIdentity",
    "require_release_evidence_key",
    "RELEASE_EVIDENCE_SCHEMA",
    "ReleaseEvidenceV1",
    "SignatureVerifier",
    "SignedEvidenceEnvelope",
    "TrustPolicy",
    "accept_release_evidence",
]

RELEASE_EVIDENCE_SCHEMA: Final = "ReleaseEvidence.v1"

#: The purpose this facility's release-evidence signing key exists for, and the
#: ONLY purpose a key verifying release evidence may declare.
#:
#: Five signing identities exist in this estate — authorization, dispatch,
#: observation, recovery and this one. Before this constant, four of them were
#: TYPES that refuse a wrong purpose at construction and the fifth was a dict
#: literal and a JSON field. `vendor_cp/deployment/signers.py` said so outright:
#: *"`deployment_dispatch` and `platform_release_evidence` do not exist as types
#: yet, so they are named here as literals until they do."*
#:
#: The measured cost of that asymmetry was 4 typed diagonals and 16 typed
#: refusals where the material supports 5 and 20. The shortfall was never a
#: skipped test — it was an identity that could not refuse, because data does
#: not refuse anything.
RELEASE_EVIDENCE_PURPOSE: Final = "platform_release_evidence"

#: The document schema the public verification identity is installed as on a
#: target. Named here because this facility READS that file; it does not write
#: it, and the custody adapter that installs it belongs to the product.
RELEASE_EVIDENCE_IDENTITY_SCHEMA: Final = "PlatformCpPublicVerificationIdentity.v1"

#: MACHINE-READABLE, and that is the point rather than a nicety. A caller
#: deciding what to do about a wrong-purpose key must be able to branch on the
#: refusal; a caller that has to match on a sentence is coupled to the wording,
#: and the wording is the thing most likely to be improved.
PURPOSE_MISMATCH: Final = "release_evidence.purpose_mismatch"
IDENTITY_MALFORMED: Final = "release_evidence.identity_malformed"
IDENTITY_KEY_MISMATCH: Final = "release_evidence.identity_key_mismatch"

_REVISION = re.compile(r"^[0-9a-f]{40}$")

#: Required on every document. Absent means refuse — a document missing a field
#: this version checks is a document this version cannot judge, and treating an
#: absent field as satisfied is how a gate quietly stops gating.
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "schema",
    "revision",
    "repository",
    "repository_id",
    "head_repository_id",
    "ref",
    "run_id",
    "workflow",
    "conclusion",
)


@runtime_checkable
class SignatureVerifier(Protocol):
    """Whatever the assembly uses to check a signature.

    `key_id` is passed so the verifier can select a key WITHOUT this module
    knowing anything about key formats. Returning `False` and raising are both
    treated as a refusal by the caller below, so a verifier may do either.
    """

    def verify(self, *, key_id: str, message: bytes, signature: str) -> bool: ...


#: `sha256:` + 64 lower-case hex. Foundation validates the SHAPE and never the
#: key: this package holds no crypto library and must not acquire one — that is
#: the same zero-dependency line `provenance.py` draws when it declines to import
#: Control's `ApprovalEvidence`.
_FINGERPRINT: Final = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclasses.dataclass(frozen=True, slots=True)
class ReleaseEvidenceVerificationIdentity:
    """WHO may verify release evidence, as a type that refuses a wrong purpose.

    Mirrors the shape Control's four signer identities already use —
    ``key_id``, ``algorithm``, ``public_key_fingerprint``, ``purpose``, refusing
    at construction — because the estate's fifth identity being a dict literal
    while the other four were types is the whole defect. Deliberately NOT an
    import of Control's class: this facility declares zero runtime dependencies,
    and `provenance.AuthorizationReceipt` already draws that line and says why.

    ## What the construction-time refusal buys

    An off-diagonal case becomes PROVABLE rather than argued. A caller cannot
    hold one of these bearing the authorization, dispatch, observation or
    recovery purpose — not "it would be rejected later", but the value does not
    exist. That is what turns four typed refusals into twenty.

    ## What it does NOT do

    It verifies no signature and reads no key. `SignatureVerifier` remains the
    assembly's, and this type says which key it is entitled to be — not whether
    the bytes check out. Binding the two is :func:`require_release_evidence_key`.
    """

    key_id: str
    algorithm: str
    public_key_fingerprint: str
    purpose: str = RELEASE_EVIDENCE_PURPOSE

    def __post_init__(self) -> None:
        for field in ("key_id", "algorithm"):
            value = str(getattr(self, field)).strip()
            if not value or len(value) > 200:
                raise SpecError(
                    f"ReleaseEvidenceVerificationIdentity.{field} must be "
                    "non-empty and at most 200 characters",
                    code=IDENTITY_MALFORMED,
                )
        if not _FINGERPRINT.match(str(self.public_key_fingerprint)):
            raise SpecError(
                "public_key_fingerprint must be sha256: followed by 64 "
                f"lower-case hex characters, got "
                f"{self.public_key_fingerprint!r}",
                code=IDENTITY_MALFORMED,
            )
        if self.purpose != RELEASE_EVIDENCE_PURPOSE:
            raise SpecError(
                f"a release-evidence verification identity must declare "
                f"{RELEASE_EVIDENCE_PURPOSE!r}, not {self.purpose!r}. The four "
                "other signing purposes in this estate have their own identity "
                "types for the same reason: a key minted to authorize a "
                "deployment must not be able to vouch for a release, and the "
                "only way to make that unrepresentable is to refuse it here",
                code=PURPOSE_MISMATCH,
            )

    @classmethod
    def from_document(cls, document: Any) -> ReleaseEvidenceVerificationIdentity:
        """Read the identity a target carries, refusing anything else.

        The installed file is `PlatformCpPublicVerificationIdentity.v1`. This is
        the ONE door from that document to this type, so a caller cannot
        half-parse it and pass the rest along loose — the same rule Control's
        `RecoveryGrantV1.parse` states for its envelope.
        """
        if not isinstance(document, Mapping):
            raise SpecError(
                "a verification identity must be a JSON object, got "
                f"{type(document).__name__}",
                code=IDENTITY_MALFORMED,
            )
        schema = document.get("schema")
        if schema != RELEASE_EVIDENCE_IDENTITY_SCHEMA:
            raise SpecError(
                f"this is not a {RELEASE_EVIDENCE_IDENTITY_SCHEMA} document "
                f"(schema {schema!r})",
                code=IDENTITY_MALFORMED,
            )
        missing = sorted(
            {"key_id", "algorithm", "public_key_fingerprint", "purpose"} - set(document)
        )
        if missing:
            raise SpecError(
                f"the verification identity is missing {missing}. A purpose "
                "absent from the document is NOT a purpose defaulted to this "
                "one — that would let a document say nothing and be read as "
                "saying the right thing",
                code=IDENTITY_MALFORMED,
            )
        return cls(
            key_id=str(document["key_id"]),
            algorithm=str(document["algorithm"]),
            public_key_fingerprint=str(document["public_key_fingerprint"]),
            purpose=str(document["purpose"]),
        )

    def as_document(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "public_key_fingerprint": self.public_key_fingerprint,
            "purpose": self.purpose,
            "schema": RELEASE_EVIDENCE_IDENTITY_SCHEMA,
        }


def require_release_evidence_key(
    identity: ReleaseEvidenceVerificationIdentity, *, key_id: str
) -> None:
    """The identity must be the identity of the key that actually signed.

    An identity held beside a verification proves nothing on its own — it has to
    be bound to the key the envelope nominated, or a correct release-evidence
    identity could sit next to a signature made by any other key in the trust
    policy. That is the same reason `TrustPolicy.accepted_key_ids` exists and
    the same reason a document may not carry its own key id.
    """
    if str(identity.key_id) != str(key_id):
        raise SpecError(
            f"the release-evidence identity names key {identity.key_id!r} and "
            f"the envelope was signed by {key_id!r}. An identity that is not "
            "the signing key's is a statement about a different key",
            code=IDENTITY_KEY_MISMATCH,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class TrustPolicy:
    """Who may vouch for a release, and what shape a vouching run must have.

    Every field is a declared expectation rather than something read out of the
    evidence. A policy derived from the document it judges is not a policy.
    """

    #: Key ids permitted to sign. Empty is refused rather than meaning "any" —
    #: "no configured signers" must never be the most permissive setting.
    accepted_key_ids: frozenset[str]
    #: The repository evidence must come from, e.g. `michaelayoade/dotmac_starter_mt`.
    repository: str
    #: Refs whose green runs count. A branch build is not a protected-ref build.
    protected_refs: frozenset[str] = frozenset({"refs/heads/main"})
    #: The only run conclusion that is evidence of acceptance.
    required_conclusion: str = "success"

    def __post_init__(self) -> None:
        if not self.accepted_key_ids:
            raise SpecError(
                "TrustPolicy.accepted_key_ids is empty. An empty signer set "
                "must not mean 'anyone' — configure the signers, or this "
                "facility cannot tell an authority from a stranger"
            )
        if not str(self.repository).strip():
            raise SpecError("TrustPolicy.repository is empty")
        if not self.protected_refs:
            raise SpecError(
                "TrustPolicy.protected_refs is empty, which would accept a "
                "green run on any branch as though it were a merge gate"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class ReleaseEvidenceV1:
    """One CI run, as the thing that vouches for a revision."""

    revision: str
    repository: str
    repository_id: str
    head_repository_id: str
    ref: str
    run_id: str
    workflow: str
    conclusion: str

    @property
    def from_a_fork(self) -> bool:
        """True when the branch lived somewhere other than the workflow.

        The single discriminator that separates "our CI ran this" from "someone
        else's CI ran something and told us about it".
        """
        return self.repository_id != self.head_repository_id

    def as_document(self) -> dict[str, Any]:
        return {
            "schema": RELEASE_EVIDENCE_SCHEMA,
            "conclusion": self.conclusion,
            "head_repository_id": self.head_repository_id,
            "ref": self.ref,
            "repository": self.repository,
            "repository_id": self.repository_id,
            "revision": self.revision,
            "run_id": self.run_id,
            "workflow": self.workflow,
        }

    def canonical_bytes(self) -> bytes:
        """The exact bytes a signature covers.

        Sorted keys and tight separators, so the same facts always produce the
        same message. Signing raw file bytes instead would make an innocent
        re-serialization look like tampering, and would let two byte strings
        with identical meaning have different standings.
        """
        return json.dumps(
            self.as_document(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def sha256_digest(self) -> str:
        return str(Digest.of(self.canonical_bytes()))

    @classmethod
    def from_document(cls, document: Any) -> ReleaseEvidenceV1:
        if not isinstance(document, dict):
            raise SpecError("release evidence must be a JSON object")
        missing = sorted(set(REQUIRED_FIELDS) - set(document))
        if missing:
            raise SpecError(
                f"release evidence is missing required field(s) {missing}. A "
                "document missing a field this version checks is one it cannot "
                "judge; treating the absence as satisfied is how a gate stops "
                "gating"
            )
        schema = str(document["schema"])
        if schema != RELEASE_EVIDENCE_SCHEMA:
            raise SpecError(
                f"release evidence schema is {schema!r}, expected "
                f"{RELEASE_EVIDENCE_SCHEMA!r}"
            )
        revision = str(document["revision"]).strip().lower()
        if not _REVISION.match(revision):
            raise SpecError(
                f"release evidence revision {document['revision']!r} is not a "
                "full 40-hex commit"
            )
        return cls(
            revision=revision,
            repository=str(document["repository"]),
            repository_id=str(document["repository_id"]),
            head_repository_id=str(document["head_repository_id"]),
            ref=str(document["ref"]),
            run_id=str(document["run_id"]),
            workflow=str(document["workflow"]),
            conclusion=str(document["conclusion"]),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class SignedEvidenceEnvelope:
    """The on-disk envelope, parsed once and carried TYPED to the verifier.

    ## Why this type exists — the seam itself forced a corruption

    `Effects.release_evidence` used to be typed ``Mapping[str, str]``, and the
    compose-host provider dutifully satisfied that type:

        return {str(key): str(value) for key, value in entry.items()}

    ``entry["document"]`` is a NESTED OBJECT — the very thing the signature
    covers — and ``str()`` of a dict is its Python repr. So the envelope
    reached `accept_release_evidence` with its document flattened into
    ``"{'schema': ...}"``, `ReleaseEvidenceV1.from_document` blew up on a
    string, and the gate could never pass against a GENUINE signed envelope on
    the real provider. The type at the seam did not merely permit the bug; it
    REQUIRED it — every conforming implementation had to stringify.

    A signature is over exact canonical bytes. Any restringification between
    the file and the verifier is a forgery-shaped no-op: what gets verified is
    a restatement, not the document — the same reason
    `AuthorizationReceipt.control_plan_digest` is recorded verbatim and never
    normalized. This facility must not restate what it does not own.

    So the seam now carries THIS type, and the type makes the corruption
    unrepresentable rather than discouraged: construction refuses a document
    that is not a mapping, so there is no conforming implementation left that
    stringifies. The fix has the same shape as the defect — compiler-shaped
    pressure, pointing the other way.
    """

    #: The signed document EXACTLY as parsed from disk. Never rebuilt, never
    #: restated; `ReleaseEvidenceV1.from_document` reads it and re-derives the
    #: canonical bytes the signer signed.
    document: Mapping[str, Any]
    #: Outside the document on purpose — a document carrying its own key id
    #: would let a forger nominate the key that verifies it.
    signature: str
    key_id: str

    def __post_init__(self) -> None:
        if isinstance(self.document, str) or not isinstance(self.document, Mapping):
            raise SpecError(
                "SignedEvidenceEnvelope.document must be the parsed JSON "
                f"object the signature covers, got {type(self.document).__name__}. "
                "A stringified document is a restatement, and a signature "
                "verified over a restatement verifies nothing"
            )
        if not str(self.signature).strip():
            raise PreconditionFailed(
                "the release evidence carries no signature. An unsigned file "
                "proves only that somebody could write to this directory"
            )
        if not str(self.key_id).strip():
            raise PreconditionFailed(
                "the release evidence names no signing key, so no policy can "
                "accept it. A signature that does not say which key made it "
                "cannot be checked against anything"
            )

    @classmethod
    def from_payload(cls, payload: Any) -> SignedEvidenceEnvelope:
        """Parse the on-disk shape ``{"document": .., "signature": .., "key_id": ..}``.

        The ONE place file bytes become this type, so a reader cannot
        half-parse an envelope and hand the rest along loose.
        """
        if not isinstance(payload, Mapping):
            raise SpecError("release evidence envelope must be a JSON object")
        unknown = sorted(set(payload) - {"document", "signature", "key_id"})
        if unknown:
            raise SpecError(
                f"release evidence envelope has unknown member(s) {unknown}. "
                "An envelope carrying more than the document, its signature "
                "and its key id may have been produced by something this "
                "version cannot judge"
            )
        return cls(
            document=payload.get("document"),  # type: ignore[arg-type]
            signature=str(payload.get("signature") or ""),
            key_id=str(payload.get("key_id") or ""),
        )


def accept_release_evidence(
    envelope: SignedEvidenceEnvelope | Any,
    *,
    revision: str,
    policy: TrustPolicy,
    verifier: SignatureVerifier | None,
) -> ReleaseEvidenceV1:
    """Accept evidence for `revision`, or refuse and say which property failed.

    Takes the TYPED envelope, and a raw mapping is parsed through the same one
    door (`SignedEvidenceEnvelope.from_payload`) rather than picked apart here —
    two parse paths for one on-disk shape is how they drift. The signature and
    key id sit OUTSIDE the signed document on purpose — a document carrying its
    own key id would let a forger nominate the key that verifies it, and the
    policy's `accepted_key_ids` is what makes that nomination worthless.
    """
    if verifier is None:
        raise PreconditionFailed(
            "no signature verifier is installed, so this release evidence "
            "cannot be verified. Refusing rather than accepting it unchecked: "
            "an unverifiable document is not a document that passed, and "
            "degrading to trust when the verifier is missing is a bypass "
            "anyone can trigger by deleting configuration"
        )
    if not isinstance(envelope, SignedEvidenceEnvelope):
        envelope = SignedEvidenceEnvelope.from_payload(envelope)

    document = envelope.document
    signature = envelope.signature
    key_id = envelope.key_id
    if key_id not in policy.accepted_key_ids:
        raise PreconditionFailed(
            f"release evidence is signed by {key_id!r}, which is not an "
            f"accepted signer {sorted(policy.accepted_key_ids)}. A valid "
            "signature from a stranger is still a stranger"
        )

    evidence = ReleaseEvidenceV1.from_document(document)

    try:
        ok = verifier.verify(
            key_id=key_id, message=evidence.canonical_bytes(), signature=signature
        )
    except Exception as exc:
        raise PreconditionFailed(
            f"release evidence signature verification failed: {exc}"
        ) from exc
    if not ok:
        raise PreconditionFailed(
            "the release evidence signature does not verify over its canonical "
            "bytes. Either the document was edited after signing or it was "
            "signed by a different key than it claims"
        )

    # Content checks run AFTER the signature, deliberately: refusing on content
    # first would let an attacker probe which values this facility accepts using
    # documents they never had to sign.
    wanted = str(revision).strip().lower()
    if evidence.revision != wanted:
        raise PreconditionFailed(
            f"the release evidence vouches for {evidence.revision}, not for "
            f"{wanted}. Evidence about another commit is not evidence about "
            "this one"
        )
    if evidence.repository != policy.repository:
        raise PreconditionFailed(
            f"release evidence comes from {evidence.repository!r}, not "
            f"{policy.repository!r}"
        )
    if evidence.from_a_fork:
        raise PreconditionFailed(
            f"release evidence describes a FORK run: repository_id "
            f"{evidence.repository_id} but head_repository_id "
            f"{evidence.head_repository_id}. The run is real and may well be "
            "green, but it ran somewhere else on somebody else's branch — "
            "admitting it would accept code that never ran here"
        )
    if evidence.ref not in policy.protected_refs:
        raise PreconditionFailed(
            f"release evidence is for ref {evidence.ref!r}, which is not one "
            f"of the protected refs {sorted(policy.protected_refs)}. A green "
            "run on an unprotected branch never had the merge gate enforced"
        )
    if evidence.conclusion != policy.required_conclusion:
        raise PreconditionFailed(
            f"release evidence conclusion is {evidence.conclusion!r}, not "
            f"{policy.required_conclusion!r}"
        )
    return evidence
