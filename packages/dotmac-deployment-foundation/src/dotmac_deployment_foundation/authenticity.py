"""Authenticated, domain-separated deployment evidence.

The released controller and an application authorization have different trust
purposes.  This module keeps them different in the type system, in the trust
policy, and in the bytes supplied to Ed25519.  Verification uses only pinned
public keys and an absolute, pinned OpenSSL executable; private key material is
neither represented nor read here.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
import subprocess  # nosec B404 -- argv lists, shell=False; see below
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import urlsplit

from .errors import SpecError, UnknownFieldError, UnknownSchemaError

WORKFLOW_RUN_SCHEMA: Final = "GitHubWorkflowRunV1"
RELEASE_ARTIFACT_SCHEMA: Final = "DeploymentControllerReleaseArtifact.v1"
RELEASE_EVIDENCE_SCHEMA: Final = "DeploymentControllerReleaseEvidence.v1"
AUTHORIZATION_EVIDENCE_SCHEMA: Final = "DeploymentAuthorizationEvidence.v1"
HISTORY_SNAPSHOT_SCHEMA: Final = "ApplicationHistorySnapshot.v1"
SIGNATURE_SCHEMA: Final = "DetachedEvidenceSignature.v1"
TRUST_POLICY_SCHEMA: Final = "DeploymentEvidenceTrustPolicy.v1"

RELEASE_EVIDENCE_DOMAIN: Final = b"DOTMAC deployment release evidence v1"
AUTHORIZATION_EVIDENCE_DOMAIN: Final = b"DOTMAC deployment authorization evidence v1"

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORKFLOW = re.compile(r"^\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml$")
_KEY_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:a[0-9]+)?$")
_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]+/[a-z0-9][a-z0-9.+-]+$")
_EVENT = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_PROTECTED_REF = re.compile(r"^refs/(?:heads|tags)/[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_WORKFLOW_REF = re.compile(r"^(?:[0-9a-f]{40}|[A-Za-z0-9][A-Za-z0-9._/-]{0,254})$")


def strict_json_loads(text: str) -> object:
    """Parse evidence JSON while refusing duplicate fields at every depth."""

    def object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise SpecError(f"duplicate JSON field {key!r}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=object_from_pairs)


def canonical_json_bytes(document: dict[str, object]) -> bytes:
    """Return the one JSON representation whose bytes are signed."""

    return json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _object(value: object, *, name: str, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SpecError(f"{name} must be an object")
    document = dict(value)
    missing = sorted(fields - set(document))
    unknown = sorted(set(document) - fields)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise UnknownFieldError(f"{name}: {', '.join(details)}")
    return document


def _text(value: object, name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SpecError(f"invalid {name}")
    return value


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SpecError(f"{name} must be a non-empty, trimmed string")
    return value


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SpecError(f"{name} must be a positive integer")
    return value


def _schema(document: dict[str, object], expected: str) -> None:
    if document["schema"] != expected:
        raise UnknownSchemaError(
            f"schema is {document['schema']!r}; expected {expected!r}"
        )


def _https_origin(value: object, name: str) -> str:
    origin = _nonempty(value, name)
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SpecError(f"{name} has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise SpecError(f"{name} must be an HTTPS origin without credentials or path")
    canonical = f"https://{parsed.hostname.lower()}"
    if port is not None:
        canonical += f":{port}"
    if origin.rstrip("/") != canonical:
        raise SpecError(f"{name} must use its canonical origin spelling")
    return canonical


def _https_api_origin(value: object, name: str) -> str:
    api_origin = _nonempty(value, name)
    parsed = urlsplit(api_origin)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SpecError(f"{name} has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/api/v3")
        or parsed.query
        or parsed.fragment
    ):
        raise SpecError(f"{name} must be a canonical GitHub HTTPS API base")
    canonical = f"https://{parsed.hostname.lower()}"
    if port is not None:
        canonical += f":{port}"
    canonical += parsed.path
    if api_origin != canonical:
        raise SpecError(f"{name} must use its canonical API base spelling")
    return canonical


def _ref(value: object, name: str, pattern: re.Pattern[str]) -> str:
    ref = _text(value, name, pattern)
    if (
        ".." in ref
        or "//" in ref
        or "@{" in ref
        or "\\" in ref
        or ref.endswith(("/", "."))
        or any(component in {"", ".", ".."} for component in ref.split("/"))
    ):
        raise SpecError(f"invalid {name}")
    return ref


@dataclass(frozen=True, slots=True)
class GitHubReferencedWorkflowV1:
    """Exact resolved code identity of one called reusable workflow."""

    repository: str
    workflow_path: str
    workflow_ref: str
    workflow_revision: str
    workflow_blob_sha256: str

    def __post_init__(self) -> None:
        _text(self.repository, "referenced workflow repository", _REPOSITORY)
        _text(self.workflow_path, "referenced workflow path", _WORKFLOW)
        _ref(self.workflow_ref, "referenced workflow ref", _WORKFLOW_REF)
        _text(self.workflow_revision, "referenced workflow revision", _REVISION)
        _text(self.workflow_blob_sha256, "referenced workflow blob sha256", _DIGEST)

    def to_document(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "workflow_path": self.workflow_path,
            "workflow_ref": self.workflow_ref,
            "workflow_revision": self.workflow_revision,
            "workflow_blob_sha256": self.workflow_blob_sha256,
        }

    @classmethod
    def from_document(cls, value: object) -> GitHubReferencedWorkflowV1:
        document = _object(
            value,
            name="referenced workflow",
            fields=frozenset(
                {
                    "repository",
                    "workflow_path",
                    "workflow_ref",
                    "workflow_revision",
                    "workflow_blob_sha256",
                }
            ),
        )
        return cls(
            repository=_text(
                document["repository"], "referenced workflow repository", _REPOSITORY
            ),
            workflow_path=_text(
                document["workflow_path"], "referenced workflow path", _WORKFLOW
            ),
            workflow_ref=_ref(
                document["workflow_ref"], "referenced workflow ref", _WORKFLOW_REF
            ),
            workflow_revision=_text(
                document["workflow_revision"],
                "referenced workflow revision",
                _REVISION,
            ),
            workflow_blob_sha256=_text(
                document["workflow_blob_sha256"],
                "referenced workflow blob sha256",
                _DIGEST,
            ),
        )


def _referenced_workflows(value: object) -> tuple[GitHubReferencedWorkflowV1, ...]:
    if not isinstance(value, list):
        raise SpecError("referenced_workflows must be a list")
    workflows = tuple(GitHubReferencedWorkflowV1.from_document(item) for item in value)
    documents = [canonical_json_bytes(item.to_document()) for item in workflows]
    if len(documents) != len(set(documents)) or documents != sorted(documents):
        raise SpecError("referenced_workflows must be unique and canonically sorted")
    return workflows


@dataclass(frozen=True, slots=True)
class GitHubWorkflowRunV1:
    """The full immutable identity and terminal result of one workflow run."""

    server_origin: str
    api_origin: str
    repository_id: int
    repository: str
    head_repository_id: int
    head_repository: str
    workflow_id: int
    workflow_path: str
    workflow_revision: str
    workflow_blob_sha256: str
    run_id: int
    run_attempt: int
    event: str
    head_sha: str
    head_ref: str
    referenced_workflows: tuple[GitHubReferencedWorkflowV1, ...]
    status: str
    conclusion: str

    def __post_init__(self) -> None:
        _https_origin(self.server_origin, "server_origin")
        _https_api_origin(self.api_origin, "api_origin")
        _positive_int(self.repository_id, "repository_id")
        _text(self.repository, "repository", _REPOSITORY)
        _positive_int(self.head_repository_id, "head_repository_id")
        _text(self.head_repository, "head_repository", _REPOSITORY)
        _positive_int(self.workflow_id, "workflow_id")
        _text(self.workflow_path, "workflow_path", _WORKFLOW)
        _text(self.workflow_revision, "workflow_revision", _REVISION)
        _text(self.workflow_blob_sha256, "workflow_blob_sha256", _DIGEST)
        _positive_int(self.run_id, "run_id")
        _positive_int(self.run_attempt, "run_attempt")
        _text(self.event, "event", _EVENT)
        _text(self.head_sha, "head_sha", _REVISION)
        _ref(self.head_ref, "head_ref", _PROTECTED_REF)
        workflow_documents = [
            canonical_json_bytes(item.to_document())
            for item in self.referenced_workflows
            if isinstance(item, GitHubReferencedWorkflowV1)
        ]
        if len(workflow_documents) != len(self.referenced_workflows):
            raise SpecError("referenced_workflows contains an invalid identity")
        if len(workflow_documents) != len(set(workflow_documents)) or (
            workflow_documents != sorted(workflow_documents)
        ):
            raise SpecError(
                "referenced_workflows must be unique and canonically sorted"
            )
        if self.status != "completed" or self.conclusion != "success":
            raise SpecError("workflow run must be completed successfully")
        if (
            self.repository_id != self.head_repository_id
            or self.repository != self.head_repository
        ):
            raise SpecError("workflow run must execute from the protected repository")

    def to_document(self) -> dict[str, object]:
        return {
            "schema": WORKFLOW_RUN_SCHEMA,
            "server_origin": self.server_origin,
            "api_origin": self.api_origin,
            "repository_id": self.repository_id,
            "repository": self.repository,
            "head_repository_id": self.head_repository_id,
            "head_repository": self.head_repository,
            "workflow_id": self.workflow_id,
            "workflow_path": self.workflow_path,
            "workflow_revision": self.workflow_revision,
            "workflow_blob_sha256": self.workflow_blob_sha256,
            "run_id": self.run_id,
            "run_attempt": self.run_attempt,
            "event": self.event,
            "head_sha": self.head_sha,
            "head_ref": self.head_ref,
            "referenced_workflows": [
                workflow.to_document() for workflow in self.referenced_workflows
            ],
            "status": self.status,
            "conclusion": self.conclusion,
        }

    @classmethod
    def from_document(cls, value: object) -> GitHubWorkflowRunV1:
        fields = frozenset(
            {
                "schema",
                "server_origin",
                "api_origin",
                "repository_id",
                "repository",
                "head_repository_id",
                "head_repository",
                "workflow_id",
                "workflow_path",
                "workflow_revision",
                "workflow_blob_sha256",
                "run_id",
                "run_attempt",
                "event",
                "head_sha",
                "head_ref",
                "referenced_workflows",
                "status",
                "conclusion",
            }
        )
        document = _object(value, name="workflow run", fields=fields)
        _schema(document, WORKFLOW_RUN_SCHEMA)
        return cls(
            server_origin=_https_origin(document["server_origin"], "server_origin"),
            api_origin=_https_api_origin(document["api_origin"], "api_origin"),
            repository_id=_positive_int(document["repository_id"], "repository_id"),
            repository=_text(document["repository"], "repository", _REPOSITORY),
            head_repository_id=_positive_int(
                document["head_repository_id"], "head_repository_id"
            ),
            head_repository=_text(
                document["head_repository"], "head_repository", _REPOSITORY
            ),
            workflow_id=_positive_int(document["workflow_id"], "workflow_id"),
            workflow_path=_text(document["workflow_path"], "workflow_path", _WORKFLOW),
            workflow_revision=_text(
                document["workflow_revision"], "workflow_revision", _REVISION
            ),
            workflow_blob_sha256=_text(
                document["workflow_blob_sha256"], "workflow_blob_sha256", _DIGEST
            ),
            run_id=_positive_int(document["run_id"], "run_id"),
            run_attempt=_positive_int(document["run_attempt"], "run_attempt"),
            event=_text(document["event"], "event", _EVENT),
            head_sha=_text(document["head_sha"], "head_sha", _REVISION),
            head_ref=_ref(document["head_ref"], "head_ref", _PROTECTED_REF),
            referenced_workflows=_referenced_workflows(
                document["referenced_workflows"]
            ),
            status=_nonempty(document["status"], "status"),
            conclusion=_nonempty(document["conclusion"], "conclusion"),
        )

    def require_exact(self, expected: GitHubWorkflowRunV1) -> None:
        """Refuse a successful but foreign/replayed workflow identity."""

        if self != expected:
            changed = [
                name
                for name in self.__dataclass_fields__
                if getattr(self, name) != getattr(expected, name)
            ]
            raise SpecError(f"foreign workflow run fields: {sorted(changed)}")


@dataclass(frozen=True, slots=True)
class DeploymentControllerReleaseArtifactV1:
    name: str
    media_type: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        _text(self.name, "artifact name", _ARTIFACT_NAME)
        _text(self.media_type, "media_type", _MEDIA_TYPE)
        _positive_int(self.size, "artifact size")
        _text(self.sha256, "artifact sha256", _DIGEST)

    def to_document(self) -> dict[str, object]:
        return {
            "schema": RELEASE_ARTIFACT_SCHEMA,
            "name": self.name,
            "media_type": self.media_type,
            "size": self.size,
            "sha256": self.sha256,
        }

    @classmethod
    def from_document(cls, value: object) -> DeploymentControllerReleaseArtifactV1:
        document = _object(
            value,
            name="release artifact",
            fields=frozenset({"schema", "name", "media_type", "size", "sha256"}),
        )
        _schema(document, RELEASE_ARTIFACT_SCHEMA)
        return cls(
            name=_text(document["name"], "artifact name", _ARTIFACT_NAME),
            media_type=_text(document["media_type"], "media_type", _MEDIA_TYPE),
            size=_positive_int(document["size"], "artifact size"),
            sha256=_text(document["sha256"], "artifact sha256", _DIGEST),
        )


@dataclass(frozen=True, slots=True)
class DeploymentControllerReleaseEvidenceV1:
    workflow_run: GitHubWorkflowRunV1
    distribution: str
    exact_version: str
    tag: str
    source_revision: str
    artifacts: tuple[DeploymentControllerReleaseArtifactV1, ...]

    def __post_init__(self) -> None:
        _nonempty(self.distribution, "distribution")
        _text(self.exact_version, "exact_version", _VERSION)
        _text(self.tag, "tag", _TAG)
        _text(self.source_revision, "source_revision", _REVISION)
        expected_tag = f"{self.distribution}-v{self.exact_version}"
        if self.tag != expected_tag:
            raise SpecError(f"release tag must be {expected_tag!r}")
        if self.source_revision != self.workflow_run.head_sha:
            raise SpecError("release source revision must equal workflow head SHA")
        if not self.artifacts:
            raise SpecError("release evidence must contain artifacts")
        names = [artifact.name for artifact in self.artifacts]
        if len(names) != len(set(names)):
            raise SpecError("release artifact names must be unique")
        if names != sorted(names):
            raise SpecError("release artifacts must be sorted by name")

    @property
    def evidence_digest(self) -> str:
        return _sha256(canonical_json_bytes(self.to_document()))

    def artifact(self, name: str) -> DeploymentControllerReleaseArtifactV1:
        matches = [artifact for artifact in self.artifacts if artifact.name == name]
        if len(matches) != 1:
            raise SpecError(f"release evidence has no exact artifact {name!r}")
        return matches[0]

    def to_document(self) -> dict[str, object]:
        return {
            "schema": RELEASE_EVIDENCE_SCHEMA,
            "workflow_run": self.workflow_run.to_document(),
            "distribution": self.distribution,
            "exact_version": self.exact_version,
            "tag": self.tag,
            "source_revision": self.source_revision,
            "artifacts": [artifact.to_document() for artifact in self.artifacts],
        }

    @classmethod
    def from_document(cls, value: object) -> DeploymentControllerReleaseEvidenceV1:
        document = _object(
            value,
            name="release evidence",
            fields=frozenset(
                {
                    "schema",
                    "workflow_run",
                    "distribution",
                    "exact_version",
                    "tag",
                    "source_revision",
                    "artifacts",
                }
            ),
        )
        _schema(document, RELEASE_EVIDENCE_SCHEMA)
        raw_artifacts = document["artifacts"]
        if not isinstance(raw_artifacts, list):
            raise SpecError("release artifacts must be a list")
        return cls(
            workflow_run=GitHubWorkflowRunV1.from_document(document["workflow_run"]),
            distribution=_nonempty(document["distribution"], "distribution"),
            exact_version=_text(document["exact_version"], "exact_version", _VERSION),
            tag=_text(document["tag"], "tag", _TAG),
            source_revision=_text(
                document["source_revision"], "source_revision", _REVISION
            ),
            artifacts=tuple(
                DeploymentControllerReleaseArtifactV1.from_document(item)
                for item in raw_artifacts
            ),
        )


@dataclass(frozen=True, slots=True)
class ApplicationHistorySnapshotV1:
    """An immutable application checkout/bundle, distinct from the authorizer."""

    server_origin: str
    api_origin: str
    repository_id: int
    repository: str
    object_format: str
    from_revision: str | None
    to_revision: str
    bundle_name: str
    bundle_size: int
    bundle_sha256: str

    def __post_init__(self) -> None:
        _https_origin(self.server_origin, "server_origin")
        _https_api_origin(self.api_origin, "api_origin")
        _positive_int(self.repository_id, "repository_id")
        _text(self.repository, "repository", _REPOSITORY)
        if self.object_format != "sha1":
            raise SpecError("application history object_format must be 'sha1' for v1")
        if self.from_revision is not None:
            _text(self.from_revision, "from_revision", _REVISION)
        _text(self.to_revision, "to_revision", _REVISION)
        _text(self.bundle_name, "bundle_name", _ARTIFACT_NAME)
        _positive_int(self.bundle_size, "bundle_size")
        _text(self.bundle_sha256, "bundle_sha256", _DIGEST)

    @property
    def snapshot_digest(self) -> str:
        return _sha256(canonical_json_bytes(self.to_document()))

    def to_document(self) -> dict[str, object]:
        return {
            "schema": HISTORY_SNAPSHOT_SCHEMA,
            "server_origin": self.server_origin,
            "api_origin": self.api_origin,
            "repository_id": self.repository_id,
            "repository": self.repository,
            "object_format": self.object_format,
            "from_revision": self.from_revision,
            "to_revision": self.to_revision,
            "bundle_name": self.bundle_name,
            "bundle_size": self.bundle_size,
            "bundle_sha256": self.bundle_sha256,
        }

    @classmethod
    def from_document(cls, value: object) -> ApplicationHistorySnapshotV1:
        document = _object(
            value,
            name="application history snapshot",
            fields=frozenset(
                {
                    "schema",
                    "server_origin",
                    "api_origin",
                    "repository_id",
                    "repository",
                    "object_format",
                    "from_revision",
                    "to_revision",
                    "bundle_name",
                    "bundle_size",
                    "bundle_sha256",
                }
            ),
        )
        _schema(document, HISTORY_SNAPSHOT_SCHEMA)
        raw_from = document["from_revision"]
        if raw_from is not None:
            raw_from = _text(raw_from, "from_revision", _REVISION)
        return cls(
            server_origin=_https_origin(document["server_origin"], "server_origin"),
            api_origin=_https_api_origin(document["api_origin"], "api_origin"),
            repository_id=_positive_int(document["repository_id"], "repository_id"),
            repository=_text(document["repository"], "repository", _REPOSITORY),
            object_format=_nonempty(document["object_format"], "object_format"),
            from_revision=raw_from,
            to_revision=_text(document["to_revision"], "to_revision", _REVISION),
            bundle_name=_text(document["bundle_name"], "bundle_name", _ARTIFACT_NAME),
            bundle_size=_positive_int(document["bundle_size"], "bundle_size"),
            bundle_sha256=_text(document["bundle_sha256"], "bundle_sha256", _DIGEST),
        )


@dataclass(frozen=True, slots=True)
class DeploymentAuthorizationEvidenceV1:
    workflow_run: GitHubWorkflowRunV1
    execution_envelope_digest: str
    controller_release_evidence_digest: str
    application_history: ApplicationHistorySnapshotV1

    def __post_init__(self) -> None:
        _text(self.execution_envelope_digest, "execution_envelope_digest", _DIGEST)
        _text(
            self.controller_release_evidence_digest,
            "controller_release_evidence_digest",
            _DIGEST,
        )

    @property
    def evidence_digest(self) -> str:
        return _sha256(canonical_json_bytes(self.to_document()))

    def to_document(self) -> dict[str, object]:
        return {
            "schema": AUTHORIZATION_EVIDENCE_SCHEMA,
            "workflow_run": self.workflow_run.to_document(),
            "execution_envelope_digest": self.execution_envelope_digest,
            "controller_release_evidence_digest": (
                self.controller_release_evidence_digest
            ),
            "application_history": self.application_history.to_document(),
        }

    @classmethod
    def from_document(cls, value: object) -> DeploymentAuthorizationEvidenceV1:
        document = _object(
            value,
            name="authorization evidence",
            fields=frozenset(
                {
                    "schema",
                    "workflow_run",
                    "execution_envelope_digest",
                    "controller_release_evidence_digest",
                    "application_history",
                }
            ),
        )
        _schema(document, AUTHORIZATION_EVIDENCE_SCHEMA)
        return cls(
            workflow_run=GitHubWorkflowRunV1.from_document(document["workflow_run"]),
            execution_envelope_digest=_text(
                document["execution_envelope_digest"],
                "execution_envelope_digest",
                _DIGEST,
            ),
            controller_release_evidence_digest=_text(
                document["controller_release_evidence_digest"],
                "controller_release_evidence_digest",
                _DIGEST,
            ),
            application_history=ApplicationHistorySnapshotV1.from_document(
                document["application_history"]
            ),
        )


class EvidencePurpose(str, Enum):
    RELEASE = "release"
    AUTHORIZATION = "authorization"


_PURPOSE_SCHEMA = {
    EvidencePurpose.RELEASE: RELEASE_EVIDENCE_SCHEMA,
    EvidencePurpose.AUTHORIZATION: AUTHORIZATION_EVIDENCE_SCHEMA,
}
_PURPOSE_DOMAIN = {
    EvidencePurpose.RELEASE: RELEASE_EVIDENCE_DOMAIN,
    EvidencePurpose.AUTHORIZATION: AUTHORIZATION_EVIDENCE_DOMAIN,
}


def _pae(parts: tuple[bytes, ...]) -> bytes:
    """Length-frame signature parts so no two field sequences collide."""

    output = bytearray(f"DOTMAC-PAE-V1 {len(parts)} ".encode("ascii"))
    for part in parts:
        output.extend(str(len(part)).encode("ascii"))
        output.extend(b" ")
        output.extend(part)
    return bytes(output)


def signing_payload_bytes(
    *,
    purpose: EvidencePurpose,
    key_id: str,
    payload_schema: str,
    document: dict[str, object],
) -> bytes:
    """Build the exact bytes an external Ed25519 signer signs."""

    if not isinstance(purpose, EvidencePurpose):
        raise SpecError("unknown evidence signer purpose")
    _text(key_id, "key_id", _KEY_ID)
    expected_schema = _PURPOSE_SCHEMA[purpose]
    if payload_schema != expected_schema or document.get("schema") != expected_schema:
        raise SpecError(
            f"{purpose.value} signatures require payload schema {expected_schema!r}"
        )
    return _pae(
        (
            _PURPOSE_DOMAIN[purpose],
            purpose.value.encode("ascii"),
            key_id.encode("ascii"),
            payload_schema.encode("ascii"),
            canonical_json_bytes(document),
        )
    )


@dataclass(frozen=True, slots=True)
class DetachedEvidenceSignatureV1:
    purpose: EvidencePurpose
    key_id: str
    payload_schema: str
    payload_sha256: str
    signature_b64: str

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, EvidencePurpose):
            raise SpecError("unknown evidence signer purpose")
        _text(self.key_id, "key_id", _KEY_ID)
        if self.payload_schema != _PURPOSE_SCHEMA[self.purpose]:
            raise SpecError("signature purpose and payload schema disagree")
        _text(self.payload_sha256, "payload_sha256", _DIGEST)
        try:
            decoded = base64.b64decode(self.signature_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SpecError("signature_b64 is not canonical base64") from exc
        canonical = base64.b64encode(decoded).decode("ascii")
        if len(decoded) != 64 or canonical != self.signature_b64:
            raise SpecError("signature_b64 must encode one canonical Ed25519 signature")

    def to_document(self) -> dict[str, object]:
        return {
            "schema": SIGNATURE_SCHEMA,
            "algorithm": "ed25519",
            "purpose": self.purpose.value,
            "key_id": self.key_id,
            "payload_schema": self.payload_schema,
            "payload_sha256": self.payload_sha256,
            "signature_b64": self.signature_b64,
        }

    @classmethod
    def from_document(cls, value: object) -> DetachedEvidenceSignatureV1:
        document = _object(
            value,
            name="detached evidence signature",
            fields=frozenset(
                {
                    "schema",
                    "algorithm",
                    "purpose",
                    "key_id",
                    "payload_schema",
                    "payload_sha256",
                    "signature_b64",
                }
            ),
        )
        _schema(document, SIGNATURE_SCHEMA)
        if document["algorithm"] != "ed25519":
            raise SpecError("only Ed25519 evidence signatures are accepted")
        try:
            purpose = EvidencePurpose(document["purpose"])
        except (TypeError, ValueError) as exc:
            raise SpecError(
                f"unknown evidence purpose {document['purpose']!r}"
            ) from exc
        return cls(
            purpose=purpose,
            key_id=_text(document["key_id"], "key_id", _KEY_ID),
            payload_schema=_nonempty(document["payload_schema"], "payload_schema"),
            payload_sha256=_text(document["payload_sha256"], "payload_sha256", _DIGEST),
            signature_b64=_nonempty(document["signature_b64"], "signature_b64"),
        )


@dataclass(frozen=True, slots=True)
class TrustedEvidenceKeyV1:
    key_id: str
    purpose: EvidencePurpose
    public_key_path: Path
    public_key_sha256: str
    public_key_spki_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, EvidencePurpose):
            raise SpecError("unknown evidence signer purpose")
        _text(self.key_id, "key_id", _KEY_ID)
        if not self.public_key_path.is_absolute():
            raise SpecError("public_key_path must be absolute")
        _text(self.public_key_sha256, "public_key_sha256", _DIGEST)
        _text(self.public_key_spki_sha256, "public_key_spki_sha256", _DIGEST)

    def to_document(self) -> dict[str, object]:
        return {
            "key_id": self.key_id,
            "purpose": self.purpose.value,
            "public_key_path": str(self.public_key_path),
            "public_key_sha256": self.public_key_sha256,
            "public_key_spki_sha256": self.public_key_spki_sha256,
        }


@dataclass(frozen=True, slots=True)
class WorkflowAuthorityV1:
    """One protected workflow whose terminal runs may carry evidence."""

    server_origin: str
    api_origin: str
    repository_id: int
    repository: str
    workflow_id: int
    workflow_path: str
    event: str
    protected_ref: str
    referenced_workflows: tuple[GitHubReferencedWorkflowV1, ...]

    def __post_init__(self) -> None:
        _https_origin(self.server_origin, "server_origin")
        _https_api_origin(self.api_origin, "api_origin")
        _positive_int(self.repository_id, "repository_id")
        _text(self.repository, "repository", _REPOSITORY)
        _positive_int(self.workflow_id, "workflow_id")
        _text(self.workflow_path, "workflow_path", _WORKFLOW)
        _text(self.event, "event", _EVENT)
        _ref(self.protected_ref, "protected_ref", _PROTECTED_REF)
        workflow_documents = [
            canonical_json_bytes(item.to_document())
            for item in self.referenced_workflows
            if isinstance(item, GitHubReferencedWorkflowV1)
        ]
        if len(workflow_documents) != len(self.referenced_workflows):
            raise SpecError("referenced_workflows contains an invalid identity")
        if len(workflow_documents) != len(set(workflow_documents)) or (
            workflow_documents != sorted(workflow_documents)
        ):
            raise SpecError(
                "referenced_workflows must be unique and canonically sorted"
            )

    def to_document(self) -> dict[str, object]:
        return {
            "server_origin": self.server_origin,
            "api_origin": self.api_origin,
            "repository_id": self.repository_id,
            "repository": self.repository,
            "workflow_id": self.workflow_id,
            "workflow_path": self.workflow_path,
            "event": self.event,
            "protected_ref": self.protected_ref,
            "referenced_workflows": [
                workflow.to_document() for workflow in self.referenced_workflows
            ],
        }

    @classmethod
    def from_document(cls, value: object) -> WorkflowAuthorityV1:
        document = _object(
            value,
            name="workflow authority",
            fields=frozenset(
                {
                    "server_origin",
                    "api_origin",
                    "repository_id",
                    "repository",
                    "workflow_id",
                    "workflow_path",
                    "event",
                    "protected_ref",
                    "referenced_workflows",
                }
            ),
        )
        return cls(
            server_origin=_https_origin(document["server_origin"], "server_origin"),
            api_origin=_https_api_origin(document["api_origin"], "api_origin"),
            repository_id=_positive_int(document["repository_id"], "repository_id"),
            repository=_text(document["repository"], "repository", _REPOSITORY),
            workflow_id=_positive_int(document["workflow_id"], "workflow_id"),
            workflow_path=_text(document["workflow_path"], "workflow_path", _WORKFLOW),
            event=_text(document["event"], "event", _EVENT),
            protected_ref=_ref(
                document["protected_ref"], "protected_ref", _PROTECTED_REF
            ),
            referenced_workflows=_referenced_workflows(
                document["referenced_workflows"]
            ),
        )

    def matches(self, run: GitHubWorkflowRunV1) -> bool:
        return (
            self.server_origin == run.server_origin
            and self.api_origin == run.api_origin
            and self.repository_id == run.repository_id
            and self.repository == run.repository
            and self.workflow_id == run.workflow_id
            and self.workflow_path == run.workflow_path
            and self.event == run.event
            and self.protected_ref == run.head_ref
            and self.referenced_workflows == run.referenced_workflows
        )


@dataclass(frozen=True, slots=True)
class ApplicationRepositoryAuthorityV1:
    """One application repository allowed to supply signed Git history."""

    server_origin: str
    api_origin: str
    repository_id: int
    repository: str

    def __post_init__(self) -> None:
        _https_origin(self.server_origin, "server_origin")
        _https_api_origin(self.api_origin, "api_origin")
        _positive_int(self.repository_id, "repository_id")
        _text(self.repository, "repository", _REPOSITORY)

    def to_document(self) -> dict[str, object]:
        return {
            "server_origin": self.server_origin,
            "api_origin": self.api_origin,
            "repository_id": self.repository_id,
            "repository": self.repository,
        }

    @classmethod
    def from_document(cls, value: object) -> ApplicationRepositoryAuthorityV1:
        document = _object(
            value,
            name="application repository authority",
            fields=frozenset(
                {"server_origin", "api_origin", "repository_id", "repository"}
            ),
        )
        return cls(
            server_origin=_https_origin(document["server_origin"], "server_origin"),
            api_origin=_https_api_origin(document["api_origin"], "api_origin"),
            repository_id=_positive_int(document["repository_id"], "repository_id"),
            repository=_text(document["repository"], "repository", _REPOSITORY),
        )

    def matches(self, snapshot: ApplicationHistorySnapshotV1) -> bool:
        return (
            self.server_origin == snapshot.server_origin
            and self.api_origin == snapshot.api_origin
            and self.repository_id == snapshot.repository_id
            and self.repository == snapshot.repository
        )


@dataclass(frozen=True, slots=True)
class DeploymentEvidenceTrustPolicyV1:
    openssl_path: Path
    openssl_sha256: str
    git_path: Path
    git_sha256: str
    docker_path: Path
    docker_sha256: str
    keys: tuple[TrustedEvidenceKeyV1, ...]
    release_authorities: tuple[WorkflowAuthorityV1, ...]
    authorization_authorities: tuple[WorkflowAuthorityV1, ...]
    application_repositories: tuple[ApplicationRepositoryAuthorityV1, ...]

    def __post_init__(self) -> None:
        if not self.openssl_path.is_absolute():
            raise SpecError("openssl_path must be absolute")
        _text(self.openssl_sha256, "openssl_sha256", _DIGEST)
        for name, path, digest in (
            ("git", self.git_path, self.git_sha256),
            ("docker", self.docker_path, self.docker_sha256),
        ):
            if not path.is_absolute():
                raise SpecError(f"{name}_path must be absolute")
            _text(digest, f"{name}_sha256", _DIGEST)
        key_ids = [key.key_id for key in self.keys]
        if len(key_ids) != len(set(key_ids)):
            raise SpecError("trust policy key IDs must be globally unique")
        fingerprints: dict[str, EvidencePurpose] = {}
        for key in self.keys:
            prior_purpose = fingerprints.setdefault(
                key.public_key_spki_sha256, key.purpose
            )
            if prior_purpose is not key.purpose:
                raise SpecError(
                    "release and authorization purposes require distinct public keys"
                )
        for purpose in EvidencePurpose:
            if not any(key.purpose is purpose for key in self.keys):
                raise SpecError(f"trust policy has no {purpose.value} key")
        for name, authorities in (
            ("release", self.release_authorities),
            ("authorization", self.authorization_authorities),
            ("application repository", self.application_repositories),
        ):
            if not authorities:
                raise SpecError(f"trust policy has no {name} authority")
            documents = [
                canonical_json_bytes(item.to_document()) for item in authorities
            ]
            if len(documents) != len(set(documents)):
                raise SpecError(f"trust policy repeats a {name} authority")

    def to_document(self) -> dict[str, object]:
        return {
            "schema": TRUST_POLICY_SCHEMA,
            "openssl_path": str(self.openssl_path),
            "openssl_sha256": self.openssl_sha256,
            "git_path": str(self.git_path),
            "git_sha256": self.git_sha256,
            "docker_path": str(self.docker_path),
            "docker_sha256": self.docker_sha256,
            "keys": [key.to_document() for key in self.keys],
            "release_authorities": [
                authority.to_document() for authority in self.release_authorities
            ],
            "authorization_authorities": [
                authority.to_document() for authority in self.authorization_authorities
            ],
            "application_repositories": [
                repository.to_document() for repository in self.application_repositories
            ],
        }

    @classmethod
    def from_document(cls, value: object) -> DeploymentEvidenceTrustPolicyV1:
        document = _object(
            value,
            name="trust policy",
            fields=frozenset(
                {
                    "schema",
                    "openssl_path",
                    "openssl_sha256",
                    "git_path",
                    "git_sha256",
                    "docker_path",
                    "docker_sha256",
                    "keys",
                    "release_authorities",
                    "authorization_authorities",
                    "application_repositories",
                }
            ),
        )
        _schema(document, TRUST_POLICY_SCHEMA)
        raw_keys = document["keys"]
        if not isinstance(raw_keys, list):
            raise SpecError("trust policy keys must be a list")
        keys: list[TrustedEvidenceKeyV1] = []
        for raw_key in raw_keys:
            key_document = _object(
                raw_key,
                name="trusted evidence key",
                fields=frozenset(
                    {
                        "key_id",
                        "purpose",
                        "public_key_path",
                        "public_key_sha256",
                        "public_key_spki_sha256",
                    }
                ),
            )
            try:
                purpose = EvidencePurpose(key_document["purpose"])
            except (TypeError, ValueError) as exc:
                raise SpecError(
                    f"unknown evidence purpose {key_document['purpose']!r}"
                ) from exc
            keys.append(
                TrustedEvidenceKeyV1(
                    key_id=_text(key_document["key_id"], "key_id", _KEY_ID),
                    purpose=purpose,
                    public_key_path=Path(
                        _nonempty(key_document["public_key_path"], "public_key_path")
                    ),
                    public_key_sha256=_text(
                        key_document["public_key_sha256"],
                        "public_key_sha256",
                        _DIGEST,
                    ),
                    public_key_spki_sha256=_text(
                        key_document["public_key_spki_sha256"],
                        "public_key_spki_sha256",
                        _DIGEST,
                    ),
                )
            )

        def object_list(name: str) -> list[object]:
            raw = document[name]
            if not isinstance(raw, list):
                raise SpecError(f"trust policy {name} must be a list")
            return raw

        return cls(
            openssl_path=Path(_nonempty(document["openssl_path"], "openssl_path")),
            openssl_sha256=_text(document["openssl_sha256"], "openssl_sha256", _DIGEST),
            git_path=Path(_nonempty(document["git_path"], "git_path")),
            git_sha256=_text(document["git_sha256"], "git_sha256", _DIGEST),
            docker_path=Path(_nonempty(document["docker_path"], "docker_path")),
            docker_sha256=_text(document["docker_sha256"], "docker_sha256", _DIGEST),
            keys=tuple(keys),
            release_authorities=tuple(
                WorkflowAuthorityV1.from_document(item)
                for item in object_list("release_authorities")
            ),
            authorization_authorities=tuple(
                WorkflowAuthorityV1.from_document(item)
                for item in object_list("authorization_authorities")
            ),
            application_repositories=tuple(
                ApplicationRepositoryAuthorityV1.from_document(item)
                for item in object_list("application_repositories")
            ),
        )

    @classmethod
    def load_root_owned(cls, path: str | Path) -> DeploymentEvidenceTrustPolicyV1:
        policy_path = Path(path)
        _require_root_owned_regular_file(policy_path, executable=False)
        try:
            raw = strict_json_loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SpecError(f"cannot read trust policy {policy_path}: {exc}") from exc
        return cls.from_document(raw)

    def require_key(
        self, *, key_id: str, purpose: EvidencePurpose
    ) -> TrustedEvidenceKeyV1:
        matches = [
            key for key in self.keys if key.key_id == key_id and key.purpose is purpose
        ]
        if len(matches) != 1:
            raise SpecError(f"untrusted {purpose.value} evidence key {key_id!r}")
        return matches[0]

    def require_workflow_authority(
        self, *, run: GitHubWorkflowRunV1, purpose: EvidencePurpose
    ) -> None:
        authorities = (
            self.release_authorities
            if purpose is EvidencePurpose.RELEASE
            else self.authorization_authorities
        )
        if not any(authority.matches(run) for authority in authorities):
            raise SpecError(f"workflow run is not a trusted {purpose.value} authority")

    def require_application_repository(
        self, snapshot: ApplicationHistorySnapshotV1
    ) -> None:
        if not any(
            repository.matches(snapshot) for repository in self.application_repositories
        ):
            raise SpecError("application history names an untrusted repository")


class _EvidenceDocument(Protocol):
    def to_document(self) -> dict[str, object]: ...


def _public_key_spki_sha256(*, openssl_path: Path, public_key_path: Path) -> str:
    """Hash canonical DER SubjectPublicKeyInfo, not PEM presentation bytes."""

    try:
        completed = subprocess.run(  # noqa: S603  # nosec B603 -- absolute, pinned executable, argv LIST, shell=False
            [
                str(openssl_path),
                "pkey",
                "-pubin",
                "-in",
                str(public_key_path),
                "-pubout",
                "-outform",
                "DER",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
            cwd="/",
            env={"LANG": "C", "LC_ALL": "C"},
            close_fds=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SpecError("trusted public key normalization failed closed") from exc
    if completed.returncode != 0 or not completed.stdout:
        raise SpecError("trusted public key is not valid SubjectPublicKeyInfo")
    return _sha256(completed.stdout)


def _require_root_owned_regular_file(path: Path, *, executable: bool) -> bytes:
    if not path.is_absolute():
        raise SpecError(f"trusted path must be absolute: {path}")
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise SpecError(f"cannot inspect trusted path {path}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
        raise SpecError(f"trusted path must be a root-owned regular file: {path}")
    if metadata.st_mode & 0o022:
        raise SpecError(f"trusted path must not be group/world writable: {path}")
    if executable and not metadata.st_mode & 0o111:
        raise SpecError(f"trusted executable is not executable: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SpecError(f"cannot read trusted path {path}: {exc}") from exc


def verify_detached_evidence(
    evidence: _EvidenceDocument,
    signature: DetachedEvidenceSignatureV1,
    *,
    policy: DeploymentEvidenceTrustPolicyV1,
    expected_purpose: EvidencePurpose,
) -> None:
    """Verify evidence with the one trusted key for its exact ID and purpose."""

    if signature.purpose is not expected_purpose:
        raise SpecError("evidence signature has the wrong signer purpose")
    expected_schema = _PURPOSE_SCHEMA[expected_purpose]
    document = evidence.to_document()
    if document.get("schema") != expected_schema:
        raise SpecError("evidence object has the wrong schema for its signer purpose")
    if isinstance(evidence, DeploymentControllerReleaseEvidenceV1):
        policy.require_workflow_authority(
            run=evidence.workflow_run, purpose=EvidencePurpose.RELEASE
        )
    elif isinstance(evidence, DeploymentAuthorizationEvidenceV1):
        policy.require_workflow_authority(
            run=evidence.workflow_run, purpose=EvidencePurpose.AUTHORIZATION
        )
        policy.require_application_repository(evidence.application_history)
    else:
        raise SpecError("evidence object has no trusted authority contract")
    payload = canonical_json_bytes(document)
    if signature.payload_sha256 != _sha256(payload):
        raise SpecError("signed evidence digest does not match the supplied evidence")
    trusted_key = policy.require_key(key_id=signature.key_id, purpose=expected_purpose)
    openssl = _require_root_owned_regular_file(policy.openssl_path, executable=True)
    if _sha256(openssl) != policy.openssl_sha256:
        raise SpecError("trusted OpenSSL binary digest does not match policy")
    public_key = _require_root_owned_regular_file(
        trusted_key.public_key_path, executable=False
    )
    if _sha256(public_key) != trusted_key.public_key_sha256:
        raise SpecError("trusted public key digest does not match policy")
    observed_spki = _public_key_spki_sha256(
        openssl_path=policy.openssl_path,
        public_key_path=trusted_key.public_key_path,
    )
    if observed_spki != trusted_key.public_key_spki_sha256:
        raise SpecError("trusted public key SPKI digest does not match policy")

    signed_bytes = signing_payload_bytes(
        purpose=signature.purpose,
        key_id=signature.key_id,
        payload_schema=signature.payload_schema,
        document=document,
    )
    raw_signature = base64.b64decode(signature.signature_b64, validate=True)
    try:
        with tempfile.NamedTemporaryFile(
            prefix="dotmac-evidence-", suffix=".sig"
        ) as handle:
            handle.write(raw_signature)
            handle.flush()
            completed = subprocess.run(  # noqa: S603  # nosec B603 -- absolute, pinned executable, argv LIST, shell=False
                [
                    str(policy.openssl_path),
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-inkey",
                    str(trusted_key.public_key_path),
                    "-sigfile",
                    handle.name,
                    "-rawin",
                ],
                input=signed_bytes,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=15,
                cwd="/",
                env={"LANG": "C", "LC_ALL": "C"},
                close_fds=True,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SpecError(f"evidence signature verifier failed closed: {exc}") from exc
    if completed.returncode != 0:
        raise SpecError("Ed25519 evidence signature verification failed")
