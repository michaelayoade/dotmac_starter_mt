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
from typing import Any, Final, Protocol, runtime_checkable

from .digest import Digest
from .errors import PreconditionFailed, SpecError

__all__ = [
    "RELEASE_EVIDENCE_SCHEMA",
    "ReleaseEvidenceV1",
    "SignatureVerifier",
    "TrustPolicy",
    "accept_release_evidence",
]

RELEASE_EVIDENCE_SCHEMA: Final = "ReleaseEvidence.v1"

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


def accept_release_evidence(
    payload: Any,
    *,
    revision: str,
    policy: TrustPolicy,
    verifier: SignatureVerifier | None,
) -> ReleaseEvidenceV1:
    """Accept evidence for `revision`, or refuse and say which property failed.

    `payload` is the on-disk envelope: ``{"document": {...}, "signature": ...,
    "key_id": ...}``. The signature and key id sit OUTSIDE the signed document
    on purpose — a document carrying its own key id would let a forger nominate
    the key that verifies it, and the policy's `accepted_key_ids` is what makes
    that nomination worthless.
    """
    if verifier is None:
        raise PreconditionFailed(
            "no signature verifier is installed, so this release evidence "
            "cannot be verified. Refusing rather than accepting it unchecked: "
            "an unverifiable document is not a document that passed, and "
            "degrading to trust when the verifier is missing is a bypass "
            "anyone can trigger by deleting configuration"
        )
    if not isinstance(payload, dict):
        raise SpecError("release evidence envelope must be a JSON object")

    document = payload.get("document")
    signature = str(payload.get("signature") or "")
    key_id = str(payload.get("key_id") or "")
    if not signature:
        raise PreconditionFailed(
            "the release evidence carries no signature. An unsigned file "
            "proves only that somebody could write to this directory"
        )
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
