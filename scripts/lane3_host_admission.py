#!/usr/bin/env python3
"""The producer of the target-signed ``host-admission.json`` (design section 4).

Subcommands:

* ``key-init``  (target, E10.4) generate the presentation key, root-only 0400, and
  print its PUBLIC half. It refuses to replace a key. Needs only the standard
  library and ``cryptography``: nothing is staged yet at E10.4.
* ``pop``       (target, E10.6) answer Control's possession challenge with it.
  Needs the Kernel (see below) and the E10.6 challenge, so it is a second visit.
* ``assemble``  (launcher) build the unsigned document ``{attempt_id, candidate,
  installed}`` from the controller-side ``dispatch-materials.json`` and the two
  attestation envelopes, checking each against an expected digest.
* ``present``   (target, before ``start``) sign Control's
  ``HostAdmissionPresentationStatementV1`` and write ``host-admission.json``
  create-only, 0600, atomically.
* ``present --refresh`` (target, between ``ready`` and ``proceed``) re-sign with a
  later store time and atomically replace the file, keeping the presentation's age
  at admission to seconds.

``read_host_admission`` / ``parse_host_admission`` are the public digest-only
reader (design section 4) for the offline verifier: they verify the presentation
signature and return digests, never the envelopes.

Foundation and Kernel are loaded LAZILY, only by the paths that need them. Where
this runs they must be the digest-pinned tool venv the kit stages (Kernel equal to
CP's).

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
``host_admission_source``). A fixed vector in the test file pins the statement
bytes and signature; CP-5 must check it against real Control a17.

## Filesystem

Key and output paths are absolute with no ``.``/``..``. Every component is opened
with ``O_NOFOLLOW`` through a directory fd, the final directory is ``fstat``-ed
(owner, no group/other access), and files are opened relative to that fd and
``fstat``-ed on the OPEN descriptor (owner, mode, regular, and for the key a single
link). The directory is fsynced after a create or replace. ``--owner-uid`` is
required and must equal the effective uid.

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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

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

if TYPE_CHECKING:
    from dotmac_deployment_foundation.trusted_host_source import AttestationEnvelopeV2

__all__ = [
    "BACKDATE",
    "LIFETIME",
    "PRESENTATION_PURPOSE",
    "HostAdmissionDigest",
    "Refused",
    "assemble_unsigned",
    "build_presentation",
    "build_statement",
    "key_init",
    "main",
    "parse_host_admission",
    "pop_response",
    "present",
    "read_host_admission",
    "sign_statement",
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
_STATEMENT_KEYS: Final = frozenset(
    {
        "schema",
        "version",
        "purpose",
        "audience",
        "presentation_id",
        "key_id",
        "dispatch_id",
        "candidate_attestation_envelope_digest",
        "installed_attestation_envelope_digest",
        "issued_at",
        "expires_at",
    }
)
_INSTANT: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}")
_TEXT: Final = re.compile(r"[\x21-\x7e]{1,256}")
_MAX_INPUT: Final = 1024 * 1024
_NOFOLLOW_DIR: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


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


def _b64url_decode(value: object, code: str) -> bytes:
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


def _require_uid(owner_uid: object) -> int:
    """`--owner-uid` must be the effective uid: a file can only be trusted as
    belonging to the user who is about to read or write it."""
    if (
        not isinstance(owner_uid, int)
        or isinstance(owner_uid, bool)
        or owner_uid != os.geteuid()
    ):
        raise Refused("owner-uid-not-effective-uid")
    return owner_uid


# ── Foundation, loaded only where needed ────────────────────────────────────


def _tools() -> Any:
    try:
        from dotmac_deployment_foundation import errors, trusted_host_source
    except ImportError:
        raise Refused("foundation-unavailable") from None

    class _Tools:
        SpecError = errors.SpecError
        module = trusted_host_source

    return _Tools


def _envelope(value: object, code: str) -> AttestationEnvelopeV2:
    tools = _tools()
    try:
        parsed: AttestationEnvelopeV2 = tools.module.AttestationEnvelopeV2.from_mapping(
            value
        )
    except tools.SpecError:
        raise Refused(code) from None
    return parsed


def _envelope_digest(envelope: AttestationEnvelopeV2) -> str:
    return str(_tools().module.attestation_envelope_digest(envelope))


def _bound(
    candidate: AttestationEnvelopeV2, installed: AttestationEnvelopeV2, attempt_id: str
) -> None:
    """Structural agreement of the pair with the dispatch. Signatures are NOT
    checked here: that is the ceremony's `verify` and Foundation on the target."""
    tools = _tools()
    module = tools.module
    if candidate.purpose != module.CANDIDATE_ATTESTATION_PURPOSE:
        raise Refused("candidate-purpose-unexpected")
    if installed.purpose != module.INSTALLED_OBSERVATION_PURPOSE:
        raise Refused("installed-purpose-unexpected")
    if candidate.public_key_fingerprint == installed.public_key_fingerprint:
        raise Refused("pair-shares-a-signing-key")
    if installed.observation_id != attempt_id:
        raise Refused("installed-observation-is-not-the-dispatch")
    try:
        candidate_subject = module.CandidateAttestationSubjectV2.from_mapping(
            candidate.subject_mapping()
        )
        installed_subject = module.InstalledHostAttestationSubjectV2.from_mapping(
            installed.subject_mapping()
        )
    except tools.SpecError:
        raise Refused("attestation-subject-malformed") from None
    if installed_subject.candidate_subject_digest != module.candidate_subject_digest(
        candidate_subject
    ):
        raise Refused("installed-does-not-bind-the-candidate")


# ── filesystem, through directory fds ───────────────────────────────────────


def _walk_dir(path: Path, code: str) -> int:
    """An fd for `path`, every component opened O_NOFOLLOW from `/`."""
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise Refused(code)
    descriptor = os.open("/", _NOFOLLOW_DIR)
    try:
        for part in path.parts[1:]:
            following = os.open(part, _NOFOLLOW_DIR, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
    except OSError:
        os.close(descriptor)
        raise Refused(code) from None
    return descriptor


def _safe_dir(path: Path, uid: int, code: str) -> int:
    """An fd for an owner-only directory owned by `uid`, checked on the fd."""
    descriptor = _walk_dir(path, code)
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != uid
        or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        os.close(descriptor)
        raise Refused(code)
    return descriptor


def _leaf(path: Path, code: str) -> str:
    if path.name in ("", ".", ".."):
        raise Refused(code)
    return path.name


def _slurp(descriptor: int, code: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > _MAX_INPUT:
            raise Refused(code)
        chunks.append(chunk)


def _read_regular(path: str | Path, code: str) -> bytes:
    """Read a regular file without following a symlink at any component."""
    target = Path(path)
    directory = _walk_dir(target.parent, code)
    try:
        descriptor = os.open(
            _leaf(target, code),
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory,
        )
    except OSError:
        raise Refused(code) from None
    finally:
        os.close(directory)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise Refused(code)
        data = _slurp(descriptor, code)
    finally:
        os.close(descriptor)
    if not data:
        raise Refused(code)
    return data


def _json_object(path: str | Path, code: str) -> dict[str, Any]:
    try:
        document = json.loads(_read_regular(path, code))
    except ValueError:
        raise Refused(code) from None
    if not isinstance(document, dict):
        raise Refused(code)
    return document


#: The only names `_write_output` will create: the per-run host-admission document
#: and its unsigned precursor. Fail-closed on anything else, so this writer can
#: never be pointed at a store record. The publish ratchet asserts this set.
_OUTPUT_NAMES: Final = frozenset(
    {"host-admission.json", "host-admission-unsigned.json"}
)


def _write_output(path: Path, data: bytes, *, uid: int, replace: bool) -> None:
    """Atomic 0600 write in an owner-only directory, relative to a directory fd.

    Create-only unless `replace`. Never follows a symlink; the directory is
    fsynced afterwards so the name survives a crash. Refuses, before any file
    operation, a leaf name outside `_OUTPUT_NAMES`.
    """
    if path.name not in _OUTPUT_NAMES:
        raise Refused("output-name-not-allowed")
    directory = _safe_dir(path.parent, uid, "output-directory-unsafe")
    name = _leaf(path, "output-not-creatable")
    partial = f".{name}.{os.getpid()}.partial"
    try:
        try:
            descriptor = os.open(
                partial,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
        except OSError:
            raise Refused("output-not-creatable") from None
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if replace:
                os.replace(partial, name, src_dir_fd=directory, dst_dir_fd=directory)
            else:
                try:
                    os.link(
                        partial,
                        name,
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                        follow_symlinks=False,
                    )
                except OSError:
                    raise Refused("output-exists") from None
            os.fsync(directory)
        finally:
            try:
                os.unlink(partial, dir_fd=directory)
            except OSError:
                pass
    finally:
        os.close(directory)


# ── the presentation key ────────────────────────────────────────────────────


def key_init(key_path: str | Path, *, owner_uid: int) -> dict[str, str]:
    """Generate the presentation key. Print only its public half.

    Created O_EXCL with mode 0400 inside a directory owned by the effective uid
    with no group or other access, relative to a directory fd, so there is no
    window in which it is readable by anyone else and an existing key (or a
    symlink at its name) is never replaced.
    """
    uid = _require_uid(owner_uid)
    path = Path(key_path)
    directory = _safe_dir(path.parent, uid, "key-directory-unsafe")
    name = _leaf(path, "key-path-invalid")
    try:
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o400,
                dir_fd=directory,
            )
        except OSError:
            raise Refused("key-exists-or-not-creatable") from None
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(pem)
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(directory)
        except BaseException:
            os.unlink(name, dir_fd=directory)
            raise
    finally:
        os.close(directory)
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {
        "public_key_b64url": _b64url(raw),
        "public_key_fingerprint": _fingerprint(raw),
    }


def load_key(key_path: str | Path, *, owner_uid: int) -> Ed25519PrivateKey:
    """Read the key only if it is exactly what `key-init` leaves.

    Checked on the OPEN descriptor: a regular file owned by the effective uid,
    mode 0400, one link, in an owner-only directory, no symlink anywhere on the
    path.
    """
    uid = _require_uid(owner_uid)
    path = Path(key_path)
    directory = _safe_dir(path.parent, uid, "key-directory-unsafe")
    name = _leaf(path, "key-path-invalid")
    try:
        try:
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except OSError:
            raise Refused("key-missing") from None
        if not stat.S_ISREG(before.st_mode):
            raise Refused("key-not-a-regular-file")
        try:
            descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory
            )
        except OSError:
            raise Refused("key-unreadable") from None
    finally:
        os.close(directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise Refused("key-not-a-regular-file")
        if info.st_uid != uid:
            raise Refused("key-owner-unexpected")
        if stat.S_IMODE(info.st_mode) != 0o400:
            raise Refused("key-mode-unexpected")
        if info.st_nlink != 1:
            raise Refused("key-has-extra-links")
        data = _slurp(descriptor, "key-unreadable")
    finally:
        os.close(descriptor)
    try:
        key = load_pem_private_key(data, password=None)
    except (ValueError, TypeError):
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
    where this runs (the kit's pinned tool venv, Kernel equal to CP's).
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
    if _envelope_digest(candidate_envelope) != expected_candidate_digest:
        raise Refused("candidate-digest-mismatch")
    if _envelope_digest(installed_envelope) != expected_installed_digest:
        raise Refused("installed-digest-mismatch")
    _bound(candidate_envelope, installed_envelope, attempt_id)
    return {
        "attempt_id": attempt_id,
        "candidate": candidate_envelope.canonical_document(),
        "installed": installed_envelope.canonical_document(),
    }


# ── the statement ───────────────────────────────────────────────────────────


def build_statement(
    *,
    presentation_id: str,
    key_id: str,
    dispatch_id: str,
    candidate_digest: str,
    installed_digest: str,
    store_time: datetime,
) -> dict[str, Any]:
    """Control's `HostAdmissionPresentationStatementV1` mapping. Pure: no Foundation.

    `issued_at` is the store time minus 2 s and `expires_at` is 300 s later.
    """
    issued = store_time - BACKDATE
    return {
        "schema": PRESENTATION_SCHEMA,
        "version": PRESENTATION_VERSION,
        "purpose": PRESENTATION_PURPOSE,
        "audience": PRESENTATION_AUDIENCE,
        "presentation_id": presentation_id,
        "key_id": key_id,
        "dispatch_id": dispatch_id,
        "candidate_attestation_envelope_digest": candidate_digest,
        "installed_attestation_envelope_digest": installed_digest,
        "issued_at": _format(issued),
        "expires_at": _format(issued + LIFETIME),
    }


def sign_statement(
    key: Ed25519PrivateKey, statement: Mapping[str, Any]
) -> dict[str, Any]:
    """Sign the statement's canonical bytes; self-verify. `{statement, signature}`."""
    signature = _b64url(key.sign(_canonical(statement)))
    _verify_signature(key.public_key(), statement, signature)
    return {"statement": dict(statement), "signature": signature}


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
    return sign_statement(
        key,
        build_statement(
            presentation_id=presentation_id,
            key_id=key_id,
            dispatch_id=attempt_id,
            candidate_digest=_envelope_digest(candidate),
            installed_digest=_envelope_digest(installed),
            store_time=store_time,
        ),
    )


def _verify_signature(
    public_key: Ed25519PublicKey, statement: Mapping[str, Any], signature: object
) -> None:
    raw = _b64url_decode(signature, "presentation-signature-malformed")
    try:
        public_key.verify(raw, _canonical(statement))
    except InvalidSignature:
        raise Refused("presentation-signature-invalid") from None


def _statement_checked(statement: object) -> dict[str, Any]:
    """Control's parse rules for a statement, on the wire form."""
    if not isinstance(statement, dict) or set(statement) != _STATEMENT_KEYS:
        raise Refused("presentation-malformed")
    if (
        statement["schema"] != PRESENTATION_SCHEMA
        or type(statement["version"]) is not int
        or statement["version"] != PRESENTATION_VERSION
        or statement["purpose"] != PRESENTATION_PURPOSE
        or statement["audience"] != PRESENTATION_AUDIENCE
    ):
        raise Refused("presentation-malformed")
    for name in ("presentation_id", "key_id", "dispatch_id"):
        _text(statement[name], "presentation-malformed")
    for name in (
        "candidate_attestation_envelope_digest",
        "installed_attestation_envelope_digest",
    ):
        if not isinstance(statement[name], str) or not _DIGEST.fullmatch(
            statement[name]
        ):
            raise Refused("presentation-malformed")
    issued = _instant(statement["issued_at"], "presentation-malformed")
    expires = _instant(statement["expires_at"], "presentation-malformed")
    if not timedelta(0) < expires - issued <= LIFETIME:
        raise Refused("presentation-lifetime-invalid")
    return statement


# ── the digest-only reader ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HostAdmissionDigest:
    """What the offline verifier needs from `host-admission.json`, digests only."""

    file_sha256: str
    attempt_id: str
    candidate_attestation_digest: str
    installed_attestation_digest: str
    presentation_id: str
    presentation_key_id: str
    presentation_key_fingerprint: str
    issued_at: str
    expires_at: str


def parse_host_admission(
    data: bytes, *, presentation_public_key_b64url: str
) -> HostAdmissionDigest:
    """Verify a `host-admission.json` and return its digests (design section 4).

    Refuses unless: the member set is closed; the presentation parses under
    Control's v1 rules; its signature verifies under the given presentation public
    key (unpadded base64url, 32 bytes); its dispatch id is the document's
    `attempt_id`; and its two digests equal Foundation's digest of the carried
    envelopes. Signatures of the ENVELOPES are not checked here (the pair verifier
    does that). Needs Foundation, loaded lazily.
    """
    if not isinstance(data, bytes) or not data or len(data) > _MAX_INPUT:
        raise Refused("host-admission-unreadable")
    try:
        document = json.loads(data)
    except ValueError:
        raise Refused("host-admission-unreadable") from None
    if not isinstance(document, dict) or set(document) != _DOCUMENT_KEYS:
        raise Refused("host-admission-malformed")
    attempt_id = _attempt_id(document["attempt_id"], "attempt-id-invalid")
    presentation = document["presentation"]
    if not isinstance(presentation, dict) or set(presentation) != {
        "statement",
        "signature",
    }:
        raise Refused("presentation-malformed")
    statement = _statement_checked(presentation["statement"])
    raw = _b64url_decode(presentation_public_key_b64url, "presentation-key-invalid")
    if len(raw) != 32:
        raise Refused("presentation-key-invalid")
    _verify_signature(
        Ed25519PublicKey.from_public_bytes(raw), statement, presentation["signature"]
    )
    if statement["dispatch_id"] != attempt_id:
        raise Refused("presentation-dispatch-mismatch")
    candidate = _envelope(document["candidate"], "candidate-envelope-malformed")
    installed = _envelope(document["installed"], "installed-envelope-malformed")
    candidate_digest = _envelope_digest(candidate)
    installed_digest = _envelope_digest(installed)
    if (
        statement["candidate_attestation_envelope_digest"] != candidate_digest
        or statement["installed_attestation_envelope_digest"] != installed_digest
    ):
        raise Refused("presentation-digest-mismatch")
    return HostAdmissionDigest(
        file_sha256=hashlib.sha256(data).hexdigest(),
        attempt_id=attempt_id,
        candidate_attestation_digest=candidate_digest,
        installed_attestation_digest=installed_digest,
        presentation_id=statement["presentation_id"],
        presentation_key_id=statement["key_id"],
        presentation_key_fingerprint=_fingerprint(raw),
        issued_at=statement["issued_at"],
        expires_at=statement["expires_at"],
    )


def read_host_admission(
    path: str | Path, *, presentation_public_key_b64url: str
) -> HostAdmissionDigest:
    """`parse_host_admission` over a file, read without following symlinks."""
    return parse_host_admission(
        _read_regular(path, "host-admission-unreadable"),
        presentation_public_key_b64url=presentation_public_key_b64url,
    )


# ── present ─────────────────────────────────────────────────────────────────


def _load_inputs(
    document: dict[str, Any], *, refresh: bool
) -> tuple[str, AttestationEnvelopeV2, AttestationEnvelopeV2, object]:
    expected = _DOCUMENT_KEYS if refresh else _UNSIGNED_KEYS
    if set(document) != expected:
        raise Refused("admission-input-malformed")
    attempt_id = _attempt_id(document["attempt_id"], "attempt-id-invalid")
    candidate = _envelope(document["candidate"], "candidate-envelope-malformed")
    installed = _envelope(document["installed"], "installed-envelope-malformed")
    _bound(candidate, installed, attempt_id)
    return attempt_id, candidate, installed, document.get("presentation")


def present(
    *,
    key_path: str | Path,
    key_id: str,
    store_time: str,
    out: str | Path,
    owner_uid: int,
    admission_input: str | Path | None = None,
    refresh: bool = False,
    presentation_id: str | None = None,
) -> dict[str, str]:
    """Write (or refresh) `host-admission.json`.

    First write: `admission_input` is the unsigned document `assemble` produced,
    and `out` must not exist. Refresh: the input IS `out`; its existing
    presentation must verify under this key, name this key id, dispatch and
    digests, and `store_time` must be strictly later than the one it was issued at,
    so a replayed or reordered refresh cannot step the clock backwards.
    """
    uid = _require_uid(owner_uid)
    key_id = _text(key_id, "key-id-invalid")
    now = _instant(store_time, "store-time-invalid")
    out_path = Path(out)
    if not out_path.is_absolute():
        raise Refused("output-not-absolute")
    if refresh:
        if admission_input is not None and Path(admission_input) != out_path:
            raise Refused("refresh-input-must-be-the-output")
        source = out_path
    else:
        if admission_input is None:
            raise Refused("admission-input-required")
        source = Path(admission_input)
    key = load_key(key_path, owner_uid=uid)
    attempt_id, candidate, installed, previous = _load_inputs(
        _json_object(source, "admission-input-unreadable"), refresh=refresh
    )
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
    data = _canonical(
        {
            "attempt_id": attempt_id,
            "presentation": presentation,
            "candidate": candidate.canonical_document(),
            "installed": installed.canonical_document(),
        }
    )
    _write_output(out_path, data, uid=uid, replace=refresh)
    statement = presentation["statement"]
    return {
        "file_sha256": hashlib.sha256(data).hexdigest(),
        "presentation_id": new_id,
        "issued_at": statement["issued_at"],
        "expires_at": statement["expires_at"],
        "candidate_attestation_digest": statement[
            "candidate_attestation_envelope_digest"
        ],
        "installed_attestation_digest": statement[
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
    _verify_signature(key.public_key(), statement, previous["signature"])
    if (
        statement.get("key_id") != key_id
        or statement.get("dispatch_id") != attempt_id
        or statement.get("candidate_attestation_envelope_digest")
        != _envelope_digest(candidate)
        or statement.get("installed_attestation_envelope_digest")
        != _envelope_digest(installed)
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
    data = _canonical(document)
    _write_output(out, data, uid=_require_uid(args.owner_uid), replace=False)
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
        command.add_argument("--owner-uid", type=int, required=True)
        return command

    # The key path is a required argument: the fixed location is a kit decision
    # (design 4: "a fixed path outside the per-run tmpfs and the deploy dir").
    add("key-init", _cmd_key_init, "key-path")
    add("pop", _cmd_pop, "key-path", "challenge", "key-id", "deployment-ref")
    present_parser = add(
        "present", _cmd_present, "key-path", "key-id", "store-time", "out"
    )
    present_parser.add_argument("--admission-input")
    present_parser.add_argument("--refresh", action="store_true")
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
    print(json.dumps(dict(result)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
