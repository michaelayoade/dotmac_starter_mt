#!/usr/bin/env python3
"""The adoption-clearance gate: DrillClearance.v1 and ``require-adoptable``.

The Foundation 0.4.0a2 publication gate (``foundation_publication_gate.py``)
lets DR3_DR5 be WAIVED under a policy ``adoption_conditions`` entry
(``DR3_DR5_STAGING_DRILL``) that blocks production use of the version until a
staging drill has been run. Nothing downstream read that condition. This module
is the read side, and nothing else. It is defence in depth: the tool and the
publish lane refuse without a clearance, but the cryptographic barrier is custody
of the ceremony's transit key, which signs whatever bytes it is given:

* ``verify-clearance`` authenticates a Michael-signed ``DrillClearance.v1``
  (SSHSIG over the exact bytes, its OWN namespace and OWN signer file) and binds
  it to the policy condition, the committed candidate receipt and the
  publication record.
* ``require-adoptable`` exits 0 only if every adoption condition that blocks the
  scope has a verified clearance committed in the repository, or the version has
  no blocking condition at all. The candidate-envelope signer calls it before
  rendering, assembling or verifying a production-scope envelope.

## Scope

The condition blocks EVERY Foundation operation on a production target, not
only a governed deploy. Admission compares one envelope audience for all of
them, so this gate names a production audience, ``<facility>/<blocks>``, and
nothing about which operation a host runs. (``blocks`` is the policy's own
vocabulary; the policy file is unchanged.)

## What is deliberately absent

No flag, environment variable or input selects a signer file, a namespace, a
policy or a clearance directory. The signer file ships EMPTY, so every
clearance is refused (``clearance_signer_unenrolled``) until Michael enrols a
key in a reviewed change. The oracle table of expected fault outcomes ships
EMPTY for the same reason: it is lifted from the drill-recipe owner's reviewed
recipes at enrolment, never invented here (``clearance_oracle_unpinned``).
The private receipts a clearance cites are NOT re-verified in CI; the signature
attests that a reader re-hashed them, exactly as for Gate 2.

Exit codes: 0 satisfied, 1 refused, 2 indeterminate (an unreadable or malformed
policy or clearance is a refusal, never the default). Stdlib only. Never prints
supplied content, only stable reason codes.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import foundation_publication_gate as gate
from lane3_offline_result import OfflineResultRefused, canonical_result_bytes

REPO_ROOT = gate.REPO_ROOT
INVENTORIES = gate.INVENTORIES
SIGNERS_PATH = INVENTORIES / "foundation-adoption-clearance.allowed_signers"
REVOKED_PATH = INVENTORIES / "foundation-adoption-clearance.revoked_keys"
CLEARANCE_DIR = INVENTORIES / "foundation-adoption-clearances"
RECORDS_PATH = INVENTORIES / "foundation-publication-records.json"

SCHEMA = "DrillClearance.v1"
NAMESPACE = "dotmac.foundation.adoption-clearance.v1"
PRINCIPAL = "foundation-adoption-clearance@dotmac.io"
RECORDS_SCHEMA = "dotmac.foundation.publication-records.v1"

SCOPE_PRODUCTION = "production"

MAX_CLEARANCE_BYTES = 64 * 1024
MAX_SIGNATURE_BYTES = 8192
MAX_SIGNERS_BYTES = 8192
MAX_REVOKED_BYTES = 65536
MAX_RECORDS_BYTES = 1024 * 1024

DRILL_BAO_ENVIRONMENT = "staging"
DRILL_HOST_ALIAS = "seabone"

#: fault -> the outcome the drill must have produced. EMPTY by design: the exact
#: codes are the drill-recipe owner's, lifted verbatim at enrolment (Slice A').
#: An empty or incomplete table refuses every clearance.
EXPECTED_OUTCOMES: dict[str, str] = {}

_SSH_TIME = gate._SSH_TIME
_DIGEST = gate._DIGEST
_HEX64 = gate._HEX64
_REVISION = gate._REVISION
_TIME = gate._TIME
_PERSON = gate._PERSON
_REF = gate._REF
_VERSION = gate._VERSION
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_TARGET_REF = re.compile(
    r"[a-z0-9][a-z0-9_.-]{0,63}/staging/" + DRILL_HOST_ALIAS + r"\Z"
)


def production_audience(facility: str) -> str:
    """The envelope audience a production-class target's policy must carry."""
    return f"{facility}/{gate.CONDITION_BLOCKS}"


def clearance_paths(
    facility: str, version: str, condition_id: str
) -> tuple[Path, Path]:
    base = f"{facility}-{version}-{condition_id}"
    return CLEARANCE_DIR / f"{base}.json", CLEARANCE_DIR / f"{base}.sshsig"


# ── signature ──────────────────────────────────────────────────────────────


def _read_trust(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as source:
            payload = source.read(limit + 1)
    except OSError:
        raise gate.GateIndeterminate("clearance_trust.unavailable") from None
    if len(payload) > limit:
        raise gate.GateRefused("clearance_trust.too_large")
    return payload


def parse_signer(signers: bytes) -> str:
    """The one enrolled key (base64 blob), or a refusal. Empty is unenrolled."""
    if not signers:
        raise gate.GateRefused("clearance_signer_unenrolled")
    text = gate._trust_bytes(signers, MAX_SIGNERS_BYTES)
    lines = text.splitlines()
    if len(lines) != 1:
        raise gate.GateRefused("clearance_signer.schema")
    parts = lines[0].split(" ")
    if len(parts) != 4 or parts[0] != PRINCIPAL or parts[2] != "ssh-ed25519":
        raise gate.GateRefused("clearance_signer.schema")
    options: dict[str, str] = {}
    for option in parts[1].split(","):
        match = re.fullmatch(r'([a-z-]+)="([^"\s,]+)"', option)
        if match is None or match[1] in options:
            raise gate.GateRefused("clearance_signer.options")
        options[match[1]] = match[2]
    if (
        set(options) != {"namespaces", "valid-after", "valid-before"}
        or options["namespaces"] != NAMESPACE
    ):
        # A key enrolled for any other namespace (Gate 2's, Lane 3's) is refused.
        raise gate.GateRefused("clearance_signer.options")
    moments = []
    for name in ("valid-after", "valid-before"):
        if _SSH_TIME.fullmatch(options[name]) is None:
            raise gate.GateRefused("clearance_signer.time")
        try:
            moments.append(datetime.strptime(options[name], "%Y%m%d%H%M%SZ"))
        except ValueError:
            raise gate.GateRefused("clearance_signer.time") from None
    if moments[0] >= moments[1]:
        raise gate.GateRefused("clearance_signer.time")
    gate._public_key(parts[3])
    return parts[3]


def parse_revoked(revoked: bytes) -> frozenset[str]:
    text = gate._trust_bytes(revoked, MAX_REVOKED_BYTES)
    keys = set()
    for line in text.splitlines():
        parts = line.split(" ")
        if len(parts) != 2 or parts[0] != "ssh-ed25519":
            raise gate.GateRefused("clearance_revocation.schema")
        gate._public_key(parts[1])
        keys.add(parts[1])
    return frozenset(keys)


def verify_signature(clearance: bytes, signature: bytes) -> str:
    """SSHSIG over the exact clearance bytes. Returns the signer fingerprint.

    The validity window in the signer line is enforced by ``ssh-keygen`` against
    the current time, so a clearance signed under an elapsed enrolment refuses.
    """
    signers = _read_trust(SIGNERS_PATH, MAX_SIGNERS_BYTES)
    key_blob = parse_signer(signers)
    if type(signature) is not bytes or not 0 < len(signature) <= MAX_SIGNATURE_BYTES:
        raise gate.GateRefused("clearance_signature.size")
    revoked = _read_trust(REVOKED_PATH, MAX_REVOKED_BYTES)
    if key_blob in parse_revoked(revoked):
        raise gate.GateRefused("clearance_signer_revoked")
    gate._require_tool()
    try:
        with tempfile.TemporaryDirectory(prefix="adoption-clearance-") as directory:
            allowed = Path(directory) / "allowed"
            sigfile = Path(directory) / "signature"
            revfile = Path(directory) / "revoked"
            for path, raw in ((allowed, signers), (sigfile, signature)):
                with path.open("xb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(raw)
            command = [
                gate._SSH_KEYGEN,
                "-Y",
                "verify",
                "-f",
                str(allowed),
                "-I",
                PRINCIPAL,
                "-n",
                NAMESPACE,
                "-s",
                str(sigfile),
            ]
            if revoked:
                with revfile.open("xb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(revoked)
                command += ["-r", str(revfile)]
            completed = subprocess.run(
                command,
                input=clearance,
                shell=False,
                timeout=10,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    except subprocess.TimeoutExpired:
        raise gate.GateIndeterminate("clearance_tool.timeout") from None
    except OSError:
        raise gate.GateIndeterminate("clearance_tool.unavailable") from None
    if completed.returncode != 0:
        raise gate.GateRefused("clearance_signature_invalid")
    return gate.fingerprint(key_blob)


# ── the document ───────────────────────────────────────────────────────────

_FAULT_KEYS = {
    "fault",
    "dispatch_id",
    "plan_digest",
    "injected_phase",
    "admission",
    "expected_outcome",
    "observed_outcome",
    "foundation_receipt_sha256",
    "agent_observation_envelope_sha256",
    "recovery_receipt_sha256",
    "receipt_sha256",
    "reviewed_by",
    "recorded_at",
}
_ADMISSION_KEYS = {
    "candidate_attestation_envelope_digest",
    "installed_attestation_envelope_digest",
    "installed_wheel_sha256",
}


def _hex(value: object, code: str) -> str:
    """A bare or ``sha256:``-prefixed digest, returned as bare hex."""
    found = _HEX64.fullmatch(value) if type(value) is str else None
    if found is None:
        raise gate.GateRefused(code)
    return found[1]


def _text(value: object, pattern: re.Pattern[str], code: str) -> str:
    return gate._field(value, pattern, code)


def _instant(value: object, code: str) -> datetime:
    return gate._check_time(value, code)


def _load_json_file(path: Path, limit: int, scope: str) -> dict:
    try:
        payload = path.read_bytes()
    except OSError:
        raise gate.GateIndeterminate(f"{scope}.unavailable") from None
    document = gate._loads(payload, limit, scope, gate.GateIndeterminate)
    if type(document) is not dict:
        raise gate.GateIndeterminate(f"{scope}.schema")
    return document


def _candidate_receipt(version: str) -> dict:
    """The committed CandidateArtifact.v1 receipt for the version."""
    if type(version) is not str or _VERSION.fullmatch(version) is None:
        raise gate.GateRefused("clearance_candidate.version")
    receipt = _load_json_file(
        INVENTORIES / f"foundation-candidate-{version}.json",
        MAX_RECORDS_BYTES,
        "clearance_candidate_receipt",
    )
    if receipt.get("schema") != "CandidateArtifact.v1":
        raise gate.GateRefused("clearance_candidate_receipt.schema")
    return receipt


def _publication_row(facility: str, version: str) -> dict:
    ledger = _load_json_file(RECORDS_PATH, MAX_RECORDS_BYTES, "clearance_records")
    if (
        ledger.get("schema") != RECORDS_SCHEMA
        or type(ledger.get("records")) is not list
    ):
        raise gate.GateIndeterminate("clearance_records.schema")
    for row in ledger["records"]:
        if (
            type(row) is dict
            and row.get("distribution") == facility
            and row.get("version") == version
            and row.get("publication_gate") == gate.GATE_2
        ):
            return row
    raise gate.GateRefused("clearance_publication_record_missing")


def _waiver_names(row: dict, condition: gate.AdoptionCondition) -> bool:
    for item in row.get("evidence") or []:
        waiver = item.get("waiver") if type(item) is dict else None
        if (
            type(waiver) is dict
            and item.get("id") == condition.waives
            and item.get("status") == "WAIVED"
            and waiver.get("condition_id") == condition.id
        ):
            return True
    return False


def check_clearance(
    clearance: bytes,
    selected: gate.GateException,
    *,
    receipt: dict,
    row: dict,
    now: datetime | None = None,
) -> gate.AdoptionCondition:
    """Validate the signed bytes against the policy, receipt and record."""
    document = gate._loads(clearance, MAX_CLEARANCE_BYTES, "clearance")
    if type(document) is not dict:
        raise gate.GateRefused("clearance.schema")
    # One encoding per document, never two spellings of one claim.
    if canonical_result_bytes(document) != clearance:
        raise gate.GateRefused("clearance.noncanonical")
    gate._exact_keys(
        document,
        {
            "schema",
            "facility",
            "version",
            "condition_id",
            "register_ref",
            "candidate",
            "environment",
            "drill_target",
            "faults",
            "issued_at",
        },
        "clearance",
    )
    if document["schema"] != SCHEMA:
        raise gate.GateRefused("clearance.schema")
    if (
        document["facility"] != selected.facility
        or document["version"] != selected.version
    ):
        raise gate.GateRefused("clearance.identity")
    condition = selected.condition(document["condition_id"])
    if condition is None or condition.blocks != gate.CONDITION_BLOCKS:
        raise gate.GateRefused("clearance.condition")
    if (
        type(document["register_ref"]) is not int
        or document["register_ref"] != condition.register_ref
    ):
        raise gate.GateRefused("clearance.register_ref")
    if document["environment"] != condition.environment:
        raise gate.GateRefused("clearance.environment")

    candidate = gate._exact_keys(
        document["candidate"],
        {"artifact_sha256", "wheel_sha256", "source_sha", "gate2_manifest_sha256"},
        "clearance.candidate",
    )
    wheel = _hex(candidate["wheel_sha256"], "clearance.candidate.wheel_sha256")
    artifact = _hex(candidate["artifact_sha256"], "clearance.candidate.artifact_sha256")
    manifest = _hex(
        candidate["gate2_manifest_sha256"], "clearance.candidate.gate2_manifest_sha256"
    )
    _text(candidate["source_sha"], _REVISION, "clearance.candidate.source_sha")
    # The committed receipt names the exact a2 wheel; the gate-2 manifest's
    # `artifact_sha256` is that same wheel digest (candidate_sha256).
    if wheel != _hex(f"sha256:{receipt.get('sha256')}", "clearance.candidate.receipt"):
        raise gate.GateRefused("clearance.candidate.wheel_mismatch")
    if artifact != wheel:
        raise gate.GateRefused("clearance.candidate.artifact_mismatch")
    if candidate["source_sha"] != receipt.get("source_sha"):
        raise gate.GateRefused("clearance.candidate.source_mismatch")
    if f"sha256:{manifest}" != row.get("gate2_manifest_sha256"):
        raise gate.GateRefused("clearance.candidate.manifest_mismatch")
    if not _waiver_names(row, condition):
        raise gate.GateRefused("clearance.candidate.waiver_mismatch")

    target = gate._exact_keys(
        document["drill_target"],
        {"target_ref", "control_target_id", "host_id", "bao_environment"},
        "clearance.drill_target",
    )
    _text(target["target_ref"], _TARGET_REF, "clearance.drill_target.target_ref")
    _text(target["control_target_id"], _REF, "clearance.drill_target.control_target_id")
    _text(target["host_id"], _REF, "clearance.drill_target.host_id")
    if target["bao_environment"] != DRILL_BAO_ENVIRONMENT:
        raise gate.GateRefused("clearance.drill_target.bao_environment")

    issued = _instant(document["issued_at"], "clearance.issued_at")
    if issued > (now or gate._utcnow()):
        raise gate.GateRefused("clearance.not_yet_valid")

    faults = document["faults"]
    if type(faults) is not list or len(faults) != len(condition.required_faults):
        raise gate.GateRefused("clearance.faults")
    seen_faults: set[str] = set()
    seen_dispatches: set[str] = set()
    for entry in faults:
        item = gate._exact_keys(entry, _FAULT_KEYS, "clearance.fault")
        name = item["fault"]
        if type(name) is not str or name not in condition.required_faults:
            raise gate.GateRefused("clearance.fault.unknown")
        if name in seen_faults:
            raise gate.GateRefused("clearance.fault.duplicate")
        seen_faults.add(name)
        dispatch = _text(item["dispatch_id"], _REF, "clearance.fault.dispatch_id")
        if dispatch in seen_dispatches:
            raise gate.GateRefused("clearance.fault.dispatch_reused")
        seen_dispatches.add(dispatch)
        _text(item["plan_digest"], _DIGEST, "clearance.fault.plan_digest")
        _text(item["injected_phase"], _REF, "clearance.fault.injected_phase")
        admission = gate._exact_keys(
            item["admission"], _ADMISSION_KEYS, "clearance.fault.admission"
        )
        _text(
            admission["candidate_attestation_envelope_digest"],
            _DIGEST,
            "clearance.fault.admission",
        )
        _text(
            admission["installed_attestation_envelope_digest"],
            _DIGEST,
            "clearance.fault.admission",
        )
        # The host's own attestation of what it ran must be the exact a2 wheel.
        if (
            _hex(admission["installed_wheel_sha256"], "clearance.fault.admission")
            != wheel
        ):
            raise gate.GateRefused("clearance.fault.installed_wheel_mismatch")
        for key in (
            "foundation_receipt_sha256",
            "agent_observation_envelope_sha256",
            "recovery_receipt_sha256",
            "receipt_sha256",
        ):
            _text(item[key], _DIGEST, f"clearance.fault.{key}")
        expected = EXPECTED_OUTCOMES.get(name)
        if not expected:
            raise gate.GateRefused("clearance_oracle_unpinned")
        if item["expected_outcome"] != expected:
            raise gate.GateRefused("clearance.fault.expected_outcome")
        if item["observed_outcome"] != expected:
            raise gate.GateRefused("clearance.fault.observed_outcome")
        _text(item["reviewed_by"], _PERSON, "clearance.fault.reviewed_by")
        if _instant(item["recorded_at"], "clearance.fault.recorded_at") > issued:
            raise gate.GateRefused("clearance.fault.recorded_after_issue")
    if seen_faults != set(condition.required_faults):
        raise gate.GateRefused("clearance.fault.missing")
    return condition


def verify_clearance(
    facility: str,
    version: str,
    clearance: bytes,
    signature: bytes,
    *,
    now: datetime | None = None,
) -> str:
    """Policy, signature, then document bindings. Returns the signer fingerprint."""
    policy = gate.load_policy()
    selected = policy.exceptions.get((facility, version))
    if selected is None:
        raise gate.GateRefused("clearance.no_exception")
    signer = verify_signature(clearance, signature)
    receipt = _candidate_receipt(version)
    row = _publication_row(facility, version)
    check_clearance(clearance, selected, receipt=receipt, row=row, now=now)
    return signer


def require_adoptable(facility: str, version: str) -> None:
    """Return normally only if nothing blocks production adoption.

    Raises ``GateRefused("adoption_condition_unsatisfied:<id>")`` for the first
    blocking condition without a verified clearance; ``GateIndeterminate`` for
    an unreadable policy.
    """
    policy = gate.load_policy()
    selected = policy.exceptions.get((facility, version))
    if selected is None:
        return
    for condition in selected.adoption_conditions:
        if condition.blocks != gate.CONDITION_BLOCKS:
            # Every adoption condition blocks production; an unknown scope
            # fails closed rather than being skipped.
            raise gate.GateRefused(
                f"adoption_condition_unsatisfied:{condition.id}:unknown_scope"
            )
        clearance_path, signature_path = clearance_paths(
            facility, version, condition.id
        )
        try:
            clearance = clearance_path.read_bytes()
            signature = signature_path.read_bytes()
        except OSError:
            raise gate.GateRefused(
                f"adoption_condition_unsatisfied:{condition.id}"
            ) from None
        try:
            verify_clearance(facility, version, clearance, signature)
        except gate.GateRefused as refusal:
            raise gate.GateRefused(
                f"adoption_condition_unsatisfied:{condition.id}:{refusal}"
            ) from None
        document = gate._loads(clearance, MAX_CLEARANCE_BYTES, "clearance")
        if type(document) is not dict or document.get("condition_id") != condition.id:
            raise gate.GateRefused(f"adoption_condition_unsatisfied:{condition.id}")


# ── commands ───────────────────────────────────────────────────────────────


def cmd_verify_clearance(args: argparse.Namespace) -> int:
    try:
        clearance = Path(args.clearance).read_bytes()
        signature = Path(args.signature).read_bytes()
    except OSError:
        raise gate.GateRefused("clearance_input.unavailable") from None
    signer = verify_clearance(args.facility, args.version, clearance, signature)
    print(f"clearance_sha256={gate._digest(clearance)}")
    print(f"clearance_signer_fingerprint={signer}")
    return 0


def cmd_require_adoptable(args: argparse.Namespace) -> int:
    require_adoptable(args.facility, args.version)
    print(f"adoptable={args.facility} {args.version} scope={args.scope}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("verify-clearance", help="authenticate and bind a clearance")
    p.add_argument("facility")
    p.add_argument("--version", required=True)
    p.add_argument("--clearance", required=True)
    p.add_argument("--signature", required=True)
    p.set_defaults(func=cmd_verify_clearance)

    p = sub.add_parser(
        "require-adoptable", help="exit 0 only if nothing blocks the scope"
    )
    p.add_argument("facility")
    p.add_argument("--version", required=True)
    p.add_argument("--scope", required=True, choices=(SCOPE_PRODUCTION,))
    p.set_defaults(func=cmd_require_adoptable)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (gate.GateRefused, OfflineResultRefused) as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 1
    except gate.GateIndeterminate as indeterminate:
        print(f"INDETERMINATE: {indeterminate}", file=sys.stderr)
        return 2
    except Exception:
        print("INDETERMINATE: gate_unexpected", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
