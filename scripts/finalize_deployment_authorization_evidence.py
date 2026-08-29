#!/usr/bin/env python3
"""Finalize one deployment authorization after its workflow has completed.

This is a portable owner tool, not a Starter-owned authorization workflow.  A
future repository-local ``workflow_run`` finalizer supplies the candidate run
coordinates; this program independently re-reads the run, repository,
workflow, workflow blob, application repository and application commits before
it signs anything. The authorizing run must come from a re-queried protected
branch; reusable workflows are separately resolved and content-bound, while
branch movement after the run is allowed. It verifies a self-contained Git
bundle for the exact authorized transition and emits that bundle beside strict
authorization evidence and its detached Ed25519 signature.

The signing key is named only by an absolute path.  Python never reads or logs
it; an absolute OpenSSL executable receives the domain-separated authorization
payload on stdin.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(
    0,
    str(REPO_ROOT / "packages" / "dotmac-deployment-foundation" / "src"),
)

from dotmac_deployment_foundation.authenticity import (  # noqa: E402
    AUTHORIZATION_EVIDENCE_SCHEMA,
    ApplicationHistorySnapshotV1,
    DeploymentAuthorizationEvidenceV1,
    DeploymentControllerReleaseEvidenceV1,
    DetachedEvidenceSignatureV1,
    EvidencePurpose,
    GitHubReferencedWorkflowV1,
    GitHubWorkflowRunV1,
    canonical_json_bytes,
    signing_payload_bytes,
    strict_json_loads,
)
from dotmac_deployment_foundation.errors import SpecError  # noqa: E402
from dotmac_deployment_foundation.execution import (  # noqa: E402
    DeploymentExecutionEnvelopeV1,
)

EVIDENCE_NAME: Final = "DeploymentAuthorizationEvidence.v1.json"
SIGNATURE_NAME: Final = "DetachedEvidenceSignature.v1.json"
HISTORY_NAME: Final = "ApplicationHistory.v1.bundle"

_REVISION = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORKFLOW = re.compile(r"^\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml$")
_KEY_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_EVENT = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_BRANCH = re.compile(r"^(?!/)(?!.*(?:\.\.|//|@\{|\\))(?!.*[/.]$)[A-Za-z0-9._/-]+$")
_GIT_REF = re.compile(r"^refs/(?:heads|tags)/[^\x00-\x20~^:?*\\[\\]]+$")
_MAX_JSON = 4 * 1024 * 1024
_MAX_BUNDLE = 1024 * 1024 * 1024


class FinalizationRefused(RuntimeError):
    """The requested authorization could not be independently proved."""


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _object(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise FinalizationRefused(f"{name} must be an object")
    return dict(value)


def _text(value: object, *, name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise FinalizationRefused(f"invalid {name}")
    return value


def _nonempty(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise FinalizationRefused(f"{name} must be non-empty trimmed text")
    return value


def _positive(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FinalizationRefused(f"{name} must be a positive integer")
    return value


def _exact(value: object, expected: object, *, name: str) -> None:
    if value != expected:
        raise FinalizationRefused(f"{name} does not match the triggered authorization")


def _canonical_https(value: str, *, name: str, allow_path: bool = False) -> str:
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise FinalizationRefused(f"{name} has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (not allow_path and parsed.path not in ("", "/"))
    ):
        raise FinalizationRefused(f"{name} must be a credential-free HTTPS URL")
    authority = parsed.hostname.lower()
    if port is not None:
        authority += f":{port}"
    path = parsed.path.rstrip("/") if allow_path else ""
    canonical = f"https://{authority}{path}"
    if value.rstrip("/") != canonical:
        raise FinalizationRefused(f"{name} is not canonically spelled")
    return canonical


def _expected_api_origin(server_origin: str) -> str:
    server = _canonical_https(server_origin, name="trusted server origin")
    if server == "https://github.com":
        return "https://api.github.com"
    return f"{server}/api/v3"


def _require_api_origin(*, server_origin: str, api_url: str, name: str) -> str:
    api = _canonical_https(api_url, name=name, allow_path=True)
    expected = _expected_api_origin(server_origin)
    if api != expected:
        raise FinalizationRefused(f"{name} is not derived from the trusted server")
    return api


def _api_url(api_origin: str, path: str) -> str:
    if not path.startswith("/") or path.startswith("//"):
        raise FinalizationRefused("GitHub API path is not absolute")
    return f"{api_origin}{path}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: IO[bytes],
        code: int,
        message: str,
        headers: http.client.HTTPMessage,
        new_url: str,
    ) -> None:
        del request, file_pointer, code, message, headers, new_url
        return None


class HttpReader:
    """Bounded GitHub API reader which never includes response bodies in errors."""

    def __init__(self, *, server_origin: str, api_url: str, github_token: str) -> None:
        self._github_api = _require_api_origin(
            server_origin=server_origin,
            api_url=api_url,
            name="GitHub API origin",
        )
        if not github_token:
            raise FinalizationRefused("GITHUB_TOKEN is not provisioned")
        self._authorization = f"Bearer {github_token}"
        self._opener = urllib.request.build_opener(_NoRedirect())

    def _get(self, url: str, *, name: str, accept: str, limit: int) -> bytes:
        if not url.startswith(f"{self._github_api}/"):
            raise FinalizationRefused(f"{name} URL leaves the trusted GitHub API")
        request = urllib.request.Request(  # noqa: S310 -- HTTPS checked above
            url,
            headers={
                "Accept": accept,
                "Authorization": self._authorization,
                "User-Agent": "dotmac-deployment-authorization-finalizer/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                if getattr(response, "status", None) != 200:
                    raise FinalizationRefused(
                        f"{name} returned HTTP {getattr(response, 'status', None)}"
                    )
                data = response.read(limit + 1)
        except FinalizationRefused:
            raise
        except urllib.error.HTTPError as exc:
            raise FinalizationRefused(f"{name} returned HTTP {exc.code}") from exc
        except (OSError, urllib.error.URLError) as exc:
            raise FinalizationRefused(f"{name} could not be read") from exc
        if not isinstance(data, bytes) or len(data) > limit:
            raise FinalizationRefused(f"{name} exceeds its size limit")
        return data

    def github_json(self, url: str, *, name: str) -> dict[str, object]:
        raw = self._get(
            url,
            name=name,
            accept="application/vnd.github+json",
            limit=_MAX_JSON,
        )
        try:
            return _object(
                strict_json_loads(raw.decode("utf-8")),
                name=name,
            )
        except (UnicodeError, json.JSONDecodeError, SpecError) as exc:
            raise FinalizationRefused(f"{name} is not strict JSON") from exc

    def github_blob(self, url: str, *, name: str) -> bytes:
        return self._get(
            url,
            name=name,
            accept="application/vnd.github.raw+json",
            limit=_MAX_JSON,
        )


@dataclass(frozen=True, slots=True)
class ExpectedRun:
    server_origin: str
    api_url: str
    repository_id: int
    repository: str
    head_repository_id: int
    head_repository: str
    workflow_id: int
    workflow_name: str
    workflow_path: str
    run_id: int
    run_attempt: int
    event: str
    head_sha: str
    status: str
    conclusion: str

    def __post_init__(self) -> None:
        _canonical_https(self.server_origin, name="server origin")
        _require_api_origin(
            server_origin=self.server_origin,
            api_url=self.api_url,
            name="API URL",
        )
        _positive(self.repository_id, name="repository ID")
        _text(self.repository, name="repository", pattern=_REPOSITORY)
        _positive(self.head_repository_id, name="head repository ID")
        _text(self.head_repository, name="head repository", pattern=_REPOSITORY)
        _positive(self.workflow_id, name="workflow ID")
        _nonempty(self.workflow_name, name="workflow name")
        _text(self.workflow_path, name="workflow path", pattern=_WORKFLOW)
        _positive(self.run_id, name="run ID")
        _positive(self.run_attempt, name="run attempt")
        _text(self.event, name="event", pattern=_EVENT)
        _text(self.head_sha, name="head SHA", pattern=_REVISION)
        if self.status != "completed" or self.conclusion != "success":
            raise FinalizationRefused("triggered authorization was not successful")
        if (
            self.repository_id != self.head_repository_id
            or self.repository != self.head_repository
        ):
            raise FinalizationRefused(
                "triggered authorization did not run from the protected repository"
            )


@dataclass(frozen=True, slots=True)
class ExpectedApplicationRepository:
    server_origin: str
    api_url: str
    repository_id: int
    repository: str

    def __post_init__(self) -> None:
        _canonical_https(self.server_origin, name="application server origin")
        _require_api_origin(
            server_origin=self.server_origin,
            api_url=self.api_url,
            name="application API URL",
        )
        _positive(self.repository_id, name="application repository ID")
        _text(self.repository, name="application repository", pattern=_REPOSITORY)


def _head_ref(run_document: dict[str, object]) -> tuple[str, str]:
    branch = _text(
        run_document.get("head_branch"),
        name="run head branch",
        pattern=_BRANCH,
    )
    return branch, f"refs/heads/{branch}"


def _validate_protected_branch(document: dict[str, object], *, branch: str) -> None:
    _exact(document.get("name"), branch, name="protected branch name")
    if document.get("protected") is not True:
        raise FinalizationRefused("authorization run head branch is not protected")


def _workflow_path(value: object, *, expected: str, head_ref: str) -> str:
    raw = _nonempty(value, name="run workflow path")
    path, separator, workflow_ref = raw.rpartition("@")
    if not separator:
        path = raw
    elif workflow_ref not in {head_ref, head_ref.removeprefix("refs/heads/")}:
        raise FinalizationRefused("run workflow path ref is not the protected head ref")
    _text(path, name="run workflow path", pattern=_WORKFLOW)
    _exact(path, expected, name="run workflow path")
    return path


def _referenced_identity(value: object) -> tuple[str, str, str, str]:
    item = _object(value, name="referenced workflow")
    if set(item) != {"path", "sha", "ref"}:
        raise FinalizationRefused("referenced workflow fields are not exact")
    raw_path = _nonempty(item.get("path"), name="referenced workflow path")
    prefix, separator, workflow_ref = raw_path.rpartition("@")
    marker = "/.github/workflows/"
    if not separator or marker not in prefix:
        raise FinalizationRefused("referenced workflow path is not fully qualified")
    repository, suffix = prefix.split(marker, 1)
    _text(repository, name="referenced workflow repository", pattern=_REPOSITORY)
    workflow_path = f".github/workflows/{suffix}"
    _text(workflow_path, name="referenced workflow path", pattern=_WORKFLOW)
    revision = _text(
        item.get("sha"), name="referenced workflow revision", pattern=_REVISION
    )
    resolved_ref = _nonempty(item.get("ref"), name="referenced workflow ref")
    if workflow_ref == revision:
        if resolved_ref != revision:
            raise FinalizationRefused(
                "referenced workflow SHA ref does not resolve to itself"
            )
    else:
        _text(resolved_ref, name="referenced workflow resolved ref", pattern=_GIT_REF)
        short_ref = resolved_ref.split("/", 2)[2]
        if workflow_ref not in {resolved_ref, short_ref}:
            raise FinalizationRefused(
                "referenced workflow path ref disagrees with its resolved ref"
            )
    return repository, workflow_path, workflow_ref, revision


def _referenced_workflows(
    *,
    reader: HttpReader,
    expected: ExpectedRun,
    run_document: dict[str, object],
) -> tuple[GitHubReferencedWorkflowV1, ...]:
    raw_items = run_document.get("referenced_workflows")
    if not isinstance(raw_items, list):
        raise FinalizationRefused("run referenced_workflows must be an exact list")
    identities = [_referenced_identity(item) for item in raw_items]
    if len(identities) != len(set(identities)):
        raise FinalizationRefused("run referenced_workflows contains duplicates")
    result: list[GitHubReferencedWorkflowV1] = []
    for repository, workflow_path, workflow_ref, revision in identities:
        encoded_repository = "/".join(
            urllib.parse.quote(part, safe="") for part in repository.split("/")
        )
        encoded_path = urllib.parse.quote(workflow_path, safe="/")
        encoded_revision = urllib.parse.quote(revision, safe="")
        blob = reader.github_blob(
            _api_url(
                expected.api_url,
                f"/repos/{encoded_repository}/contents/{encoded_path}"
                f"?ref={encoded_revision}",
            ),
            name=f"referenced workflow blob {repository}/{workflow_path}",
        )
        if not blob:
            raise FinalizationRefused("referenced workflow blob is empty")
        result.append(
            GitHubReferencedWorkflowV1(
                repository=repository,
                workflow_path=workflow_path,
                workflow_ref=workflow_ref,
                workflow_revision=revision,
                workflow_blob_sha256=_sha256(blob),
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: canonical_json_bytes(item.to_document()),
        )
    )


def validate_authorizer_run(
    *,
    run_document: dict[str, object],
    repository_document: dict[str, object],
    workflow_document: dict[str, object],
    workflow_blob: bytes,
    branch_document: dict[str, object],
    referenced_workflows: tuple[GitHubReferencedWorkflowV1, ...],
    expected: ExpectedRun,
) -> GitHubWorkflowRunV1:
    """Bind every trigger coordinate to independently queried API state."""

    for name, value, wanted in (
        ("run ID", run_document.get("id"), expected.run_id),
        ("run attempt", run_document.get("run_attempt"), expected.run_attempt),
        ("workflow ID", run_document.get("workflow_id"), expected.workflow_id),
        ("event", run_document.get("event"), expected.event),
        ("head SHA", run_document.get("head_sha"), expected.head_sha),
        ("status", run_document.get("status"), expected.status),
        ("conclusion", run_document.get("conclusion"), expected.conclusion),
    ):
        _exact(value, wanted, name=name)
    branch, head_ref = _head_ref(run_document)
    _validate_protected_branch(branch_document, branch=branch)
    _workflow_path(
        run_document.get("path"), expected=expected.workflow_path, head_ref=head_ref
    )
    run_repository = _object(run_document.get("repository"), name="run repository")
    head_repository = _object(
        run_document.get("head_repository"), name="run head repository"
    )
    for name, value, wanted in (
        ("run repository ID", run_repository.get("id"), expected.repository_id),
        ("run repository", run_repository.get("full_name"), expected.repository),
        (
            "run head repository ID",
            head_repository.get("id"),
            expected.head_repository_id,
        ),
        (
            "run head repository",
            head_repository.get("full_name"),
            expected.head_repository,
        ),
        ("repository API ID", repository_document.get("id"), expected.repository_id),
        (
            "repository API name",
            repository_document.get("full_name"),
            expected.repository,
        ),
        ("workflow API ID", workflow_document.get("id"), expected.workflow_id),
        ("workflow API name", workflow_document.get("name"), expected.workflow_name),
        ("workflow API path", workflow_document.get("path"), expected.workflow_path),
        ("workflow API state", workflow_document.get("state"), "active"),
    ):
        _exact(value, wanted, name=name)
    return GitHubWorkflowRunV1(
        server_origin=expected.server_origin,
        api_origin=expected.api_url,
        repository_id=expected.repository_id,
        repository=expected.repository,
        head_repository_id=expected.head_repository_id,
        head_repository=expected.head_repository,
        workflow_id=expected.workflow_id,
        workflow_path=expected.workflow_path,
        workflow_revision=expected.head_sha,
        workflow_blob_sha256=_sha256(workflow_blob),
        head_ref=head_ref,
        referenced_workflows=referenced_workflows,
        run_id=expected.run_id,
        run_attempt=expected.run_attempt,
        event=expected.event,
        head_sha=expected.head_sha,
        status=expected.status,
        conclusion=expected.conclusion,
    )


def validate_application_repository(
    document: dict[str, object], *, expected: ExpectedApplicationRepository
) -> None:
    _exact(document.get("id"), expected.repository_id, name="application repository ID")
    _exact(
        document.get("full_name"),
        expected.repository,
        name="application repository name",
    )


def validate_application_commit(document: dict[str, object], *, revision: str) -> None:
    _exact(document.get("sha"), revision, name="application commit SHA")


def _read_strict_json(path: Path, *, name: str) -> tuple[bytes, dict[str, object]]:
    try:
        data = path.read_bytes()
        if len(data) > _MAX_JSON:
            raise FinalizationRefused(f"{name} exceeds its size limit")
        value = strict_json_loads(data.decode("utf-8"))
        return data, _object(value, name=name)
    except FinalizationRefused:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, SpecError) as exc:
        raise FinalizationRefused(f"{name} is not strict JSON") from exc


def load_envelope(path: Path) -> DeploymentExecutionEnvelopeV1:
    raw, document = _read_strict_json(path, name="execution envelope")
    canonical = canonical_json_bytes(document)
    if raw != canonical:
        raise FinalizationRefused("execution envelope is not canonical JSON")
    try:
        return DeploymentExecutionEnvelopeV1.loads(canonical.decode("ascii"))
    except SpecError as exc:
        raise FinalizationRefused("execution envelope is invalid") from exc


def load_controller_release_evidence(
    path: Path,
) -> DeploymentControllerReleaseEvidenceV1:
    _, document = _read_strict_json(path, name="controller release evidence")
    try:
        return DeploymentControllerReleaseEvidenceV1.from_document(document)
    except SpecError as exc:
        raise FinalizationRefused("controller release evidence is invalid") from exc


def bind_controller_release(
    envelope: DeploymentExecutionEnvelopeV1,
    release: DeploymentControllerReleaseEvidenceV1,
) -> None:
    controller = envelope.required_controller
    if (
        controller.distribution != release.distribution
        or controller.exact_version != release.exact_version
        or controller.source_revision != release.source_revision
        or controller.release_run_id != release.workflow_run.run_id
        or controller.tag != release.tag
    ):
        raise FinalizationRefused(
            "execution envelope names a different controller release"
        )
    wheel = [item for item in release.artifacts if item.name.endswith(".whl")]
    launcher = [
        item
        for item in release.artifacts
        if item.name == "run_deployment_controller.py"
    ]
    if (
        len(wheel) != 1
        or len(launcher) != 1
        or wheel[0].sha256 != controller.artifact_sha256
        or launcher[0].sha256 != controller.launcher_sha256
    ):
        raise FinalizationRefused(
            "controller release artifacts disagree with the execution envelope"
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


def _run_git(git: Path, *arguments: str, cwd: Path | None = None) -> bytes:
    try:
        completed = subprocess.run(
            [str(git), *arguments],
            check=False,
            capture_output=True,
            timeout=120,
            cwd=str(cwd) if cwd is not None else "/",
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FinalizationRefused("Git history verification failed closed") from exc
    if completed.returncode != 0:
        raise FinalizationRefused(
            f"Git history verification failed with {completed.returncode}"
        )
    return completed.stdout


def verify_history_bundle(
    *,
    bundle: Path,
    git_binary: Path,
    application: ExpectedApplicationRepository,
    from_revision: str | None,
    to_revision: str,
) -> ApplicationHistorySnapshotV1:
    if not git_binary.is_absolute():
        raise FinalizationRefused("Git executable must be absolute")
    bundle = bundle.resolve()
    try:
        bundle_bytes = bundle.read_bytes()
    except OSError as exc:
        raise FinalizationRefused("application-history bundle is unreadable") from exc
    if not bundle_bytes or len(bundle_bytes) > _MAX_BUNDLE:
        raise FinalizationRefused("application-history bundle has an invalid size")
    with tempfile.TemporaryDirectory(prefix="dotmac-authorization-history-") as temp:
        sealed_bundle = Path(temp) / HISTORY_NAME
        _write_exclusive(sealed_bundle, bundle_bytes)
        repository = Path(temp) / "history.git"
        _run_git(git_binary, "init", "--bare", str(repository))
        _run_git(
            git_binary,
            "-C",
            str(repository),
            "bundle",
            "verify",
            str(sealed_bundle),
        )
        _run_git(
            git_binary,
            "-C",
            str(repository),
            "bundle",
            "unbundle",
            str(sealed_bundle),
        )
        object_format = (
            _run_git(
                git_binary,
                "-C",
                str(repository),
                "rev-parse",
                "--show-object-format",
            )
            .decode("ascii")
            .strip()
        )
        shallow = (
            _run_git(
                git_binary,
                "-C",
                str(repository),
                "rev-parse",
                "--is-shallow-repository",
            )
            .decode("ascii")
            .strip()
        )
        if object_format != "sha1" or shallow != "false":
            raise FinalizationRefused("history bundle has an unsupported Git topology")
        revisions = [to_revision]
        if from_revision is not None:
            revisions.append(from_revision)
        for revision in revisions:
            _run_git(
                git_binary,
                "-C",
                str(repository),
                "cat-file",
                "-e",
                f"{revision}^{{commit}}",
            )
        if (repository / "objects" / "info" / "alternates").exists():
            raise FinalizationRefused("history bundle must not use object alternates")
    return ApplicationHistorySnapshotV1(
        server_origin=application.server_origin,
        api_origin=application.api_url,
        repository_id=application.repository_id,
        repository=application.repository,
        object_format="sha1",
        from_revision=from_revision,
        to_revision=to_revision,
        bundle_name=HISTORY_NAME,
        bundle_size=len(bundle_bytes),
        bundle_sha256=_sha256(bundle_bytes),
    )


def build_authorization_evidence(
    *,
    workflow_run: GitHubWorkflowRunV1,
    envelope: DeploymentExecutionEnvelopeV1,
    controller_release: DeploymentControllerReleaseEvidenceV1,
    application_history: ApplicationHistorySnapshotV1,
) -> DeploymentAuthorizationEvidenceV1:
    authorizer = envelope.authorizer
    if (
        workflow_run.repository != authorizer.repository
        or workflow_run.workflow_path != authorizer.workflow_path
        or workflow_run.workflow_revision != authorizer.workflow_revision
        or workflow_run.run_id != authorizer.run_id
    ):
        raise FinalizationRefused(
            "successful workflow run does not match the execution authorizer"
        )
    current_revision = (
        None
        if envelope.expected_current is None
        else envelope.expected_current.source_revision
    )
    relation = envelope.relation_evidence
    if (
        application_history.from_revision != current_revision
        or application_history.to_revision != envelope.candidate.source_revision
        or relation.from_revision != application_history.from_revision
        or relation.to_revision != application_history.to_revision
        or relation.history_snapshot_digest != application_history.snapshot_digest
    ):
        raise FinalizationRefused(
            "execution relation is not bound to the verified application history"
        )
    bind_controller_release(envelope, controller_release)
    return DeploymentAuthorizationEvidenceV1(
        workflow_run=workflow_run,
        execution_envelope_digest=envelope.envelope_digest,
        controller_release_evidence_digest=controller_release.evidence_digest,
        application_history=application_history,
    )


def sign_evidence(
    evidence: DeploymentAuthorizationEvidenceV1,
    *,
    key_id: str,
    private_key_path: Path,
    openssl_path: Path,
) -> DetachedEvidenceSignatureV1:
    _text(key_id, name="key ID", pattern=_KEY_ID)
    if not private_key_path.is_absolute() or not openssl_path.is_absolute():
        raise FinalizationRefused("signing key and OpenSSL paths must be absolute")
    try:
        key_metadata = private_key_path.stat()
        openssl_metadata = openssl_path.stat()
    except OSError as exc:
        raise FinalizationRefused("signing authority is not installed") from exc
    if (
        not stat.S_ISREG(key_metadata.st_mode)
        or key_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(key_metadata.st_mode) != 0o600
        or not stat.S_ISREG(openssl_metadata.st_mode)
        or not openssl_metadata.st_mode & 0o111
        or openssl_metadata.st_mode & 0o022
    ):
        raise FinalizationRefused("signing authority paths have unsafe metadata")
    payload = signing_payload_bytes(
        purpose=EvidencePurpose.AUTHORIZATION,
        key_id=key_id,
        payload_schema=AUTHORIZATION_EVIDENCE_SCHEMA,
        document=evidence.to_document(),
    )
    try:
        completed = subprocess.run(
            [
                str(openssl_path),
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private_key_path),
                "-rawin",
            ],
            input=payload,
            check=False,
            capture_output=True,
            timeout=15,
            cwd="/",
            env={"LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FinalizationRefused(
            "authorization evidence signing failed closed"
        ) from exc
    if completed.returncode != 0 or len(completed.stdout) != 64:
        raise FinalizationRefused(
            "authorization evidence signing did not produce Ed25519"
        )
    return DetachedEvidenceSignatureV1(
        purpose=EvidencePurpose.AUTHORIZATION,
        key_id=key_id,
        payload_schema=AUTHORIZATION_EVIDENCE_SCHEMA,
        payload_sha256=_sha256(canonical_json_bytes(evidence.to_document())),
        signature_b64=base64.b64encode(completed.stdout).decode("ascii"),
    )


def _write_exclusive(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written < 1:
                raise FinalizationRefused(f"could not write {path.name}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_outputs(
    *,
    output: Path,
    source_bundle: Path,
    evidence: DeploymentAuthorizationEvidenceV1,
    signature: DetachedEvidenceSignatureV1,
) -> tuple[Path, Path, Path]:
    if output.exists():
        raise FinalizationRefused("authorization output directory already exists")
    output.mkdir(mode=0o700, parents=False)
    bundle_path = output / HISTORY_NAME
    evidence_path = output / EVIDENCE_NAME
    signature_path = output / SIGNATURE_NAME
    try:
        bundle_bytes = source_bundle.read_bytes()
    except OSError as exc:
        raise FinalizationRefused("verified history bundle became unreadable") from exc
    if (
        len(bundle_bytes) != evidence.application_history.bundle_size
        or _sha256(bundle_bytes) != evidence.application_history.bundle_sha256
    ):
        raise FinalizationRefused("verified history bundle changed before output")
    _write_exclusive(bundle_path, bundle_bytes)
    _write_exclusive(evidence_path, canonical_json_bytes(evidence.to_document()))
    _write_exclusive(signature_path, canonical_json_bytes(signature.to_document()))
    directory = os.open(output, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return evidence_path, signature_path, bundle_path


def _workflow_blob_url(api_base: str, expected: ExpectedRun) -> str:
    path = urllib.parse.quote(expected.workflow_path, safe="/")
    revision = urllib.parse.quote(expected.head_sha, safe="")
    return f"{api_base}/contents/{path}?ref={revision}"


def finalize(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    expected = ExpectedRun(
        server_origin=args.server_origin,
        api_url=args.api_url,
        repository_id=args.repository_id,
        repository=args.repository,
        head_repository_id=args.head_repository_id,
        head_repository=args.head_repository,
        workflow_id=args.workflow_id,
        workflow_name=args.workflow_name,
        workflow_path=args.workflow_path,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        event=args.event,
        head_sha=args.head_sha,
        status=args.status,
        conclusion=args.conclusion,
    )
    application = ExpectedApplicationRepository(
        server_origin=args.application_server_origin,
        api_url=args.application_api_url,
        repository_id=args.application_repository_id,
        repository=args.application_repository,
    )
    if (
        application.server_origin != expected.server_origin
        or application.api_url != expected.api_url
    ):
        raise FinalizationRefused(
            "authorizer and application APIs must share the trusted server"
        )
    reader = HttpReader(
        server_origin=expected.server_origin,
        api_url=expected.api_url,
        github_token=os.environ.get("GITHUB_TOKEN", ""),
    )
    authorizer_api = f"{expected.api_url}/repos/{expected.repository}"
    run_document = reader.github_json(
        f"{authorizer_api}/actions/runs/{expected.run_id}",
        name="authorization workflow run",
    )
    workflow_document = reader.github_json(
        f"{authorizer_api}/actions/workflows/{expected.workflow_id}",
        name="authorization workflow identity",
    )
    branch, _head = _head_ref(run_document)
    branch_document = reader.github_json(
        f"{authorizer_api}/branches/{urllib.parse.quote(branch, safe='')}",
        name="authorization protected branch",
    )
    referenced_workflows = _referenced_workflows(
        reader=reader,
        expected=expected,
        run_document=run_document,
    )
    workflow_run = validate_authorizer_run(
        run_document=run_document,
        repository_document=reader.github_json(
            authorizer_api, name="authorizer repository"
        ),
        workflow_document=workflow_document,
        workflow_blob=reader.github_blob(
            _workflow_blob_url(authorizer_api, expected),
            name="authorization workflow blob",
        ),
        branch_document=branch_document,
        referenced_workflows=referenced_workflows,
        expected=expected,
    )
    application_api = f"{application.api_url}/repos/{application.repository}"
    validate_application_repository(
        reader.github_json(application_api, name="application repository"),
        expected=application,
    )
    envelope = load_envelope(Path(args.execution_envelope))
    controller_release = load_controller_release_evidence(
        Path(args.controller_release_evidence)
    )
    revisions = [envelope.candidate.source_revision]
    if envelope.expected_current is not None:
        revisions.append(envelope.expected_current.source_revision)
    for revision in revisions:
        validate_application_commit(
            reader.github_json(
                f"{application_api}/commits/{revision}",
                name=f"application commit {revision}",
            ),
            revision=revision,
        )
    history = verify_history_bundle(
        bundle=Path(args.history_bundle),
        git_binary=Path(args.git_bin),
        application=application,
        from_revision=(
            None
            if envelope.expected_current is None
            else envelope.expected_current.source_revision
        ),
        to_revision=envelope.candidate.source_revision,
    )
    evidence = build_authorization_evidence(
        workflow_run=workflow_run,
        envelope=envelope,
        controller_release=controller_release,
        application_history=history,
    )
    signature = sign_evidence(
        evidence,
        key_id=args.key_id,
        private_key_path=Path(args.private_key_path),
        openssl_path=Path(args.openssl_path),
    )
    return write_outputs(
        output=Path(args.output_dir),
        source_bundle=Path(args.history_bundle),
        evidence=evidence,
        signature=signature,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-origin", required=True)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--repository-id", type=int, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-repository-id", type=int, required=True)
    parser.add_argument("--head-repository", required=True)
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--workflow-name", required=True)
    parser.add_argument("--workflow-path", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--conclusion", required=True)
    parser.add_argument("--application-server-origin", required=True)
    parser.add_argument("--application-api-url", required=True)
    parser.add_argument("--application-repository-id", type=int, required=True)
    parser.add_argument("--application-repository", required=True)
    parser.add_argument("--execution-envelope", required=True)
    parser.add_argument("--controller-release-evidence", required=True)
    parser.add_argument("--history-bundle", required=True)
    parser.add_argument("--git-bin", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--private-key-path", required=True)
    parser.add_argument("--openssl-path", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main() -> int:
    try:
        evidence, signature, bundle = finalize(_parser().parse_args())
    except (FinalizationRefused, SpecError, OSError, ValueError) as exc:
        print(f"authorization finalization refused: {exc}", file=sys.stderr)
        return 1
    print(
        "wrote authenticated authorization evidence: "
        f"{evidence.name}, {signature.name}, {bundle.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
