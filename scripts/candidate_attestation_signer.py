#!/usr/bin/env python3
"""Render, assemble and verify a ``TrustedHostAttestation.v2`` candidate envelope.

This script holds NO KEY and has no key path at all. It is the publish-only half
of the candidate-release signer (ruling Q5-B, 2026-11-18; PRE-E11 design 2.5).
The Ed25519 signature is made elsewhere, once, in the operator's ceremony
session by an OpenBao transit key (``transit-a2-candidate/``,
``candidate-release-v1``, non-exportable) and arrives here only as a finished
signature over bytes this script rendered. There is no PEM, no private-key
argument, no ``sign`` command and no signing primitive in this module; a test
proves that by AST.

Three commands, in ceremony order:

``render``
    Reads the committed ``CandidateArtifact.v1`` receipt and emits the
    UNSIGNED envelope plus the exact ``signed_bytes`` to be signed. The subject
    is built from the receipt verbatim, never from a rebuilt or recomputed
    digest (ADR-0070 2026-09-08, decisions 1 and 2). The envelope carries
    ``custody_domain = candidate_release_signer`` (fixes F-A: the old
    ``dotmac.foundation.candidate`` label named no real custody), the ceremony
    issuer, the enrolment UUID as ``trust_root_version``, and an explicit
    ``issued_at`` / ``expires_at`` the ceremony chooses.

``assemble``
    Joins the unsigned envelope and the externally produced signature into the
    final envelope. It does no cryptography.

``verify``
    Authenticates the envelope with Foundation's own
    ``verify_candidate_attestation`` against ONE pinned public identity (key id,
    fingerprint, trust-root version) and checks the subject against the
    committed receipt. This is what the publishing workflow runs.

What this script is not: it never builds an ``InstalledHostAttestationSubjectV2``
or an installed-host envelope, imports no host-attestation module, and performs
no trust-root registration (Control owns enrolment).

Scope (Slice A of the DR3_DR5 adoption-clearance design, 2026-11-28)
--------------------------------------------------------------------
Every command takes ``--scope {candidate,production}``; the default,
``candidate``, is byte-identical to the behaviour before the flag existed (the
ordinary audience, no new import, no new check). ``production`` renders,
assembles and verifies an envelope whose audience is
``<facility>/<blocks>``, the audience a production-class target's Control
admission policy requires. It calls ``foundation_adoption_gate``'s
``require-adoptable`` BEFORE anything else, so no production-scope envelope
exists without a verified DrillClearance.v1 for every adoption condition on
the version. The scope names every Foundation operation on a production
target, not only a deploy: admission compares the audience for all of them.

The envelope is written through
``dotmac_deployment_foundation.attestation_store.write_attestation_record``,
the package's own atomic-once writer; this script has no JSON writer of its own.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import dataclasses
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from dotmac_deployment_foundation.attestation_store import write_attestation_record
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.errors import DeploymentFoundationError
from dotmac_deployment_foundation.trusted_host_source import (
    ATTESTATION_SCHEMA,
    CANDIDATE_ATTESTATION_PURPOSE,
    AttestationEnvelopeV2,
    AttestationTrustPolicy,
    AttestationTrustRootV2,
    CandidateAttestationSubjectV2,
    verify_candidate_attestation,
)

CANDIDATE_ARTIFACT_SCHEMA = "CandidateArtifact.v1"

#: Constants of the ceremony-signed candidate role. They are not CLI inputs:
#: the enrolled root descriptor and the envelope must carry the identical
#: values, so a flag would only be a way to disagree with the enrolment.
CEREMONY_ISSUER = "urn:dotmac:foundation:candidate-release-ceremony:a2"
CUSTODY_DOMAIN = "candidate_release_signer"
CANDIDATE_AUDIENCE = "dotmac-deployment-foundation"
ALGORITHM = "ed25519"
#: Ed25519 signature length in bytes.
SIGNATURE_BYTES = 64
#: Stands in for the envelope's signature while it is unsigned. Excluded from
#: ``signed_bytes()``; ``assemble`` refuses anything but this placeholder.
UNSIGNED_PLACEHOLDER = "unsigned"
#: The verifier's policy type needs a second role. ``verify`` is candidate-only,
#: so that role is filled with an inert, per-process random root in a distinct
#: custody domain; nothing can ever be admitted through it.
_INERT_ROLE_DOMAIN = "inert-role-for-candidate-only-verification"


#: ``--scope`` values. ``candidate`` is the default and the only scope that
#: existed before the adoption-clearance gate.
SCOPE_CANDIDATE = "candidate"
SCOPE_PRODUCTION = "production"
SCOPES = (SCOPE_CANDIDATE, SCOPE_PRODUCTION)


class Refused(SystemExit):
    """A fail-closed refusal; the message names the cause, never a value."""


def _canonical_instant(value: datetime) -> str:
    """Fixed-width UTC RFC3339, matching ``trusted_host_source``'s regex."""
    if value.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_instant(text: str, name: str) -> datetime:
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise Refused(
            f"{name} must be canonical UTC RFC3339 (YYYY-MM-DDTHH:MM:SSZ)"
        ) from exc
    return parsed.replace(tzinfo=UTC)


def fingerprint_of(raw_public_key: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw_public_key).hexdigest()}"


def _adoption_gate():
    """The clearance gate, imported only when a production scope is asked for."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import foundation_adoption_gate as adoption

    return adoption


def audience_for_scope(scope: str, facility: str) -> str:
    """The envelope audience for a scope. The default scope never imports."""
    if scope == SCOPE_CANDIDATE:
        return CANDIDATE_AUDIENCE
    if scope == SCOPE_PRODUCTION:
        return _adoption_gate().production_audience(facility)
    raise Refused(f"unknown --scope {scope!r}")


def require_scope_cleared(scope: str, facility: str, version: str) -> None:
    """Refuse a production-scope act unless every blocking condition is cleared.

    A no-op for the default scope. Any refusal or indeterminate answer from the
    gate is a refusal here; there is no path that proceeds on an error.
    """
    if scope == SCOPE_CANDIDATE:
        return
    if scope != SCOPE_PRODUCTION:
        raise Refused(f"unknown --scope {scope!r}")
    adoption = _adoption_gate()
    try:
        adoption.require_adoptable(facility, version)
    except (adoption.gate.GateRefused, adoption.gate.GateIndeterminate) as exc:
        raise Refused(f"adoption gate: {exc}") from exc


def load_candidate_artifact_receipt(path: Path) -> dict[str, Any]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("schema") != CANDIDATE_ARTIFACT_SCHEMA:
        raise Refused(
            f"{path}: not a {CANDIDATE_ARTIFACT_SCHEMA} receipt "
            f"(schema={receipt.get('schema')!r}). The candidate attestation "
            "subject binds against Foundation's own CandidateArtifact.v1 "
            "record, never a rebuilt or recomputed digest (ADR-0070 "
            "2026-09-08, decision 1)."
        )
    return receipt


def candidate_subject_from_receipt(
    receipt: dict[str, Any],
) -> CandidateAttestationSubjectV2:
    """Build the exact subject the verifier expects, from the exact receipt.

    Every field is read from the committed receipt; none is recomputed.
    """
    return CandidateAttestationSubjectV2(
        package=str(receipt["facility"]),
        version=str(receipt["version"]),
        wheel_sha256=Digest.parse(
            f"sha256:{receipt['sha256']}", where="receipt.sha256"
        ),
        source_revision=str(receipt["source_sha"]),
        repository=str(receipt["repository"]),
        run_id=str(receipt["run_id"]),
        artifact_id=str(receipt["artifact_id"]),
    )


def render_unsigned_envelope(
    *,
    receipt: dict[str, Any],
    key_id: str,
    public_key_fingerprint: str,
    trust_root_version: str,
    issued_at: datetime,
    expires_at: datetime,
    observation_id: str | None = None,
) -> AttestationEnvelopeV2:
    """The unsigned envelope over the receipt's subject. Holds no key.

    Purpose, issuer, custody domain, audience and algorithm are constants of
    this role, never parameters, so there is no way to render an envelope for
    another role or another custody. The production-scope audience is applied
    only by ``cmd_render``, after the adoption gate has passed.
    """
    return AttestationEnvelopeV2(
        schema=ATTESTATION_SCHEMA,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
        issuer=CEREMONY_ISSUER,
        key_id=key_id,
        algorithm=ALGORITHM,
        public_key_fingerprint=public_key_fingerprint,
        custody_domain=CUSTODY_DOMAIN,
        trust_root_version=trust_root_version,
        issued_at=_canonical_instant(issued_at),
        expires_at=_canonical_instant(expires_at),
        audience=CANDIDATE_AUDIENCE,
        observation_id=observation_id or str(uuid.uuid4()),
        subject=candidate_subject_from_receipt(receipt).canonical_document(),
        signature=UNSIGNED_PLACEHOLDER,
    )


def assemble_envelope(
    unsigned: AttestationEnvelopeV2, signature_b64: str
) -> AttestationEnvelopeV2:
    """Attach an externally produced signature. No cryptography happens here."""
    if unsigned.signature != UNSIGNED_PLACEHOLDER:
        raise Refused("the envelope to assemble is already signed")
    try:
        raw = base64.b64decode(signature_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise Refused("signature is not strict base64") from exc
    if len(raw) != SIGNATURE_BYTES:
        raise Refused(
            f"signature must be {SIGNATURE_BYTES} raw bytes (ed25519), got {len(raw)}"
        )
    return dataclasses.replace(
        unsigned, signature=base64.b64encode(raw).decode("ascii")
    )


class _Ed25519Verifier:
    """Pure crypto primitive; the trust decision is the policy's, not this."""

    def verify(
        self, *, public_key: bytes, algorithm: str, message: bytes, signature: str
    ) -> bool:
        if algorithm != ALGORITHM:
            return False
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                base64.b64decode(signature, validate=True), message
            )
        except (InvalidSignature, ValueError):
            return False
        return True


def _pinned_policy(
    *,
    public_key_b64: str,
    key_id: str,
    pinned_fingerprint: str,
    trust_root_version: str,
    envelope: AttestationEnvelopeV2,
    audience: str = CANDIDATE_AUDIENCE,
) -> AttestationTrustPolicy:
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise Refused("public key is not strict base64") from exc
    if len(raw) != 32:
        raise Refused("public key must be a raw 32-byte ed25519 key")
    if fingerprint_of(raw) != pinned_fingerprint:
        raise Refused("public key does not match the pinned fingerprint")
    # The root validity window is the envelope's own lifetime: the workflow pins
    # only key id, fingerprint and trust-root version, so root not_before and
    # not_after are not known here. Expiry (`now < expires_at`) and issuance
    # (`issued_at <= now`) are still enforced by the verifier.
    candidate_root = AttestationTrustRootV2(
        public_key_fingerprint=pinned_fingerprint,
        public_key_base64=public_key_b64,
        issuer=CEREMONY_ISSUER,
        key_id=key_id,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
        custody_domain=CUSTODY_DOMAIN,
        algorithm=ALGORITHM,
        trust_root_version=trust_root_version,
        not_before=envelope.issued_at,
        not_after=envelope.expires_at,
    )
    inert_raw = hashlib.sha256(b"inert:" + uuid.uuid4().bytes).digest()
    inert_root = AttestationTrustRootV2(
        public_key_fingerprint=fingerprint_of(inert_raw),
        public_key_base64=base64.b64encode(inert_raw).decode("ascii"),
        issuer="urn:inert",
        key_id="inert",
        purpose="inert",
        custody_domain=_INERT_ROLE_DOMAIN,
        algorithm=ALGORITHM,
        trust_root_version="inert",
        not_before=envelope.issued_at,
        not_after=envelope.expires_at,
    )
    return AttestationTrustPolicy(
        candidate_roots=(candidate_root,),
        installed_roots=(inert_root,),
        candidate_audience=audience,
        installed_audience="inert",
    )


def verify_published_envelope(
    *,
    envelope: AttestationEnvelopeV2,
    receipt: dict[str, Any],
    public_key_b64: str,
    key_id: str,
    pinned_fingerprint: str,
    trust_root_version: str,
    now: datetime,
    audience: str = CANDIDATE_AUDIENCE,
) -> CandidateAttestationSubjectV2:
    """Authenticate the envelope against the pinned identity and the receipt.

    Refuses, each as its own cause: a public key that is not the pinned
    fingerprint; any envelope binding that differs from the pin or the role
    constants (key id, fingerprint, trust-root version, issuer, custody domain,
    audience, purpose, algorithm); an invalid or absent signature; an expired or
    future envelope; and a subject that is not exactly the committed receipt's.
    """
    if envelope.signature == UNSIGNED_PLACEHOLDER:
        raise Refused("the envelope is unsigned")
    if envelope.public_key_fingerprint != pinned_fingerprint:
        raise Refused("envelope fingerprint differs from the pinned fingerprint")
    policy = _pinned_policy(
        public_key_b64=public_key_b64,
        key_id=key_id,
        pinned_fingerprint=pinned_fingerprint,
        trust_root_version=trust_root_version,
        envelope=envelope,
        audience=audience,
    )
    try:
        subject = verify_candidate_attestation(
            candidate=envelope,
            verifier=_Ed25519Verifier(),
            trust_policy=policy,
            now=now,
        )
    except DeploymentFoundationError as exc:
        raise Refused(
            f"envelope refused by the verifier: {getattr(exc, 'code', 'unknown')}"
        ) from exc
    expected = candidate_subject_from_receipt(receipt).canonical_document()
    if dict(envelope.subject_mapping()) != expected:
        raise Refused("envelope subject is not the committed CandidateArtifact.v1")
    return subject


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate JSON object key")
    return dict(pairs)


def _load_envelope(path: str) -> AttestationEnvelopeV2:
    try:
        return AttestationEnvelopeV2.from_mapping(
            json.loads(
                Path(path).read_text(encoding="utf-8"),
                object_pairs_hook=_no_duplicate_keys,
            )
        )
    except (ValueError, DeploymentFoundationError) as exc:
        raise Refused(f"{path}: not a well-formed attestation envelope") from exc


def cmd_render(args: argparse.Namespace) -> int:
    receipt = load_candidate_artifact_receipt(Path(args.receipt))
    scope = args.scope
    require_scope_cleared(scope, str(receipt["facility"]), str(receipt["version"]))
    audience = audience_for_scope(scope, str(receipt["facility"]))
    issued_at = _parse_instant(args.issued_at, "--issued-at")
    expires_at = _parse_instant(args.expires_at, "--expires-at")
    if expires_at <= issued_at:
        raise Refused("--expires-at must follow --issued-at")
    try:
        uuid.UUID(args.trust_root_version)
    except ValueError as exc:
        raise Refused("--trust-root-version must be the enrolment UUID") from exc
    unsigned = render_unsigned_envelope(
        receipt=receipt,
        key_id=args.key_id,
        public_key_fingerprint=args.public_key_fingerprint,
        trust_root_version=args.trust_root_version,
        issued_at=issued_at,
        expires_at=expires_at,
        observation_id=args.observation_id,
    )
    if audience != CANDIDATE_AUDIENCE:
        unsigned = dataclasses.replace(unsigned, audience=audience)
    signed_bytes = unsigned.signed_bytes()
    write_attestation_record(Path(args.out_envelope), unsigned)
    Path(args.out_signed_bytes).write_bytes(signed_bytes)
    print(
        f"rendered unsigned {unsigned.purpose} for {receipt['facility']} "
        f"{receipt['version']}; signed_bytes sha256:"
        f"{hashlib.sha256(signed_bytes).hexdigest()}"
    )
    return 0


def cmd_assemble(args: argparse.Namespace) -> int:
    unsigned = _load_envelope(args.unsigned)
    scope = args.scope
    if scope != SCOPE_CANDIDATE:
        subject = unsigned.subject_mapping()
        facility, version = str(subject.get("package")), str(subject.get("version"))
        require_scope_cleared(scope, facility, version)
        if unsigned.audience != audience_for_scope(scope, facility):
            raise Refused("the envelope audience is not the production audience")
    signature = Path(args.signature_file).read_text(encoding="ascii").strip()
    envelope = assemble_envelope(unsigned, signature)
    write_attestation_record(Path(args.out), envelope)
    print(f"assembled {envelope.purpose} (key {envelope.key_id})")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    receipt = load_candidate_artifact_receipt(Path(args.receipt))
    scope = args.scope
    require_scope_cleared(scope, str(receipt["facility"]), str(receipt["version"]))
    audience = audience_for_scope(scope, str(receipt["facility"]))
    envelope = _load_envelope(args.envelope)
    now = _parse_instant(args.now, "--now") if args.now else datetime.now(UTC)
    subject = verify_published_envelope(
        envelope=envelope,
        receipt=receipt,
        public_key_b64=args.public_key_base64,
        key_id=args.key_id,
        pinned_fingerprint=args.public_key_fingerprint,
        trust_root_version=args.trust_root_version,
        now=now,
        audience=audience,
    )
    if args.out:
        # Published bytes are the package's canonical record of the VERIFIED
        # envelope, never the raw input text.
        write_attestation_record(Path(args.out), envelope)
    print(
        f"verified {envelope.purpose} for {subject.package} {subject.version} "
        f"(fingerprint {envelope.public_key_fingerprint})"
    )
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(
                "## Candidate attestation — verified and published, NOT admitted\n\n"
                "Signed in the operator ceremony; this workflow only verified "
                "and published it. It is verifier input only: no executor "
                "accepts it yet (ADR-0070, 2026-09-08 amendment, decision 5).\n\n"
                "| fact | value |\n|---|---|\n"
                f"| purpose | `{envelope.purpose}` |\n"
                f"| package | `{subject.package}` |\n"
                f"| version | `{subject.version}` |\n"
                f"| fingerprint | `{envelope.public_key_fingerprint}` |\n"
                f"| trust_root_version | `{envelope.trust_root_version}` |\n"
                f"| observation_id | `{envelope.observation_id}` |\n"
                f"| issued_at | `{envelope.issued_at}` |\n"
                f"| expires_at | `{envelope.expires_at}` |\n"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)

    def add_scope(command: argparse.ArgumentParser) -> None:
        command.add_argument("--scope", choices=SCOPES, default=SCOPE_CANDIDATE)

    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="unsigned envelope + signed_bytes")
    render.set_defaults(handler=cmd_render)
    render.add_argument("--receipt", required=True)
    render.add_argument("--key-id", required=True)
    render.add_argument("--public-key-fingerprint", required=True)
    render.add_argument("--trust-root-version", required=True)
    render.add_argument("--issued-at", required=True)
    render.add_argument("--expires-at", required=True)
    render.add_argument("--observation-id", default=None)
    render.add_argument("--out-envelope", required=True)
    render.add_argument("--out-signed-bytes", required=True)
    add_scope(render)

    assemble = sub.add_parser("assemble", help="attach an external signature")
    assemble.set_defaults(handler=cmd_assemble)
    assemble.add_argument("--unsigned", required=True)
    assemble.add_argument("--signature-file", required=True)
    assemble.add_argument("--out", required=True)
    add_scope(assemble)

    verify = sub.add_parser("verify", help="verify against the pinned identity")
    verify.set_defaults(handler=cmd_verify)
    verify.add_argument("--envelope", required=True)
    verify.add_argument("--receipt", required=True)
    verify.add_argument("--public-key-base64", required=True)
    verify.add_argument("--key-id", required=True)
    verify.add_argument("--public-key-fingerprint", required=True)
    verify.add_argument("--trust-root-version", required=True)
    verify.add_argument("--out", default="")
    verify.add_argument("--now", default="")
    verify.add_argument("--summary", default="")
    add_scope(verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
