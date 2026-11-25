#!/usr/bin/env python3
"""The Foundation publication gate selector, and the Gate 2 evidence oracle.

`release-facility.yml` publishes `dotmac-deployment-foundation` only after ONE
publication gate has passed. The default gate is the Lane 3 rehearsal receipt
(`scripts/require_rehearsal.py`). Michael ruled on 2026-11-23 (register
`dotmac-debt-register` v2149) that signed Gate 2 evidence REPLACES that receipt
for exactly one version, `0.4.0a2`. This module implements that ruling and
nothing wider.

## Shape

* `docs/inventories/foundation-publication-gates.json` is the policy. The
  default is `lane3_rehearsal` and the parser refuses any other default. An
  exception is matched EXACTLY on (facility, version); wildcard, range,
  prefix, duplicate and other-facility entries are refused. The list of
  required evidence ids lives in that file, never in a manifest, so a manifest
  can never drop an item.
* `select` prints `gate=<name>`. An unreadable or malformed policy exits 2 and
  the workflow treats that as a refusal, never as the default.
* `verify-gate2` authenticates a manifest (SSHSIG over the exact bytes, under
  its OWN namespace and OWN signer file, so a Lane 3 offline signature can
  never satisfy it and the reverse), validates it against the policy, and
  machine re-checks E3 at the candidate source commit.
* `recheck-gate2` re-verifies the same signature and digests at the
  publication boundary, before `twine upload`.

## What is deliberately absent

No flag, environment variable or input selects a gate, a policy, a signer file
or a namespace. The only inputs are the manifest and its signature (public
evidence, never trust). An EMPTY signer file refuses
(`gate2_signer_unenrolled`): enrolling a key is Michael's act, in a reviewed
change, never a builder's.

Exit codes follow the other oracles: 0 satisfied, 1 refused, 2 indeterminate.
Stdlib only. Never prints supplied content, only stable reason codes.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lane3_offline_result import (
    OfflineResultRefused,
    _public_key,
    _trust_bytes,
    canonical_result_bytes,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORIES = REPO_ROOT / "docs" / "inventories"
POLICY_PATH = INVENTORIES / "foundation-publication-gates.json"
SIGNERS_PATH = INVENTORIES / "foundation-gate2-evidence.allowed_signers"
REVOKED_PATH = INVENTORIES / "foundation-gate2-evidence.revoked_keys"

POLICY_SCHEMA = "dotmac.foundation.publication-gates.v1"
MANIFEST_SCHEMA = "dotmac.foundation.gate2-evidence.v1"
GATE_LANE3 = "lane3_rehearsal"
GATE_2 = "gate2_evidence"
FACILITY = "dotmac-deployment-foundation"
RULING_REGISTER = "dotmac-debt-register"

# Distinct from the Lane 3 offline verifier's namespace and principal.
NAMESPACE = "dotmac.foundation.gate2-evidence.v1"
PRINCIPAL = "foundation-gate2-evidence@dotmac.io"

MAX_POLICY_BYTES = 16 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SIGNATURE_BYTES = 8192
MAX_SIGNERS_BYTES = 8192
MAX_REVOKED_BYTES = 65536

EVIDENCE_ENV = "FOUNDATION_GATE2_EVIDENCE_B64"
SIGNATURE_ENV = "FOUNDATION_GATE2_SIGNATURE_B64"
EXPECTED_MANIFEST_ENV = "FOUNDATION_EXPECTED_GATE2_MANIFEST_SHA256"
EXPECTED_SIGNATURE_ENV = "FOUNDATION_EXPECTED_GATE2_SIGNATURE_SHA256"

E3_BASELINE = "tests/architecture/foundation_operator_job_baseline.json"
E3_TEST = "tests/architecture/test_foundation_operator_job_isolation.py"
E3_ID = "E3"
#: The machine re-check can never be waived, whatever the policy lists.
NEVER_WAIVABLE = frozenset({E3_ID})
#: An adoption condition is POLICY data (Michael's ruling, register
#: `dotmac-debt-register`): a waiver defers the drill, it does not drop it. The
#: manifest names a condition by id; the structure below is the only shape the
#: parser accepts, and the release record copies it in full.
CONDITION_BLOCKS = "production_governed_deploy"
CONDITION_ENVIRONMENT = "seabone-staging"
CONDITION_FAULTS = frozenset(
    {"db_outage_at_migrate", "network_loss", "timeout_sigterm"}
)
MAX_AGE_HOURS_LIMIT = 168
STATUSES = ("PASS", "CLEAR", "WAIVED")

_SSH_KEYGEN = "/usr/bin/ssh-keygen"
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?\Z")
_EVIDENCE_ID = re.compile(r"[A-Z][A-Z0-9_]{0,31}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HEX64 = re.compile(r"(?:sha256:)?([0-9a-f]{64})\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_SSH_TIME = re.compile(r"[0-9]{14}Z\Z")
_PERSON = re.compile(r"[A-Za-z0-9._@:+-][A-Za-z0-9._@:+ -]{0,127}\Z")
_CONDITION_ID = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_REF = re.compile(r"[\x21-\x7e][\x20-\x7e]{0,254}\Z")


class GateRefused(Exception):
    """A stable reason code; never include supplied document text."""


class GateIndeterminate(Exception):
    """The oracle could not answer. Exit 2, never a pass."""


class PolicyInvalid(GateIndeterminate):
    """The policy is unreadable or malformed: a refusal, never the default."""


# ── strict JSON ────────────────────────────────────────────────────────────


def _loads(payload: bytes, limit: int, scope: str, exc=GateRefused) -> object:
    """ASCII JSON, integers only, no duplicate keys, bounded."""
    if type(payload) is not bytes or not 0 < len(payload) <= limit:
        raise exc(f"{scope}.size")

    def pairs(items):
        result: dict = {}
        for key, value in items:
            if key in result:
                raise exc(f"{scope}.duplicate_key")
            result[key] = value
        return result

    def noninteger(_value):
        raise exc(f"{scope}.number")

    try:
        return json.loads(
            payload.decode("ascii"),
            object_pairs_hook=pairs,
            parse_float=noninteger,
            parse_constant=noninteger,
        )
    except (GateRefused, GateIndeterminate):
        raise
    except (UnicodeDecodeError, ValueError, RecursionError, OverflowError):
        raise exc(f"{scope}.encoding") from None


def _exact_keys(document: object, keys: set[str], scope: str, exc=GateRefused) -> dict:
    if type(document) is not dict or set(document) != keys:
        raise exc(f"{scope}.schema")
    return document


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


# ── policy ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdoptionCondition:
    id: str
    waives: str
    blocks: str
    environment: str
    required_faults: tuple[str, ...]
    register_ref: int

    def as_record(self) -> dict:
        return {
            "id": self.id,
            "waives": self.waives,
            "blocks": self.blocks,
            "environment": self.environment,
            "required_faults": list(self.required_faults),
            "register_ref": self.register_ref,
        }


@dataclass(frozen=True)
class GateException:
    facility: str
    version: str
    gate: str
    ruling_register: str
    ruling_version: int
    required_evidence: tuple[str, ...]
    waivable: tuple[str, ...]
    subject_evidence: tuple[str, ...]
    max_age_hours: int
    adoption_conditions: tuple[AdoptionCondition, ...]

    def condition(self, identifier: object) -> AdoptionCondition | None:
        for found in self.adoption_conditions:
            if found.id == identifier:
                return found
        return None


@dataclass(frozen=True)
class Policy:
    default: str
    exceptions: dict[tuple[str, str], GateException]


def _parse_conditions(
    raw: object, waivable: tuple[str, ...], ruling_version: int, exc
) -> tuple[AdoptionCondition, ...]:
    """Strict: every waivable id has exactly one condition, nothing else."""
    if type(raw) is not list:
        raise exc("gate_policy.adoption_conditions")
    found: list[AdoptionCondition] = []
    for entry in raw:
        item = _exact_keys(
            entry,
            {
                "id",
                "waives",
                "blocks",
                "environment",
                "required_faults",
                "register_ref",
            },
            "gate_policy.adoption_condition",
            exc,
        )
        faults = item["required_faults"]
        if (
            type(item["id"]) is not str
            or _CONDITION_ID.fullmatch(item["id"]) is None
            or item["waives"] not in waivable
            or item["blocks"] != CONDITION_BLOCKS
            or item["environment"] != CONDITION_ENVIRONMENT
            or type(faults) is not list
            or any(type(fault) is not str for fault in faults)
            or len(set(faults)) != len(faults)
            or set(faults) != CONDITION_FAULTS
            or type(item["register_ref"]) is not int
            or item["register_ref"] != ruling_version
        ):
            raise exc("gate_policy.adoption_condition")
        found.append(
            AdoptionCondition(
                id=item["id"],
                waives=item["waives"],
                blocks=item["blocks"],
                environment=item["environment"],
                required_faults=tuple(faults),
                register_ref=item["register_ref"],
            )
        )
    if len({each.id for each in found}) != len(found) or sorted(
        each.waives for each in found
    ) != sorted(waivable):
        raise exc("gate_policy.adoption_condition")
    return tuple(found)


def parse_policy(payload: bytes) -> Policy:
    """Refuse every shape that is not the one designed. Exit 2 on any defect."""
    exc = PolicyInvalid
    document = _exact_keys(
        _loads(payload, MAX_POLICY_BYTES, "gate_policy", exc),
        {"schema", "default", "exceptions"},
        "gate_policy",
        exc,
    )
    if document["schema"] != POLICY_SCHEMA:
        raise exc("gate_policy.schema")
    if document["default"] != GATE_LANE3:
        raise exc("gate_policy.default")
    entries = document["exceptions"]
    if type(entries) is not list:
        raise exc("gate_policy.exceptions")
    exceptions: dict[tuple[str, str], GateException] = {}
    for raw in entries:
        entry = _exact_keys(
            raw,
            {
                "facility",
                "version",
                "gate",
                "ruling",
                "required_evidence",
                "waivable",
                "subject_evidence",
                "max_age_hours",
                "adoption_conditions",
            },
            "gate_policy.exception",
            exc,
        )
        facility, version = entry["facility"], entry["version"]
        if facility != FACILITY:
            raise exc("gate_policy.facility")
        if type(version) is not str or _VERSION.fullmatch(version) is None:
            # No wildcard, range or prefix can satisfy this pattern.
            raise exc("gate_policy.version")
        if entry["gate"] != GATE_2:
            raise exc("gate_policy.gate")
        ruling = _exact_keys(
            entry["ruling"], {"register", "version"}, "gate_policy.ruling", exc
        )
        if (
            ruling["register"] != RULING_REGISTER
            or type(ruling["version"]) is not int
            or ruling["version"] < 1
        ):
            raise exc("gate_policy.ruling")
        evidence = entry["required_evidence"]
        if (
            type(evidence) is not list
            or not evidence
            or len(set(evidence)) != len(evidence)
            or any(
                type(item) is not str or _EVIDENCE_ID.fullmatch(item) is None
                for item in evidence
            )
            or E3_ID not in evidence
        ):
            raise exc("gate_policy.required_evidence")
        waivable = entry["waivable"]
        if (
            type(waivable) is not list
            or len(set(map(str, waivable))) != len(waivable)
            or any(
                type(item) is not str or item not in evidence or item in NEVER_WAIVABLE
                for item in waivable
            )
        ):
            raise exc("gate_policy.waivable")
        subjects = entry["subject_evidence"]
        if (
            type(subjects) is not list
            or len(set(map(str, subjects))) != len(subjects)
            or any(type(item) is not str or item not in evidence for item in subjects)
        ):
            raise exc("gate_policy.subject_evidence")
        max_age = entry["max_age_hours"]
        if type(max_age) is not int or not 1 <= max_age <= MAX_AGE_HOURS_LIMIT:
            raise exc("gate_policy.max_age_hours")
        conditions = _parse_conditions(
            entry["adoption_conditions"], tuple(waivable), ruling["version"], exc
        )
        key = (facility, version)
        if key in exceptions:
            raise exc("gate_policy.duplicate_exception")
        exceptions[key] = GateException(
            facility=facility,
            version=version,
            gate=GATE_2,
            ruling_register=ruling["register"],
            ruling_version=ruling["version"],
            required_evidence=tuple(evidence),
            waivable=tuple(waivable),
            subject_evidence=tuple(subjects),
            max_age_hours=max_age,
            adoption_conditions=conditions,
        )
    return Policy(default=GATE_LANE3, exceptions=exceptions)


def load_policy() -> Policy:
    try:
        payload = POLICY_PATH.read_bytes()
    except OSError:
        raise PolicyInvalid("gate_policy.unavailable") from None
    return parse_policy(payload)


def select_gate(policy: Policy, facility: str, version: str) -> str:
    """Exact (facility, version) match, else the default. No other relation."""
    found = policy.exceptions.get((facility, version))
    return found.gate if found is not None else policy.default


def _require_selected(policy: Policy, facility: str, version: str) -> GateException:
    found = policy.exceptions.get((facility, version))
    if found is None or select_gate(policy, facility, version) != GATE_2:
        raise GateRefused("gate2_not_selected")
    return found


# ── signature ──────────────────────────────────────────────────────────────


def _read_trust(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as source:
            payload = source.read(limit + 1)
    except OSError:
        raise GateIndeterminate("gate2_trust.unavailable") from None
    if len(payload) > limit:
        raise GateRefused("gate2_trust.too_large")
    return payload


def parse_signer(signers: bytes) -> str:
    """The one enrolled key (base64 blob), or a refusal. Empty is unenrolled."""
    if not signers:
        raise GateRefused("gate2_signer_unenrolled")
    text = _trust_bytes(signers, MAX_SIGNERS_BYTES)
    lines = text.splitlines()
    if len(lines) != 1:
        raise GateRefused("gate2_signer.schema")
    parts = lines[0].split(" ")
    if len(parts) != 4 or parts[0] != PRINCIPAL or parts[2] != "ssh-ed25519":
        raise GateRefused("gate2_signer.schema")
    options: dict[str, str] = {}
    for option in parts[1].split(","):
        match = re.fullmatch(r'([a-z-]+)="([^"\s,]+)"', option)
        if match is None or match[1] in options:
            raise GateRefused("gate2_signer.options")
        options[match[1]] = match[2]
    if (
        set(options) != {"namespaces", "valid-after", "valid-before"}
        or options["namespaces"] != NAMESPACE
    ):
        # A key enrolled for any other namespace (Lane 3's included) is refused.
        raise GateRefused("gate2_signer.options")
    moments = []
    for name in ("valid-after", "valid-before"):
        if _SSH_TIME.fullmatch(options[name]) is None:
            raise GateRefused("gate2_signer.time")
        try:
            moments.append(datetime.strptime(options[name], "%Y%m%d%H%M%SZ"))
        except ValueError:
            raise GateRefused("gate2_signer.time") from None
    if moments[0] >= moments[1]:
        raise GateRefused("gate2_signer.time")
    _public_key(parts[3])
    return parts[3]


def parse_revoked(revoked: bytes) -> frozenset[str]:
    text = _trust_bytes(revoked, MAX_REVOKED_BYTES)
    keys = set()
    for line in text.splitlines():
        parts = line.split(" ")
        if len(parts) != 2 or parts[0] != "ssh-ed25519":
            raise GateRefused("gate2_revocation.schema")
        _public_key(parts[1])
        keys.add(parts[1])
    return frozenset(keys)


def fingerprint(key_blob: str) -> str:
    raw = base64.b64decode(key_blob, validate=True)
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode(
        "ascii"
    ).rstrip("=")


def _require_tool() -> None:
    """A fixed, root-owned, non-symlink regular file; never a PATH lookup."""
    try:
        metadata = os.lstat(_SSH_KEYGEN)
    except OSError:
        raise GateIndeterminate("gate2_tool.unavailable") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o022
        or not metadata.st_mode & 0o111
        or metadata.st_uid != 0
    ):
        raise GateRefused("gate2_tool.unsafe")


def verify_signature(manifest: bytes, signature: bytes) -> str:
    """SSHSIG over the exact manifest bytes. Returns the signer fingerprint."""
    signers = _read_trust(SIGNERS_PATH, MAX_SIGNERS_BYTES)
    key_blob = parse_signer(signers)
    if type(signature) is not bytes or not 0 < len(signature) <= MAX_SIGNATURE_BYTES:
        raise GateRefused("gate2_signature.size")
    revoked = _read_trust(REVOKED_PATH, MAX_REVOKED_BYTES)
    if key_blob in parse_revoked(revoked):
        raise GateRefused("gate2_signer_revoked")
    _require_tool()
    try:
        with tempfile.TemporaryDirectory(prefix="gate2-evidence-") as directory:
            allowed = Path(directory) / "allowed"
            sigfile = Path(directory) / "signature"
            revfile = Path(directory) / "revoked"
            for path, raw in ((allowed, signers), (sigfile, signature)):
                with path.open("xb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(raw)
            command = [
                _SSH_KEYGEN,
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
                input=manifest,
                shell=False,
                timeout=10,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    except subprocess.TimeoutExpired:
        raise GateIndeterminate("gate2_tool.timeout") from None
    except OSError:
        raise GateIndeterminate("gate2_tool.unavailable") from None
    if completed.returncode != 0:
        raise GateRefused("gate2_signature_invalid")
    return fingerprint(key_blob)


# ── manifest ───────────────────────────────────────────────────────────────


def _field(value: object, pattern: re.Pattern[str], code: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise GateRefused(code)
    return value


def _utcnow() -> datetime:
    """Naive UTC now; a seam so tests can fix the clock."""
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _check_time(
    value: object, code: str = "gate2_manifest.item.recorded_at"
) -> datetime:
    _field(value, _TIME, code)
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise GateRefused(code) from None


def _check_freshness(document: dict, policy: GateException) -> None:
    """Signed evidence expires: bounded window, current, and recorded before issue."""
    issued = _check_time(document["issued_at"], "gate2_manifest.issued_at")
    expires = _check_time(document["expires_at"], "gate2_manifest.expires_at")
    if expires <= issued:
        raise GateRefused("gate2_manifest.validity_window")
    if expires - issued > timedelta(hours=policy.max_age_hours):
        raise GateRefused("gate2_manifest.over_max_age")
    now = _utcnow()
    if issued > now:
        raise GateRefused("gate2_manifest.not_yet_valid")
    if now >= expires:
        raise GateRefused("gate2_manifest.expired")
    for item in document["evidence"]:
        if _check_time(item["recorded_at"]) > issued:
            raise GateRefused("gate2_manifest.item.recorded_after_issue")


def _check_items(manifest: dict, policy: GateException) -> dict[str, dict]:
    required = policy.required_evidence
    raw_items = manifest["evidence"]
    if type(raw_items) is not list:
        raise GateRefused("gate2_manifest.evidence")
    by_id: dict[str, dict] = {}
    for raw in raw_items:
        if type(raw) is not dict or type(raw.get("id")) is not str:
            raise GateRefused("gate2_manifest.item.schema")
        identifier = raw["id"]
        if identifier not in required:
            raise GateRefused("gate2_manifest.item.unknown")
        if identifier in by_id:
            raise GateRefused("gate2_manifest.item.duplicate")
        by_id[identifier] = raw
    if set(by_id) != set(required):
        raise GateRefused("gate2_manifest.item.missing")

    base = {"id", "sha256", "status", "reviewed_by", "recorded_at", "register_ref"}
    for identifier in required:
        item = by_id[identifier]
        status = item.get("status")
        if status not in STATUSES:
            raise GateRefused("gate2_manifest.item.status")
        keys = set(base)
        if identifier in policy.subject_evidence:
            keys.add("subject_sha256")
        if status == "WAIVED":
            keys.add("waiver")
        _exact_keys(item, keys, "gate2_manifest.item")
        _field(item["sha256"], _DIGEST, "gate2_manifest.item.sha256")
        _field(item["reviewed_by"], _PERSON, "gate2_manifest.item.reviewed_by")
        _field(item["register_ref"], _REF, "gate2_manifest.item.register_ref")
        _check_time(item["recorded_at"])
        if identifier in policy.subject_evidence:
            # The record digest and the subject digest are different claims:
            # RECOVERY's subject is the kit manifest digest.
            _field(
                item["subject_sha256"], _DIGEST, "gate2_manifest.item.subject_sha256"
            )
        if status == "WAIVED":
            # The waivable ids are POLICY data; a manifest cannot extend them.
            if identifier not in policy.waivable:
                raise GateRefused("gate2_manifest.item.waiver_not_allowed")
            waiver = _exact_keys(
                item["waiver"],
                {"register_ref", "condition_id"},
                "gate2_manifest.item.waiver",
            )
            condition = policy.condition(waiver["condition_id"])
            if condition is None or condition.waives != identifier:
                raise GateRefused("gate2_manifest.item.waiver_condition")
            if (
                type(waiver["register_ref"]) is not int
                or waiver["register_ref"] != condition.register_ref
            ):
                raise GateRefused("gate2_manifest.item.waiver_register_ref")
    return by_id


def check_manifest(
    manifest: bytes,
    policy_exception: GateException,
    *,
    candidate_sha256: str | None,
    candidate_source_sha: str | None,
) -> dict[str, dict]:
    """Validate the signed bytes against the policy. Returns items by id.

    The candidate bindings are skipped only by `recheck-gate2`, which has no
    candidate coordinates and re-verifies the build job's decision instead.
    """
    document = _loads(manifest, MAX_MANIFEST_BYTES, "gate2_manifest")
    if type(document) is not dict:
        raise GateRefused("gate2_manifest.schema")
    # The signature covers these exact bytes, so only canonical bytes are
    # accepted: one encoding per document, never two spellings of one claim.
    if canonical_result_bytes(document) != manifest:
        raise GateRefused("gate2_manifest.noncanonical")
    _exact_keys(
        document,
        {
            "schema",
            "facility",
            "version",
            "candidate",
            "ruling",
            "issued_at",
            "expires_at",
            "evidence",
        },
        "gate2_manifest",
    )
    if document["schema"] != MANIFEST_SCHEMA:
        raise GateRefused("gate2_manifest.schema")
    if (
        document["facility"] != policy_exception.facility
        or document["version"] != policy_exception.version
    ):
        raise GateRefused("gate2_manifest.identity")
    candidate = _exact_keys(
        document["candidate"],
        {"artifact_sha256", "source_sha"},
        "gate2_manifest.candidate",
    )
    artifact = _HEX64.fullmatch(str(candidate["artifact_sha256"]))
    if artifact is None:
        raise GateRefused("gate2_manifest.candidate")
    _field(candidate["source_sha"], _REVISION, "gate2_manifest.candidate")
    if candidate_sha256 is not None:
        wanted = _HEX64.fullmatch(candidate_sha256)
        if wanted is None or wanted[1] != artifact[1]:
            raise GateRefused("gate2_manifest.candidate_digest")
    if candidate_source_sha is not None and (
        candidate["source_sha"] != candidate_source_sha
    ):
        raise GateRefused("gate2_manifest.candidate_source")
    ruling = _exact_keys(
        document["ruling"], {"register", "version"}, "gate2_manifest.ruling"
    )
    if (
        ruling["register"] != policy_exception.ruling_register
        or type(ruling["version"]) is not int
        or ruling["version"] != policy_exception.ruling_version
    ):
        raise GateRefused("gate2_manifest.ruling")
    items = _check_items(document, policy_exception)
    _check_freshness(document, policy_exception)
    return items


# ── E3 machine re-check ────────────────────────────────────────────────────


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            shell=False,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise GateIndeterminate("gate2_e3.git_unavailable") from None


def recheck_e3(source_sha: str, e3_python: str) -> str:
    """Run the T14 isolation test at the candidate source commit.

    Proves, at the exact bytes being published, that the deploy, rollback and
    recovery bytes equal the pre-change commit. Returns the baseline file's
    digest at that commit.
    """
    if _REVISION.fullmatch(source_sha) is None:
        raise GateRefused("gate2_e3.source_sha")
    if _git("cat-file", "-e", f"{source_sha}^{{commit}}", cwd=REPO_ROOT).returncode:
        raise GateRefused("gate2_e3.source_unknown")
    scratch = Path(tempfile.mkdtemp(prefix="gate2-e3-"))
    tree = scratch / "tree"
    try:
        added = _git(
            "worktree", "add", "--detach", str(tree), source_sha, cwd=REPO_ROOT
        )
        if added.returncode != 0:
            raise GateIndeterminate("gate2_e3.worktree")
        baseline = tree / E3_BASELINE
        try:
            metadata = os.lstat(baseline)
        except OSError:
            raise GateRefused("gate2_e3.baseline_missing") from None
        if not stat.S_ISREG(metadata.st_mode):
            raise GateRefused("gate2_e3.baseline_missing")
        baseline_bytes = baseline.read_bytes()
        try:
            document = json.loads(baseline_bytes.decode("utf-8"))
            base_commit = document["base_commit"]
        except (UnicodeDecodeError, ValueError, KeyError, TypeError):
            raise GateRefused("gate2_e3.baseline_schema") from None
        if type(base_commit) is not str or _REVISION.fullmatch(base_commit) is None:
            raise GateRefused("gate2_e3.baseline_schema")
        ancestry = _git(
            "merge-base", "--is-ancestor", base_commit, source_sha, cwd=REPO_ROOT
        )
        if ancestry.returncode == 1:
            raise GateRefused("gate2_e3.base_not_ancestor")
        if ancestry.returncode != 0:
            raise GateIndeterminate("gate2_e3.ancestry_unanswerable")
        if not (tree / E3_TEST).is_file():
            raise GateRefused("gate2_e3.test_missing")
        try:
            completed = subprocess.run(
                [
                    e3_python,
                    "-m",
                    "pytest",
                    "-q",
                    "--noconftest",
                    "-p",
                    "no:cacheprovider",
                    E3_TEST,
                ],
                cwd=tree,
                shell=False,
                timeout=900,
                env={
                    "LC_ALL": "C",
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise GateIndeterminate("gate2_e3.runner_unavailable") from None
        if completed.returncode != 0:
            raise GateRefused("gate2_e3.isolation_failed")
        return _digest(baseline_bytes)
    finally:
        _git("worktree", "remove", "--force", str(tree), cwd=REPO_ROOT)
        _git("worktree", "prune", cwd=REPO_ROOT)
        shutil.rmtree(scratch, ignore_errors=True)


# ── commands ───────────────────────────────────────────────────────────────


def _decode_env(name: str, limit: int) -> bytes:
    encoded = os.environ.get(name, "")
    if not encoded:
        raise GateRefused("gate2_input.unavailable")
    if len(encoded) > 4 * ((limit + 2) // 3):
        raise GateRefused("gate2_input.too_large")
    try:
        payload = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeError, ValueError, binascii.Error):
        raise GateRefused("gate2_input.encoding") from None
    if (
        not payload
        or len(payload) > limit
        or base64.b64encode(payload).decode("ascii") != encoded
    ):
        raise GateRefused("gate2_input.encoding")
    return payload


def cmd_select(args: argparse.Namespace) -> int:
    policy = load_policy()
    print(f"gate={select_gate(policy, args.facility, args.version)}")
    return 0


def _record_item(item: dict) -> dict:
    """Ids and digests only, plus the waiver's register ref and condition id."""
    shown = {"id": item["id"], "sha256": item["sha256"], "status": item["status"]}
    if "subject_sha256" in item:
        shown["subject_sha256"] = item["subject_sha256"]
    if item["status"] == "WAIVED":
        shown["waiver"] = {
            "register_ref": item["waiver"]["register_ref"],
            "condition_id": item["waiver"]["condition_id"],
        }
    return shown


def cmd_verify_gate2(args: argparse.Namespace) -> int:
    policy = load_policy()
    selected = _require_selected(policy, args.facility, args.version)
    manifest = _decode_env(EVIDENCE_ENV, MAX_MANIFEST_BYTES)
    signature = _decode_env(SIGNATURE_ENV, MAX_SIGNATURE_BYTES)
    signer = verify_signature(manifest, signature)
    items = check_manifest(
        manifest,
        selected,
        candidate_sha256=args.candidate_sha256,
        candidate_source_sha=args.candidate_source_sha,
    )
    stamps = json.loads(manifest)
    issued_at, expires_at = stamps["issued_at"], stamps["expires_at"]
    baseline_digest = recheck_e3(
        args.candidate_source_sha, os.path.abspath(args.e3_python)
    )
    if items[E3_ID]["sha256"] != baseline_digest:
        raise GateRefused("gate2_e3.digest_mismatch")

    manifest_digest = _digest(manifest)
    signature_digest = _digest(signature)
    record = {
        "gate": GATE_2,
        "manifest_sha256": manifest_digest,
        "signature_sha256": signature_digest,
        "signer_fingerprint": signer,
        "e3_baseline_sha256": baseline_digest,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "evidence": [_record_item(items[i]) for i in selected.required_evidence],
    }
    out = Path(args.out)
    try:
        out.mkdir(parents=True, exist_ok=True)
        (out / "manifest.json").write_bytes(manifest)
        (out / "manifest.sshsig").write_bytes(signature)
        (out / "gate.json").write_bytes(canonical_result_bytes(record))
    except OSError:
        raise GateIndeterminate("gate2_output.unavailable") from None
    print(f"gate={GATE_2}")
    print(f"gate2_manifest_sha256={manifest_digest}")
    print(f"gate2_signature_sha256={signature_digest}")
    print(f"gate2_signer_fingerprint={signer}")
    print(f"gate2_e3_baseline_sha256={baseline_digest}")
    return 0


def cmd_recheck_gate2(args: argparse.Namespace) -> int:
    policy = load_policy()
    selected = _require_selected(policy, args.facility, args.version)
    expected_manifest = os.environ.get(EXPECTED_MANIFEST_ENV, "")
    expected_signature = os.environ.get(EXPECTED_SIGNATURE_ENV, "")
    for value in (expected_manifest, expected_signature):
        if _DIGEST.fullmatch(value) is None:
            raise GateRefused("gate2_recheck.expected")
    manifest = _decode_env(EVIDENCE_ENV, MAX_MANIFEST_BYTES)
    signature = _decode_env(SIGNATURE_ENV, MAX_SIGNATURE_BYTES)
    if _digest(manifest) != expected_manifest:
        raise GateRefused("gate2_recheck.manifest_digest")
    if _digest(signature) != expected_signature:
        raise GateRefused("gate2_recheck.signature_digest")
    verify_signature(manifest, signature)
    check_manifest(manifest, selected, candidate_sha256=None, candidate_source_sha=None)
    print("gate2_recheck=verified")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("select", help="print the publication gate for a version")
    p.add_argument("facility")
    p.add_argument("--version", required=True)
    p.set_defaults(func=cmd_select)

    p = sub.add_parser(
        "verify-gate2", help="authenticate and validate the Gate 2 evidence"
    )
    p.add_argument("facility")
    p.add_argument("--version", required=True)
    p.add_argument("--candidate-sha256", required=True)
    p.add_argument("--candidate-source-sha", required=True)
    p.add_argument("--out", required=True)
    p.add_argument(
        "--e3-python",
        default=sys.executable,
        help="interpreter that has pytest, for the E3 re-check only",
    )
    p.set_defaults(func=cmd_verify_gate2)

    p = sub.add_parser(
        "recheck-gate2", help="re-verify the Gate 2 evidence before publication"
    )
    p.add_argument("facility")
    p.add_argument("--version", required=True)
    p.set_defaults(func=cmd_recheck_gate2)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (GateRefused, OfflineResultRefused) as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 1
    except GateIndeterminate as indeterminate:
        print(f"INDETERMINATE: {indeterminate}", file=sys.stderr)
        return 2
    except Exception:
        print("INDETERMINATE: gate_unexpected", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
