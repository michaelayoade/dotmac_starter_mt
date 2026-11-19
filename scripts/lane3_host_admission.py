#!/usr/bin/env python3
"""The producer of the target-signed ``host-admission.json`` (design section 4).

Subcommands:

* ``key-init``  (target, E10.4) generate the presentation key, root-only 0400, and
  print its PUBLIC half. It refuses to replace a key.
* ``pop``       (target, E10.4) answer Control's possession challenge with it.
* ``assemble``  (launcher) build the unsigned document ``{attempt_id, candidate,
  installed}`` from the controller-side ``dispatch-materials.json`` and the two
  attestation envelopes, checking each against an expected digest.
* ``present``   (target, before ``start``) sign Control's
  ``HostAdmissionPresentationStatementV1`` and write ``host-admission.json``
  create-only, 0600, atomically.
* ``present --refresh`` (target, between ``ready`` and ``proceed``) re-sign with a
  later store time and atomically replace the file, keeping the presentation's age
  at admission to seconds.

Widening W9 (design section 4): this key IS reachable from Foundation's execution
path, since both run as root. That is inherent to ADR-0073's ``TargetCredential``.
It cannot mint an installed attestation; on its own it can only re-present the one
genuine, single-use I(D). It is NOT the incarnation key, which never touches the
target.

Wire contract (Control ``host_admission.py`` 0.1.0a17, not importable from here):
the statement mapping is schema, version, purpose, audience, presentation_id,
key_id, dispatch_id, the two envelope digests, issued_at, expires_at; its bytes are
``json.dumps(..., sort_keys=True, separators=(",", ":"))`` in UTF-8; the signature
is Ed25519 over those bytes, unpadded base64url. The document's closed member set
is ``attempt_id``, ``presentation``, ``candidate``, ``installed`` (CP
``host_admission_source``). ``tests/unit/test_lane3_host_admission.py`` pins both.

The clock is never read. ``issued_at`` is the CP store time the launcher passes,
minus two seconds, and ``expires_at`` is 300 seconds later.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.trusted_host_source import (
    CANDIDATE_ATTESTATION_PURPOSE,
    INSTALLED_OBSERVATION_PURPOSE,
    AttestationEnvelopeV2,
    CandidateAttestationSubjectV2,
    InstalledHostAttestationSubjectV2,
    attestation_envelope_digest,
    candidate_subject_digest,
)

__all__ = [
    "BACKDATE",
    "LIFETIME",
    "PRESENTATION_PURPOSE",
    "Refused",
    "assemble_unsigned",
    "build_presentation",
    "key_init",
    "main",
    "pop_response",
    "present",
]

PRESENTATION_SCHEMA: Final = "dotmac.control.host-admission-presentation"
PRESENTATION_VERSION: Final = 1
PRESENTATION_PURPOSE: Final = "dotmac.control.host-admission-presentation.v1"
PRESENTATION_AUDIENCE: Final = "dotmac.deployment-control"
#: `issued_at` = store time minus this; `expires_at` = `issued_at` + LIFETIME.
BACKDATE: Final = timedelta(seconds=2)
LIFETIME: Final = timedelta(seconds=300)
_DOCUMENT_KEYS: Final = frozenset(
    {"attempt_id", "presentation", "candidate", "installed"}
)
_UNSIGNED_KEYS: Final = frozenset({"attempt_id", "candidate", "installed"})
_INSTANT: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}")
_TEXT: Final = re.compile(r"[\x21-\x7e]{1,256}")
_MAX_INPUT: Final = 1024 * 1024


class Refused(Exception):
    """A refusal with a stable code. Never carries key material."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ── small helpers ───────────────────────────────────────────────────────────


def _canonical(value: Mapping[str, Any]) -> bytes:
    """Control's `canonical_json`: the bytes the presentation signature covers."""
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str, code: str) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise Refused(code)
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        raise Refused(code) from None
    if _b64url(raw) != value:
        raise Refused(code)
    return raw


def _instant(value: object, code: str) -> datetime:
    """Whole-second `...Z` only, the form Control renders and parses."""
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


def _json_object(path: str | Path, code: str) -> dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            data = handle.read(_MAX_INPUT + 1)
        document = json.loads(data)
    except (OSError, ValueError):
        raise Refused(code) from None
    if not data or len(data) > _MAX_INPUT or not isinstance(document, dict):
        raise Refused(code)
    return document


def _envelope(value: object, code: str) -> AttestationEnvelopeV2:
    try:
        return AttestationEnvelopeV2.from_mapping(value)
    except SpecError:
        raise Refused(code) from None


def _attempt_id(value: object, code: str) -> str:
    if not isinstance(value, str):
        raise Refused(code)
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        raise Refused(code) from None
    if canonical != value:
        raise Refused(code)
    return value


def _fingerprint(public_raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(public_raw).hexdigest()


def _owner_only_dir(path: Path, uid: int, code: str) -> None:
    try:
        info = os.lstat(path)
    except OSError:
        raise Refused(code) from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != uid
        or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise Refused(code)


def _write_file(path: Path, data: bytes, *, replace: bool) -> None:
    """Atomic 0600 write. Create-only unless `replace`; a symlink is never followed."""
    partial = path.parent / f".{path.name}.{os.getpid()}.partial"
    try:
        descriptor = os.open(
            partial,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
    except OSError:
        raise Refused("output-not-creatable") from None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(partial, path)
        else:
            try:
                os.link(partial, path)
            except OSError:
                raise Refused("output-exists") from None
    finally:
        try:
            os.unlink(partial)
        except OSError:
            pass


# ── the presentation key ────────────────────────────────────────────────────


def key_init(key_path: str | Path, *, owner_uid: int = 0) -> dict[str, str]:
    """Generate the presentation key. Print only its public half.

    Created O_EXCL with mode 0400 inside a directory owned by `owner_uid` with no
    group or other access, so there is no window in which it is readable by anyone
    else and an existing key is never replaced. The process must itself be
    `owner_uid`. `owner_uid` is 0 on a target; tests pass their own uid.
    """
    path = Path(key_path)
    if not path.is_absolute():
        raise Refused("key-path-not-absolute")
    if os.geteuid() != owner_uid:
        raise Refused("key-init-wrong-user")
    _owner_only_dir(path.parent, owner_uid, "key-directory-unsafe")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o400,
        )
    except OSError:
        raise Refused("key-exists-or-not-creatable") from None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(pem)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        os.unlink(path)
        raise
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {
        "public_key_b64url": _b64url(raw),
        "public_key_fingerprint": _fingerprint(raw),
    }


def load_key(key_path: str | Path, *, owner_uid: int = 0) -> Ed25519PrivateKey:
    """Read the key only if it is exactly what `key-init` leaves: a regular file,
    not a symlink, owned by `owner_uid`, mode 0400, in a safe directory."""
    path = Path(key_path)
    if not path.is_absolute():
        raise Refused("key-path-not-absolute")
    _owner_only_dir(path.parent, owner_uid, "key-directory-unsafe")
    try:
        info = os.lstat(path)
    except OSError:
        raise Refused("key-missing") from None
    if not stat.S_ISREG(info.st_mode):
        raise Refused("key-not-a-regular-file")
    if info.st_uid != owner_uid:
        raise Refused("key-owner-unexpected")
    if stat.S_IMODE(info.st_mode) != 0o400:
        raise Refused("key-mode-unexpected")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as handle:
            data = handle.read(8192)
        key = load_pem_private_key(data, password=None)
    except (OSError, ValueError, TypeError):
        raise Refused("key-unreadable") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise Refused("key-not-ed25519")
    return key


# ── pop ─────────────────────────────────────────────────────────────────────


def pop_response(
    key: Ed25519PrivateKey,
    *,
    challenge: Mapping[str, Any],
    key_id: str,
    deployment_ref: str,
) -> dict[str, str]:
    """Answer Control's possession challenge, through Kernel's own signing input.

    Kernel owns the challenge's signing bytes (`DeploymentPossessionChallenge
    .signing_input`) and refuses a challenge naming another key or deployment
    BEFORE signing, so neither is restated here. The kernel must be importable
    where this runs.
    """
    try:
        from dotmac_kernel.licensing import (
            DeploymentMismatchError,
            DeploymentPossessionChallenge,
            answer_possession_challenge,
        )
    except ImportError:
        raise Refused("kernel-unavailable") from None
    key_id = _text(key_id, "key-id-invalid")
    deployment_ref = _text(deployment_ref, "deployment-ref-invalid")

    class _Signer:
        def __init__(self) -> None:
            self.key_id = key_id
            self.deployment_ref = deployment_ref

        def sign(self, data: bytes) -> bytes:
            return key.sign(data)

    try:
        parsed = DeploymentPossessionChallenge.from_wire(dict(challenge))
        answer = answer_possession_challenge(parsed, signer=_Signer())
    except DeploymentMismatchError:
        raise Refused("challenge-not-for-this-key") from None
    except Exception:
        raise Refused("challenge-malformed") from None
    return answer.to_wire()


# ── assemble ────────────────────────────────────────────────────────────────


def _bound(
    candidate: AttestationEnvelopeV2, installed: AttestationEnvelopeV2, attempt_id: str
) -> None:
    """Structural agreement of the pair with the dispatch. Signatures are NOT
    checked here: that is the ceremony's `verify` and Foundation on the target."""
    if candidate.purpose != CANDIDATE_ATTESTATION_PURPOSE:
        raise Refused("candidate-purpose-unexpected")
    if installed.purpose != INSTALLED_OBSERVATION_PURPOSE:
        raise Refused("installed-purpose-unexpected")
    if candidate.public_key_fingerprint == installed.public_key_fingerprint:
        raise Refused("pair-shares-a-signing-key")
    if installed.observation_id != attempt_id:
        raise Refused("installed-observation-is-not-the-dispatch")
    try:
        candidate_subject = CandidateAttestationSubjectV2.from_mapping(
            candidate.subject_mapping()
        )
        installed_subject = InstalledHostAttestationSubjectV2.from_mapping(
            installed.subject_mapping()
        )
    except SpecError:
        raise Refused("attestation-subject-malformed") from None
    if installed_subject.candidate_subject_digest != candidate_subject_digest(
        candidate_subject
    ):
        raise Refused("installed-does-not-bind-the-candidate")


def assemble_unsigned(
    *,
    dispatch_materials: Mapping[str, Any],
    candidate: Mapping[str, Any],
    installed: Mapping[str, Any],
    expected_candidate_digest: str,
    expected_installed_digest: str,
) -> dict[str, Any]:
    """`{attempt_id, candidate, installed}`, refusing any digest that is not expected.

    `attempt_id` is read from the controller-side `dispatch-materials.json`, never
    typed. The candidate is identified by its expected envelope digest, the
    installed envelope by the digest the ceremony printed at step 6.
    """
    attempt_id = _attempt_id(dispatch_materials.get("attempt_id"), "attempt-id-invalid")
    for digest in (expected_candidate_digest, expected_installed_digest):
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise Refused("expected-digest-invalid")
    candidate_envelope = _envelope(candidate, "candidate-envelope-malformed")
    installed_envelope = _envelope(installed, "installed-envelope-malformed")
    if (
        str(attestation_envelope_digest(candidate_envelope))
        != expected_candidate_digest
    ):
        raise Refused("candidate-digest-mismatch")
    if (
        str(attestation_envelope_digest(installed_envelope))
        != expected_installed_digest
    ):
        raise Refused("installed-digest-mismatch")
    _bound(candidate_envelope, installed_envelope, attempt_id)
    return {
        "attempt_id": attempt_id,
        "candidate": candidate_envelope.canonical_document(),
        "installed": installed_envelope.canonical_document(),
    }


# ── present ─────────────────────────────────────────────────────────────────


def build_presentation(
    key: Ed25519PrivateKey,
    *,
    key_id: str,
    attempt_id: str,
    candidate: AttestationEnvelopeV2,
    installed: AttestationEnvelopeV2,
    store_time: datetime,
    presentation_id: str,
) -> dict[str, Any]:
    """Sign and self-verify one presentation. `{statement, signature}`."""
    issued = store_time - BACKDATE
    statement = {
        "schema": PRESENTATION_SCHEMA,
        "version": PRESENTATION_VERSION,
        "purpose": PRESENTATION_PURPOSE,
        "audience": PRESENTATION_AUDIENCE,
        "presentation_id": presentation_id,
        "key_id": key_id,
        "dispatch_id": attempt_id,
        "candidate_attestation_envelope_digest": str(
            attestation_envelope_digest(candidate)
        ),
        "installed_attestation_envelope_digest": str(
            attestation_envelope_digest(installed)
        ),
        "issued_at": _format(issued),
        "expires_at": _format(issued + LIFETIME),
    }
    signature = _b64url(key.sign(_canonical(statement)))
    _check_signature(key.public_key(), statement, signature)
    return {"statement": statement, "signature": signature}


def _check_signature(
    public_key: Ed25519PublicKey, statement: Mapping[str, Any], signature: str
) -> None:
    try:
        public_key.verify(
            _b64url_decode(signature, "presentation-signature-malformed"),
            _canonical(statement),
        )
    except InvalidSignature:
        raise Refused("presentation-signature-invalid") from None


def _load_inputs(
    path: Path, *, refresh: bool
) -> tuple[str, AttestationEnvelopeV2, AttestationEnvelopeV2, Mapping[str, Any] | None]:
    document = _json_object(path, "admission-input-unreadable")
    expected = _DOCUMENT_KEYS if refresh else _UNSIGNED_KEYS
    if set(document) != expected:
        raise Refused("admission-input-malformed")
    attempt_id = _attempt_id(document["attempt_id"], "attempt-id-invalid")
    candidate = _envelope(document["candidate"], "candidate-envelope-malformed")
    installed = _envelope(document["installed"], "installed-envelope-malformed")
    _bound(candidate, installed, attempt_id)
    previous = document["presentation"] if refresh else None
    return attempt_id, candidate, installed, previous


def present(
    *,
    key_path: str | Path,
    key_id: str,
    store_time: str,
    out: str | Path,
    admission_input: str | Path | None = None,
    refresh: bool = False,
    owner_uid: int = 0,
    presentation_id: str | None = None,
) -> dict[str, str]:
    """Write (or refresh) `host-admission.json`.

    First write: `admission_input` is the unsigned document `assemble` produced,
    and `out` must not exist. Refresh: the input IS `out`; its existing
    presentation must verify under this key, name this key id, dispatch and
    digests, and `store_time` must be strictly later than the one it was issued at,
    so a replayed or reordered refresh cannot step the clock backwards.
    """
    key_id = _text(key_id, "key-id-invalid")
    now = _instant(store_time, "store-time-invalid")
    out_path = Path(out)
    if not out_path.is_absolute():
        raise Refused("output-not-absolute")
    _owner_only_dir(out_path.parent, os.geteuid(), "output-directory-unsafe")
    if refresh:
        if admission_input is not None and Path(admission_input) != out_path:
            raise Refused("refresh-input-must-be-the-output")
        source = out_path
    else:
        if admission_input is None:
            raise Refused("admission-input-required")
        source = Path(admission_input)
        if out_path.exists() or out_path.is_symlink():
            raise Refused("output-exists")
    key = load_key(key_path, owner_uid=owner_uid)
    attempt_id, candidate, installed, previous = _load_inputs(source, refresh=refresh)

    if refresh:
        _require_previous(previous, key, key_id, attempt_id, candidate, installed, now)
    new_id = _text(
        presentation_id or f"presentation-{uuid.uuid4().hex}",
        "presentation-id-invalid",
    )

    presentation = build_presentation(
        key,
        key_id=key_id,
        attempt_id=attempt_id,
        candidate=candidate,
        installed=installed,
        store_time=now,
        presentation_id=new_id,
    )
    document = {
        "attempt_id": attempt_id,
        "presentation": presentation,
        "candidate": candidate.canonical_document(),
        "installed": installed.canonical_document(),
    }
    data = _canonical(document)
    _write_file(out_path, data, replace=refresh)
    return {
        "file_sha256": hashlib.sha256(data).hexdigest(),
        "presentation_id": new_id,
        "issued_at": presentation["statement"]["issued_at"],
        "expires_at": presentation["statement"]["expires_at"],
        "candidate_attestation_digest": presentation["statement"][
            "candidate_attestation_envelope_digest"
        ],
        "installed_attestation_digest": presentation["statement"][
            "installed_attestation_envelope_digest"
        ],
    }


def _require_previous(
    previous: object,
    key: Ed25519PrivateKey,
    key_id: str,
    attempt_id: str,
    candidate: AttestationEnvelopeV2,
    installed: AttestationEnvelopeV2,
    now: datetime,
) -> None:
    if not isinstance(previous, dict) or set(previous) != {"statement", "signature"}:
        raise Refused("previous-presentation-malformed")
    statement = previous["statement"]
    if not isinstance(statement, dict):
        raise Refused("previous-presentation-malformed")
    _check_signature(key.public_key(), statement, previous["signature"])
    if (
        statement.get("key_id") != key_id
        or statement.get("dispatch_id") != attempt_id
        or statement.get("candidate_attestation_envelope_digest")
        != str(attestation_envelope_digest(candidate))
        or statement.get("installed_attestation_envelope_digest")
        != str(attestation_envelope_digest(installed))
    ):
        raise Refused("previous-presentation-differs")
    issued = _instant(statement.get("issued_at"), "previous-presentation-malformed")
    if now - BACKDATE <= issued:
        raise Refused("refresh-store-time-not-later")


# ── CLI ─────────────────────────────────────────────────────────────────────


def _cmd_key_init(args: argparse.Namespace) -> Mapping[str, str]:
    return key_init(args.key_path, owner_uid=args.owner_uid)


def _cmd_pop(args: argparse.Namespace) -> Mapping[str, str]:
    key = load_key(args.key_path, owner_uid=args.owner_uid)
    challenge = _json_object(args.challenge, "challenge-unreadable")
    return pop_response(
        key, challenge=challenge, key_id=args.key_id, deployment_ref=args.deployment_ref
    )


def _cmd_assemble(args: argparse.Namespace) -> Mapping[str, str]:
    document = assemble_unsigned(
        dispatch_materials=_json_object(
            args.dispatch_materials, "dispatch-materials-unreadable"
        ),
        candidate=_json_object(args.candidate, "candidate-envelope-malformed"),
        installed=_json_object(args.installed, "installed-envelope-malformed"),
        expected_candidate_digest=args.candidate_digest,
        expected_installed_digest=args.installed_digest,
    )
    out = Path(args.out)
    if not out.is_absolute():
        raise Refused("output-not-absolute")
    _owner_only_dir(out.parent, os.geteuid(), "output-directory-unsafe")
    data = _canonical(document)
    _write_file(out, data, replace=False)
    return {
        "attempt_id": document["attempt_id"],
        "file_sha256": hashlib.sha256(data).hexdigest(),
    }


def _cmd_present(args: argparse.Namespace) -> Mapping[str, str]:
    return present(
        key_path=args.key_path,
        key_id=args.key_id,
        store_time=args.store_time,
        out=args.out,
        admission_input=args.admission_input,
        refresh=args.refresh,
        owner_uid=args.owner_uid,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler: Any, *required: str) -> argparse.ArgumentParser:
        command = sub.add_parser(name)
        command.set_defaults(handler=handler)
        for option in required:
            command.add_argument(f"--{option}", required=True)
        return command

    # The key path is a required argument: the fixed location is a kit decision
    # (design 4: "a fixed path outside the per-run tmpfs and the deploy dir").
    for command in (
        add("key-init", _cmd_key_init, "key-path"),
        add("pop", _cmd_pop, "key-path", "challenge", "key-id", "deployment-ref"),
        add("present", _cmd_present, "key-path", "key-id", "store-time", "out"),
    ):
        command.add_argument("--owner-uid", type=int, default=0)
    sub.choices["present"].add_argument("--admission-input")
    sub.choices["present"].add_argument("--refresh", action="store_true")
    add(
        "assemble",
        _cmd_assemble,
        "dispatch-materials",
        "candidate",
        "installed",
        "candidate-digest",
        "installed-digest",
        "out",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.handler(args)
    except Refused as refusal:
        print(f"refused: {refusal.code}", file=sys.stderr)
        return 2
    print(json.dumps(dict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
