"""E9 offline orchestrator, joins (S8a): evidence + CP-6 export -> offline result.

Runs only under offline custody, outside every runner. It reads one rehearsal's
published ciphertext and receipt, the CP-6 export, and independently held pins,
and joins them. Success emits the closed, canonical, UNSIGNED
`lane3.offline-verification-result.v1` bytes that `require_rehearsal.py`
authenticates (SSHSIG, `lane3_offline_signature`). `sign_result` is the
separate custody step that signs them.

Joins (each refusal is a stable code, never supplied text):

* receipt: `RehearsalReceiptV2`, publication, run, artifact, plan, descriptor;
* ciphertext digest == the receipt's, age decryption, archive, rule and
  foreign-preservation join (`lane3_bundle_verification`);
* Control: dispatch signed by a PINNED CP identity; ledger key == dispatch id;
  ledger `created_at` inside the launcher-observed DB-time bracket
  `[lease acquired_at, release released_at or lease expires_at]`;
* E10: destruction <= lease acquired <= ledger created_at; the installed digest
  recorded at destruction == the one in `host-admission.json`; the installed
  root was revoked AFTER the ledger row;
* candidate: the candidate (and installed) envelope verifies against the
  enrolled roots, the candidate root pinned by fingerprint.

NOT built here (S8b): the two-target / negative-target compensation proof. The
archive's outer carrier stays fixed at three members; that proof belongs inside
the encrypted manifest; there is one newest run per SHA.

DEPENDS ON S2 (`lane3_host_admission.py`): `read_host_admission` below is the
minimal interface consumed, marked `S2 INTERFACE`. Unverified offline: the
presentation signature (Control verified it at consumption).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import lane3_offline_result as result_codec
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.execution_plan_v3 import FoundationExecutionPlanV3
from dotmac_deployment_foundation.rehearsal import (
    ExecutionRunBindingV1,
    RehearsalReceiptV2,
    require_execution_run,
    require_rehearsed_artifact,
    verify_publication,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec
from dotmac_deployment_foundation.trusted_host_source import (
    CANDIDATE_ATTESTATION_PURPOSE,
    INSTALLED_OBSERVATION_PURPOSE,
    AttestationEnvelopeV2,
    AttestationTrustPolicy,
    AttestationTrustRootV2,
    CandidateAttestationSubjectV2,
    attestation_envelope_digest,
    verify_attestation_pair,
)
from lane3_bundle_verification import (
    BundleVerificationRefused,
    require_plaintext_exposure_bundle,
)
from lane3_cp6_export import (
    CONSUME_SCOPE,
    CONSUME_STATUS,
    Cp6Export,
    Cp6ExportRefused,
    PinnedIdentity,
    RootBlock,
    ledger_fingerprint,
    ledger_key_for,
    parse_cp6_export,
    receipt_digest,
    verify_dispatch,
)
from lane3_e10_receipt import E10ReceiptRef, ReceiptRefused
from lane3_evidence_finalization import (
    MAX_ARCHIVE_BYTES,
    MAX_CIPHERTEXT_BYTES,
    FinalizationRefused,
    read_plaintext_evidence_archive,
)
from lane3_host_source_admission import (
    EXPECTED_PACKAGE,
    Ed25519AttestationVerifier,
)
from lane3_offline_result import (
    AUDIENCE,
    MAX_SIGNATURE_BYTES,
    NAMESPACE,
    RESULT_SCHEMA,
    OfflineResultCoordinates,
    OfflineResultRefused,
    OfflineResultUnavailable,
    VerificationPolicy,
    parse_result,
    require_result_bindings,
)
from lane3_offline_signature import _require_tool, require_authenticated_result
from lane3_receipt_v2 import evidence_bundle_digest

__all__ = [
    "CANDIDATE_AUDIENCE",
    "HostAdmissionFacts",
    "JoinRefused",
    "OfflineVerification",
    "PinnedIdentity",
    "age_decryptor",
    "read_host_admission",
    "sign_result",
    "verify_offline",
]

#: PRE-E11-DESIGN 2.5: the candidate audience every E16 target policy names.
CANDIDATE_AUDIENCE: Final = "dotmac-deployment-foundation"
_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}")
_SSH_KEYGEN: Final = "/usr/bin/ssh-keygen"
_CANDIDATE_CUSTODY: Final = "candidate_release_signer"
_INSTALLED_CUSTODY: Final = "host_attester"
_PAIR_CODE: Final = re.compile(r"trusted-host-source-([a-z-]{1,60})")
_MAX_TOOL_BYTES: Final = 64 * 1024 * 1024
_AGE_HEADER: Final = b"age-encryption.org/v1\n"


class JoinRefused(ValueError):
    """A join failed. The message is a stable code; never any supplied text."""


def _refuse(code: str) -> None:
    raise JoinRefused(f"offline-join-{code}")


@dataclasses.dataclass(frozen=True, slots=True)
class HostAdmissionFacts:
    attempt_id: str
    candidate_digest: str
    installed_digest: str


@dataclasses.dataclass(frozen=True, slots=True)
class OfflineVerification:
    #: The canonical UNSIGNED `offline-result.json` bytes.
    result_bytes: bytes
    coordinates: OfflineResultCoordinates
    #: Digests of the inputs joined, for the retained report.
    input_digests: Mapping[str, str]


# --- S2 INTERFACE -------------------------------------------------------------


def _member_digest(value: Any) -> str:
    if isinstance(value, str):
        if not _DIGEST.fullmatch(value):
            _refuse("host-admission-invalid")
        return value
    try:
        return str(
            attestation_envelope_digest(AttestationEnvelopeV2.from_mapping(value))
        )
    except (SpecError, TypeError, ValueError):
        _refuse("host-admission-invalid")
    raise AssertionError  # pragma: no cover


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def read_host_admission(payload: bytes) -> HostAdmissionFacts:
    """S2 INTERFACE: parse `host-admission.json` for its two digests only.

    Closed document `{attempt_id, presentation, candidate, installed}` (design
    V5). `candidate` and `installed` may each be a `sha256:` digest or the
    envelope mapping (design section 4 is ambiguous; both are accepted). The
    presentation is not verified here. Replace with S2's reader when it lands.
    """
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        _refuse("host-admission-invalid")
    if (
        not isinstance(document, dict)
        or set(document) != {"attempt_id", "presentation", "candidate", "installed"}
        or not isinstance(document["attempt_id"], str)
        or not _is_uuid(document["attempt_id"])
    ):
        _refuse("host-admission-invalid")
    return HostAdmissionFacts(
        document["attempt_id"],
        _member_digest(document["candidate"]),
        _member_digest(document["installed"]),
    )


# --- decryption ---------------------------------------------------------------


def age_decryptor(
    *, identity_path: pathlib.Path, age_path: str, age_sha256: str
) -> Callable[[bytes], bytes]:
    """An `age --decrypt -i <identity>` decryptor with a pinned executable.

    The identity is a path read by `age` itself; this process never reads it
    and the path is the only thing carried. Tests inject their own decryptor.
    """

    def decrypt(ciphertext: bytes) -> bytes:
        try:
            raw = pathlib.Path(age_path).read_bytes()
        except OSError:
            raise JoinRefused("offline-join-age-unavailable") from None
        if len(raw) > _MAX_TOOL_BYTES or "sha256:" + hashlib.sha256(
            raw
        ).hexdigest() != (age_sha256):
            _refuse("age-pin-mismatch")
        try:
            done = subprocess.run(
                [age_path, "--decrypt", "-i", str(identity_path)],
                input=ciphertext,
                capture_output=True,
                timeout=60,
                check=False,
                shell=False,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"},
            )
        except (OSError, subprocess.TimeoutExpired):
            raise JoinRefused("offline-join-age-unavailable") from None
        if done.returncode != 0:
            _refuse("decrypt-failed")
        return done.stdout

    return decrypt


# --- the joins ----------------------------------------------------------------


def _json_object(payload: bytes, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        _refuse(code)
    if not isinstance(value, dict):
        _refuse(code)
    return value


def _envelope(payload: bytes, code: str) -> AttestationEnvelopeV2:
    try:
        return AttestationEnvelopeV2.from_mapping(_json_object(payload, code))
    except SpecError:
        _refuse(code)
    raise AssertionError  # pragma: no cover


def _root(block: RootBlock) -> AttestationTrustRootV2:
    # Revocation is judged by the callers on RECORDED times (as of the
    # consumption instant), so the signature is checked as of that instant.
    try:
        return AttestationTrustRootV2(
            public_key_fingerprint=block.fingerprint,
            public_key_base64=block.public_key_base64,
            issuer=block.issuer,
            key_id=block.key_id,
            purpose=block.purpose,
            custody_domain=block.custody_domain,
            algorithm=block.algorithm,
            trust_root_version=block.enrolment_uuid,
            not_before=block.not_before,
            not_after=block.not_after,
            revoked=False,
        )
    except SpecError:
        _refuse("root-invalid")
    raise AssertionError  # pragma: no cover


def _join_receipt(
    receipt: RehearsalReceiptV2,
    *,
    run: ExecutionRunBindingV1,
    release_revision: str,
    candidate_artifact_digest: str,
    plan: FoundationExecutionPlanV3,
    descriptor: ProductDeploymentSpec,
) -> None:
    try:
        verify_publication(receipt, revision=release_revision)
    except (SpecError, ValueError):
        _refuse("receipt-not-published")
    try:
        require_execution_run(receipt, run=run)
    except SpecError:
        _refuse("receipt-run-mismatch")
    try:
        require_rehearsed_artifact(receipt, artifact_digest=candidate_artifact_digest)
    except SpecError:
        _refuse("receipt-artifact-mismatch")
    content = receipt.content
    if (
        content["execution_plan_digest"] != plan.digest()
        or content["executed_execution_plan_digest"] != plan.digest()
    ):
        _refuse("receipt-plan-mismatch")
    wanted = str(descriptor.to_canonical_document().sha256_digest())
    if content["descriptor_digest"] != wanted or plan.descriptor_digest != wanted:
        _refuse("receipt-descriptor-mismatch")
    if content["host_id"] != plan.host_id or content["target_id"] != plan.target_id:
        _refuse("receipt-host-mismatch")


def _join_control(
    export: Cp6Export,
    *,
    receipt: RehearsalReceiptV2,
    plan: FoundationExecutionPlanV3,
    pinned: Sequence[PinnedIdentity],
    admission: HostAdmissionFacts,
) -> None:
    """Control join: signed dispatch, ledger row, lease bracket."""
    dispatch = receipt.content["control_dispatch"]
    wanted = dispatch["dispatch_id"]
    if export.dispatch_id != wanted:
        _refuse("export-dispatch-mismatch")
    try:
        statement = verify_dispatch(export.dispatch, pinned)
    except Cp6ExportRefused as exc:
        raise JoinRefused(
            "offline-join-" + str(exc).removeprefix("cp6-export-")
        ) from None
    # Everything below is read from the SIGNED statement, never export fields.
    if statement.dispatch_id != wanted:
        _refuse("dispatch-id-mismatch")
    if statement.execution_plan_digest != str(plan.digest()):
        _refuse("dispatch-plan-mismatch")
    if statement.target_id != plan.target_id or statement.target_ref != plan.target:
        _refuse("dispatch-target-mismatch")
    if (statement.execution_sequence, statement.attempt_no) != (
        dispatch["execution_sequence"],
        dispatch["attempt_no"],
    ):
        _refuse("dispatch-coordinate-mismatch")
    if admission.attempt_id != wanted:
        _refuse("host-admission-attempt-mismatch")

    ledger = export.ledger
    if ledger.key != ledger_key_for(wanted):
        _refuse("ledger-key-mismatch")
    if ledger.scope != CONSUME_SCOPE or ledger.status != CONSUME_STATUS:
        _refuse("ledger-row-mismatch")
    if (ledger.execution_sequence, ledger.attempt_no) != (
        statement.execution_sequence,
        statement.attempt_no,
    ):
        _refuse("ledger-coordinate-mismatch")
    expected_fingerprint = ledger_fingerprint(
        dispatch_envelope_digest=export.dispatch.envelope_digest,
        candidate_attestation_envelope_digest=admission.candidate_digest,
        installed_attestation_envelope_digest=admission.installed_digest,
    )
    if ledger.fingerprint != expected_fingerprint:
        _refuse("ledger-fingerprint-mismatch")

    lease = export.lease
    # The receipt's `lease_id` is the launcher's authorization run id.
    if (
        lease.authorization_run != receipt.content["lease_id"]
        or lease.host_id != receipt.content["host_id"]
        or export.host_id != lease.host_id
        or (export.release is not None and export.release.lease_id != lease.lease_id)
    ):
        _refuse("lease-mismatch")
    if lease.expires_at <= lease.acquired_at:
        _refuse("lease-mismatch")
    if export.release is None:
        # Foundation `covers` treats now >= expires_at as expired.
        if not lease.acquired_at <= ledger.created_at < lease.expires_at:
            _refuse("ledger-outside-bracket")
        return
    if not export.release.host_mutated:
        # The launcher forces host_mutated once the target reported ready.
        _refuse("release-not-mutated")
    if export.release.released_at > lease.expires_at:
        _refuse("release-after-expiry")
    if not lease.acquired_at <= ledger.created_at <= export.release.released_at:
        _refuse("ledger-outside-bracket")


def _join_e10(
    export: Cp6Export,
    *,
    plan: FoundationExecutionPlanV3,
    expected_receipt_digest: str,
    admission: HostAdmissionFacts,
) -> None:
    row = export.receipt
    recomputed = receipt_digest(row.document)
    if export.e10_receipt_digest != recomputed:
        _refuse("e10-receipt-export-digest-mismatch")
    if recomputed != expected_receipt_digest:
        _refuse("e10-receipt-digest-mismatch")
    destruction, lease, ledger = export.destruction, export.lease, export.ledger
    if destruction.receipt_ref != row.receipt_ref or (
        lease.e10_receipt_ref != row.receipt_ref
    ):
        _refuse("e10-receipt-ref-mismatch")
    if row.host_id != export.host_id or row.host_id != plan.host_id:
        _refuse("e10-host-mismatch")
    if (
        row.incarnation_fingerprint != plan.host_incarnation
        or row.enrolment_uuid != plan.host_enrolment_ref
    ):
        _refuse("e10-plan-binding-mismatch")
    try:
        E10ReceiptRef(
            vm_alias=row.vm_alias,
            host_id=row.host_id,
            machine_id=row.machine_id,
            ssh_host_key_fingerprint=row.ssh_host_key_fingerprint,
            incarnation_public_key_b64url=row.incarnation_public_key_b64url,
            incarnation_fingerprint=row.incarnation_fingerprint,
            enrolment_uuid=row.enrolment_uuid,
            incarnation_key_destroyed_at=destruction.destroyed_at,
            receipt_ref=row.receipt_ref,
            vm_slot=row.vm_slot,
        )
    except ReceiptRefused:
        _refuse("e10-receipt-invalid")
    if destruction.route != "transit_mount_disabled":
        _refuse("e10-destruction-route")
    if (
        destruction.dispatch_id != export.dispatch_id
        or destruction.installed_attestation_digest is None
    ):
        _refuse("e10-destruction-dispatch-mismatch")
    root = export.installed_root
    # enrolment <= destruction <= lease acquired <= ledger created_at
    if root.enrolled_at > destruction.destroyed_at:
        _refuse("e10-enrolled-after-destruction")
    if destruction.destroyed_at > lease.acquired_at:
        _refuse("e10-destroyed-after-acquire")
    if lease.acquired_at > ledger.created_at:
        _refuse("e10-acquired-after-ledger")
    if destruction.installed_attestation_digest != admission.installed_digest:
        _refuse("e10-installed-digest-mismatch")
    if (
        root.fingerprint != row.incarnation_fingerprint
        or root.enrolment_uuid != row.enrolment_uuid
        or root.purpose != INSTALLED_OBSERVATION_PURPOSE
        or root.custody_domain != _INSTALLED_CUSTODY
    ):
        _refuse("e10-root-mismatch")
    if root.revoked_at is None:
        _refuse("e10-root-not-revoked")
    if root.revoked_at <= ledger.created_at:
        _refuse("e10-root-revoked-before-ledger")


def _pair_code(exc: BaseException) -> str:
    match = _PAIR_CODE.fullmatch(str(getattr(exc, "code", "")))
    return "pair-" + match[1] if match else "pair-refused"


def _join_candidate(
    export: Cp6Export,
    *,
    plan: FoundationExecutionPlanV3,
    candidate_bytes: bytes,
    installed_bytes: bytes,
    admission: HostAdmissionFacts,
    pinned_candidate_root_fingerprint: str,
    candidate_audience: str,
) -> None:
    root = export.candidate_root
    if (
        not _DIGEST.fullmatch(pinned_candidate_root_fingerprint)
        or root.fingerprint != pinned_candidate_root_fingerprint
        or root.purpose != CANDIDATE_ATTESTATION_PURPOSE
    ):
        _refuse("candidate-root-unpinned")
    if root.custody_domain != _CANDIDATE_CUSTODY:
        _refuse("candidate-root-custody")
    if root.enrolled_at > export.ledger.created_at:
        _refuse("candidate-root-enrolled-late")
    if root.revoked_at is not None and root.revoked_at <= export.ledger.created_at:
        _refuse("candidate-root-revoked")
    candidate = _envelope(candidate_bytes, "candidate-envelope-invalid")
    installed = _envelope(installed_bytes, "installed-envelope-invalid")
    if (
        str(attestation_envelope_digest(candidate)) != admission.candidate_digest
        or str(attestation_envelope_digest(installed)) != admission.installed_digest
    ):
        _refuse("host-admission-digest-mismatch")
    try:
        subject = CandidateAttestationSubjectV2.from_mapping(
            candidate.subject_mapping()
        )
    except SpecError:
        _refuse("candidate-envelope-invalid")
    if str(subject.wheel_sha256) != plan.candidate_wheel_digest:
        _refuse("candidate-wheel-mismatch")
    try:
        policy = AttestationTrustPolicy(
            candidate_roots=(_root(root),),
            installed_roots=(_root(export.installed_root),),
            candidate_audience=candidate_audience,
            installed_audience=plan.host_id,
        )
    except SpecError:
        _refuse("pair-policy-invalid")
    try:
        verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=Ed25519AttestationVerifier(),
            trust_policy=policy,
            expected_host_identity=plan.host_id,
            expected_observation_id=export.dispatch_id,
            expected_package=EXPECTED_PACKAGE,
            # Opaque echo, not joined offline; the dispatch digest stands in.
            verification_context_digest=export.dispatch.envelope_digest,
            now=export.ledger.created_at,
        )
    except Exception as exc:
        # Codes only, never the refusal text.
        raise JoinRefused("offline-join-" + _pair_code(exc)) from None


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _result_bytes(
    coordinates: OfflineResultCoordinates, policy: VerificationPolicy, now: datetime
) -> bytes:
    verified_at = now.astimezone(UTC).replace(microsecond=0)
    document = {
        "schema": RESULT_SCHEMA,
        "purpose": NAMESPACE,
        "audience": AUDIENCE,
        "coordinates": dataclasses.asdict(coordinates),
        "verifier_source_revision": policy.verifier_source_revision,
        "verifier_profile_digest": policy.verifier_profile_digest,
        "policy_digest": policy.sha256_digest(),
        "verified_at": _iso(verified_at),
        "expires_at": _iso(
            verified_at + timedelta(seconds=policy.max_result_age_seconds)
        ),
        "conclusion": "verified",
    }
    return result_codec.canonical_json_bytes(document)


def verify_offline(
    *,
    receipt_bytes: bytes,
    ciphertext: bytes,
    decrypt: Callable[[bytes], bytes],
    run: ExecutionRunBindingV1,
    release_revision: str,
    candidate_artifact_digest: str,
    execution_plan: FoundationExecutionPlanV3,
    descriptor: ProductDeploymentSpec,
    export_bytes: bytes,
    expected_e10_receipt_digest: str,
    pinned_cp_identities: Sequence[PinnedIdentity],
    pinned_candidate_root_fingerprint: str,
    candidate_envelope_bytes: bytes,
    installed_envelope_bytes: bytes,
    host_admission_bytes: bytes,
    policy: VerificationPolicy,
    now: datetime,
    candidate_audience: str = CANDIDATE_AUDIENCE,
) -> OfflineVerification:
    """Run every join; return the unsigned result bytes or raise `JoinRefused`.

    `execution_plan` and `descriptor` are trusted inputs the caller holds; the
    private evidence never chooses its own authority. Everything is refused
    with a stable code; private text is never quoted.
    """
    if policy.state != "enrolled":
        _refuse("policy-unenrolled")
    if type(now) is not datetime or now.tzinfo is None:
        _refuse("clock")
    if type(execution_plan) is not FoundationExecutionPlanV3:
        _refuse("plan-required")
    if type(descriptor) is not ProductDeploymentSpec:
        _refuse("descriptor-required")
    try:
        receipt = RehearsalReceiptV2.from_json(receipt_bytes)
    except (SpecError, TypeError, ValueError):
        _refuse("receipt-invalid")
    _join_receipt(
        receipt,
        run=run,
        release_revision=release_revision,
        candidate_artifact_digest=candidate_artifact_digest,
        plan=execution_plan,
        descriptor=descriptor,
    )

    # Ciphertext: size, header and digest BEFORE any decryption.
    if type(ciphertext) is not bytes or not 0 < len(ciphertext) <= MAX_CIPHERTEXT_BYTES:
        _refuse("ciphertext-invalid")
    if not ciphertext.startswith(_AGE_HEADER):
        _refuse("ciphertext-invalid")
    try:
        ciphertext_digest = evidence_bundle_digest(ciphertext)
    except ReceiptRefused:
        _refuse("ciphertext-invalid")
    except Exception:
        _refuse("ciphertext-invalid")
    if ciphertext_digest != receipt.content["evidence_bundle_digest"]:
        _refuse("ciphertext-digest-mismatch")
    try:
        plaintext = decrypt(ciphertext)
    except JoinRefused:
        raise
    except Exception:
        _refuse("decrypt-failed")
    if type(plaintext) is not bytes or not 0 < len(plaintext) <= MAX_ARCHIVE_BYTES:
        _refuse("decrypt-failed")
    try:
        manifest, members = read_plaintext_evidence_archive(plaintext)
        require_plaintext_exposure_bundle(
            manifest, members, execution_plan=execution_plan, descriptor=descriptor
        )
    except (FinalizationRefused, BundleVerificationRefused):
        _refuse("bundle-inconsistent")

    try:
        export = parse_cp6_export(export_bytes)
    except Cp6ExportRefused:
        _refuse("export-invalid")
    admission = read_host_admission(host_admission_bytes)
    _join_control(
        export,
        receipt=receipt,
        plan=execution_plan,
        pinned=pinned_cp_identities,
        admission=admission,
    )
    _join_e10(
        export,
        plan=execution_plan,
        expected_receipt_digest=expected_e10_receipt_digest,
        admission=admission,
    )
    _join_candidate(
        export,
        plan=execution_plan,
        candidate_bytes=candidate_envelope_bytes,
        installed_bytes=installed_envelope_bytes,
        admission=admission,
        pinned_candidate_root_fingerprint=pinned_candidate_root_fingerprint,
        candidate_audience=candidate_audience,
    )

    coordinates = OfflineResultCoordinates(
        repository_id=run.repository_id,
        run_id=run.run_id,
        run_attempt=run.run_attempt,
        receipt_digest=receipt.sha256_digest(),
        ciphertext_digest=ciphertext_digest,
        candidate_artifact_digest=receipt.foundation_artifact_digest,
        release_revision=release_revision,
    )
    payload = _result_bytes(coordinates, policy, now)
    # Self-check with the consumer's own parser and bindings.
    try:
        require_result_bindings(
            parse_result(payload), expected=coordinates, policy=policy, now=now
        )
    except OfflineResultRefused:
        _refuse("result-self-check")
    return OfflineVerification(
        result_bytes=payload,
        coordinates=coordinates,
        input_digests={
            "cp6_export": export.export_digest,
            "e10_receipt": expected_e10_receipt_digest,
            "host_admission": "sha256:"
            + hashlib.sha256(host_admission_bytes).hexdigest(),
            "receipt": coordinates.receipt_digest,
            "ciphertext": ciphertext_digest,
        },
    )


# --- custody: signing the result ----------------------------------------------


def sign_result(
    verification: OfflineVerification,
    *,
    key_path: pathlib.Path,
    policy: VerificationPolicy,
    now: datetime,
) -> bytes:
    """SSHSIG-sign a result `verify_offline` produced (`ssh-keygen -Y sign`).

    Only an `OfflineVerification` is accepted, never arbitrary bytes. Order:
    parse and bind the result, hash-check `ssh-keygen` against the policy pin,
    and only THEN let the custody key be used. The key is named by path (or
    held by an agent); its bytes are never read here. The signature is checked
    with the consumer's own `require_authenticated_result` before it returns.
    (The tool is hash-checked before use and again at self-check; a swap in
    between is bounded by the root-owned, non-writable tool path.)
    """
    if type(verification) is not OfflineVerification or not isinstance(
        key_path, pathlib.Path
    ):
        raise JoinRefused("offline-join-sign-input-invalid")
    try:
        require_result_bindings(
            parse_result(verification.result_bytes),
            expected=verification.coordinates,
            policy=policy,
            now=now,
        )
        if policy.ssh_keygen_sha256 is None:
            raise JoinRefused("offline-join-sign-tool-refused")
        _require_tool(policy.ssh_keygen_sha256)
    except OfflineResultUnavailable:
        raise JoinRefused("offline-join-sign-unavailable") from None
    except OfflineResultRefused as exc:
        if str(exc).startswith("offline-tool"):
            raise JoinRefused("offline-join-sign-tool-refused") from None
        raise JoinRefused("offline-join-sign-self-check") from None
    payload = verification.result_bytes
    environment = {"LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"}
    if os.environ.get("SSH_AUTH_SOCK"):
        environment["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    try:
        with tempfile.TemporaryDirectory(prefix="lane3-sign-") as directory:
            target = pathlib.Path(directory) / "offline-result.json"
            with target.open("xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload)
            done = subprocess.run(
                [
                    _SSH_KEYGEN,
                    "-Y",
                    "sign",
                    "-f",
                    str(key_path),
                    "-n",
                    NAMESPACE,
                    str(target),
                ],
                shell=False,
                timeout=60,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if done.returncode != 0:
                raise JoinRefused("offline-join-sign-failed")
            signature = (
                pathlib.Path(directory) / "offline-result.json.sig"
            ).read_bytes()
    except (OSError, subprocess.TimeoutExpired):
        raise JoinRefused("offline-join-sign-failed") from None
    if not 0 < len(signature) <= MAX_SIGNATURE_BYTES:
        raise JoinRefused("offline-join-sign-failed")
    try:
        require_authenticated_result(
            payload,
            signature,
            expected=verification.coordinates,
            policy=policy,
            now=now,
        )
    except OfflineResultUnavailable:
        raise JoinRefused("offline-join-sign-unavailable") from None
    except OfflineResultRefused:
        raise JoinRefused("offline-join-sign-self-check") from None
    return signature
