#!/usr/bin/env python3
"""Render, assemble and verify the installed-host attestation I(D). Holds NO key.

Gate 2 pre-E11 design section 2.2 (steps 3 and 6). The incarnation key lives only
inside an OpenBao transit mount and signs exactly once, off-target, in Michael's
attended ceremony. This tool is the two halves AROUND that signature:

* ``render``   builds the unsigned installed envelope and writes its
  ``signed_bytes`` (Foundation's own canonical bytes) so the ceremony can sign
  exactly those bytes. It prints their sha256. It runs in the candidate venv and
  imports no signing code and reads no key.
* ``assemble`` takes the transit signature, refuses unless it verifies against the
  enrolled public key over exactly the rendered bytes, and writes the signed
  envelope with Foundation's own writer.
* ``verify``   checks the signed envelope with Foundation's pair verifier against
  Control-enrolled roots, as an offline read-back.

Nothing here signs. The only secret-free inputs are public: envelopes, public
keys, trust-root descriptors, instants. The clock is never read: every instant is
an input (the CP database time t0), because store time is the only clock.

The installed subject is derived, never typed: package, version and wheel digest
come from the candidate subject that survived verification, and the candidate
binding digest is Foundation's `candidate_subject_digest`. No digest of the
installed bytes can be supplied.

Refusals carry a code. Foundation's own refusals pass through with Foundation's
code; the codes defined here are named in ``Refused`` call sites.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import dataclasses
import hashlib
import json
import os
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.trusted_host_source import (
    ATTESTATION_SCHEMA,
    INSTALLED_OBSERVATION_PURPOSE,
    AttestationEnvelopeV2,
    AttestationTrustPolicy,
    AttestationTrustRootV2,
    InstalledHostAttestationSubjectV2,
    attestation_envelope_digest,
    candidate_subject_digest,
    verify_attestation_pair,
    verify_candidate_attestation,
)
from lane3_e10_receipt import ReceiptRefused, _raw_public_key
from lane3_host_source_admission import EXPECTED_PACKAGE, Ed25519AttestationVerifier

__all__ = [
    "HOST_ATTESTER_CUSTODY_DOMAIN",
    "MAX_VALIDITY",
    "Refused",
    "assemble_installed",
    "main",
    "render_installed",
    "verify_installed",
]

#: Custody domain of the installed-host role. Fixed: the incarnation key is a
#: host attester and nothing else (design 1.1 E10.5, 2.2 step 3).
HOST_ATTESTER_CUSTODY_DOMAIN: Final = "host_attester"
_ALGORITHM: Final = "ed25519"
#: `expires_at = min(t0 + 2 h, authorization expiry, root not_after)` (design 2.2).
MAX_VALIDITY: Final = timedelta(hours=2)
#: Transit signature prefix for key version 1 (design 2.2 step 5: `key_version=1`).
_TRANSIT_PREFIX: Final = "vault:v1:"
#: Echoed by Foundation's pair verifier but never consumed offline. Control's
#: verification context digest only exists at resolve, after the lock.
OFFLINE_CONTEXT: Final = "offline-readback-not-a-control-context"
_INSTANT: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")
_TEXT: Final = re.compile(r"[\x21-\x7e]{1,256}")
_MAX_INPUT: Final = 1024 * 1024
_ROOT_FIELDS: Final = frozenset(
    {
        "public_key_fingerprint",
        "public_key_base64",
        "issuer",
        "key_id",
        "purpose",
        "custody_domain",
        "algorithm",
        "trust_root_version",
        "not_before",
        "not_after",
        "revoked",
    }
)
_ENVELOPE_FIELDS: Final = frozenset(
    field.name for field in dataclasses.fields(AttestationEnvelopeV2) if field.init
)


class Refused(Exception):
    """A refusal with a stable code. Never carries a secret or a path's content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ── inputs ──────────────────────────────────────────────────────────────────


def _read(path: str | Path, code: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            data = handle.read(_MAX_INPUT + 1)
    except OSError:
        raise Refused(code) from None
    if not data or len(data) > _MAX_INPUT:
        raise Refused(code)
    return data


def _json_object(path: str | Path, code: str) -> dict[str, Any]:
    try:
        document = json.loads(_read(path, code))
    except ValueError:
        raise Refused(code) from None
    if not isinstance(document, dict):
        raise Refused(code)
    return document


def _instant(value: object, code: str) -> datetime:
    """`YYYY-MM-DDTHH:MM:SSZ` only: the form Foundation's envelope carries."""
    if not isinstance(value, str) or not _INSTANT.fullmatch(value):
        raise Refused(code)
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)
    except ValueError:
        raise Refused(code) from None


def _format(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not _TEXT.fullmatch(value):
        raise Refused(code)
    return value


def load_envelope(path: str | Path, *, code: str) -> AttestationEnvelopeV2:
    document = _json_object(path, code)
    try:
        return AttestationEnvelopeV2.from_mapping(document)
    except SpecError:
        raise Refused(code) from None


def load_root(path: str | Path, *, code: str) -> AttestationTrustRootV2:
    """A Control-resolved root descriptor. Closed member set, `revoked` optional."""
    document = _json_object(path, code)
    keys = set(document)
    if not (_ROOT_FIELDS - {"revoked"}) <= keys <= _ROOT_FIELDS:
        raise Refused(code)
    try:
        return AttestationTrustRootV2(**document)
    except (SpecError, TypeError):
        raise Refused(code) from None


def _incarnation(public_key_b64url: str, fingerprint: str) -> tuple[bytes, str]:
    """The raw key and its fingerprint, with the receipt's fingerprint checked."""
    try:
        raw = _raw_public_key(public_key_b64url)
    except ReceiptRefused:
        raise Refused("incarnation-public-key-invalid") from None
    derived = "sha256:" + hashlib.sha256(raw).hexdigest()
    if derived != fingerprint:
        raise Refused("incarnation-fingerprint-does-not-identify-the-key")
    return raw, derived


def _canonical_uuid(value: str) -> str:
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        raise Refused("enrolment-uuid-invalid") from None
    if canonical != value:
        raise Refused("enrolment-uuid-invalid")
    return canonical


def _foundation(refusal: Exception) -> Exception:
    """Foundation's refusals keep Foundation's code; anything else is a bug."""
    code = getattr(refusal, "code", None)
    return Refused(code) if isinstance(code, str) else refusal


# ── render ──────────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class Rendered:
    signed_bytes: bytes
    signed_bytes_sha256: str
    issued_at: str
    expires_at: str
    candidate_envelope_digest: str


def render_installed(
    *,
    candidate: AttestationEnvelopeV2,
    candidate_root: AttestationTrustRootV2,
    candidate_audience: str,
    host_id: str,
    observation_id: str,
    enrolment_uuid: str,
    key_id: str,
    issuer: str,
    incarnation_public_key_b64url: str,
    incarnation_fingerprint: str,
    t0: str,
    authorization_expires_at: str,
    root_not_after: str,
) -> Rendered:
    """Build I(D) unsigned and return the exact bytes the ceremony will sign.

    The candidate is verified here, with Foundation's `verify_candidate_attestation`
    against the Control-enrolled candidate root at `t0`. The installed role needs a
    root for Foundation's policy to construct, so one is built from the receipt's
    own key; the policy's own distinctness checks then also refuse an incarnation
    key that equals, or shares a custody domain with, the candidate root.
    """
    host_id = _text(host_id, "host-id-invalid")
    observation_id = _text(observation_id, "observation-id-invalid")
    key_id = _text(key_id, "key-id-invalid")
    issuer = _text(issuer, "issuer-invalid")
    candidate_audience = _text(candidate_audience, "candidate-audience-invalid")
    trust_root_version = _canonical_uuid(enrolment_uuid)
    raw_key, fingerprint = _incarnation(
        incarnation_public_key_b64url, incarnation_fingerprint
    )
    issued = _instant(t0, "t0-invalid")
    authorization_expiry = _instant(
        authorization_expires_at, "authorization-expiry-invalid"
    )
    root_expiry = _instant(root_not_after, "root-not-after-invalid")
    expires = min(issued + MAX_VALIDITY, authorization_expiry, root_expiry)
    if expires <= issued:
        raise Refused("installed-expiry-not-after-issue")

    try:
        installed_root = AttestationTrustRootV2(
            public_key_fingerprint=fingerprint,
            public_key_base64=base64.b64encode(raw_key).decode("ascii"),
            issuer=issuer,
            key_id=key_id,
            purpose=INSTALLED_OBSERVATION_PURPOSE,
            custody_domain=HOST_ATTESTER_CUSTODY_DOMAIN,
            algorithm=_ALGORITHM,
            trust_root_version=trust_root_version,
            not_before=_format(issued),
            not_after=_format(root_expiry),
        )
        policy = AttestationTrustPolicy(
            candidate_roots=(candidate_root,),
            installed_roots=(installed_root,),
            candidate_audience=candidate_audience,
            installed_audience=host_id,
        )
        subject = verify_candidate_attestation(
            candidate=candidate,
            verifier=Ed25519AttestationVerifier(),
            trust_policy=policy,
            now=issued,
        )
    except Exception as refusal:  # Foundation's SpecError / PreconditionFailed
        raise _foundation(refusal) from None
    if subject.package != EXPECTED_PACKAGE:
        raise Refused("candidate-package-unexpected")

    installed_subject = InstalledHostAttestationSubjectV2(
        host_id,
        subject.package,
        subject.version,
        subject.wheel_sha256,
        candidate_subject_digest(subject),
    )
    # The signature is a placeholder Foundation's constructor needs; it is not in
    # `signed_bytes()` and the envelope itself is never written.
    envelope = AttestationEnvelopeV2(
        ATTESTATION_SCHEMA,
        INSTALLED_OBSERVATION_PURPOSE,
        issuer,
        key_id,
        _ALGORITHM,
        fingerprint,
        HOST_ATTESTER_CUSTODY_DOMAIN,
        trust_root_version,
        _format(issued),
        _format(expires),
        host_id,
        observation_id,
        installed_subject.canonical_document(),
        "unsigned",
    )
    signed = envelope.signed_bytes()
    return Rendered(
        signed_bytes=signed,
        signed_bytes_sha256=hashlib.sha256(signed).hexdigest(),
        issued_at=_format(issued),
        expires_at=_format(expires),
        candidate_envelope_digest=str(attestation_envelope_digest(candidate)),
    )


# ── assemble ────────────────────────────────────────────────────────────────


def _signature_b64(value: str) -> str:
    """Standard padded base64 of exactly 64 bytes, the form Foundation verifies.

    Accepts a bare signature or OpenBao transit's `vault:v1:` form (key version 1,
    design 2.2 step 5). Any other version prefix is refused, as is a non-canonical
    spelling of the same bytes.
    """
    if not isinstance(value, str) or value != value.strip():
        raise Refused("installed-signature-malformed")
    body = value.removeprefix(_TRANSIT_PREFIX)
    if body.startswith("vault:") or ":" in body:
        raise Refused("installed-signature-key-version-unexpected")
    try:
        raw = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        raise Refused("installed-signature-malformed") from None
    if len(raw) != 64 or base64.b64encode(raw).decode("ascii") != body:
        raise Refused("installed-signature-malformed")
    return body


def assemble_installed(
    *,
    signed_bytes: bytes,
    signature: str,
    incarnation_public_key_b64url: str,
    expect_signed_bytes_sha256: str,
) -> AttestationEnvelopeV2:
    """Attach the transit signature to the rendered bytes, after verifying it.

    Refuses unless the file is exactly the bytes `render` printed the digest of,
    those bytes are Foundation's canonical form of an installed-host envelope, and
    the signature verifies under the enrolled key. A bad signature never becomes a
    file.
    """
    if not _SHA256_HEX.fullmatch(expect_signed_bytes_sha256):
        raise Refused("signed-bytes-digest-invalid")
    if hashlib.sha256(signed_bytes).hexdigest() != expect_signed_bytes_sha256:
        raise Refused("signed-bytes-digest-mismatch")
    try:
        document = json.loads(signed_bytes)
    except ValueError:
        raise Refused("signed-bytes-malformed") from None
    if not isinstance(document, dict) or set(document) != _ENVELOPE_FIELDS - {
        "signature"
    }:
        raise Refused("signed-bytes-malformed")
    signature_b64 = _signature_b64(signature)
    try:
        envelope = AttestationEnvelopeV2.from_mapping(
            {**document, "signature": signature_b64}
        )
    except SpecError:
        raise Refused("signed-bytes-malformed") from None
    if envelope.signed_bytes() != signed_bytes:
        raise Refused("signed-bytes-not-canonical")
    if (
        envelope.purpose != INSTALLED_OBSERVATION_PURPOSE
        or envelope.custody_domain != HOST_ATTESTER_CUSTODY_DOMAIN
        or envelope.algorithm != _ALGORITHM
    ):
        raise Refused("signed-bytes-not-an-installed-attestation")
    try:
        raw_key = _raw_public_key(incarnation_public_key_b64url)
    except ReceiptRefused:
        raise Refused("incarnation-public-key-invalid") from None
    if (
        "sha256:" + hashlib.sha256(raw_key).hexdigest()
        != envelope.public_key_fingerprint
    ):
        raise Refused("signed-bytes-key-differs-from-enrolled-key")
    if not Ed25519AttestationVerifier().verify(
        public_key=raw_key,
        algorithm=_ALGORITHM,
        message=signed_bytes,
        signature=signature_b64,
    ):
        raise Refused("installed-signature-invalid")
    return envelope


# ── verify ──────────────────────────────────────────────────────────────────


def verify_installed(
    *,
    installed: AttestationEnvelopeV2,
    candidate: AttestationEnvelopeV2,
    candidate_root: AttestationTrustRootV2,
    installed_root: AttestationTrustRootV2,
    candidate_audience: str,
    now: str,
) -> dict[str, str]:
    """Pair-verify with Foundation: both signatures, bindings, audience, package.

    `now` is the CP database time and is required: there is no wall-clock default.
    The expected host and dispatch are read from the installed envelope's own
    audience and observation id and then CHECKED by Foundation against the root
    policy, so this proves internal agreement, not Control's choice of either.
    """
    instant = _instant(now, "now-invalid")
    try:
        result = verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=Ed25519AttestationVerifier(),
            trust_policy=AttestationTrustPolicy(
                candidate_roots=(candidate_root,),
                installed_roots=(installed_root,),
                candidate_audience=_text(
                    candidate_audience, "candidate-audience-invalid"
                ),
                installed_audience=installed.audience,
            ),
            expected_host_identity=installed.audience,
            expected_observation_id=installed.observation_id,
            expected_package=EXPECTED_PACKAGE,
            verification_context_digest=OFFLINE_CONTEXT,
            now=instant,
        )
    except Exception as refusal:
        raise _foundation(refusal) from None
    return {
        "installed_attestation_digest": result.installed_attestation_envelope_digest,
        "candidate_attestation_digest": result.candidate_attestation_envelope_digest,
        "observation_id": installed.observation_id,
        "host_id": installed.audience,
        "installed_fingerprint": installed.public_key_fingerprint,
        "expires_at": installed.expires_at,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────


def _write_new(path: str, data: bytes) -> None:
    """Create-only, 0600. A file already there is refused, never overwritten."""
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
    except OSError:
        raise Refused("output-not-creatable") from None
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _cmd_render(args: argparse.Namespace) -> dict[str, str]:
    rendered = render_installed(
        candidate=load_envelope(args.candidate, code="candidate-unreadable"),
        candidate_root=load_root(args.candidate_root, code="candidate-root-unreadable"),
        candidate_audience=args.candidate_audience,
        host_id=args.host_id,
        observation_id=args.observation_id,
        enrolment_uuid=args.enrolment_uuid,
        key_id=args.key_id,
        issuer=args.issuer,
        incarnation_public_key_b64url=args.incarnation_public_key,
        incarnation_fingerprint=args.incarnation_fingerprint,
        t0=args.t0,
        authorization_expires_at=args.authorization_expires_at,
        root_not_after=args.root_not_after,
    )
    _write_new(args.out_signed_bytes, rendered.signed_bytes)
    return {
        "signed_bytes_sha256": rendered.signed_bytes_sha256,
        "issued_at": rendered.issued_at,
        "expires_at": rendered.expires_at,
        "candidate_attestation_digest": rendered.candidate_envelope_digest,
    }


def _cmd_assemble(args: argparse.Namespace) -> dict[str, str]:
    from dotmac_deployment_foundation.attestation_store import (
        write_attestation_record,
    )

    envelope = assemble_installed(
        signed_bytes=_read(args.signed_bytes, "signed-bytes-unreadable"),
        signature=args.signature,
        incarnation_public_key_b64url=args.incarnation_public_key,
        expect_signed_bytes_sha256=args.expect_signed_bytes_sha256,
    )
    try:
        write_attestation_record(Path(args.out), envelope)
    except OSError:
        raise Refused("output-not-creatable") from None
    return {"installed_attestation_digest": str(attestation_envelope_digest(envelope))}


def _cmd_verify(args: argparse.Namespace) -> dict[str, str]:
    return verify_installed(
        installed=load_envelope(args.installed, code="installed-unreadable"),
        candidate=load_envelope(args.candidate, code="candidate-unreadable"),
        candidate_root=load_root(args.candidate_root, code="candidate-root-unreadable"),
        installed_root=load_root(args.installed_root, code="installed-root-unreadable"),
        candidate_audience=args.candidate_audience,
        now=args.now,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="build I(D) unsigned; write signed_bytes")
    render.set_defaults(handler=_cmd_render)
    for name in (
        "candidate",
        "candidate-root",
        "candidate-audience",
        "host-id",
        "observation-id",
        "enrolment-uuid",
        "key-id",
        "issuer",
        "incarnation-public-key",
        "incarnation-fingerprint",
        "t0",
        "authorization-expires-at",
        "root-not-after",
        "out-signed-bytes",
    ):
        render.add_argument(f"--{name}", required=True)

    assemble = sub.add_parser("assemble", help="attach the transit signature")
    assemble.set_defaults(handler=_cmd_assemble)
    for name in (
        "signed-bytes",
        "signature",
        "incarnation-public-key",
        "expect-signed-bytes-sha256",
        "out",
    ):
        assemble.add_argument(f"--{name}", required=True)

    verify = sub.add_parser("verify", help="pair-verify with Foundation")
    verify.set_defaults(handler=_cmd_verify)
    for name in (
        "installed",
        "candidate",
        "candidate-root",
        "installed-root",
        "candidate-audience",
        "now",
    ):
        verify.add_argument(f"--{name}", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: Mapping[str, str] = args.handler(args)
    except Refused as refusal:
        print(f"refused: {refusal.code}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
