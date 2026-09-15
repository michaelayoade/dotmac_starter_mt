#!/usr/bin/env python3
"""Run the pinned dependency-bundle action without deriving product policy."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

from bundle_artifact_download import (  # noqa: E402
    MAX_ARTIFACT_BYTES,
    acquire_observed_artifact,
)
from bundle_envelope import (  # noqa: E402
    MAX_COMPRESSION_RATIO,
    ExpectedArtifact,
    ExpectedArtifactSet,
    build_local_index,
    canonical_json_bytes,
    create_bundle_manifest,
    extract_verified_bundle,
    verify_archive_digest,
)
from bundle_github_observation import TrustedProducerPolicy  # noqa: E402
from bundle_sidecar import consume_sidecar  # noqa: E402

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_WORKFLOW = re.compile(r"\.github/workflows/[^/]+\.yml\Z")
_ACTION_REPOSITORY = "michaelayoade/dotmac_starter_mt"
_MAX_EXPECTED_FILE_BYTES = 2 * 1024 * 1024


class BundleActionError(ValueError):
    """Raised for a refused action input or trusted-action boundary."""


def _positive(value: str, name: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise BundleActionError(f"{name} must be a positive integer")
    return int(value)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise BundleActionError(f"{name} is required")
    return value


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleActionError("expected-file contains duplicate JSON keys")
        result[key] = value
    return result


def load_expected(path: Path) -> ExpectedArtifactSet:
    """Load the exact v1 product-policy document; never derive one here."""
    try:
        contents = path.read_bytes()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleActionError(f"cannot read expected-file: {exc}") from exc
    if len(contents) > _MAX_EXPECTED_FILE_BYTES:
        raise BundleActionError("expected-file exceeds size cap")
    try:
        payload = json.loads(contents, object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleActionError(f"cannot read expected-file: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "plan_digest",
        "artifacts",
    }:
        raise BundleActionError("expected-file shape is not exact")
    if (
        not isinstance(payload["schema_version"], int)
        or isinstance(payload["schema_version"], bool)
        or payload["schema_version"] != 1
        or not isinstance(payload["artifacts"], list)
    ):
        raise BundleActionError("expected-file schema is invalid")
    if (
        not isinstance(payload["plan_digest"], str)
        or not _HEX.fullmatch(payload["plan_digest"])
        or not payload["artifacts"]
    ):
        raise BundleActionError("expected-file plan is invalid")
    artifacts: list[ExpectedArtifact] = []
    for record in payload["artifacts"]:
        if not isinstance(record, dict) or set(record) != {
            "package_normalised_name",
            "filename",
            "sha256",
        }:
            raise BundleActionError("expected-file artifact shape is not exact")
        if not all(isinstance(record[key], str) and record[key] for key in record):
            raise BundleActionError("expected-file artifact fields must be text")
        if not _HEX.fullmatch(record["sha256"]):
            raise BundleActionError("expected-file artifact sha256 is invalid")
        artifacts.append(ExpectedArtifact(**record))
    return ExpectedArtifactSet(
        plan_digest=payload["plan_digest"], artifacts=tuple(artifacts)
    )


def _require_action_identity(script_path: Path) -> None:
    if os.environ.get("BUNDLE_ACTION_REPOSITORY") != _ACTION_REPOSITORY:
        raise BundleActionError("action repository is not trusted")
    if not _SHA.fullmatch(_required_env("BUNDLE_ACTION_REF")):
        raise BundleActionError("action ref must be a full commit SHA")
    action_path = Path(_required_env("GITHUB_ACTION_PATH")).resolve()
    expected = (action_path / "../../../scripts/run_bundle_action.py").resolve()
    if script_path.resolve() != expected:
        raise BundleActionError("executing script is not this action's script")


def _runner_directory() -> Path:
    root = Path(_required_env("RUNNER_TEMP"))
    if not root.is_absolute():
        raise BundleActionError("RUNNER_TEMP must be absolute")
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise BundleActionError("RUNNER_TEMP must be an existing directory") from exc
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or stat.S_ISLNK(root_stat.st_mode)
        or root_stat.st_uid != os.geteuid()
    ):
        raise BundleActionError("RUNNER_TEMP must be an owned real directory")
    return Path(tempfile.mkdtemp(prefix="verified-bundle-", dir=root))


def _output(name: str, value: Path) -> None:
    output = Path(_required_env("GITHUB_OUTPUT"))
    with output.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _policy(args: argparse.Namespace, artifact_name: str) -> TrustedProducerPolicy:
    if not _WORKFLOW.fullmatch(args.producer_workflow_path):
        raise BundleActionError("producer-workflow-path is not canonical")
    return TrustedProducerPolicy(
        repository_id=_positive(args.producer_repository_id, "producer-repository-id"),
        repository=args.producer_repository,
        workflow_path=args.producer_workflow_path,
        artifact_name=artifact_name,
    )


def _require_regular(path: Path, name: str) -> None:
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except OSError as exc:
        raise BundleActionError(f"{name} is unavailable") from exc
    if not stat.S_ISREG(mode):
        raise BundleActionError(f"{name} must be a regular file")


def _extract_outer_bundle(outer_zip: Path, destination: Path) -> Path:
    destination_identity: tuple[int, int] | None = None
    succeeded = False
    try:
        with zipfile.ZipFile(outer_zip) as archive:
            infos = archive.infolist()
            if len(infos) != 1 or infos[0].filename != "bundle.zip":
                raise BundleActionError(
                    "archive artifact must contain exactly bundle.zip"
                )
            info = infos[0]
            member_type = stat.S_IFMT((info.external_attr >> 16) & 0xFFFF)
            if (
                member_type not in (0, stat.S_IFREG)
                or info.file_size > MAX_ARTIFACT_BYTES
            ):
                raise BundleActionError("archive artifact bundle.zip is unsafe")
            if info.file_size and (
                info.compress_size == 0
                or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
            ):
                raise BundleActionError("archive artifact bundle.zip compression ratio")
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            destination_stat = os.fstat(fd)
            destination_identity = (destination_stat.st_dev, destination_stat.st_ino)
            with archive.open(info) as source, os.fdopen(fd, "wb") as target:
                count = 0
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    if count > info.file_size or count > MAX_ARTIFACT_BYTES:
                        raise BundleActionError(
                            "archive artifact bundle.zip exceeds cap"
                        )
                    target.write(chunk)
                if count != info.file_size:
                    raise BundleActionError("archive artifact bundle.zip size differs")
        succeeded = True
    except (OSError, zipfile.BadZipFile) as exc:
        raise BundleActionError(f"cannot read archive artifact: {exc}") from exc
    except BundleActionError:
        raise
    finally:
        if destination_identity is not None and not destination.exists():
            destination_identity = None
        if destination_identity is not None and not succeeded:
            try:
                current = destination.lstat()
                if (current.st_dev, current.st_ino) == destination_identity:
                    destination.unlink()
            except FileNotFoundError:
                pass
    return destination


def _construct(args: argparse.Namespace, expected: ExpectedArtifactSet) -> None:
    if os.environ.get("GITHUB_REPOSITORY") != args.producer_repository:
        raise BundleActionError("current repository is not the producer")
    if _positive(
        _required_env("GITHUB_REPOSITORY_ID"), "GITHUB_REPOSITORY_ID"
    ) != _positive(args.producer_repository_id, "producer-repository-id"):
        raise BundleActionError("current repository ID is not the producer")
    if (
        os.environ.get("GITHUB_REF") != "refs/heads/main"
        or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
    ):
        raise BundleActionError("construct requires a main workflow_dispatch")
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise BundleActionError("construct requires first run attempt")
    sha = _required_env("GITHUB_SHA")
    if not _SHA.fullmatch(sha) or sha == "0" * 40:
        raise BundleActionError("GITHUB_SHA is not a non-zero commit SHA")
    expected_workflow_ref = (
        f"{args.producer_repository}/{args.producer_workflow_path}@refs/heads/main"
    )
    if _required_env("GITHUB_WORKFLOW_REF") != expected_workflow_ref:
        raise BundleActionError(
            "GITHUB_WORKFLOW_REF does not name the producer workflow"
        )
    acquired_dir = Path(args.acquired_dir)
    _require_regular(Path(args.archive_path), "archive-path")
    acquired: dict[str, Path] = {}
    names = {artifact.filename for artifact in expected.artifacts}
    try:
        entries = list(acquired_dir.iterdir())
    except OSError as exc:
        raise BundleActionError("acquired-dir is unreadable") from exc
    if {entry.name for entry in entries} != names:
        raise BundleActionError(
            "acquired-dir does not contain exactly expected artifacts"
        )
    for entry in entries:
        _require_regular(entry, f"acquired artifact {entry.name}")
        acquired[entry.name] = entry
    if not args.environment_name:
        raise BundleActionError("environment-name is required")
    run = {
        "repository_full_name": args.producer_repository,
        "repository_id": _positive(
            args.producer_repository_id, "producer-repository-id"
        ),
        "workflow_path": args.producer_workflow_path,
        "run_id": _positive(_required_env("GITHUB_RUN_ID"), "GITHUB_RUN_ID"),
        "run_attempt": 1,
        "trusted_workflow_sha": sha,
        "artifact_id": _positive(args.archive_artifact_id, "archive-artifact-id"),
        "artifact_name": args.archive_artifact_name,
        "artifact_run_id": _positive(_required_env("GITHUB_RUN_ID"), "GITHUB_RUN_ID"),
        "environment_name": args.environment_name,
    }
    manifest = create_bundle_manifest(
        expected=expected,
        acquired_files=acquired,
        archive_path=Path(args.archive_path),
        run=run,
    )
    output = _runner_directory() / "bundle-manifest.json"
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(canonical_json_bytes(manifest))
    _output("manifest-path", output)


def _verify(args: argparse.Namespace, expected: ExpectedArtifactSet) -> None:
    token = os.environ.get("BUNDLE_GITHUB_TOKEN", "")
    if not token:
        raise BundleActionError("github-token is required")
    directory = _runner_directory()
    run_id = _positive(args.run_id, "run-id")
    archive_id = _positive(args.archive_artifact_id, "archive-artifact-id")
    sidecar_id = _positive(args.sidecar_artifact_id, "sidecar-artifact-id")
    archive_observation, archive_zip = acquire_observed_artifact(
        _policy(args, args.archive_artifact_name),
        run_id,
        archive_id,
        token,
        directory / "archive.zip",
    )
    sidecar_observation, sidecar_zip = acquire_observed_artifact(
        _policy(args, args.sidecar_artifact_name),
        run_id,
        sidecar_id,
        token,
        directory / "sidecar.zip",
    )
    manifest = consume_sidecar(sidecar_zip, archive_observation, sidecar_observation)
    bundle_zip = _extract_outer_bundle(archive_zip, directory / "bundle.zip")
    verify_archive_digest(bundle_zip, manifest["archive_sha256"])
    extracted = directory / "extracted"
    extract_verified_bundle(bundle_zip, extracted, manifest)
    index = directory / "index"
    build_local_index(index, expected, manifest, extracted)
    _output("index-root", index)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--operation", choices=("construct", "verify-and-index"), required=True
    )
    parser.add_argument("--expected-file", required=True)
    parser.add_argument("--producer-repository", required=True)
    parser.add_argument("--producer-repository-id", required=True)
    parser.add_argument("--producer-workflow-path", required=True)
    parser.add_argument("--archive-artifact-name", required=True)
    parser.add_argument("--sidecar-artifact-name", required=True)
    parser.add_argument("--archive-artifact-id", required=True)
    parser.add_argument("--acquired-dir")
    parser.add_argument("--archive-path")
    parser.add_argument("--environment-name")
    parser.add_argument("--run-id")
    parser.add_argument("--sidecar-artifact-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _require_action_identity(Path(__file__))
    expected = load_expected(Path(args.expected_file))
    if args.operation == "construct":
        if not all((args.acquired_dir, args.archive_path, args.environment_name)):
            raise BundleActionError("construct inputs are incomplete")
        _construct(args, expected)
    else:
        if not all((args.run_id, args.sidecar_artifact_id)):
            raise BundleActionError("verify-and-index inputs are incomplete")
        _verify(args, expected)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BundleActionError as exc:
        raise SystemExit(f"verified dependency bundle refused: {exc}") from exc
