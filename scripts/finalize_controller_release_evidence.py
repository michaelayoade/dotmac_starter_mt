#!/usr/bin/env python3
"""Finalize authenticated controller-release evidence after the release run.

This program is deliberately a *post-completion* finalizer.  A workflow cannot
truthfully sign its own conclusion while it is still running, so the release
lane never calls this file.  ``workflow_run`` supplies a candidate run ID and
this program independently re-reads the terminal run, repository, workflow,
workflow blob, tag, Actions artifact and all three registry files before it
constructs and signs ``DeploymentControllerReleaseEvidence.v1``. The run must
come from a re-queried protected branch; any reusable workflows are separately
resolved and content-bound, while branch movement after the run is allowed.

The signing key is only ever named by an absolute file path.  Python never
reads it; the protected environment projects it into a mode-0600 temporary
file and an absolute OpenSSL executable consumes it.  No refusal includes key
material, registry credentials or response bodies.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import html.parser
import http.client
import io
import json
import os
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Final

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(
    0,
    str(REPO_ROOT / "packages" / "dotmac-deployment-foundation" / "src"),
)

from dotmac_deployment_foundation.authenticity import (  # noqa: E402
    RELEASE_EVIDENCE_SCHEMA,
    DeploymentControllerReleaseArtifactV1,
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

RELEASE_WORKFLOW_NAME: Final = "Release facility"
RELEASE_WORKFLOW_PATH: Final = ".github/workflows/release-facility.yml"
RELEASE_EVENT: Final = "workflow_dispatch"
CONTROLLER_DISTRIBUTION: Final = "dotmac-deployment-foundation"
CONTROLLER_ARTIFACT_PREFIX: Final = f"{CONTROLLER_DISTRIBUTION}-controller-"
CONTROLLER_GENERIC_PACKAGE: Final = "dotmac-deployment-controller"
RECEIPT_NAME: Final = "DeploymentControllerReleaseReceipt.v1.json"
EVIDENCE_NAME: Final = "DeploymentControllerReleaseEvidence.v1.json"
SIGNATURE_NAME: Final = "DetachedEvidenceSignature.v1.json"
LAUNCHER_NAME: Final = "run_deployment_controller.py"

_REVISION = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:a[0-9]+)?$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORKFLOW = re.compile(r"^\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml$")
_BRANCH = re.compile(r"^(?!/)(?!.*(?:\.\.|//|@\{|\\))(?!.*[/.]$)[A-Za-z0-9._/-]+$")
_GIT_REF = re.compile(r"^refs/(?:heads|tags)/[^\x00-\x20~^:?*\\[\\]]+$")
_MAX_JSON = 4 * 1024 * 1024
_MAX_INDEX = 4 * 1024 * 1024
_MAX_ARTIFACT = 128 * 1024 * 1024
_MAX_MEMBER = 64 * 1024 * 1024


class FinalizationRefused(RuntimeError):
    """A missing, ambiguous or substituted release proof."""


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _object(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise FinalizationRefused(f"{name} must be an object")
    return dict(value)


def _text(value: object, *, name: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise FinalizationRefused(f"{name} must be non-empty trimmed text")
    if pattern is not None and not pattern.fullmatch(value):
        raise FinalizationRefused(f"{name} has an invalid shape")
    return value


def _positive(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FinalizationRefused(f"{name} must be a positive integer")
    return value


def _exact(value: object, expected: object, *, name: str) -> None:
    if value != expected:
        raise FinalizationRefused(f"{name} does not match the triggered release")


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
    """Derive the only API origin to which this server's token may be sent."""

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


class _CredentialStrippingRedirect(urllib.request.HTTPRedirectHandler):
    """Follow artifact redirects without forwarding the GitHub token."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: IO[bytes],
        code: int,
        message: str,
        headers: http.client.HTTPMessage,
        new_url: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )
        if redirected is None:
            return None
        old = urllib.parse.urlsplit(request.full_url)
        new = urllib.parse.urlsplit(new_url)
        if (old.scheme, old.hostname, old.port) != (new.scheme, new.hostname, new.port):
            redirected.remove_header("Authorization")
        return redirected


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
        return None


class HttpReader:
    """Bounded HTTP reader with separate GitHub and registry credentials."""

    def __init__(
        self,
        *,
        server_origin: str,
        api_url: str,
        github_token: str,
        registry_username: str,
        registry_token: str,
    ) -> None:
        self._github_api = _require_api_origin(
            server_origin=server_origin,
            api_url=api_url,
            name="GitHub API origin",
        )
        if not github_token or not registry_username or not registry_token:
            raise FinalizationRefused("required API credentials are not provisioned")
        self._github = f"Bearer {github_token}"
        basic = base64.b64encode(
            f"{registry_username}:{registry_token}".encode()
        ).decode("ascii")
        self._registry = f"Basic {basic}"
        self._github_opener = urllib.request.build_opener(
            _CredentialStrippingRedirect()
        )
        self._registry_opener = urllib.request.build_opener(_NoRedirect())

    @staticmethod
    def _read_bounded(response: object, *, limit: int, name: str) -> bytes:
        reader = getattr(response, "read", None)
        if not callable(reader):
            raise FinalizationRefused(f"{name} response is unreadable")
        data = reader(limit + 1)
        if not isinstance(data, bytes) or len(data) > limit:
            raise FinalizationRefused(f"{name} exceeds its size limit")
        return data

    def _get(
        self,
        url: str,
        *,
        authorization: str,
        limit: int,
        name: str,
        github_redirects: bool,
    ) -> bytes:
        request = urllib.request.Request(  # noqa: S310 -- HTTPS validated by callers
            url,
            headers={
                "Accept": "application/vnd.github+json"
                if authorization == self._github
                else "application/octet-stream",
                "Authorization": authorization,
                "User-Agent": "dotmac-controller-evidence-finalizer/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        opener = self._github_opener if github_redirects else self._registry_opener
        try:
            with opener.open(request, timeout=30) as response:
                status = getattr(response, "status", None)
                if status != 200:
                    raise FinalizationRefused(f"{name} returned HTTP {status}")
                return self._read_bounded(response, limit=limit, name=name)
        except FinalizationRefused:
            raise
        except urllib.error.HTTPError as exc:
            raise FinalizationRefused(f"{name} returned HTTP {exc.code}") from exc
        except (OSError, urllib.error.URLError) as exc:
            raise FinalizationRefused(f"{name} could not be read") from exc

    def github_json(self, url: str, *, name: str) -> dict[str, object]:
        if not url.startswith(f"{self._github_api}/"):
            raise FinalizationRefused(f"{name} URL leaves the trusted GitHub API")
        raw = self._get(
            url,
            authorization=self._github,
            limit=_MAX_JSON,
            name=name,
            github_redirects=False,
        )
        try:
            document = strict_json_loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, SpecError) as exc:
            raise FinalizationRefused(f"{name} is not strict JSON") from exc
        return _object(document, name=name)

    def github_artifact(self, url: str) -> bytes:
        if not url.startswith(f"{self._github_api}/"):
            raise FinalizationRefused(
                "Actions artifact URL leaves the trusted GitHub API"
            )
        return self._get(
            url,
            authorization=self._github,
            limit=_MAX_ARTIFACT,
            name="Actions artifact archive",
            github_redirects=True,
        )

    def registry_bytes(self, url: str, *, name: str, limit: int) -> bytes:
        return self._get(
            url,
            authorization=self._registry,
            limit=limit,
            name=name,
            github_redirects=False,
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
            name="GitHub API URL",
        )
        _positive(self.repository_id, name="repository ID")
        _text(self.repository, name="repository", pattern=_REPOSITORY)
        _positive(self.head_repository_id, name="head repository ID")
        _text(self.head_repository, name="head repository", pattern=_REPOSITORY)
        _positive(self.workflow_id, name="workflow ID")
        _positive(self.run_id, name="run ID")
        _positive(self.run_attempt, name="run attempt")
        _text(self.head_sha, name="head SHA", pattern=_REVISION)
        if self.event != RELEASE_EVENT:
            raise FinalizationRefused("release run event must be workflow_dispatch")
        if self.status != "completed" or self.conclusion != "success":
            raise FinalizationRefused("release run is not completed successfully")
        if (
            self.repository_id != self.head_repository_id
            or self.repository != self.head_repository
        ):
            raise FinalizationRefused("release run did not execute from its repository")


@dataclass(frozen=True, slots=True)
class ReleaseBundle:
    version: str
    wheel_name: str
    wheel: bytes
    launcher: bytes
    receipt: bytes
    receipt_document: dict[str, object]


class _Links(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value is not None:
                self.hrefs.append(value)


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
        raise FinalizationRefused("release run head branch is not protected")


def _workflow_path(value: object, *, expected: str, head_ref: str) -> str:
    raw = _text(value, name="run workflow path")
    path, separator, workflow_ref = raw.rpartition("@")
    if not separator:
        path = raw
    elif workflow_ref not in {head_ref, head_ref.removeprefix("refs/heads/")}:
        raise FinalizationRefused("run workflow path ref is not the protected head ref")
    _text(path, name="run workflow path", pattern=_WORKFLOW)
    _exact(path, expected, name="run workflow path")
    return path


def _referenced_identity(
    value: object,
) -> tuple[str, str, str, str]:
    item = _object(value, name="referenced workflow")
    if set(item) != {"path", "sha", "ref"}:
        raise FinalizationRefused("referenced workflow fields are not exact")
    raw_path = _text(item.get("path"), name="referenced workflow path")
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
    resolved_ref = _text(item.get("ref"), name="referenced workflow ref")
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
        blob = reader.github_json(
            _api_url(
                expected.api_url,
                f"/repos/{encoded_repository}/contents/{encoded_path}"
                f"?ref={encoded_revision}",
            ),
            name=f"referenced workflow blob {repository}/{workflow_path}",
        )
        _exact(blob.get("type"), "file", name="referenced workflow content type")
        _exact(blob.get("path"), workflow_path, name="referenced workflow content path")
        content = blob.get("content")
        _exact(blob.get("encoding"), "base64", name="referenced workflow encoding")
        if not isinstance(content, str) or not content:
            raise FinalizationRefused("referenced workflow content is absent")
        try:
            workflow_blob = base64.b64decode(
                "".join(content.splitlines()), validate=True
            )
        except (binascii.Error, ValueError) as exc:
            raise FinalizationRefused(
                "referenced workflow content is not canonical base64"
            ) from exc
        if not workflow_blob:
            raise FinalizationRefused("referenced workflow blob is empty")
        result.append(
            GitHubReferencedWorkflowV1(
                repository=repository,
                workflow_path=workflow_path,
                workflow_ref=workflow_ref,
                workflow_revision=revision,
                workflow_blob_sha256=_sha256(workflow_blob),
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: canonical_json_bytes(item.to_document()),
        )
    )


def validate_workflow_run(
    *,
    run_document: dict[str, object],
    repository_document: dict[str, object],
    workflow_document: dict[str, object],
    workflow_blob: bytes,
    branch_document: dict[str, object],
    referenced_workflows: tuple[GitHubReferencedWorkflowV1, ...],
    expected: ExpectedRun,
) -> GitHubWorkflowRunV1:
    """Bind the trigger to an independently queried terminal workflow run."""

    _exact(run_document.get("id"), expected.run_id, name="run ID")
    _exact(run_document.get("run_attempt"), expected.run_attempt, name="run attempt")
    _exact(run_document.get("workflow_id"), expected.workflow_id, name="workflow ID")
    _exact(run_document.get("event"), expected.event, name="run event")
    _exact(run_document.get("head_sha"), expected.head_sha, name="run head SHA")
    _exact(run_document.get("status"), expected.status, name="run status")
    _exact(run_document.get("conclusion"), expected.conclusion, name="run conclusion")
    branch, head_ref = _head_ref(run_document)
    _validate_protected_branch(branch_document, branch=branch)
    _workflow_path(
        run_document.get("path"), expected=RELEASE_WORKFLOW_PATH, head_ref=head_ref
    )

    run_repository = _object(run_document.get("repository"), name="run repository")
    head_repository = _object(
        run_document.get("head_repository"), name="run head repository"
    )
    _exact(run_repository.get("id"), expected.repository_id, name="run repository ID")
    _exact(
        run_repository.get("full_name"),
        expected.repository,
        name="run repository name",
    )
    _exact(
        head_repository.get("id"),
        expected.head_repository_id,
        name="run head repository ID",
    )
    _exact(
        head_repository.get("full_name"),
        expected.head_repository,
        name="run head repository name",
    )

    _exact(
        repository_document.get("id"),
        expected.repository_id,
        name="repository API ID",
    )
    _exact(
        repository_document.get("full_name"),
        expected.repository,
        name="repository API name",
    )
    _exact(workflow_document.get("id"), expected.workflow_id, name="workflow API ID")
    _exact(
        workflow_document.get("name"),
        RELEASE_WORKFLOW_NAME,
        name="workflow API name",
    )
    _exact(
        workflow_document.get("path"),
        RELEASE_WORKFLOW_PATH,
        name="workflow API path",
    )
    _exact(workflow_document.get("state"), "active", name="workflow API state")

    return GitHubWorkflowRunV1(
        server_origin=expected.server_origin,
        api_origin=expected.api_url,
        repository_id=expected.repository_id,
        repository=expected.repository,
        head_repository_id=expected.head_repository_id,
        head_repository=expected.head_repository,
        workflow_id=expected.workflow_id,
        workflow_path=RELEASE_WORKFLOW_PATH,
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


def _strict_receipt(
    raw: bytes,
    *,
    version: str,
    run: ExpectedRun,
    wheel_name: str,
    wheel: bytes,
    launcher: bytes,
) -> dict[str, object]:
    try:
        parsed = strict_json_loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, SpecError) as exc:
        raise FinalizationRefused("controller receipt is not strict JSON") from exc
    receipt = _object(parsed, name="controller receipt")
    expected_fields = {
        "schema",
        "distribution",
        "exact_version",
        "artifact_sha256",
        "launcher_sha256",
        "source_revision",
        "release_run_id",
        "tag",
    }
    if set(receipt) != expected_fields:
        raise FinalizationRefused("controller receipt fields are not exact")
    tag = f"{CONTROLLER_DISTRIBUTION}-v{version}"
    expected = {
        "schema": "DeploymentControllerReleaseReceipt.v1",
        "distribution": CONTROLLER_DISTRIBUTION,
        "exact_version": version,
        "artifact_sha256": _sha256(wheel),
        "launcher_sha256": _sha256(launcher),
        "source_revision": run.head_sha,
        "release_run_id": run.run_id,
        "tag": tag,
    }
    if receipt != expected:
        raise FinalizationRefused("controller receipt does not bind the exact release")
    wheel_prefix = f"{CONTROLLER_DISTRIBUTION.replace('-', '_')}-{version}-"
    if not wheel_name.startswith(wheel_prefix) or not wheel_name.endswith(".whl"):
        raise FinalizationRefused(
            "controller wheel filename disagrees with the release"
        )
    return receipt


def read_release_bundle(
    archive: bytes, *, version: str, run: ExpectedRun
) -> ReleaseBundle:
    """Read one exact three-file Actions artifact without extracting it."""

    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except (OSError, zipfile.BadZipFile) as exc:
        raise FinalizationRefused(
            "Actions artifact is not a valid ZIP archive"
        ) from exc
    with bundle:
        infos = bundle.infolist()
        names = [info.filename for info in infos if not info.is_dir()]
        if len(names) != len(set(names)):
            raise FinalizationRefused("Actions artifact contains duplicate filenames")
        for info in infos:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or info.flag_bits & 0x1:
                raise FinalizationRefused("Actions artifact has an unsafe member")
            if info.file_size > _MAX_MEMBER:
                raise FinalizationRefused("Actions artifact member is too large")
        wheels = [name for name in names if name.startswith("wheel/")]
        expected_fixed = {RECEIPT_NAME, f"launcher/{LAUNCHER_NAME}"}
        if len(wheels) != 1 or set(names) != expected_fixed | set(wheels):
            raise FinalizationRefused(
                "Actions artifact must contain exactly three files"
            )
        wheel_name = PurePosixPath(wheels[0]).name
        wheel = bundle.read(wheels[0])
        launcher = bundle.read(f"launcher/{LAUNCHER_NAME}")
        receipt = bundle.read(RECEIPT_NAME)
    if not wheel or not launcher or not receipt:
        raise FinalizationRefused("Actions artifact contains an empty release file")
    receipt_document = _strict_receipt(
        receipt,
        version=version,
        run=run,
        wheel_name=wheel_name,
        wheel=wheel,
        launcher=launcher,
    )
    return ReleaseBundle(
        version=version,
        wheel_name=wheel_name,
        wheel=wheel,
        launcher=launcher,
        receipt=receipt,
        receipt_document=receipt_document,
    )


def _artifact_archive(
    *, reader: HttpReader, expected: ExpectedRun
) -> tuple[ReleaseBundle, dict[str, object]]:
    base = f"{expected.api_url}/repos/{expected.repository}"
    listing = reader.github_json(
        f"{base}/actions/runs/{expected.run_id}/artifacts?per_page=100",
        name="release run artifact listing",
    )
    total = _positive(listing.get("total_count"), name="artifact total count")
    raw_artifacts = listing.get("artifacts")
    if not isinstance(raw_artifacts, list) or total != len(raw_artifacts):
        raise FinalizationRefused("artifact listing is incomplete or ambiguous")
    candidates: list[tuple[dict[str, object], str]] = []
    for raw in raw_artifacts:
        artifact = _object(raw, name="Actions artifact")
        name = artifact.get("name")
        if isinstance(name, str) and name.startswith(CONTROLLER_ARTIFACT_PREFIX):
            candidates.append((artifact, name.removeprefix(CONTROLLER_ARTIFACT_PREFIX)))
    if len(candidates) != 1:
        raise FinalizationRefused(
            "release run must have one controller Actions artifact"
        )
    artifact, version = candidates[0]
    _text(version, name="controller artifact version", pattern=_VERSION)
    artifact_id = _positive(artifact.get("id"), name="artifact ID")
    if artifact.get("expired") is not False:
        raise FinalizationRefused("controller Actions artifact is expired")
    _positive(artifact.get("size_in_bytes"), name="artifact size")
    archive_digest = _text(
        artifact.get("digest"), name="artifact archive digest", pattern=_DIGEST
    )
    artifact_run = _object(artifact.get("workflow_run"), name="artifact workflow run")
    _exact(artifact_run.get("id"), expected.run_id, name="artifact run ID")
    _exact(
        artifact_run.get("head_sha"), expected.head_sha, name="artifact run head SHA"
    )
    _exact(
        artifact_run.get("repository_id"),
        expected.repository_id,
        name="artifact repository ID",
    )
    _exact(
        artifact_run.get("head_repository_id"),
        expected.head_repository_id,
        name="artifact head repository ID",
    )
    archive_url = f"{base}/actions/artifacts/{artifact_id}/zip"
    _exact(
        artifact.get("archive_download_url"),
        archive_url,
        name="artifact archive URL",
    )
    archive = reader.github_artifact(archive_url)
    if _sha256(archive) != archive_digest:
        raise FinalizationRefused("downloaded Actions artifact digest changed")
    return read_release_bundle(archive, version=version, run=expected), artifact


def _workflow_blob(reader: HttpReader, expected: ExpectedRun) -> bytes:
    path = urllib.parse.quote(RELEASE_WORKFLOW_PATH, safe="/")
    ref = urllib.parse.quote(expected.head_sha, safe="")
    document = reader.github_json(
        f"{expected.api_url}/repos/{expected.repository}/contents/{path}?ref={ref}",
        name="release workflow blob",
    )
    _exact(document.get("type"), "file", name="workflow content type")
    _exact(document.get("path"), RELEASE_WORKFLOW_PATH, name="workflow content path")
    _exact(document.get("encoding"), "base64", name="workflow content encoding")
    content = document.get("content")
    if not isinstance(content, str) or not content:
        raise FinalizationRefused("release workflow content is absent")
    encoded = "".join(content.splitlines())
    if not encoded or any(character.isspace() for character in encoded):
        raise FinalizationRefused("release workflow content has invalid whitespace")
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FinalizationRefused(
            "release workflow content is not canonical base64"
        ) from exc
    if not blob:
        raise FinalizationRefused("release workflow blob is empty")
    return blob


def _require_exact_tag(reader: HttpReader, expected: ExpectedRun, tag: str) -> None:
    base = f"{expected.api_url}/repos/{expected.repository}/git"
    encoded = urllib.parse.quote(tag, safe="")
    reference = reader.github_json(
        f"{base}/ref/tags/{encoded}", name="release tag reference"
    )
    _exact(reference.get("ref"), f"refs/tags/{tag}", name="release tag ref")
    tag_pointer = _object(reference.get("object"), name="release tag pointer")
    _exact(tag_pointer.get("type"), "tag", name="release tag object type")
    tag_sha = _text(tag_pointer.get("sha"), name="annotated tag SHA", pattern=_REVISION)
    annotated = reader.github_json(
        f"{base}/tags/{tag_sha}", name="annotated release tag"
    )
    _exact(annotated.get("tag"), tag, name="annotated release tag name")
    target = _object(annotated.get("object"), name="annotated tag target")
    _exact(target.get("type"), "commit", name="release tag target type")
    _exact(target.get("sha"), expected.head_sha, name="release tag target SHA")


def _registry_material(
    *,
    reader: HttpReader,
    registry_origin: str,
    bundle: ReleaseBundle,
) -> tuple[bytes, bytes, bytes]:
    origin = _canonical_https(registry_origin, name="registry origin")
    encoded_version = urllib.parse.quote(bundle.version, safe="")
    generic = (
        f"{origin}/api/packages/dotmac/generic/"
        f"{CONTROLLER_GENERIC_PACKAGE}/{encoded_version}"
    )
    registry_launcher = reader.registry_bytes(
        f"{generic}/{LAUNCHER_NAME}",
        name="registry controller launcher",
        limit=_MAX_MEMBER,
    )
    registry_receipt = reader.registry_bytes(
        f"{generic}/{RECEIPT_NAME}",
        name="registry controller receipt",
        limit=_MAX_MEMBER,
    )
    if registry_launcher != bundle.launcher or registry_receipt != bundle.receipt:
        raise FinalizationRefused(
            "generic registry bytes differ from the Actions artifact"
        )

    normalized = re.sub(r"[-_.]+", "-", CONTROLLER_DISTRIBUTION).lower()
    simple_url = f"{origin}/api/packages/dotmac/pypi/simple/{normalized}/"
    index = reader.registry_bytes(
        simple_url, name="controller simple index", limit=_MAX_INDEX
    )
    try:
        index_text = index.decode("utf-8")
    except UnicodeError as exc:
        raise FinalizationRefused("controller simple index is not UTF-8") from exc
    links = _Links()
    links.feed(index_text)
    matches: list[str] = []
    for href in links.hrefs:
        resolved = urllib.parse.urljoin(simple_url, href)
        parsed = urllib.parse.urlsplit(resolved)
        candidate_origin = f"{parsed.scheme}://{parsed.netloc}"
        filename = urllib.parse.unquote(PurePosixPath(parsed.path).name)
        if filename == bundle.wheel_name:
            if (
                candidate_origin != origin
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
            ):
                raise FinalizationRefused("controller wheel link leaves the registry")
            matches.append(parsed._replace(fragment="").geturl())
    if len(matches) != 1:
        raise FinalizationRefused("simple index must name the exact wheel once")
    registry_wheel = reader.registry_bytes(
        matches[0], name="registry controller wheel", limit=_MAX_MEMBER
    )
    if registry_wheel != bundle.wheel:
        raise FinalizationRefused(
            "PyPI registry wheel differs from the Actions artifact"
        )
    return registry_wheel, registry_launcher, registry_receipt


def build_evidence(
    *,
    workflow_run: GitHubWorkflowRunV1,
    bundle: ReleaseBundle,
    registry_material: tuple[bytes, bytes, bytes],
) -> DeploymentControllerReleaseEvidenceV1:
    wheel, launcher, receipt = registry_material
    artifacts = (
        DeploymentControllerReleaseArtifactV1(
            name=RECEIPT_NAME,
            media_type="application/json",
            size=len(receipt),
            sha256=_sha256(receipt),
        ),
        DeploymentControllerReleaseArtifactV1(
            name=bundle.wheel_name,
            media_type="application/octet-stream",
            size=len(wheel),
            sha256=_sha256(wheel),
        ),
        DeploymentControllerReleaseArtifactV1(
            name=LAUNCHER_NAME,
            media_type="text/x-python",
            size=len(launcher),
            sha256=_sha256(launcher),
        ),
    )
    return DeploymentControllerReleaseEvidenceV1(
        workflow_run=workflow_run,
        distribution=CONTROLLER_DISTRIBUTION,
        exact_version=bundle.version,
        tag=f"{CONTROLLER_DISTRIBUTION}-v{bundle.version}",
        source_revision=workflow_run.head_sha,
        artifacts=tuple(sorted(artifacts, key=lambda artifact: artifact.name)),
    )


def sign_evidence(
    evidence: DeploymentControllerReleaseEvidenceV1,
    *,
    key_id: str,
    private_key_path: Path,
    openssl_path: Path,
) -> DetachedEvidenceSignatureV1:
    _text(key_id, name="release evidence key ID", pattern=_KEY_ID)
    if not private_key_path.is_absolute() or not openssl_path.is_absolute():
        raise FinalizationRefused("signing key and OpenSSL paths must be absolute")
    try:
        key_metadata = os.lstat(private_key_path)
        openssl_metadata = os.lstat(openssl_path)
    except OSError as exc:
        raise FinalizationRefused("signing authority is not installed") from exc
    if (
        not stat.S_ISREG(key_metadata.st_mode)
        or key_metadata.st_mode & 0o077
        or not stat.S_ISREG(openssl_metadata.st_mode)
        or not openssl_metadata.st_mode & 0o111
    ):
        raise FinalizationRefused("signing authority paths have unsafe metadata")
    environment = {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}
    try:
        key_type = subprocess.run(
            [
                str(openssl_path),
                "pkey",
                "-in",
                str(private_key_path),
                "-text_pub",
                "-noout",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
            cwd="/",
            env=environment,
            close_fds=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FinalizationRefused("signing authority inspection failed closed") from exc
    if key_type.returncode != 0 or b"ED25519" not in key_type.stdout.upper():
        raise FinalizationRefused("release evidence key is not Ed25519")

    document = evidence.to_document()
    signed_bytes = signing_payload_bytes(
        purpose=EvidencePurpose.RELEASE,
        key_id=key_id,
        payload_schema=RELEASE_EVIDENCE_SCHEMA,
        document=document,
    )
    try:
        signed = subprocess.run(
            [
                str(openssl_path),
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private_key_path),
                "-rawin",
            ],
            input=signed_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
            cwd="/",
            env=environment,
            close_fds=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FinalizationRefused("release evidence signing failed closed") from exc
    if signed.returncode != 0 or len(signed.stdout) != 64:
        raise FinalizationRefused("release evidence signing did not produce Ed25519")
    return DetachedEvidenceSignatureV1(
        purpose=EvidencePurpose.RELEASE,
        key_id=key_id,
        payload_schema=RELEASE_EVIDENCE_SCHEMA,
        payload_sha256=_sha256(canonical_json_bytes(document)),
        signature_b64=base64.b64encode(signed.stdout).decode("ascii"),
    )


def _write_exclusive(path: Path, document: dict[str, object]) -> None:
    data = canonical_json_bytes(document) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def finalize(args: argparse.Namespace) -> tuple[Path, Path]:
    expected = ExpectedRun(
        server_origin=args.server_origin,
        api_url=args.api_url,
        repository_id=args.repository_id,
        repository=args.repository,
        head_repository_id=args.head_repository_id,
        head_repository=args.head_repository,
        workflow_id=args.workflow_id,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        event=args.event,
        head_sha=args.head_sha,
        status=args.status,
        conclusion=args.conclusion,
    )
    reader = HttpReader(
        server_origin=expected.server_origin,
        api_url=expected.api_url,
        github_token=os.environ.get("GITHUB_TOKEN", ""),
        registry_username=os.environ.get("FORGEJO_USERNAME", ""),
        registry_token=os.environ.get("FORGEJO_TOKEN", ""),
    )
    api_base = f"{expected.api_url}/repos/{expected.repository}"
    run_document = reader.github_json(
        f"{api_base}/actions/runs/{expected.run_id}", name="release workflow run"
    )
    repository_document = reader.github_json(api_base, name="authorizer repository")
    workflow_document = reader.github_json(
        f"{api_base}/actions/workflows/{expected.workflow_id}",
        name="release workflow identity",
    )
    workflow_blob = _workflow_blob(reader, expected)
    branch, _head = _head_ref(run_document)
    branch_document = reader.github_json(
        f"{api_base}/branches/{urllib.parse.quote(branch, safe='')}",
        name="release protected branch",
    )
    referenced_workflows = _referenced_workflows(
        reader=reader,
        expected=expected,
        run_document=run_document,
    )
    workflow_run = validate_workflow_run(
        run_document=run_document,
        repository_document=repository_document,
        workflow_document=workflow_document,
        workflow_blob=workflow_blob,
        branch_document=branch_document,
        referenced_workflows=referenced_workflows,
        expected=expected,
    )
    bundle, _artifact = _artifact_archive(reader=reader, expected=expected)
    tag = f"{CONTROLLER_DISTRIBUTION}-v{bundle.version}"
    _require_exact_tag(reader, expected, tag)
    registry_material = _registry_material(
        reader=reader,
        registry_origin=args.registry_origin,
        bundle=bundle,
    )
    evidence = build_evidence(
        workflow_run=workflow_run,
        bundle=bundle,
        registry_material=registry_material,
    )
    signature = sign_evidence(
        evidence,
        key_id=args.key_id,
        private_key_path=Path(args.private_key_path),
        openssl_path=Path(args.openssl_path),
    )

    output = Path(args.output_dir)
    if output.exists():
        raise FinalizationRefused("release evidence output directory already exists")
    output.mkdir(mode=0o700, parents=False)
    evidence_path = output / EVIDENCE_NAME
    signature_path = output / SIGNATURE_NAME
    _write_exclusive(evidence_path, evidence.to_document())
    _write_exclusive(signature_path, signature.to_document())
    directory = os.open(output, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return evidence_path, signature_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-origin", required=True)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--repository-id", type=int, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-repository-id", type=int, required=True)
    parser.add_argument("--head-repository", required=True)
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--conclusion", required=True)
    parser.add_argument("--registry-origin", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--private-key-path", required=True)
    parser.add_argument("--openssl-path", default="/usr/bin/openssl")
    parser.add_argument("--output-dir", required=True)
    return parser


def main() -> int:
    try:
        evidence, signature = finalize(_parser().parse_args())
    except (FinalizationRefused, SpecError, OSError, ValueError) as exc:
        print(f"controller release evidence refused: {exc}", file=sys.stderr)
        return 1
    print(f"wrote authenticated release evidence: {evidence.name}, {signature.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
