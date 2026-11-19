#!/usr/bin/env python3
"""Render, assemble and verify the installed-host attestation I(D). Holds NO key.

Gate 2 pre-E11 design section 2.2 (steps 3 and 6). The incarnation key lives only
inside an OpenBao transit mount and signs exactly once, off-target, in Michael's
attended ceremony. This tool is the two halves AROUND that signature:

* ``render``   builds the unsigned installed envelope and writes its
  ``signed_bytes`` (Foundation's own canonical bytes) so the ceremony can sign
  exactly those bytes. It prints their sha256. It imports no signing code and
  reads no key.
* ``assemble`` takes the transit signature, refuses unless it verifies against the
  enrolled public key over exactly the rendered bytes, and writes the signed
  envelope with Foundation's own writer.
* ``verify``   checks the signed envelope with Foundation's pair verifier against
  Control-enrolled roots, as an offline read-back.

Nothing here signs. The only inputs are public: envelopes, trust-root
descriptors, the E10 receipt, the controller-side dispatch materials, instants.
The clock is never read: every instant is an input (the CP database time t0),
because store time is the only clock.

## Typed, closed inputs

The single-use incarnation key signs whatever `render` produced, so a typo caught
only at E16 would spend the host. Hence `render` takes no loose host, dispatch,
key, issuer or expiry flag. It reads:

* Control's installed root (closed member set, ``revoked`` REQUIRED, custody
  domain pinned to ``host_attester``, purpose pinned, valid at t0, not revoked);
* the E10 receipt as it exists at step 3 (the receipt row minus the destruction
  stamp, which does not exist yet; closed member set), cross-checked against the
  root: fingerprint, enrolment UUID and key bytes must all agree;
* ``dispatch-materials.json`` (closed member set): the dispatch id is
  ``attempt_id``, the dispatch envelope must name the same id, and the
  authorization's ``expires_at`` bounds the attestation.

The candidate root is pinned the same way (``candidate_release_signer``).

## Self-contained

Design 2.1 runs this under ``python -E -P`` (or ``-I``) in the candidate venv, so
the script directory is NOT on ``sys.path``. This module therefore imports only
the standard library, ``cryptography`` and ``dotmac_deployment_foundation``; the
few rules it shares with neighbouring scripts are copied below, each naming its
source, and ``tests/unit/test_lane3_installed_attestation.py`` asserts the copies
agree with the originals and that the script runs under ``python -I -P``.

Refusals carry a code. Foundation's own refusals pass through with Foundation's
code.
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

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.trusted_host_source import (
    ATTESTATION_SCHEMA,
    CANDIDATE_ATTESTATION_PURPOSE,
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

__all__ = [
    "CANDIDATE_CUSTODY_DOMAIN",
    "HOST_ATTESTER_CUSTODY_DOMAIN",
    "MAX_VALIDITY",
    "Refused",
    "assemble_installed",
    "load_dispatch_materials",
    "load_receipt",
    "load_root",
    "main",
    "render_installed",
    "verify_installed",
]

#: Custody domains, pinned (design 1.1 E10.5, 2.5, 2.2 step 3).
HOST_ATTESTER_CUSTODY_DOMAIN: Final = "host_attester"
CANDIDATE_CUSTODY_DOMAIN: Final = "candidate_release_signer"
_ALGORITHM: Final = "ed25519"
#: `expires_at = min(t0 + 2 h, authorization expiry, root not_after)` (design 2.2).
MAX_VALIDITY: Final = timedelta(hours=2)
#: Transit signature prefix for key version 1 (design 2.2 step 5: `key_version=1`).
_TRANSIT_PREFIX: Final = "vault:v1:"
#: Echoed by Foundation's pair verifier but never consumed offline. Control's
#: verification context digest only exists at resolve, after the lock.
OFFLINE_CONTEXT: Final = "offline-readback-not-a-control-context"
#: COPY of `lane3_host_source_admission.EXPECTED_PACKAGE`.
EXPECTED_PACKAGE: Final = "dotmac-deployment-foundation"

# COPIES of the field rules in `lane3_e10_receipt` (`_ALIAS`, `_VM_SLOT`,
# `_MACHINE_ID`, `_OPENSSH_FINGERPRINT`, `_TOKEN`). The test file asserts each
# pattern equals the original's.
_ALIAS: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")
_VM_SLOT: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}/[1-9][0-9]{0,8}")
_MACHINE_ID: Final = re.compile(r"[0-9a-f]{32}")
_OPENSSH_FINGERPRINT: Final = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
_TOKEN: Final = re.compile(r"[\x21-\x7e]{1,256}")
_INSTANT: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")
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
#: The E10 receipt row at step 3: `E10ReceiptRef` without
#: `incarnation_key_destroyed_at`, which is stamped only at step 8.
_RECEIPT_FIELDS: Final = frozenset(
    {
        "vm_alias",
        "host_id",
        "machine_id",
        "ssh_host_key_fingerprint",
        "incarnation_public_key_b64url",
        "incarnation_fingerprint",
        "enrolment_uuid",
        "receipt_ref",
        "vm_slot",
    }
)
_DISPATCH_FIELDS: Final = frozenset(
    {"attempt_id", "authorization_envelope", "dispatch_envelope"}
)
_ENVELOPE_FIELDS: Final = frozenset(
    field.name for field in dataclasses.fields(AttestationEnvelopeV2) if field.init
)


class Refused(Exception):
    """A refusal with a stable code. Never carries a secret or a path's content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ── copied rules ────────────────────────────────────────────────────────────


def raw_public_key(value: object) -> bytes:
    """COPY of `lane3_e10_receipt._raw_public_key` (same rule, own refusal).

    A canonical unpadded base64url 32-byte key, the only spelling Control reads.
    """
    if not isinstance(value, str) or not value or "=" in value:
        raise Refused("incarnation-public-key-invalid")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        raise Refused("incarnation-public-key-invalid") from None
    if (
        len(raw) != 32
        or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value
    ):
        raise Refused("incarnation-public-key-invalid")
    return raw


class Ed25519Verifier:
    """COPY of `lane3_host_source_admission.Ed25519AttestationVerifier`.

    Verifies Ed25519 attestation signatures and nothing else; `False` for every
    malformed key, signature or algorithm.
    """

    def verify(
        self, *, public_key: bytes, algorithm: str, message: bytes, signature: str
    ) -> bool:
        if algorithm != _ALGORITHM or len(public_key) != 32:
            return False
        try:
            raw_signature = base64.b64decode(signature, validate=True)
        except (binascii.Error, ValueError):
            return False
        if len(raw_signature) != 64:
            return False
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                raw_signature, message
            )
        except InvalidSignature:
            return False
        return True


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


def _token(value: object, code: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise Refused(code)
    return value


def _canonical_uuid(value: object, code: str) -> str:
    if not isinstance(value, str):
        raise Refused(code)
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        raise Refused(code) from None
    if canonical != value:
        raise Refused(code)
    return value


def load_envelope(path: str | Path, *, code: str) -> AttestationEnvelopeV2:
    document = _json_object(path, code)
    try:
        return AttestationEnvelopeV2.from_mapping(document)
    except SpecError:
        raise Refused(code) from None


_ROLE_PINS: Final = {
    "installed": (INSTALLED_OBSERVATION_PURPOSE, HOST_ATTESTER_CUSTODY_DOMAIN),
    "candidate": (CANDIDATE_ATTESTATION_PURPOSE, CANDIDATE_CUSTODY_DOMAIN),
}


def root_from_document(
    document: Mapping[str, Any], *, role: str, code: str
) -> AttestationTrustRootV2:
    """A Control-resolved root. Closed member set: `revoked` is REQUIRED, so an
    omission is a refusal rather than a silent `False`. Purpose, custody domain
    and algorithm are pinned to the role."""
    if set(document) != _ROOT_FIELDS or not isinstance(document["revoked"], bool):
        raise Refused(code)
    try:
        root = AttestationTrustRootV2(**document)
    except (SpecError, TypeError):
        raise Refused(code) from None
    purpose, custody = _ROLE_PINS[role]
    if (
        root.purpose != purpose
        or root.custody_domain != custody
        or root.algorithm != _ALGORITHM
    ):
        raise Refused(f"{role}-root-role-mismatch")
    return root


def load_root(path: str | Path, *, role: str) -> AttestationTrustRootV2:
    return root_from_document(
        _json_object(path, f"{role}-root-unreadable"),
        role=role,
        code=f"{role}-root-malformed",
    )


def load_receipt(path: str | Path) -> dict[str, Any]:
    """The E10 receipt row at step 3. Closed member set, every field checked
    against the E10 receipt's own rules (copied above)."""
    document = _json_object(path, "receipt-unreadable")
    if set(document) != _RECEIPT_FIELDS:
        raise Refused("receipt-malformed")
    for name, pattern in (
        ("vm_alias", _ALIAS),
        ("machine_id", _MACHINE_ID),
        ("ssh_host_key_fingerprint", _OPENSSH_FINGERPRINT),
    ):
        value = document[name]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise Refused("receipt-malformed")
    slot = document["vm_slot"]
    if slot is not None and (not isinstance(slot, str) or not _VM_SLOT.fullmatch(slot)):
        raise Refused("receipt-malformed")
    _token(document["host_id"], "receipt-malformed")
    _token(document["receipt_ref"], "receipt-malformed")
    _canonical_uuid(document["enrolment_uuid"], "receipt-malformed")
    raw = raw_public_key(document["incarnation_public_key_b64url"])
    if (
        document["incarnation_fingerprint"]
        != "sha256:" + hashlib.sha256(raw).hexdigest()
    ):
        raise Refused("incarnation-fingerprint-does-not-identify-the-key")
    return document


def load_dispatch_materials(path: str | Path) -> tuple[str, datetime]:
    """`(attempt_id, authorization expires_at)` from `dispatch-materials.json`.

    Closed member set. The dispatch envelope must name the same id as
    `attempt_id`: the installed attestation's observation id is that dispatch.
    The expiry is floored to the second.
    """
    document = _json_object(path, "dispatch-materials-unreadable")
    if set(document) != _DISPATCH_FIELDS:
        raise Refused("dispatch-materials-malformed")
    attempt_id = _canonical_uuid(document["attempt_id"], "attempt-id-invalid")
    authorization, dispatch = (
        document["authorization_envelope"],
        document["dispatch_envelope"],
    )
    if not isinstance(authorization, dict) or not isinstance(dispatch, dict):
        raise Refused("dispatch-materials-malformed")
    auth_statement, dispatch_statement = (
        authorization.get("statement"),
        dispatch.get("statement"),
    )
    if not isinstance(auth_statement, dict) or not isinstance(dispatch_statement, dict):
        raise Refused("dispatch-materials-malformed")
    if dispatch_statement.get("dispatch_id") != attempt_id:
        raise Refused("dispatch-envelope-names-another-dispatch")
    expires = auth_statement.get("expires_at")
    if not isinstance(expires, str) or not expires.endswith("Z"):
        raise Refused("authorization-expiry-invalid")
    try:
        parsed = datetime.fromisoformat(expires[:-1] + "+00:00")
    except ValueError:
        raise Refused("authorization-expiry-invalid") from None
    return attempt_id, parsed.astimezone(UTC).replace(microsecond=0)


# ── render ──────────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class Rendered:
    signed_bytes: bytes
    signed_bytes_sha256: str
    issued_at: str
    expires_at: str
    candidate_envelope_digest: str


def _foundation(refusal: Exception) -> Exception:
    """Foundation's refusals keep Foundation's code; anything else is a bug."""
    code = getattr(refusal, "code", None)
    return Refused(code) if isinstance(code, str) else refusal


def _agree(root: AttestationTrustRootV2, receipt: Mapping[str, Any]) -> None:
    """The receipt and Control's root must describe one enrolment."""
    raw = raw_public_key(receipt["incarnation_public_key_b64url"])
    try:
        root_key = base64.b64decode(root.public_key_base64, validate=True)
    except (binascii.Error, ValueError):
        raise Refused("installed-root-malformed") from None
    if (
        root_key != raw
        or root.public_key_fingerprint != receipt["incarnation_fingerprint"]
    ):
        raise Refused("receipt-root-key-mismatch")
    if root.trust_root_version != receipt["enrolment_uuid"]:
        raise Refused("receipt-root-enrolment-mismatch")


def render_installed(
    *,
    candidate: AttestationEnvelopeV2,
    candidate_root: AttestationTrustRootV2,
    candidate_audience: str,
    installed_root: AttestationTrustRootV2,
    receipt: Mapping[str, Any],
    dispatch_materials: tuple[str, datetime],
    t0: str,
) -> Rendered:
    """Build I(D) unsigned and return the exact bytes the ceremony will sign.

    The candidate is verified with Foundation's `verify_candidate_attestation`
    against the Control-enrolled candidate root at `t0`, under a policy that also
    holds the REAL installed root, so Foundation's own distinctness checks cover
    both roots.
    """
    candidate_audience = _token(candidate_audience, "candidate-audience-invalid")
    issued = _instant(t0, "t0-invalid")
    observation_id, authorization_expiry = dispatch_materials
    host_id = _token(receipt["host_id"], "host-id-invalid")
    if installed_root.revoked:
        raise Refused("installed-root-revoked")
    _agree(installed_root, receipt)
    root_start = _instant_of(installed_root.not_before)
    root_end = _instant_of(installed_root.not_after)
    if not root_start <= issued < root_end:
        raise Refused("installed-root-not-valid-at-t0")
    expires = min(issued + MAX_VALIDITY, authorization_expiry, root_end)
    if expires <= issued:
        raise Refused("installed-expiry-not-after-issue")

    try:
        policy = AttestationTrustPolicy(
            candidate_roots=(candidate_root,),
            installed_roots=(installed_root,),
            candidate_audience=candidate_audience,
            installed_audience=host_id,
        )
        subject = verify_candidate_attestation(
            candidate=candidate,
            verifier=Ed25519Verifier(),
            trust_policy=policy,
            now=issued,
        )
    except Exception as refusal:  # Foundation's SpecError / PreconditionFailed
        raise _foundation(refusal) from None
    if subject.package != EXPECTED_PACKAGE:
        raise Refused("candidate-package-unexpected")

    try:
        installed_subject = InstalledHostAttestationSubjectV2(
            host_id,
            subject.package,
            subject.version,
            subject.wheel_sha256,
            candidate_subject_digest(subject),
        )
        # The signature is a placeholder Foundation's constructor needs; it is
        # not in `signed_bytes()` and the envelope itself is never written.
        envelope = AttestationEnvelopeV2(
            ATTESTATION_SCHEMA,
            INSTALLED_OBSERVATION_PURPOSE,
            installed_root.issuer,
            installed_root.key_id,
            _ALGORITHM,
            installed_root.public_key_fingerprint,
            HOST_ATTESTER_CUSTODY_DOMAIN,
            installed_root.trust_root_version,
            _format(issued),
            _format(expires),
            host_id,
            observation_id,
            installed_subject.canonical_document(),
            "unsigned",
        )
    except Exception as refusal:
        raise _foundation(refusal) from None
    signed = envelope.signed_bytes()
    return Rendered(
        signed_bytes=signed,
        signed_bytes_sha256=hashlib.sha256(signed).hexdigest(),
        issued_at=_format(issued),
        expires_at=_format(expires),
        candidate_envelope_digest=str(attestation_envelope_digest(candidate)),
    )


def _instant_of(value: str) -> datetime:
    """A root bound. Foundation already required canonical UTC RFC3339."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        raise Refused("installed-root-malformed") from None


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
    installed_root: AttestationTrustRootV2,
    expect_signed_bytes_sha256: str,
) -> AttestationEnvelopeV2:
    """Attach the transit signature to the rendered bytes, after verifying it.

    Refuses unless the file is exactly the bytes `render` printed the digest of,
    those bytes are Foundation's canonical form of an installed-host envelope
    bound to the enrolled root, and the signature verifies under that root's key.
    A bad signature never becomes a file.
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
    if envelope.public_key_fingerprint != installed_root.public_key_fingerprint:
        raise Refused("signed-bytes-key-differs-from-enrolled-key")
    if (
        envelope.issuer,
        envelope.key_id,
        envelope.trust_root_version,
    ) != (
        installed_root.issuer,
        installed_root.key_id,
        installed_root.trust_root_version,
    ):
        raise Refused("signed-bytes-differ-from-installed-root")
    if not Ed25519Verifier().verify(
        public_key=base64.b64decode(installed_root.public_key_base64, validate=True),
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
    host_id: str,
    dispatch_id: str,
    now: str,
) -> dict[str, str]:
    """Pair-verify with Foundation: both signatures, bindings, audience, package.

    `host_id` and `dispatch_id` are REQUIRED and compared with the envelope, so a
    read-back proves agreement with what the ceremony meant, not merely internal
    consistency. `now` is the CP database time; there is no wall-clock default.
    """
    instant = _instant(now, "now-invalid")
    host_id = _token(host_id, "host-id-invalid")
    dispatch_id = _token(dispatch_id, "dispatch-id-invalid")
    if installed.audience != host_id:
        raise Refused("host-id-mismatch")
    if installed.observation_id != dispatch_id:
        raise Refused("dispatch-id-mismatch")
    try:
        result = verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=Ed25519Verifier(),
            trust_policy=AttestationTrustPolicy(
                candidate_roots=(candidate_root,),
                installed_roots=(installed_root,),
                candidate_audience=_token(
                    candidate_audience, "candidate-audience-invalid"
                ),
                installed_audience=host_id,
            ),
            expected_host_identity=host_id,
            expected_observation_id=dispatch_id,
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
        candidate_root=load_root(args.candidate_root, role="candidate"),
        candidate_audience=args.candidate_audience,
        installed_root=load_root(args.installed_root, role="installed"),
        receipt=load_receipt(args.receipt),
        dispatch_materials=load_dispatch_materials(args.dispatch_materials),
        t0=args.t0,
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
        installed_root=load_root(args.installed_root, role="installed"),
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
        candidate_root=load_root(args.candidate_root, role="candidate"),
        installed_root=load_root(args.installed_root, role="installed"),
        candidate_audience=args.candidate_audience,
        host_id=args.host_id,
        dispatch_id=args.dispatch_id,
        now=args.now,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler: Any, help_text: str, *required: str) -> None:
        command = sub.add_parser(name, help=help_text)
        command.set_defaults(handler=handler)
        for option in required:
            command.add_argument(f"--{option}", required=True)

    add(
        "render",
        _cmd_render,
        "build I(D) unsigned; write signed_bytes",
        "candidate",
        "candidate-root",
        "candidate-audience",
        "installed-root",
        "receipt",
        "dispatch-materials",
        "t0",
        "out-signed-bytes",
    )
    add(
        "assemble",
        _cmd_assemble,
        "attach the transit signature",
        "signed-bytes",
        "signature",
        "installed-root",
        "expect-signed-bytes-sha256",
        "out",
    )
    add(
        "verify",
        _cmd_verify,
        "pair-verify with Foundation",
        "installed",
        "candidate",
        "candidate-root",
        "installed-root",
        "candidate-audience",
        "host-id",
        "dispatch-id",
        "now",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: Mapping[str, str] = args.handler(args)
    except Refused as refusal:
        print(f"refused: {refusal.code}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
