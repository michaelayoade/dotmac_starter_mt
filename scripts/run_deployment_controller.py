#!/usr/bin/env python3
"""Run one exact Deployment Foundation wheel outside the staged application.

This launcher is intentionally NOT imported from the wheel it verifies.  The
Foundation release lane publishes it beside a strict release receipt carrying
the launcher and wheel SHA-256 values. A current authorizing workflow pins the
launcher hash in the execution envelope before invocation; this file repeats
its own hash check, binds the receipt to that envelope, and verifies the
exact wheel before installing it in a fresh venv and executing with ``python
-I``. No private checkout of the Foundation source repository is needed by a
consumer.

That ordering is the control: an old application checkout may contain an old
``dotmac_deployment_foundation`` package or an old deploy script, but it never
gets onto the isolated controller interpreter's import path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

SCHEMA: Final = "DeploymentExecutionEnvelope.v1"
RECEIPT_SCHEMA: Final = "DeploymentControllerReleaseReceipt.v1"
RELEASE_EVIDENCE_SCHEMA: Final = "DeploymentControllerReleaseEvidence.v1"
AUTHORIZATION_EVIDENCE_SCHEMA: Final = "DeploymentAuthorizationEvidence.v1"
WORKFLOW_RUN_SCHEMA: Final = "GitHubWorkflowRunV1"
HISTORY_SNAPSHOT_SCHEMA: Final = "ApplicationHistorySnapshot.v1"
BOOTSTRAP_CONTEXT_SCHEMA: Final = "AuthenticatedDeploymentBootstrapContext.v1"
CONTROLLER_DISTRIBUTION: Final = "dotmac-deployment-foundation"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORKFLOW = re.compile(r"^\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml$")
_PROTECTED_REF = re.compile(r"^refs/(?:heads|tags)/[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_WORKFLOW_REF = re.compile(r"^(?:[0-9a-f]{40}|[A-Za-z0-9][A-Za-z0-9._/-]{0,254})$")


class LaunchRefused(RuntimeError):
    """The controller was not started because its root-of-trust check failed."""


def _strict_json_loads(text: str) -> object:
    def object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise LaunchRefused(f"duplicate JSON field {key!r}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=object_from_pairs)


def _object(value: object, *, name: str, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise LaunchRefused(f"{name} must be an object")
    document = dict(value)
    missing = sorted(keys - set(document))
    extra = sorted(set(document) - keys)
    if missing or extra:
        raise LaunchRefused(f"{name} fields differ: missing={missing}, unknown={extra}")
    return document


def _text(value: object, *, name: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LaunchRefused(f"{name} must be a non-empty string")
    result = value.strip()
    if pattern is not None and not pattern.fullmatch(result):
        raise LaunchRefused(f"{name} has an invalid shape")
    return result


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LaunchRefused(f"{name} must be a positive integer")
    return value


def _https_location(value: object, *, name: str, api: bool = False) -> str:
    location = _text(value, name=name)
    parsed = urlsplit(location)
    try:
        port = parsed.port
    except ValueError as exc:
        raise LaunchRefused(f"{name} has an invalid port") from exc
    allowed_paths = ("", "/api/v3") if api else ("",)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in allowed_paths
        or parsed.query
        or parsed.fragment
    ):
        raise LaunchRefused(
            f"{name} must be a canonical credential-free HTTPS location"
        )
    canonical = f"https://{parsed.hostname.lower()}"
    if port is not None:
        canonical += f":{port}"
    canonical += parsed.path
    if location != canonical:
        raise LaunchRefused(f"{name} is not canonically spelled")
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
        raise LaunchRefused(f"{name} has an invalid Git ref")
    return ref


def _document_digest(document: dict[str, Any]) -> str:
    payload = json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _envelope_digest(document: dict[str, Any]) -> str:
    payload = json.dumps(
        {"kind": "DeploymentExecutionEnvelopeV1", **document},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _read_json(path: Path, *, name: str) -> dict[str, Any]:
    try:
        raw = _strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LaunchRefused(f"cannot read {name} {path}: {exc}") from exc
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise LaunchRefused(f"{name} must be a JSON object")
    return dict(raw)


def _workflow_run(value: object, *, name: str) -> dict[str, Any]:
    run = _object(
        value,
        name=name,
        keys=frozenset(
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
                "head_ref",
                "referenced_workflows",
                "run_id",
                "run_attempt",
                "event",
                "head_sha",
                "status",
                "conclusion",
            }
        ),
    )
    if run["schema"] != WORKFLOW_RUN_SCHEMA:
        raise LaunchRefused(f"{name} has an unknown schema")
    _https_location(run["server_origin"], name=f"{name} server_origin")
    _https_location(run["api_origin"], name=f"{name} api_origin", api=True)
    repository = _text(
        run["repository"], name=f"{name} repository", pattern=_REPOSITORY
    )
    head_repository = _text(
        run["head_repository"],
        name=f"{name} head_repository",
        pattern=_REPOSITORY,
    )
    repository_id = _positive_int(run["repository_id"], name=f"{name} repository_id")
    if (
        _positive_int(run["head_repository_id"], name=f"{name} head_repository_id")
        != repository_id
        or head_repository != repository
    ):
        raise LaunchRefused(f"{name} did not run from the protected repository")
    _positive_int(run["workflow_id"], name=f"{name} workflow_id")
    _positive_int(run["run_id"], name=f"{name} run_id")
    _positive_int(run["run_attempt"], name=f"{name} run_attempt")
    _text(run["workflow_path"], name=f"{name} workflow_path", pattern=_WORKFLOW)
    _text(
        run["workflow_revision"],
        name=f"{name} workflow_revision",
        pattern=_REVISION,
    )
    _text(
        run["workflow_blob_sha256"],
        name=f"{name} workflow_blob_sha256",
        pattern=_DIGEST,
    )
    _ref(run["head_ref"], name=f"{name} head_ref", pattern=_PROTECTED_REF)
    raw_referenced = run["referenced_workflows"]
    if not isinstance(raw_referenced, list):
        raise LaunchRefused(f"{name} referenced_workflows must be a list")
    referenced_documents: list[bytes] = []
    for item in raw_referenced:
        referenced = _object(
            item,
            name=f"{name} referenced workflow",
            keys=frozenset(
                {
                    "repository",
                    "workflow_path",
                    "workflow_ref",
                    "workflow_revision",
                    "workflow_blob_sha256",
                }
            ),
        )
        _text(
            referenced["repository"],
            name="referenced workflow repository",
            pattern=_REPOSITORY,
        )
        _text(
            referenced["workflow_path"],
            name="referenced workflow path",
            pattern=_WORKFLOW,
        )
        _ref(
            referenced["workflow_ref"],
            name="referenced workflow ref",
            pattern=_WORKFLOW_REF,
        )
        _text(
            referenced["workflow_revision"],
            name="referenced workflow revision",
            pattern=_REVISION,
        )
        _text(
            referenced["workflow_blob_sha256"],
            name="referenced workflow blob digest",
            pattern=_DIGEST,
        )
        referenced_documents.append(
            json.dumps(
                referenced,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        )
    if len(referenced_documents) != len(set(referenced_documents)) or (
        referenced_documents != sorted(referenced_documents)
    ):
        raise LaunchRefused(
            f"{name} referenced_workflows must be unique and canonically sorted"
        )
    _text(run["head_sha"], name=f"{name} head_sha", pattern=_REVISION)
    _text(run["event"], name=f"{name} event")
    if run["status"] != "completed" or run["conclusion"] != "success":
        raise LaunchRefused(f"{name} did not complete successfully")
    return run


def _load_release_evidence(
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any], tuple[dict[str, Any], ...]]:
    evidence = _object(
        _read_json(path, name="controller release evidence"),
        name="controller release evidence",
        keys=frozenset(
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
    if evidence["schema"] != RELEASE_EVIDENCE_SCHEMA:
        raise LaunchRefused("controller release evidence has an unknown schema")
    run = _workflow_run(evidence["workflow_run"], name="release workflow run")
    raw_artifacts = evidence["artifacts"]
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise LaunchRefused("controller release evidence has no artifacts")
    artifacts: list[dict[str, Any]] = []
    for raw in raw_artifacts:
        artifact = _object(
            raw,
            name="controller release artifact",
            keys=frozenset({"schema", "name", "media_type", "size", "sha256"}),
        )
        _positive_int(artifact["size"], name="release artifact size")
        _text(artifact["sha256"], name="release artifact sha256", pattern=_DIGEST)
        artifacts.append(artifact)
    names = [str(item["name"]) for item in artifacts]
    if names != sorted(names) or len(names) != len(set(names)):
        raise LaunchRefused(
            "controller release artifact names are not unique and sorted"
        )
    return evidence, run, tuple(artifacts)


def _load_authorization_evidence(
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    evidence = _object(
        _read_json(path, name="deployment authorization evidence"),
        name="deployment authorization evidence",
        keys=frozenset(
            {
                "schema",
                "workflow_run",
                "execution_envelope_digest",
                "controller_release_evidence_digest",
                "application_history",
            }
        ),
    )
    if evidence["schema"] != AUTHORIZATION_EVIDENCE_SCHEMA:
        raise LaunchRefused("deployment authorization evidence has an unknown schema")
    run = _workflow_run(evidence["workflow_run"], name="authorization workflow run")
    for field in (
        "execution_envelope_digest",
        "controller_release_evidence_digest",
    ):
        _text(evidence[field], name=field, pattern=_DIGEST)
    history = _object(
        evidence["application_history"],
        name="application history snapshot",
        keys=frozenset(
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
    if history["schema"] != HISTORY_SNAPSHOT_SCHEMA:
        raise LaunchRefused("application history snapshot has an unknown schema")
    _https_location(history["server_origin"], name="history server_origin")
    _https_location(history["api_origin"], name="history api_origin", api=True)
    _text(history["repository"], name="history repository", pattern=_REPOSITORY)
    _positive_int(history["repository_id"], name="history repository_id")
    _text(history["to_revision"], name="history to_revision", pattern=_REVISION)
    if history["from_revision"] is not None:
        _text(history["from_revision"], name="history from_revision", pattern=_REVISION)
    if history["object_format"] != "sha1":
        raise LaunchRefused("application history snapshot has an unknown object format")
    _positive_int(history["bundle_size"], name="history bundle_size")
    _text(history["bundle_sha256"], name="history bundle_sha256", pattern=_DIGEST)
    return evidence, run, history


def _load_bootstrap_context(file_descriptor: int) -> dict[str, Any]:
    if file_descriptor < 0:
        raise LaunchRefused("bootstrap context descriptor must be non-negative")
    metadata = os.fstat(file_descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o400
    ):
        raise LaunchRefused(
            "bootstrap context must be a root-owned regular file with mode 0400"
        )
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    raw = os.read(file_descriptor, 1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise LaunchRefused("bootstrap context exceeds one MiB")
    try:
        document = _strict_json_loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LaunchRefused(f"bootstrap context is invalid: {exc}") from exc
    context = _object(
        document,
        name="authenticated bootstrap context",
        keys=frozenset(
            {
                "schema",
                "release_evidence_digest",
                "authorization_evidence_digest",
                "execution_envelope_digest",
                "application_history_snapshot_digest",
            }
        ),
    )
    if context["schema"] != BOOTSTRAP_CONTEXT_SCHEMA:
        raise LaunchRefused("authenticated bootstrap context has an unknown schema")
    for field in context.keys() - {"schema"}:
        _text(context[field], name=field, pattern=_DIGEST)
    return context


def _load_envelope(
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        raw = _strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaunchRefused(f"cannot read execution envelope {path}: {exc}") from exc
    envelope_keys = frozenset(
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
    )
    envelope = _object(raw, name="execution envelope", keys=envelope_keys)
    if envelope["schema"] != SCHEMA:
        raise LaunchRefused(
            f"execution envelope schema is {envelope['schema']!r}, expected {SCHEMA!r}"
        )
    controller = _object(
        envelope["required_controller"],
        name="required controller",
        keys=frozenset(
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
    authorizer = _object(
        envelope["authorizer"],
        name="authorizer",
        keys=frozenset({"repository", "workflow_path", "workflow_revision", "run_id"}),
    )
    if controller["distribution"] != CONTROLLER_DISTRIBUTION:
        raise LaunchRefused(
            f"controller distribution is {controller['distribution']!r}, expected "
            f"{CONTROLLER_DISTRIBUTION!r}"
        )
    version = _text(controller["exact_version"], name="controller exact_version")
    expected_tag = f"{CONTROLLER_DISTRIBUTION}-v{version}"
    if controller["tag"] != expected_tag:
        raise LaunchRefused(
            f"controller tag is {controller['tag']!r}, expected {expected_tag!r}"
        )
    _text(controller["artifact_sha256"], name="artifact_sha256", pattern=_DIGEST)
    _text(controller["launcher_sha256"], name="launcher_sha256", pattern=_DIGEST)
    _text(controller["source_revision"], name="source_revision", pattern=_REVISION)
    _positive_int(controller["release_run_id"], name="release_run_id")
    _text(authorizer["repository"], name="authorizer repository")
    workflow_path = _text(
        authorizer["workflow_path"], name="workflow_path", pattern=_WORKFLOW
    )
    if Path(workflow_path).is_absolute() or ".." in Path(workflow_path).parts:
        raise LaunchRefused("workflow_path must stay inside the authorizer checkout")
    _text(
        authorizer["workflow_revision"],
        name="workflow_revision",
        pattern=_REVISION,
    )
    _positive_int(authorizer["run_id"], name="authorizer run_id")
    return envelope, controller, authorizer


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        raw = _strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaunchRefused(f"cannot read controller receipt {path}: {exc}") from exc
    receipt = _object(
        raw,
        name="controller release receipt",
        keys=frozenset(
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
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise LaunchRefused(
            f"controller receipt schema is {receipt['schema']!r}, expected "
            f"{RECEIPT_SCHEMA!r}"
        )
    if receipt["distribution"] != CONTROLLER_DISTRIBUTION:
        raise LaunchRefused(
            f"receipt distribution is {receipt['distribution']!r}, expected "
            f"{CONTROLLER_DISTRIBUTION!r}"
        )
    version = _text(receipt["exact_version"], name="receipt exact_version")
    expected_tag = f"{CONTROLLER_DISTRIBUTION}-v{version}"
    if receipt["tag"] != expected_tag:
        raise LaunchRefused(
            f"receipt tag is {receipt['tag']!r}, expected {expected_tag!r}"
        )
    _text(receipt["artifact_sha256"], name="artifact_sha256", pattern=_DIGEST)
    _text(receipt["launcher_sha256"], name="launcher_sha256", pattern=_DIGEST)
    _text(receipt["source_revision"], name="source_revision", pattern=_REVISION)
    _positive_int(receipt["release_run_id"], name="release_run_id")
    return receipt


def _run_git(git_binary: Path, repository: Path, *arguments: str) -> str:
    environment = {
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_REPLACE_REF_BASE": "refs/dotmac-disabled-replace/",
        "GIT_GRAFT_FILE": os.devnull,
    }
    try:
        result = subprocess.run(
            [str(git_binary), "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LaunchRefused(f"git oracle could not run: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise LaunchRefused(
            f"git {' '.join(arguments)} failed with {result.returncode}: {detail}"
        )
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise LaunchRefused(f"cannot hash controller wheel {path}: {exc}") from exc
    return f"sha256:{digest.hexdigest()}"


def _verify_roots(
    *,
    envelope: dict[str, Any],
    controller: dict[str, Any],
    authorizer: dict[str, Any],
    receipt: dict[str, Any],
    release_evidence: dict[str, Any],
    release_run: dict[str, Any],
    release_artifacts: tuple[dict[str, Any], ...],
    authorization_evidence: dict[str, Any],
    authorization_run: dict[str, Any],
    application_history: dict[str, Any],
    bootstrap_context: dict[str, Any],
    wheel: Path,
    receipt_path: Path,
    authorizer_repo: Path,
    application_history_repo: Path,
    staged_application_root: Path,
    git_binary: Path,
    docker_binary: Path,
) -> None:
    for name, executable in (("Git", git_binary), ("Docker", docker_binary)):
        if not executable.is_absolute():
            raise LaunchRefused(f"--{name.lower()}-bin must be an absolute path")
        try:
            executable_stat = executable.stat()
        except OSError as exc:
            raise LaunchRefused(
                f"cannot inspect {name} executable {executable}: {exc}"
            ) from exc
        if not stat.S_ISREG(executable_stat.st_mode) or executable_stat.st_mode & 0o022:
            raise LaunchRefused(
                f"{name} executable must be a non-writable regular file"
            )
    authorizer_repo = authorizer_repo.resolve()
    application_history_repo = application_history_repo.resolve()
    staged_application_root = staged_application_root.resolve()
    if not staged_application_root.is_dir():
        raise LaunchRefused("staged application root must be an existing directory")
    if authorizer_repo == staged_application_root:
        raise LaunchRefused(
            "authorizer checkout and staged application root must differ"
        )
    if authorizer_repo.is_relative_to(
        staged_application_root
    ) or staged_application_root.is_relative_to(authorizer_repo):
        raise LaunchRefused(
            "authorizer checkout and staged application root must not be nested"
        )
    if (
        application_history_repo == staged_application_root
        or application_history_repo == authorizer_repo
        or application_history_repo.is_relative_to(staged_application_root)
        or staged_application_root.is_relative_to(application_history_repo)
        or application_history_repo.is_relative_to(authorizer_repo)
        or authorizer_repo.is_relative_to(application_history_repo)
    ):
        raise LaunchRefused(
            "application-history, authorizer and staged application roots must "
            "be distinct and unnested"
        )
    receipt_controller = {
        key: receipt[key]
        for key in (
            "distribution",
            "exact_version",
            "artifact_sha256",
            "launcher_sha256",
            "source_revision",
            "release_run_id",
            "tag",
        )
    }
    if receipt_controller != controller:
        raise LaunchRefused(
            "controller receipt does not exactly match envelope required_controller"
        )
    if (
        release_evidence["distribution"] != controller["distribution"]
        or release_evidence["exact_version"] != controller["exact_version"]
        or release_evidence["tag"] != controller["tag"]
        or release_evidence["source_revision"] != controller["source_revision"]
        or release_run["run_id"] != controller["release_run_id"]
        or release_run["head_sha"] != controller["source_revision"]
    ):
        raise LaunchRefused(
            "signed controller release evidence disagrees with the required release"
        )

    artifact_by_name = {str(item["name"]): item for item in release_artifacts}
    required_artifacts = {
        wheel.name: (wheel, controller["artifact_sha256"]),
        "run_deployment_controller.py": (
            Path(__file__).resolve(),
            controller["launcher_sha256"],
        ),
        "DeploymentControllerReleaseReceipt.v1.json": (
            receipt_path,
            _sha256(receipt_path),
        ),
    }
    for name, (path, expected_digest) in required_artifacts.items():
        artifact = artifact_by_name.get(name)
        if artifact is None:
            raise LaunchRefused(f"signed release evidence omits artifact {name!r}")
        if (
            artifact["sha256"] != expected_digest
            or artifact["size"] != path.stat().st_size
            or _sha256(path) != expected_digest
        ):
            raise LaunchRefused(
                f"signed release artifact {name!r} does not match the supplied bytes"
            )

    release_evidence_digest = _document_digest(release_evidence)
    authorization_evidence_digest = _document_digest(authorization_evidence)
    history_snapshot_digest = _document_digest(application_history)
    actual_envelope_digest = _envelope_digest(envelope)
    expected_bindings = {
        "release_evidence_digest": release_evidence_digest,
        "authorization_evidence_digest": authorization_evidence_digest,
        "execution_envelope_digest": actual_envelope_digest,
        "application_history_snapshot_digest": history_snapshot_digest,
    }
    for field, observed in expected_bindings.items():
        if bootstrap_context[field] != observed:
            raise LaunchRefused(f"authenticated bootstrap context disagrees on {field}")
    if (
        authorization_evidence["execution_envelope_digest"] != actual_envelope_digest
        or authorization_evidence["controller_release_evidence_digest"]
        != release_evidence_digest
    ):
        raise LaunchRefused(
            "signed authorization evidence does not bind this envelope and "
            "controller release"
        )

    if (
        authorization_run["repository"] != authorizer["repository"]
        or authorization_run["workflow_path"] != authorizer["workflow_path"]
        or authorization_run["workflow_revision"] != authorizer["workflow_revision"]
        or authorization_run["run_id"] != authorizer["run_id"]
    ):
        raise LaunchRefused(
            "signed authorization workflow run disagrees with the execution envelope"
        )
    observed_launcher = _sha256(Path(__file__).resolve())
    if observed_launcher != receipt["launcher_sha256"]:
        raise LaunchRefused(
            f"controller launcher hashes to {observed_launcher}, receipt requires "
            f"{receipt['launcher_sha256']}"
        )

    authorizer_head = _run_git(git_binary, authorizer_repo, "rev-parse", "HEAD")
    if authorizer_head != authorizer["workflow_revision"]:
        raise LaunchRefused(
            f"authorizer checkout is {authorizer_head}, receipt requires "
            f"{authorizer['workflow_revision']}"
        )
    expected_remote = (
        f"{str(authorization_run['server_origin']).rstrip('/')}"
        f"/{authorization_run['repository']}"
    )
    observed_remote = _run_git(
        git_binary, authorizer_repo, "remote", "get-url", "origin"
    ).rstrip("/")
    if observed_remote.endswith(".git"):
        observed_remote = observed_remote[:-4]
    if observed_remote != expected_remote:
        raise LaunchRefused(
            f"authorizer checkout remote is {observed_remote!r}, expected "
            f"{expected_remote!r}"
        )
    if _run_git(
        git_binary,
        authorizer_repo,
        "status",
        "--porcelain",
        "--untracked-files=no",
    ):
        raise LaunchRefused("the exact authorizer checkout has tracked modifications")
    if (
        _run_git(git_binary, authorizer_repo, "rev-parse", "--is-shallow-repository")
        != "false"
    ):
        raise LaunchRefused("authorizer checkout must contain complete Git history")
    workflow = (authorizer_repo / authorizer["workflow_path"]).resolve()
    if not workflow.is_relative_to(authorizer_repo) or not workflow.is_file():
        raise LaunchRefused(
            f"authorizing workflow {authorizer['workflow_path']!r} is absent from "
            "the exact authorizer checkout"
        )
    tracked_workflow = _run_git(
        git_binary,
        authorizer_repo,
        "ls-files",
        "--error-unmatch",
        "--",
        str(authorizer["workflow_path"]),
    )
    if tracked_workflow != authorizer["workflow_path"]:
        raise LaunchRefused(
            "authorizing workflow is not the exact tracked path in the "
            "authorizer checkout"
        )
    if _sha256(workflow) != authorization_run["workflow_blob_sha256"]:
        raise LaunchRefused(
            "authorizing workflow bytes disagree with signed workflow-run evidence"
        )
    if authorization_run["head_sha"] != authorizer_head:
        raise LaunchRefused(
            "authorizer checkout HEAD disagrees with signed workflow run head SHA"
        )

    if application_history["to_revision"] != envelope["candidate"]["source_revision"]:
        raise LaunchRefused(
            "application history does not end at the candidate source revision"
        )
    expected_current = envelope["expected_current"]
    expected_from = (
        None if expected_current is None else expected_current["source_revision"]
    )
    if application_history["from_revision"] != expected_from:
        raise LaunchRefused(
            "application history does not start at the expected current revision"
        )
    if (
        _run_git(
            git_binary,
            application_history_repo,
            "rev-parse",
            "--show-object-format",
        )
        != application_history["object_format"]
        or _run_git(
            git_binary,
            application_history_repo,
            "rev-parse",
            "--is-shallow-repository",
        )
        != "false"
    ):
        raise LaunchRefused(
            "application-history checkout has the wrong object format or is shallow"
        )
    _run_git(
        git_binary,
        application_history_repo,
        "cat-file",
        "-e",
        f"{application_history['to_revision']}^{{commit}}",
    )
    if application_history["from_revision"] is not None:
        _run_git(
            git_binary,
            application_history_repo,
            "cat-file",
            "-e",
            f"{application_history['from_revision']}^{{commit}}",
        )

    observed_digest = _sha256(wheel)
    if observed_digest != controller["artifact_sha256"]:
        raise LaunchRefused(
            f"controller wheel hashes to {observed_digest}, receipt requires "
            f"{controller['artifact_sha256']}"
        )


def _isolated_environment(*, controller: dict[str, Any]) -> dict[str, str]:
    permitted = {
        "HOME",
        "LANG",
        "LC_ALL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
        "XDG_RUNTIME_DIR",
    }
    environment = {key: value for key, value in os.environ.items() if key in permitted}
    environment.update(
        {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "DOTMAC_CONTROLLER_ARTIFACT_SHA256": str(controller["artifact_sha256"]),
            "DOTMAC_CONTROLLER_LAUNCHER_SHA256": str(controller["launcher_sha256"]),
            "DOTMAC_CONTROLLER_SOURCE_REVISION": str(controller["source_revision"]),
            "DOTMAC_CONTROLLER_RELEASE_RUN_ID": str(controller["release_run_id"]),
            "DOTMAC_CONTROLLER_TAG": str(controller["tag"]),
        }
    )
    return environment


def launch(args: argparse.Namespace) -> int:
    bootstrap_context = _load_bootstrap_context(args.bootstrap_context_fd)
    source_envelope = Path(args.envelope).resolve()
    source_receipt = Path(args.receipt).resolve()
    source_wheel = Path(args.wheel).resolve()
    source_release_evidence = Path(args.release_evidence).resolve()
    source_authorization_evidence = Path(args.authorization_evidence).resolve()
    authorizer_repo = Path(args.authorizer_repo).resolve()
    application_history_repo = Path(args.application_history_repo).resolve()
    staged_application_root = Path(args.staged_application_root).resolve()
    for name, authority_path in (
        ("execution envelope", source_envelope),
        ("controller receipt", source_receipt),
        ("controller wheel", source_wheel),
        ("controller release evidence", source_release_evidence),
        ("deployment authorization evidence", source_authorization_evidence),
    ):
        if authority_path.is_relative_to(staged_application_root):
            raise LaunchRefused(
                f"{name} must be supplied outside the staged application"
            )
    git_input = Path(args.git_bin)
    docker_input = Path(args.docker_bin)
    if not git_input.is_absolute() or not docker_input.is_absolute():
        raise LaunchRefused("--git-bin and --docker-bin must be absolute paths")
    git_binary = git_input.resolve()
    docker_binary = docker_input.resolve()
    with tempfile.TemporaryDirectory(prefix="dotmac-controller-") as temp:
        root = Path(temp)
        envelope_path = root / "execution-envelope.json"
        receipt_path = root / "controller-receipt.json"
        release_evidence_path = root / "controller-release-evidence.json"
        authorization_evidence_path = root / "deployment-authorization-evidence.json"
        wheel = root / source_wheel.name
        try:
            shutil.copyfile(source_envelope, envelope_path)
            shutil.copyfile(source_receipt, receipt_path)
            shutil.copyfile(source_wheel, wheel)
            shutil.copyfile(source_release_evidence, release_evidence_path)
            shutil.copyfile(source_authorization_evidence, authorization_evidence_path)
        except OSError as exc:
            raise LaunchRefused(f"could not seal controller inputs: {exc}") from exc
        for sealed in (
            envelope_path,
            receipt_path,
            wheel,
            release_evidence_path,
            authorization_evidence_path,
        ):
            sealed.chmod(0o400)

        envelope, controller, authorizer = _load_envelope(envelope_path)
        receipt = _load_receipt(receipt_path)
        release_evidence, release_run, release_artifacts = _load_release_evidence(
            release_evidence_path
        )
        authorization_evidence, authorization_run, application_history = (
            _load_authorization_evidence(authorization_evidence_path)
        )
        _verify_roots(
            envelope=envelope,
            controller=controller,
            authorizer=authorizer,
            receipt=receipt,
            release_evidence=release_evidence,
            release_run=release_run,
            release_artifacts=release_artifacts,
            authorization_evidence=authorization_evidence,
            authorization_run=authorization_run,
            application_history=application_history,
            bootstrap_context=bootstrap_context,
            wheel=wheel,
            receipt_path=receipt_path,
            authorizer_repo=authorizer_repo,
            application_history_repo=application_history_repo,
            staged_application_root=staged_application_root,
            git_binary=git_binary,
            docker_binary=docker_binary,
        )
        context_path = root / "launch-context.json"
        context_path.write_text(
            json.dumps(
                {
                    "schema": "DeploymentControllerLaunchContext.v1",
                    "controller": controller,
                    "authorizer": authorizer,
                    "authorization_evidence": authorization_evidence,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        context_path.chmod(0o400)

        venv = root / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        bin_dir = venv / ("Scripts" if sys.platform == "win32" else "bin")
        python = bin_dir / ("python.exe" if sys.platform == "win32" else "python")
        pip = bin_dir / ("pip.exe" if sys.platform == "win32" else "pip")
        subprocess.run(
            [str(pip), "install", "--quiet", "--no-index", "--no-deps", str(wheel)],
            check=True,
            cwd=root,
            env=_isolated_environment(controller=controller),
        )
        command = [
            str(python),
            "-I",
            "-m",
            "dotmac_deployment_foundation.cli",
            "--execution-envelope",
            str(envelope_path),
            "--staged-application-root",
            str(staged_application_root),
            "--launch-context-fd",
            "PLACEHOLDER",
            "-f",
            args.descriptor,
            "execute-authorized",
            "--authorizer-repo",
            str(authorizer_repo),
            "--application-history-repo",
            str(application_history_repo),
            "--git-bin",
            str(git_binary),
            "--docker-bin",
            str(docker_binary),
        ]
        context_fd = os.open(context_path, os.O_RDONLY)
        try:
            command[command.index("PLACEHOLDER")] = str(context_fd)
            result = subprocess.run(
                command,
                check=False,
                cwd=root,
                env=_isolated_environment(controller=controller),
                pass_fds=(context_fd,),
            )
            return result.returncode
        finally:
            os.close(context_fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelope", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--wheel", required=True)
    parser.add_argument("--release-evidence", required=True)
    parser.add_argument("--authorization-evidence", required=True)
    parser.add_argument("--bootstrap-context-fd", required=True, type=int)
    parser.add_argument("--authorizer-repo", required=True)
    parser.add_argument("--application-history-repo", required=True)
    parser.add_argument("--staged-application-root", required=True)
    parser.add_argument("--git-bin", required=True)
    parser.add_argument("--docker-bin", required=True)
    parser.add_argument("--descriptor", default="deploy/product.toml")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return launch(build_parser().parse_args(argv))
    except LaunchRefused as exc:
        print(f"controller launch refused: {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"controller launch failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
