#!/usr/bin/env python3
"""Root-owned bootstrap for an authenticated deployment controller.

This file is deliberately standalone.  It MUST NOT import Foundation or any
application package: those bytes are untrusted until the signed release and
authorization evidence have been verified here.
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
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

WORKFLOW_RUN_SCHEMA: Final = "GitHubWorkflowRunV1"
RELEASE_ARTIFACT_SCHEMA: Final = "DeploymentControllerReleaseArtifact.v1"
RELEASE_EVIDENCE_SCHEMA: Final = "DeploymentControllerReleaseEvidence.v1"
AUTHORIZATION_EVIDENCE_SCHEMA: Final = "DeploymentAuthorizationEvidence.v1"
HISTORY_SNAPSHOT_SCHEMA: Final = "ApplicationHistorySnapshot.v1"
SIGNATURE_SCHEMA: Final = "DetachedEvidenceSignature.v1"
TRUST_POLICY_SCHEMA: Final = "DeploymentEvidenceTrustPolicy.v1"
RECEIPT_SCHEMA: Final = "DeploymentControllerReleaseReceipt.v1"
ENVELOPE_SCHEMA: Final = "DeploymentExecutionEnvelope.v1"
BOOTSTRAP_CONTEXT_SCHEMA: Final = "AuthenticatedDeploymentBootstrapContext.v1"

RELEASE_PURPOSE: Final = "release"
AUTHORIZATION_PURPOSE: Final = "authorization"
RELEASE_DOMAIN: Final = b"DOTMAC deployment release evidence v1"
AUTHORIZATION_DOMAIN: Final = b"DOTMAC deployment authorization evidence v1"
CONTROLLER_DISTRIBUTION: Final = "dotmac-deployment-foundation"
RELEASE_WORKFLOW_PATH: Final = ".github/workflows/release-facility.yml"
RELEASE_EVENT: Final = "workflow_dispatch"
LAUNCHER_NAME: Final = "run_deployment_controller.py"
RECEIPT_NAME: Final = "DeploymentControllerReleaseReceipt.v1.json"

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
_MAX_DOCUMENT = 4 * 1024 * 1024
_MAX_ARTIFACT = 1024 * 1024 * 1024


class BootstrapRefused(RuntimeError):
    """The authenticated bootstrap could not prove an exact launch."""


def _strict_json_loads(data: bytes, *, name: str) -> object:
    def object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise BootstrapRefused(f"duplicate {name} field {key!r}")
            result[key] = value
        return result

    try:
        text = data.decode("utf-8")
        return json.loads(text, object_pairs_hook=object_from_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapRefused(f"{name} is not strict UTF-8 JSON: {exc}") from exc


def _canonical_json(document: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise BootstrapRefused(f"evidence is not canonical JSON data: {exc}") from exc


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _typed_digest(kind: str, document: dict[str, object]) -> str:
    return _sha256(_canonical_json({"kind": kind, **document}))


def _object(value: object, *, name: str, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BootstrapRefused(f"{name} must be an object")
    document = dict(value)
    missing = sorted(fields - set(document))
    unknown = sorted(set(document) - fields)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise BootstrapRefused(f"{name}: {', '.join(details)}")
    return document


def _text(value: object, *, name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise BootstrapRefused(f"invalid {name}")
    return value


def _nonempty(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise BootstrapRefused(f"{name} must be a non-empty, trimmed string")
    return value


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BootstrapRefused(f"{name} must be a positive integer")
    return value


def _schema(document: dict[str, object], expected: str, *, name: str) -> None:
    if document["schema"] != expected:
        raise BootstrapRefused(
            f"{name} schema is {document['schema']!r}; expected {expected!r}"
        )


def _https_origin(value: object, *, name: str) -> str:
    origin = _nonempty(value, name=name)
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as exc:
        raise BootstrapRefused(f"{name} has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise BootstrapRefused(
            f"{name} must be an HTTPS origin without credentials or path"
        )
    canonical = f"https://{parsed.hostname.lower()}"
    if port is not None:
        canonical += f":{port}"
    if origin.rstrip("/") != canonical:
        raise BootstrapRefused(f"{name} must use its canonical origin spelling")
    return canonical


def _https_api_origin(value: object, *, name: str) -> str:
    api_origin = _nonempty(value, name=name)
    parsed = urlsplit(api_origin)
    try:
        port = parsed.port
    except ValueError as exc:
        raise BootstrapRefused(f"{name} has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/api/v3")
        or parsed.query
        or parsed.fragment
    ):
        raise BootstrapRefused(f"{name} must be a canonical GitHub HTTPS API base")
    canonical = f"https://{parsed.hostname.lower()}"
    if port is not None:
        canonical += f":{port}"
    canonical += parsed.path
    if api_origin != canonical:
        raise BootstrapRefused(f"{name} must use its canonical API base spelling")
    return canonical


def _ref(value: object, *, name: str, pattern: re.Pattern[str]) -> str:
    ref = _text(value, name=name, pattern=pattern)
    if (
        ".." in ref
        or "//" in ref
        or "@{" in ref
        or "\\" in ref
        or ref.endswith(("/", "."))
        or any(component in {"", ".", ".."} for component in ref.split("/"))
    ):
        raise BootstrapRefused(f"invalid {name}")
    return ref


@dataclass(frozen=True, slots=True)
class ReferencedWorkflow:
    repository: str
    workflow_path: str
    workflow_ref: str
    workflow_revision: str
    workflow_blob_sha256: str

    @property
    def document(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "workflow_path": self.workflow_path,
            "workflow_ref": self.workflow_ref,
            "workflow_revision": self.workflow_revision,
            "workflow_blob_sha256": self.workflow_blob_sha256,
        }


def _parse_referenced_workflows(value: object) -> tuple[ReferencedWorkflow, ...]:
    if not isinstance(value, list):
        raise BootstrapRefused("referenced_workflows must be a list")
    workflows: list[ReferencedWorkflow] = []
    for item in value:
        document = _object(
            item,
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
        workflows.append(
            ReferencedWorkflow(
                repository=_text(
                    document["repository"],
                    name="referenced workflow repository",
                    pattern=_REPOSITORY,
                ),
                workflow_path=_text(
                    document["workflow_path"],
                    name="referenced workflow path",
                    pattern=_WORKFLOW,
                ),
                workflow_ref=_ref(
                    document["workflow_ref"],
                    name="referenced workflow ref",
                    pattern=_WORKFLOW_REF,
                ),
                workflow_revision=_text(
                    document["workflow_revision"],
                    name="referenced workflow revision",
                    pattern=_REVISION,
                ),
                workflow_blob_sha256=_text(
                    document["workflow_blob_sha256"],
                    name="referenced workflow blob sha256",
                    pattern=_DIGEST,
                ),
            )
        )
    documents = [_canonical_json(item.document) for item in workflows]
    if len(documents) != len(set(documents)) or documents != sorted(documents):
        raise BootstrapRefused(
            "referenced_workflows must be unique and canonically sorted"
        )
    return tuple(workflows)


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    document: dict[str, object]
    server_origin: str
    api_origin: str
    repository_id: int
    repository: str
    workflow_id: int
    workflow_path: str
    workflow_revision: str
    workflow_blob_sha256: str
    run_id: int
    run_attempt: int
    event: str
    head_sha: str
    head_ref: str
    referenced_workflows: tuple[ReferencedWorkflow, ...]


@dataclass(frozen=True, slots=True)
class Artifact:
    name: str
    media_type: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ReleaseEvidence:
    document: dict[str, object]
    workflow_run: WorkflowRun
    distribution: str
    exact_version: str
    tag: str
    source_revision: str
    artifacts: tuple[Artifact, ...]

    @property
    def evidence_digest(self) -> str:
        return _sha256(_canonical_json(self.document))

    def artifact(self, name: str) -> Artifact:
        matches = [artifact for artifact in self.artifacts if artifact.name == name]
        if len(matches) != 1:
            raise BootstrapRefused(f"release evidence has no exact artifact {name!r}")
        return matches[0]


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    document: dict[str, object]
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

    @property
    def snapshot_digest(self) -> str:
        return _sha256(_canonical_json(self.document))


@dataclass(frozen=True, slots=True)
class AuthorizationEvidence:
    document: dict[str, object]
    workflow_run: WorkflowRun
    execution_envelope_digest: str
    controller_release_evidence_digest: str
    application_history: HistorySnapshot

    @property
    def evidence_digest(self) -> str:
        return _sha256(_canonical_json(self.document))


@dataclass(frozen=True, slots=True)
class Signature:
    purpose: str
    key_id: str
    payload_schema: str
    payload_sha256: str
    raw_signature: bytes


@dataclass(frozen=True, slots=True)
class TrustedKey:
    key_id: str
    purpose: str
    public_key_path: Path
    public_key_sha256: str
    public_key_spki_sha256: str


@dataclass(frozen=True, slots=True)
class WorkflowAuthority:
    server_origin: str
    api_origin: str
    repository_id: int
    repository: str
    workflow_id: int
    workflow_path: str
    event: str
    protected_ref: str
    referenced_workflows: tuple[ReferencedWorkflow, ...]

    def matches(self, run: WorkflowRun) -> bool:
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
class RepositoryAuthority:
    server_origin: str
    api_origin: str
    repository_id: int
    repository: str

    def matches(self, snapshot: HistorySnapshot) -> bool:
        return (
            self.server_origin == snapshot.server_origin
            and self.api_origin == snapshot.api_origin
            and self.repository_id == snapshot.repository_id
            and self.repository == snapshot.repository
        )


@dataclass(frozen=True, slots=True)
class TrustPolicy:
    openssl_path: Path
    openssl_sha256: str
    git_path: Path
    git_sha256: str
    docker_path: Path
    docker_sha256: str
    keys: tuple[TrustedKey, ...]
    release_authorities: tuple[WorkflowAuthority, ...]
    authorization_authorities: tuple[WorkflowAuthority, ...]
    application_repositories: tuple[RepositoryAuthority, ...]

    def require_key(self, *, key_id: str, purpose: str) -> TrustedKey:
        matches = [
            key for key in self.keys if key.key_id == key_id and key.purpose == purpose
        ]
        if len(matches) != 1:
            raise BootstrapRefused(f"untrusted {purpose} evidence key {key_id!r}")
        return matches[0]

    def require_workflow(self, *, run: WorkflowRun, purpose: str) -> None:
        authorities = (
            self.release_authorities
            if purpose == RELEASE_PURPOSE
            else self.authorization_authorities
        )
        if not any(authority.matches(run) for authority in authorities):
            raise BootstrapRefused(f"workflow run is not a trusted {purpose} authority")

    def require_application_repository(self, snapshot: HistorySnapshot) -> None:
        if not any(
            repository.matches(snapshot) for repository in self.application_repositories
        ):
            raise BootstrapRefused("application history names an untrusted repository")


def _parse_workflow_run(value: object, *, name: str) -> WorkflowRun:
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
    document = _object(value, name=name, fields=fields)
    _schema(document, WORKFLOW_RUN_SCHEMA, name=name)
    server_origin = _https_origin(document["server_origin"], name="server_origin")
    api_origin = _https_api_origin(document["api_origin"], name="api_origin")
    repository_id = _positive_int(document["repository_id"], name="repository_id")
    repository = _text(document["repository"], name="repository", pattern=_REPOSITORY)
    head_repository_id = _positive_int(
        document["head_repository_id"], name="head_repository_id"
    )
    head_repository = _text(
        document["head_repository"], name="head_repository", pattern=_REPOSITORY
    )
    workflow_id = _positive_int(document["workflow_id"], name="workflow_id")
    workflow_path = _text(
        document["workflow_path"], name="workflow_path", pattern=_WORKFLOW
    )
    workflow_revision = _text(
        document["workflow_revision"], name="workflow_revision", pattern=_REVISION
    )
    workflow_blob_sha256 = _text(
        document["workflow_blob_sha256"],
        name="workflow_blob_sha256",
        pattern=_DIGEST,
    )
    run_id = _positive_int(document["run_id"], name="run_id")
    run_attempt = _positive_int(document["run_attempt"], name="run_attempt")
    event = _text(document["event"], name="event", pattern=_EVENT)
    head_sha = _text(document["head_sha"], name="head_sha", pattern=_REVISION)
    head_ref = _ref(document["head_ref"], name="head_ref", pattern=_PROTECTED_REF)
    referenced_workflows = _parse_referenced_workflows(document["referenced_workflows"])
    if document["status"] != "completed" or document["conclusion"] != "success":
        raise BootstrapRefused(f"{name} must be completed successfully")
    if repository_id != head_repository_id or repository != head_repository:
        raise BootstrapRefused(f"{name} must execute from the protected repository")
    return WorkflowRun(
        document=document,
        server_origin=server_origin,
        api_origin=api_origin,
        repository_id=repository_id,
        repository=repository,
        workflow_id=workflow_id,
        workflow_path=workflow_path,
        workflow_revision=workflow_revision,
        workflow_blob_sha256=workflow_blob_sha256,
        run_id=run_id,
        run_attempt=run_attempt,
        event=event,
        head_sha=head_sha,
        head_ref=head_ref,
        referenced_workflows=referenced_workflows,
    )


def _parse_artifact(value: object) -> Artifact:
    document = _object(
        value,
        name="release artifact",
        fields=frozenset({"schema", "name", "media_type", "size", "sha256"}),
    )
    _schema(document, RELEASE_ARTIFACT_SCHEMA, name="release artifact")
    return Artifact(
        name=_text(document["name"], name="artifact name", pattern=_ARTIFACT_NAME),
        media_type=_text(
            document["media_type"], name="media_type", pattern=_MEDIA_TYPE
        ),
        size=_positive_int(document["size"], name="artifact size"),
        sha256=_text(document["sha256"], name="artifact sha256", pattern=_DIGEST),
    )


def _parse_release_evidence(data: bytes) -> ReleaseEvidence:
    document = _object(
        _strict_json_loads(data, name="release evidence"),
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
    _schema(document, RELEASE_EVIDENCE_SCHEMA, name="release evidence")
    workflow_run = _parse_workflow_run(
        document["workflow_run"], name="release workflow run"
    )
    distribution = _nonempty(document["distribution"], name="distribution")
    if distribution != CONTROLLER_DISTRIBUTION:
        raise BootstrapRefused(
            f"release distribution must be {CONTROLLER_DISTRIBUTION!r}"
        )
    exact_version = _text(
        document["exact_version"], name="exact_version", pattern=_VERSION
    )
    tag = _text(document["tag"], name="tag", pattern=_TAG)
    if tag != f"{distribution}-v{exact_version}":
        raise BootstrapRefused("release tag does not match distribution and version")
    source_revision = _text(
        document["source_revision"], name="source_revision", pattern=_REVISION
    )
    if source_revision != workflow_run.head_sha:
        raise BootstrapRefused("release source revision does not match workflow SHA")
    if (
        workflow_run.workflow_path != RELEASE_WORKFLOW_PATH
        or workflow_run.event != RELEASE_EVENT
    ):
        raise BootstrapRefused("release evidence names the wrong protected workflow")
    raw_artifacts = document["artifacts"]
    if not isinstance(raw_artifacts, list):
        raise BootstrapRefused("release artifacts must be a list")
    artifacts = tuple(_parse_artifact(item) for item in raw_artifacts)
    names = [artifact.name for artifact in artifacts]
    if len(names) != len(set(names)) or names != sorted(names):
        raise BootstrapRefused("release artifacts must be unique and sorted by name")
    wheel_names = [
        name
        for name in names
        if name.startswith(f"dotmac_deployment_foundation-{exact_version}-")
        and name.endswith(".whl")
    ]
    if len(wheel_names) != 1 or set(names) != {
        wheel_names[0],
        LAUNCHER_NAME,
        RECEIPT_NAME,
    }:
        raise BootstrapRefused(
            "release evidence must name exactly one wheel, launcher and receipt"
        )
    return ReleaseEvidence(
        document=document,
        workflow_run=workflow_run,
        distribution=distribution,
        exact_version=exact_version,
        tag=tag,
        source_revision=source_revision,
        artifacts=artifacts,
    )


def _parse_history(value: object) -> HistorySnapshot:
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
    _schema(document, HISTORY_SNAPSHOT_SCHEMA, name="application history snapshot")
    object_format = _nonempty(document["object_format"], name="object_format")
    if object_format != "sha1":
        raise BootstrapRefused("application history object_format must be 'sha1'")
    raw_from = document["from_revision"]
    from_revision = (
        None
        if raw_from is None
        else _text(raw_from, name="from_revision", pattern=_REVISION)
    )
    return HistorySnapshot(
        document=document,
        server_origin=_https_origin(document["server_origin"], name="server_origin"),
        api_origin=_https_api_origin(document["api_origin"], name="api_origin"),
        repository_id=_positive_int(document["repository_id"], name="repository_id"),
        repository=_text(
            document["repository"], name="repository", pattern=_REPOSITORY
        ),
        object_format=object_format,
        from_revision=from_revision,
        to_revision=_text(
            document["to_revision"], name="to_revision", pattern=_REVISION
        ),
        bundle_name=_text(
            document["bundle_name"], name="bundle_name", pattern=_ARTIFACT_NAME
        ),
        bundle_size=_positive_int(document["bundle_size"], name="bundle_size"),
        bundle_sha256=_text(
            document["bundle_sha256"], name="bundle_sha256", pattern=_DIGEST
        ),
    )


def _parse_authorization_evidence(data: bytes) -> AuthorizationEvidence:
    document = _object(
        _strict_json_loads(data, name="authorization evidence"),
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
    _schema(document, AUTHORIZATION_EVIDENCE_SCHEMA, name="authorization evidence")
    return AuthorizationEvidence(
        document=document,
        workflow_run=_parse_workflow_run(
            document["workflow_run"], name="authorization workflow run"
        ),
        execution_envelope_digest=_text(
            document["execution_envelope_digest"],
            name="execution_envelope_digest",
            pattern=_DIGEST,
        ),
        controller_release_evidence_digest=_text(
            document["controller_release_evidence_digest"],
            name="controller_release_evidence_digest",
            pattern=_DIGEST,
        ),
        application_history=_parse_history(document["application_history"]),
    )


def _parse_signature(data: bytes, *, name: str) -> Signature:
    document = _object(
        _strict_json_loads(data, name=name),
        name=name,
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
    _schema(document, SIGNATURE_SCHEMA, name=name)
    if document["algorithm"] != "ed25519":
        raise BootstrapRefused(f"{name} must use Ed25519")
    purpose = _nonempty(document["purpose"], name="purpose")
    if purpose not in {RELEASE_PURPOSE, AUTHORIZATION_PURPOSE}:
        raise BootstrapRefused(f"{name} has an unknown signer purpose")
    encoded = _nonempty(document["signature_b64"], name="signature_b64")
    try:
        raw_signature = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BootstrapRefused(f"{name} signature is not canonical base64") from exc
    if len(raw_signature) != 64 or base64.b64encode(raw_signature).decode() != encoded:
        raise BootstrapRefused(f"{name} must carry one canonical Ed25519 signature")
    return Signature(
        purpose=purpose,
        key_id=_text(document["key_id"], name="key_id", pattern=_KEY_ID),
        payload_schema=_nonempty(document["payload_schema"], name="payload_schema"),
        payload_sha256=_text(
            document["payload_sha256"], name="payload_sha256", pattern=_DIGEST
        ),
        raw_signature=raw_signature,
    )


def _read_regular_bytes(path: Path, *, name: str, maximum: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BootstrapRefused(f"cannot open {name}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BootstrapRefused(f"{name} must be a regular file")
        if metadata.st_size > maximum:
            raise BootstrapRefused(f"{name} exceeds its size limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise BootstrapRefused(f"{name} exceeds its size limit")
        return b"".join(chunks)
    except OSError as exc:
        raise BootstrapRefused(f"cannot read {name}: {exc}") from exc
    finally:
        os.close(descriptor)


def _root_owned_bytes(path: Path, *, executable: bool) -> bytes:
    if not path.is_absolute():
        raise BootstrapRefused(f"trusted path must be absolute: {path}")
    for parent in path.parents:
        try:
            parent_metadata = os.lstat(parent)
        except OSError as exc:
            raise BootstrapRefused(
                f"cannot inspect trusted path parent {parent}: {exc}"
            ) from exc
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != 0
            or parent_metadata.st_mode & 0o022
        ):
            raise BootstrapRefused(
                f"trusted path parent must be root-owned and non-writable: {parent}"
            )
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise BootstrapRefused(f"cannot inspect trusted path {path}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
        raise BootstrapRefused(
            f"trusted path must be a root-owned regular file: {path}"
        )
    if metadata.st_mode & 0o022:
        raise BootstrapRefused(f"trusted path must not be group/world writable: {path}")
    if executable and not metadata.st_mode & 0o111:
        raise BootstrapRefused(f"trusted executable is not executable: {path}")
    return _read_regular_bytes(path, name=f"trusted path {path}", maximum=_MAX_ARTIFACT)


def _load_policy(path: Path) -> TrustPolicy:
    raw = _root_owned_bytes(path, executable=False)
    document = _object(
        _strict_json_loads(raw, name="trust policy"),
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
    _schema(document, TRUST_POLICY_SCHEMA, name="trust policy")
    raw_keys = document["keys"]
    if not isinstance(raw_keys, list):
        raise BootstrapRefused("trust policy keys must be a list")
    keys: list[TrustedKey] = []
    for value in raw_keys:
        key = _object(
            value,
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
        purpose = _nonempty(key["purpose"], name="purpose")
        if purpose not in {RELEASE_PURPOSE, AUTHORIZATION_PURPOSE}:
            raise BootstrapRefused("trusted key has an unknown purpose")
        public_key_path = Path(
            _nonempty(key["public_key_path"], name="public_key_path")
        )
        if not public_key_path.is_absolute():
            raise BootstrapRefused("public_key_path must be absolute")
        keys.append(
            TrustedKey(
                key_id=_text(key["key_id"], name="key_id", pattern=_KEY_ID),
                purpose=purpose,
                public_key_path=public_key_path,
                public_key_sha256=_text(
                    key["public_key_sha256"],
                    name="public_key_sha256",
                    pattern=_DIGEST,
                ),
                public_key_spki_sha256=_text(
                    key["public_key_spki_sha256"],
                    name="public_key_spki_sha256",
                    pattern=_DIGEST,
                ),
            )
        )
    key_ids = [key.key_id for key in keys]
    if len(key_ids) != len(set(key_ids)):
        raise BootstrapRefused("trust policy key IDs must be globally unique")
    for purpose in (RELEASE_PURPOSE, AUTHORIZATION_PURPOSE):
        if not any(key.purpose == purpose for key in keys):
            raise BootstrapRefused(f"trust policy has no {purpose} key")
    fingerprints: dict[str, str] = {}
    for trusted_key in keys:
        previous = fingerprints.setdefault(
            trusted_key.public_key_spki_sha256, trusted_key.purpose
        )
        if previous != trusted_key.purpose:
            raise BootstrapRefused(
                "release and authorization purposes require distinct public keys"
            )
    openssl_path = Path(_nonempty(document["openssl_path"], name="openssl_path"))
    if not openssl_path.is_absolute():
        raise BootstrapRefused("openssl_path must be absolute")

    def workflow_authorities(name: str) -> tuple[WorkflowAuthority, ...]:
        raw_authorities = document[name]
        if not isinstance(raw_authorities, list) or not raw_authorities:
            raise BootstrapRefused(f"trust policy {name} must be a non-empty list")
        result: list[WorkflowAuthority] = []
        for value in raw_authorities:
            authority = _object(
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
            result.append(
                WorkflowAuthority(
                    server_origin=_https_origin(
                        authority["server_origin"], name="server_origin"
                    ),
                    api_origin=_https_api_origin(
                        authority["api_origin"], name="api_origin"
                    ),
                    repository_id=_positive_int(
                        authority["repository_id"], name="repository_id"
                    ),
                    repository=_text(
                        authority["repository"],
                        name="repository",
                        pattern=_REPOSITORY,
                    ),
                    workflow_id=_positive_int(
                        authority["workflow_id"], name="workflow_id"
                    ),
                    workflow_path=_text(
                        authority["workflow_path"],
                        name="workflow_path",
                        pattern=_WORKFLOW,
                    ),
                    event=_text(authority["event"], name="event", pattern=_EVENT),
                    protected_ref=_ref(
                        authority["protected_ref"],
                        name="protected_ref",
                        pattern=_PROTECTED_REF,
                    ),
                    referenced_workflows=_parse_referenced_workflows(
                        authority["referenced_workflows"]
                    ),
                )
            )
        if len(result) != len(set(result)):
            raise BootstrapRefused(f"trust policy repeats a {name} authority")
        return tuple(result)

    raw_repositories = document["application_repositories"]
    if not isinstance(raw_repositories, list) or not raw_repositories:
        raise BootstrapRefused(
            "trust policy application_repositories must be a non-empty list"
        )
    repositories: list[RepositoryAuthority] = []
    for value in raw_repositories:
        repository = _object(
            value,
            name="application repository authority",
            fields=frozenset(
                {"server_origin", "api_origin", "repository_id", "repository"}
            ),
        )
        repositories.append(
            RepositoryAuthority(
                server_origin=_https_origin(
                    repository["server_origin"], name="server_origin"
                ),
                api_origin=_https_api_origin(
                    repository["api_origin"], name="api_origin"
                ),
                repository_id=_positive_int(
                    repository["repository_id"], name="repository_id"
                ),
                repository=_text(
                    repository["repository"],
                    name="repository",
                    pattern=_REPOSITORY,
                ),
            )
        )
    if len(repositories) != len(set(repositories)):
        raise BootstrapRefused("trust policy repeats an application repository")

    git_path = Path(_nonempty(document["git_path"], name="git_path"))
    docker_path = Path(_nonempty(document["docker_path"], name="docker_path"))
    if not git_path.is_absolute() or not docker_path.is_absolute():
        raise BootstrapRefused("trusted Git and Docker paths must be absolute")
    return TrustPolicy(
        openssl_path=openssl_path,
        openssl_sha256=_text(
            document["openssl_sha256"], name="openssl_sha256", pattern=_DIGEST
        ),
        git_path=git_path,
        git_sha256=_text(document["git_sha256"], name="git_sha256", pattern=_DIGEST),
        docker_path=docker_path,
        docker_sha256=_text(
            document["docker_sha256"], name="docker_sha256", pattern=_DIGEST
        ),
        keys=tuple(keys),
        release_authorities=workflow_authorities("release_authorities"),
        authorization_authorities=workflow_authorities("authorization_authorities"),
        application_repositories=tuple(repositories),
    )


def _pae(parts: tuple[bytes, ...]) -> bytes:
    output = bytearray(f"DOTMAC-PAE-V1 {len(parts)} ".encode("ascii"))
    for part in parts:
        output.extend(str(len(part)).encode("ascii"))
        output.extend(b" ")
        output.extend(part)
    return bytes(output)


def _openssl_verify(
    *, openssl: Path, public_key: Path, signature: bytes, payload: bytes, root: Path
) -> None:
    signature_identity = hashlib.sha256(
        str(public_key).encode("utf-8") + b"\0" + signature
    ).hexdigest()
    signature_path = root / f"signature-{signature_identity}.bin"
    _write_private(signature_path, signature)
    try:
        completed = subprocess.run(
            [
                str(openssl),
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(public_key),
                "-sigfile",
                str(signature_path),
                "-rawin",
            ],
            input=payload,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
            cwd="/",
            env={"LANG": "C", "LC_ALL": "C"},
            close_fds=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapRefused(
            f"evidence signature verifier failed closed: {exc}"
        ) from exc
    if completed.returncode != 0:
        raise BootstrapRefused("Ed25519 evidence signature verification failed")


def _public_key_spki_sha256(*, openssl: Path, public_key: Path) -> str:
    try:
        completed = subprocess.run(
            [
                str(openssl),
                "pkey",
                "-pubin",
                "-in",
                str(public_key),
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
        raise BootstrapRefused(
            "trusted public key normalization failed closed"
        ) from exc
    if completed.returncode != 0 or not completed.stdout:
        raise BootstrapRefused("trusted public key is not valid SubjectPublicKeyInfo")
    return _sha256(completed.stdout)


def _verify_signature(
    *,
    document: dict[str, object],
    signature: Signature,
    policy: TrustPolicy,
    purpose: str,
    root: Path,
) -> None:
    expected_schema = (
        RELEASE_EVIDENCE_SCHEMA
        if purpose == RELEASE_PURPOSE
        else AUTHORIZATION_EVIDENCE_SCHEMA
    )
    domain = RELEASE_DOMAIN if purpose == RELEASE_PURPOSE else AUTHORIZATION_DOMAIN
    if signature.purpose != purpose:
        raise BootstrapRefused("evidence signature has the wrong signer purpose")
    if (
        signature.payload_schema != expected_schema
        or document.get("schema") != expected_schema
    ):
        raise BootstrapRefused("evidence signature purpose and schema disagree")
    canonical = _canonical_json(document)
    if signature.payload_sha256 != _sha256(canonical):
        raise BootstrapRefused(
            "signed evidence digest does not match supplied evidence"
        )
    key = policy.require_key(key_id=signature.key_id, purpose=purpose)
    openssl_bytes = _root_owned_bytes(policy.openssl_path, executable=True)
    if _sha256(openssl_bytes) != policy.openssl_sha256:
        raise BootstrapRefused("trusted OpenSSL binary digest does not match policy")
    public_key = _root_owned_bytes(key.public_key_path, executable=False)
    if _sha256(public_key) != key.public_key_sha256:
        raise BootstrapRefused("trusted public key digest does not match policy")
    if (
        _public_key_spki_sha256(
            openssl=policy.openssl_path, public_key=key.public_key_path
        )
        != key.public_key_spki_sha256
    ):
        raise BootstrapRefused("trusted public key SPKI digest does not match policy")
    payload = _pae(
        (
            domain,
            purpose.encode("ascii"),
            key.key_id.encode("ascii"),
            expected_schema.encode("ascii"),
            canonical,
        )
    )
    _openssl_verify(
        openssl=policy.openssl_path,
        public_key=key.public_key_path,
        signature=signature.raw_signature,
        payload=payload,
        root=root,
    )


def _write_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BootstrapRefused(
            f"cannot create sealed input {path.name}: {exc}"
        ) from exc
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written < 1:
                raise BootstrapRefused(f"could not seal input {path.name}")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise BootstrapRefused(f"cannot seal input {path.name}: {exc}") from exc
    finally:
        os.close(descriptor)
    path.chmod(0o400)


def _seal_input(source: Path, destination: Path, *, name: str, maximum: int) -> bytes:
    data = _read_regular_bytes(source, name=name, maximum=maximum)
    _write_private(destination, data)
    sealed = _read_regular_bytes(destination, name=f"sealed {name}", maximum=maximum)
    if sealed != data:
        raise BootstrapRefused(f"sealed {name} changed while it was copied")
    return sealed


def _verify_artifact(path: Path, artifact: Artifact, *, name: str) -> None:
    data = _read_regular_bytes(path, name=name, maximum=_MAX_ARTIFACT)
    if len(data) != artifact.size or _sha256(data) != artifact.sha256:
        raise BootstrapRefused(f"{name} does not match authenticated release evidence")


def _parse_receipt(data: bytes) -> dict[str, object]:
    document = _object(
        _strict_json_loads(data, name="controller receipt"),
        name="controller receipt",
        fields=frozenset(
            {
                "schema",
                "distribution",
                "exact_version",
                "artifact_sha256",
                "launcher_sha256",
                "source_revision",
                "release_run_id",
                "tag",
            }
        ),
    )
    _schema(document, RECEIPT_SCHEMA, name="controller receipt")
    _nonempty(document["distribution"], name="distribution")
    _text(document["exact_version"], name="exact_version", pattern=_VERSION)
    _text(document["artifact_sha256"], name="artifact_sha256", pattern=_DIGEST)
    _text(document["launcher_sha256"], name="launcher_sha256", pattern=_DIGEST)
    _text(document["source_revision"], name="source_revision", pattern=_REVISION)
    _positive_int(document["release_run_id"], name="release_run_id")
    _text(document["tag"], name="tag", pattern=_TAG)
    return document


def _parse_identity(value: object, *, name: str) -> dict[str, object]:
    document = _object(
        value,
        name=name,
        fields=frozenset(
            {
                "image_digest",
                "source_revision",
                "configuration_digest",
                "manifest_digest",
            }
        ),
    )
    _text(document["image_digest"], name="image_digest", pattern=_DIGEST)
    _text(document["source_revision"], name="source_revision", pattern=_REVISION)
    _text(
        document["configuration_digest"], name="configuration_digest", pattern=_DIGEST
    )
    _text(document["manifest_digest"], name="manifest_digest", pattern=_DIGEST)
    return document


def _parse_envelope(data: bytes) -> dict[str, object]:
    document = _object(
        _strict_json_loads(data, name="execution envelope"),
        name="execution envelope",
        fields=frozenset(
            {
                "schema",
                "execution_id",
                "product",
                "target_ref",
                "plan_digest",
                "required_controller",
                "authorizer",
                "candidate",
                "expected_current",
                "relation_evidence",
                "override",
            }
        ),
    )
    _schema(document, ENVELOPE_SCHEMA, name="execution envelope")
    _nonempty(document["execution_id"], name="execution_id")
    _nonempty(document["product"], name="product")
    _nonempty(document["target_ref"], name="target_ref")
    _text(document["plan_digest"], name="plan_digest", pattern=_DIGEST)
    controller = _object(
        document["required_controller"],
        name="required controller",
        fields=frozenset(
            {
                "distribution",
                "exact_version",
                "artifact_sha256",
                "launcher_sha256",
                "source_revision",
                "release_run_id",
                "tag",
            }
        ),
    )
    _nonempty(controller["distribution"], name="distribution")
    _text(controller["exact_version"], name="exact_version", pattern=_VERSION)
    _text(controller["artifact_sha256"], name="artifact_sha256", pattern=_DIGEST)
    _text(controller["launcher_sha256"], name="launcher_sha256", pattern=_DIGEST)
    _text(controller["source_revision"], name="source_revision", pattern=_REVISION)
    _positive_int(controller["release_run_id"], name="release_run_id")
    _text(controller["tag"], name="tag", pattern=_TAG)
    authorizer = _object(
        document["authorizer"],
        name="authorizer",
        fields=frozenset(
            {"repository", "workflow_path", "workflow_revision", "run_id"}
        ),
    )
    _text(authorizer["repository"], name="repository", pattern=_REPOSITORY)
    _text(authorizer["workflow_path"], name="workflow_path", pattern=_WORKFLOW)
    _text(authorizer["workflow_revision"], name="workflow_revision", pattern=_REVISION)
    _positive_int(authorizer["run_id"], name="run_id")
    _parse_identity(document["candidate"], name="candidate")
    if document["expected_current"] is not None:
        _parse_identity(document["expected_current"], name="expected_current")
    relation = _object(
        document["relation_evidence"],
        name="relation evidence",
        fields=frozenset(
            {"relation", "from_revision", "to_revision", "history_snapshot_digest"}
        ),
    )
    if relation["relation"] not in {
        "first_install",
        "same",
        "forward",
        "rollback",
        "diverged",
        "unprovable",
    }:
        raise BootstrapRefused("unknown revision relation")
    if relation["from_revision"] is not None:
        _text(relation["from_revision"], name="from_revision", pattern=_REVISION)
    _text(relation["to_revision"], name="to_revision", pattern=_REVISION)
    _text(
        relation["history_snapshot_digest"],
        name="history_snapshot_digest",
        pattern=_DIGEST,
    )
    return document


def _bind_release(
    *,
    release: ReleaseEvidence,
    receipt: dict[str, object],
    envelope: dict[str, object],
    wheel: Path,
    launcher: Path,
) -> None:
    wheel_artifacts = [
        artifact for artifact in release.artifacts if artifact.name.endswith(".whl")
    ]
    if len(wheel_artifacts) != 1:
        raise BootstrapRefused("release evidence must carry one exact controller wheel")
    wheel_artifact = wheel_artifacts[0]
    launcher_artifact = release.artifact(LAUNCHER_NAME)
    expected_controller = {
        "distribution": release.distribution,
        "exact_version": release.exact_version,
        "artifact_sha256": wheel_artifact.sha256,
        "launcher_sha256": launcher_artifact.sha256,
        "source_revision": release.source_revision,
        "release_run_id": release.workflow_run.run_id,
        "tag": release.tag,
    }
    receipt_controller = {key: receipt[key] for key in expected_controller}
    if receipt_controller != expected_controller:
        raise BootstrapRefused(
            "controller receipt does not match authenticated release evidence"
        )
    if envelope["required_controller"] != expected_controller:
        raise BootstrapRefused(
            "execution envelope requires a different controller release"
        )
    _verify_artifact(wheel, wheel_artifact, name="controller wheel")
    _verify_artifact(launcher, launcher_artifact, name="controller launcher")


def _bind_authorization(
    *,
    authorization: AuthorizationEvidence,
    release: ReleaseEvidence,
    envelope: dict[str, object],
) -> None:
    if authorization.controller_release_evidence_digest != release.evidence_digest:
        raise BootstrapRefused(
            "authorization names a different controller release evidence"
        )
    envelope_digest = _typed_digest("DeploymentExecutionEnvelopeV1", envelope)
    if authorization.execution_envelope_digest != envelope_digest:
        raise BootstrapRefused("authorization names a different execution envelope")
    authorizer = envelope["authorizer"]
    if not isinstance(authorizer, dict):
        raise BootstrapRefused("execution authorizer must be an object")
    run = authorization.workflow_run
    if run.head_sha != run.workflow_revision:
        raise BootstrapRefused(
            "authorization workflow revision does not match its successful run SHA"
        )
    expected_authorizer = {
        "repository": run.repository,
        "workflow_path": run.workflow_path,
        "workflow_revision": run.workflow_revision,
        "run_id": run.run_id,
    }
    if authorizer != expected_authorizer:
        raise BootstrapRefused(
            "authorization workflow does not match envelope authorizer"
        )
    history = authorization.application_history
    relation = envelope["relation_evidence"]
    candidate = envelope["candidate"]
    current = envelope["expected_current"]
    if not isinstance(relation, dict) or not isinstance(candidate, dict):
        raise BootstrapRefused("execution relation and candidate must be objects")
    expected_from = (
        None if current is None else current["source_revision"]  # type: ignore[index]
    )
    if (
        history.from_revision != expected_from
        or history.to_revision != candidate["source_revision"]
        or relation["from_revision"] != history.from_revision
        or relation["to_revision"] != history.to_revision
        or relation["history_snapshot_digest"] != history.snapshot_digest
    ):
        raise BootstrapRefused(
            "execution relation is not bound to the signed application history"
        )


def _git_environment() -> dict[str, str]:
    return {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_REPLACE_REF_BASE": "refs/dotmac-disabled-replace/",
        "GIT_GRAFT_FILE": os.devnull,
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "",
    }


def _run_git(git: Path, arguments: Sequence[str], *, cwd: Path | None = None) -> bytes:
    try:
        completed = subprocess.run(
            [str(git), *arguments],
            capture_output=True,
            check=False,
            timeout=120,
            cwd=str(cwd) if cwd is not None else "/",
            env=_git_environment(),
            close_fds=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapRefused(f"Git evidence operation failed closed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise BootstrapRefused(
            f"Git evidence operation failed with {completed.returncode}: {detail}"
        )
    return completed.stdout


def _canonical_repository_url(run: WorkflowRun) -> str:
    return f"{run.server_origin}/{run.repository}.git"


def _prepare_authorizer_checkout(
    *, git: Path, source: Path, destination: Path, run: WorkflowRun
) -> None:
    source = source.resolve()
    if not source.is_dir():
        raise BootstrapRefused("authorizer checkout must be an existing directory")
    remotes = (
        _run_git(git, ["-C", str(source), "remote", "get-url", "--all", "origin"])
        .decode("utf-8", errors="strict")
        .splitlines()
    )
    canonical_remote = _canonical_repository_url(run)
    if remotes != [canonical_remote]:
        raise BootstrapRefused("authorizer checkout has a foreign or ambiguous origin")
    _run_git(
        git,
        [
            "-c",
            "protocol.file.allow=always",
            "clone",
            "--no-local",
            "--no-hardlinks",
            "--no-checkout",
            str(source),
            str(destination),
        ],
    )
    _run_git(
        git,
        ["-C", str(destination), "checkout", "--detach", run.workflow_revision],
    )
    head = _run_git(git, ["-C", str(destination), "rev-parse", "HEAD"]).decode().strip()
    shallow = (
        _run_git(git, ["-C", str(destination), "rev-parse", "--is-shallow-repository"])
        .decode()
        .strip()
    )
    if head != run.workflow_revision or shallow != "false":
        raise BootstrapRefused(
            "sealed authorizer checkout is not the exact full revision"
        )
    mode_line = (
        _run_git(
            git,
            [
                "-C",
                str(destination),
                "ls-tree",
                run.workflow_revision,
                "--",
                run.workflow_path,
            ],
        )
        .decode("utf-8", errors="strict")
        .strip()
    )
    if not (
        mode_line.startswith("100644 blob ") or mode_line.startswith("100755 blob ")
    ):
        raise BootstrapRefused("authorizing workflow is not one regular tracked blob")
    workflow = _run_git(
        git,
        [
            "-C",
            str(destination),
            "show",
            f"{run.workflow_revision}:{run.workflow_path}",
        ],
    )
    if _sha256(workflow) != run.workflow_blob_sha256:
        raise BootstrapRefused(
            "authorizing workflow blob does not match signed evidence"
        )
    _run_git(
        git,
        ["-C", str(destination), "remote", "set-url", "origin", canonical_remote],
    )


def _prepare_history_checkout(
    *, git: Path, bundle: Path, destination: Path, snapshot: HistorySnapshot
) -> None:
    _run_git(git, ["init", "--bare", str(destination)])
    _run_git(git, ["-C", str(destination), "bundle", "verify", str(bundle)])
    _run_git(git, ["-C", str(destination), "bundle", "unbundle", str(bundle)])
    object_format = (
        _run_git(git, ["-C", str(destination), "rev-parse", "--show-object-format"])
        .decode()
        .strip()
    )
    shallow = (
        _run_git(git, ["-C", str(destination), "rev-parse", "--is-shallow-repository"])
        .decode()
        .strip()
    )
    if object_format != snapshot.object_format or shallow != "false":
        raise BootstrapRefused("application-history bundle has the wrong Git topology")
    revisions = [snapshot.to_revision]
    if snapshot.from_revision is not None:
        revisions.append(snapshot.from_revision)
    for revision in revisions:
        _run_git(
            git,
            ["-C", str(destination), "cat-file", "-e", f"{revision}^{{commit}}"],
        )
    alternates = destination / "objects" / "info" / "alternates"
    if alternates.exists():
        raise BootstrapRefused("application-history checkout must not use alternates")


def _invoke_launcher(
    *,
    launcher: Path,
    bootstrap_context: Path,
    release_evidence: Path,
    authorization_evidence: Path,
    envelope: Path,
    receipt: Path,
    wheel: Path,
    authorizer_repo: Path,
    history_repo: Path,
    staged_application_root: Path,
    git: Path,
    docker: Path,
    descriptor: str,
) -> int:
    python = Path(sys.executable).resolve()
    _root_owned_bytes(python, executable=True)
    command = [
        str(python),
        "-I",
        str(launcher),
        "--bootstrap-context-fd",
        "PLACEHOLDER",
        "--release-evidence",
        str(release_evidence),
        "--authorization-evidence",
        str(authorization_evidence),
        "--envelope",
        str(envelope),
        "--receipt",
        str(receipt),
        "--wheel",
        str(wheel),
        "--authorizer-repo",
        str(authorizer_repo),
        "--application-history-repo",
        str(history_repo),
        "--staged-application-root",
        str(staged_application_root),
        "--git-bin",
        str(git),
        "--docker-bin",
        str(docker),
        "--descriptor",
        descriptor,
    ]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"LANG", "LC_ALL", "SSL_CERT_DIR", "SSL_CERT_FILE"}
    }
    environment["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    environment["HOME"] = str(launcher.parent)
    environment["TMPDIR"] = str(launcher.parent)
    context_fd = os.open(bootstrap_context, os.O_RDONLY)
    try:
        command[command.index("PLACEHOLDER")] = str(context_fd)
        try:
            completed = subprocess.run(
                command,
                check=False,
                cwd=str(launcher.parent),
                env=environment,
                close_fds=True,
                pass_fds=(context_fd,),
            )
        except OSError as exc:
            raise BootstrapRefused(
                f"authenticated launcher could not start: {exc}"
            ) from exc
        return completed.returncode
    finally:
        os.close(context_fd)


def authenticate_and_launch(args: argparse.Namespace) -> int:
    staged_application_root = Path(args.staged_application_root).resolve()
    authorizer_source = Path(args.authorizer_repo).resolve()
    if not staged_application_root.is_dir():
        raise BootstrapRefused("staged application root must be an existing directory")
    if (
        authorizer_source == staged_application_root
        or authorizer_source.is_relative_to(staged_application_root)
        or staged_application_root.is_relative_to(authorizer_source)
    ):
        raise BootstrapRefused("authorizer checkout must be outside staged application")

    _root_owned_bytes(Path(__file__).resolve(), executable=False)
    authority_inputs = (
        Path(args.trust_policy),
        Path(args.release_evidence),
        Path(args.release_signature),
        Path(args.authorization_evidence),
        Path(args.authorization_signature),
        Path(args.launcher),
        Path(args.wheel),
        Path(args.receipt),
        Path(args.execution_envelope),
        Path(args.history_bundle),
    )
    if any(
        path.resolve().is_relative_to(staged_application_root)
        for path in authority_inputs
    ):
        raise BootstrapRefused(
            "trust, evidence and controller inputs must be outside staged application"
        )

    policy = _load_policy(Path(args.trust_policy))
    git = Path(args.git_bin)
    docker = Path(args.docker_bin)
    if not git.is_absolute() or not docker.is_absolute():
        raise BootstrapRefused("Git and Docker executables must use absolute paths")
    if git != policy.git_path or docker != policy.docker_path:
        raise BootstrapRefused("Git or Docker executable differs from trust policy")
    if _sha256(_root_owned_bytes(git, executable=True)) != policy.git_sha256:
        raise BootstrapRefused("trusted Git binary digest does not match policy")
    if _sha256(_root_owned_bytes(docker, executable=True)) != policy.docker_sha256:
        raise BootstrapRefused("trusted Docker binary digest does not match policy")

    with tempfile.TemporaryDirectory(prefix="dotmac-authenticated-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        paths = {
            "release_evidence": root / "release-evidence.json",
            "release_signature": root / "release-signature.json",
            "authorization_evidence": root / "authorization-evidence.json",
            "authorization_signature": root / "authorization-signature.json",
            "launcher": root / LAUNCHER_NAME,
            "wheel": root / Path(args.wheel).name,
            "receipt": root / RECEIPT_NAME,
            "envelope": root / "execution-envelope.json",
            "history_bundle": root / "application-history.bundle",
            "bootstrap_context": root / "bootstrap-context.json",
        }
        release_bytes = _seal_input(
            Path(args.release_evidence),
            paths["release_evidence"],
            name="release evidence",
            maximum=_MAX_DOCUMENT,
        )
        release_signature_bytes = _seal_input(
            Path(args.release_signature),
            paths["release_signature"],
            name="release signature",
            maximum=_MAX_DOCUMENT,
        )
        authorization_bytes = _seal_input(
            Path(args.authorization_evidence),
            paths["authorization_evidence"],
            name="authorization evidence",
            maximum=_MAX_DOCUMENT,
        )
        authorization_signature_bytes = _seal_input(
            Path(args.authorization_signature),
            paths["authorization_signature"],
            name="authorization signature",
            maximum=_MAX_DOCUMENT,
        )
        _seal_input(
            Path(args.launcher),
            paths["launcher"],
            name="controller launcher",
            maximum=_MAX_DOCUMENT,
        )
        _seal_input(
            Path(args.wheel),
            paths["wheel"],
            name="controller wheel",
            maximum=_MAX_ARTIFACT,
        )
        receipt_bytes = _seal_input(
            Path(args.receipt),
            paths["receipt"],
            name="controller receipt",
            maximum=_MAX_DOCUMENT,
        )
        envelope_bytes = _seal_input(
            Path(args.execution_envelope),
            paths["envelope"],
            name="execution envelope",
            maximum=_MAX_DOCUMENT,
        )
        history_bytes = _seal_input(
            Path(args.history_bundle),
            paths["history_bundle"],
            name="application-history bundle",
            maximum=_MAX_ARTIFACT,
        )

        release = _parse_release_evidence(release_bytes)
        release_signature = _parse_signature(
            release_signature_bytes, name="release signature"
        )
        authorization = _parse_authorization_evidence(authorization_bytes)
        authorization_signature = _parse_signature(
            authorization_signature_bytes, name="authorization signature"
        )
        receipt = _parse_receipt(receipt_bytes)
        envelope = _parse_envelope(envelope_bytes)
        if envelope_bytes != _canonical_json(envelope):
            raise BootstrapRefused(
                "execution envelope must use its exact canonical byte representation"
            )

        policy.require_workflow(run=release.workflow_run, purpose=RELEASE_PURPOSE)
        policy.require_workflow(
            run=authorization.workflow_run, purpose=AUTHORIZATION_PURPOSE
        )
        policy.require_application_repository(authorization.application_history)

        _verify_signature(
            document=release.document,
            signature=release_signature,
            policy=policy,
            purpose=RELEASE_PURPOSE,
            root=root,
        )
        _verify_signature(
            document=authorization.document,
            signature=authorization_signature,
            policy=policy,
            purpose=AUTHORIZATION_PURPOSE,
            root=root,
        )
        _bind_release(
            release=release,
            receipt=receipt,
            envelope=envelope,
            wheel=paths["wheel"],
            launcher=paths["launcher"],
        )
        receipt_artifact = release.artifact(RECEIPT_NAME)
        _verify_artifact(paths["receipt"], receipt_artifact, name="controller receipt")
        _bind_authorization(
            authorization=authorization, release=release, envelope=envelope
        )
        history = authorization.application_history
        if (
            history.bundle_name != Path(args.history_bundle).name
            or len(history_bytes) != history.bundle_size
            or _sha256(history_bytes) != history.bundle_sha256
        ):
            raise BootstrapRefused(
                "application-history bundle does not match signed authorization"
            )
        bootstrap_context: dict[str, object] = {
            "schema": BOOTSTRAP_CONTEXT_SCHEMA,
            "release_evidence_digest": release.evidence_digest,
            "authorization_evidence_digest": authorization.evidence_digest,
            "execution_envelope_digest": authorization.execution_envelope_digest,
            "application_history_snapshot_digest": history.snapshot_digest,
        }
        _write_private(paths["bootstrap_context"], _canonical_json(bootstrap_context))

        sealed_authorizer = root / "authorizer"
        sealed_history = root / "application-history.git"
        _prepare_authorizer_checkout(
            git=git,
            source=authorizer_source,
            destination=sealed_authorizer,
            run=authorization.workflow_run,
        )
        _prepare_history_checkout(
            git=git,
            bundle=paths["history_bundle"],
            destination=sealed_history,
            snapshot=history,
        )
        if (
            sealed_authorizer == sealed_history
            or sealed_authorizer.is_relative_to(sealed_history)
            or sealed_history.is_relative_to(sealed_authorizer)
        ):
            raise BootstrapRefused(
                "authorizer and application-history checkouts must be distinct"
            )

        # Re-read every execution-bearing byte after checkout preparation and
        # immediately before exec.  Source files can change now without
        # changing these private sealed copies.
        _verify_artifact(
            paths["wheel"],
            release.artifact(Path(args.wheel).name),
            name="controller wheel",
        )
        _verify_artifact(
            paths["launcher"],
            release.artifact(LAUNCHER_NAME),
            name="controller launcher",
        )
        _verify_artifact(paths["receipt"], receipt_artifact, name="controller receipt")
        sealed_envelope = _parse_envelope(
            _read_regular_bytes(
                paths["envelope"],
                name="sealed execution envelope",
                maximum=_MAX_DOCUMENT,
            )
        )
        if (
            _typed_digest("DeploymentExecutionEnvelopeV1", sealed_envelope)
            != authorization.execution_envelope_digest
        ):
            raise BootstrapRefused("sealed execution envelope changed before launch")
        sealed_history_bytes = _read_regular_bytes(
            paths["history_bundle"],
            name="sealed history bundle",
            maximum=_MAX_ARTIFACT,
        )
        if _sha256(sealed_history_bytes) != history.bundle_sha256:
            raise BootstrapRefused(
                "sealed application-history bundle changed before launch"
            )

        return _invoke_launcher(
            launcher=paths["launcher"],
            bootstrap_context=paths["bootstrap_context"],
            release_evidence=paths["release_evidence"],
            authorization_evidence=paths["authorization_evidence"],
            envelope=paths["envelope"],
            receipt=paths["receipt"],
            wheel=paths["wheel"],
            authorizer_repo=sealed_authorizer,
            history_repo=sealed_history,
            staged_application_root=staged_application_root,
            git=git,
            docker=docker,
            descriptor=args.descriptor,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trust-policy", required=True)
    parser.add_argument("--release-evidence", required=True)
    parser.add_argument("--release-signature", required=True)
    parser.add_argument("--authorization-evidence", required=True)
    parser.add_argument("--authorization-signature", required=True)
    parser.add_argument("--launcher", required=True)
    parser.add_argument("--wheel", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--execution-envelope", required=True)
    parser.add_argument("--history-bundle", required=True)
    parser.add_argument("--authorizer-repo", required=True)
    parser.add_argument("--staged-application-root", required=True)
    parser.add_argument("--git-bin", required=True)
    parser.add_argument("--docker-bin", required=True)
    parser.add_argument("--descriptor", default="deploy/product.toml")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return authenticate_and_launch(build_parser().parse_args(argv))
    except BootstrapRefused as exc:
        print(f"authenticated deployment refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
