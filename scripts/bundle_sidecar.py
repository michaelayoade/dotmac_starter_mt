"""Verify the small GitHub sidecar containing a bundle manifest."""

from __future__ import annotations

import json
import re
import stat
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MAX_SIDECAR_BYTES = 2 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_RUN_KEYS = {
    "repository_full_name",
    "repository_id",
    "workflow_path",
    "run_id",
    "run_attempt",
    "trusted_workflow_sha",
    "artifact_id",
    "artifact_name",
    "artifact_run_id",
    "environment_name",
}
_OBSERVATION_POSITIVE_INTS = {
    "repository_id",
    "head_repository_id",
    "run_id",
    "run_attempt",
    "workflow_id",
    "workflow_observed_id",
    "artifact_id",
    "artifact_size_in_bytes",
    "artifact_run_id",
    "artifact_repository_id",
    "artifact_head_repository_id",
}
_OBSERVATION_TEXT = {
    "repository_full_name",
    "head_repository_full_name",
    "head_sha",
    "head_branch",
    "event",
    "workflow_path",
    "workflow_observed_path",
    "artifact_name",
    "artifact_digest",
    "artifact_head_sha",
    "artifact_head_branch",
}
_SAME_PRODUCING_RUN = {
    "repository_id",
    "repository_full_name",
    "head_repository_id",
    "head_repository_full_name",
    "run_id",
    "run_attempt",
    "head_sha",
    "head_branch",
    "event",
    "workflow_id",
    "workflow_observed_id",
    "workflow_observed_path",
    "artifact_run_id",
    "artifact_repository_id",
    "artifact_head_repository_id",
    "artifact_head_sha",
    "artifact_head_branch",
}


class SidecarError(ValueError):
    """Raised when sidecar shape or provenance is invalid."""


def _positive(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_observation(observation: Mapping[str, Any], label: str) -> None:
    """Require a complete, individually successful GitHub observation."""
    for key in _OBSERVATION_POSITIVE_INTS:
        if not _positive(observation.get(key)):
            raise SidecarError(f"{label} observation {key} is invalid")
    for key in _OBSERVATION_TEXT:
        if not isinstance(observation.get(key), str) or not observation[key]:
            raise SidecarError(f"{label} observation {key} is invalid")
    if observation.get("status") != "completed":
        raise SidecarError(f"{label} observation is not completed")
    if observation.get("conclusion") != "success":
        raise SidecarError(f"{label} observation is not successful")
    if not _COMMIT_SHA.fullmatch(observation["head_sha"]):
        raise SidecarError(f"{label} observation head_sha is invalid")
    artifact_digest = observation["artifact_digest"]
    if not artifact_digest.startswith("sha256:") or not _SHA256.fullmatch(
        artifact_digest.removeprefix("sha256:")
    ):
        raise SidecarError(f"{label} observation artifact_digest is invalid")
    if observation["artifact_run_id"] != observation["run_id"]:
        raise SidecarError(f"{label} observation artifact run is not its run")
    if observation["artifact_repository_id"] != observation["repository_id"]:
        raise SidecarError(f"{label} observation artifact repository disagrees")
    if observation["artifact_head_repository_id"] != observation["head_repository_id"]:
        raise SidecarError(f"{label} observation artifact head repository disagrees")
    if observation["artifact_head_sha"] != observation["head_sha"]:
        raise SidecarError(f"{label} observation artifact SHA disagrees")
    if observation["artifact_head_branch"] != observation["head_branch"]:
        raise SidecarError(f"{label} observation artifact branch disagrees")


def _read_manifest(sidecar_zip_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(sidecar_zip_path) as archive:
            infos = archive.infolist()
            if len(infos) != 1 or infos[0].filename != "bundle-manifest.json":
                raise SidecarError("sidecar must contain exactly bundle-manifest.json")
            info = infos[0]
            member_type = stat.S_IFMT((info.external_attr >> 16) & 0xFFFF)
            if member_type not in (0, stat.S_IFREG):
                raise SidecarError("sidecar member must be a regular file")
            if info.file_size > MAX_SIDECAR_BYTES:
                raise SidecarError("sidecar member exceeds size cap")
            raw = archive.read(info)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SidecarError(f"cannot read sidecar: {exc}") from exc
    if len(raw) > MAX_SIDECAR_BYTES:
        raise SidecarError("sidecar member exceeds size cap")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SidecarError("sidecar manifest contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        manifest = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SidecarError("sidecar manifest is not JSON") from exc
    if not isinstance(manifest, dict):
        raise SidecarError("sidecar manifest shape is not exact")
    return manifest


def _validate_manifest(
    manifest: dict[str, Any], archive_observation: Mapping[str, Any]
) -> None:
    if set(manifest) != {
        "schema_version",
        "plan_digest",
        "archive_sha256",
        "members",
        "run",
    }:
        raise SidecarError("sidecar manifest shape is not exact")
    if manifest["schema_version"] != 2 or isinstance(manifest["schema_version"], bool):
        raise SidecarError("sidecar schema version is unsupported")
    for key in ("plan_digest", "archive_sha256"):
        if not isinstance(manifest[key], str) or not _SHA256.fullmatch(manifest[key]):
            raise SidecarError(f"sidecar {key} is not a sha256")
    if not isinstance(manifest["members"], dict):
        raise SidecarError("sidecar members shape is invalid")
    run = manifest["run"]
    if not isinstance(run, dict) or set(run) != _RUN_KEYS:
        raise SidecarError("sidecar run shape is not exact")
    for key in (
        "run_id",
        "artifact_id",
        "artifact_run_id",
        "repository_id",
        "run_attempt",
    ):
        if not _positive(run[key]):
            raise SidecarError(f"sidecar run.{key} is invalid")
    for key in (
        "repository_full_name",
        "workflow_path",
        "artifact_name",
        "environment_name",
    ):
        if not isinstance(run[key], str) or not run[key]:
            raise SidecarError(f"sidecar run.{key} is invalid")
    if not isinstance(run["trusted_workflow_sha"], str) or not _COMMIT_SHA.fullmatch(
        run["trusted_workflow_sha"]
    ):
        raise SidecarError("sidecar run.trusted_workflow_sha is invalid")
    for key in (
        "run_id",
        "artifact_id",
        "artifact_run_id",
        "repository_id",
        "run_attempt",
        "repository_full_name",
        "artifact_name",
    ):
        if run[key] != archive_observation[key]:
            raise SidecarError(f"sidecar run.{key} does not match archive observation")
    if run["workflow_path"] != archive_observation["workflow_observed_path"]:
        raise SidecarError(
            "sidecar run.workflow_path does not match archive observation"
        )
    if run["trusted_workflow_sha"] != archive_observation["head_sha"]:
        raise SidecarError("sidecar trusted workflow SHA does not match archive")


def consume_sidecar(
    sidecar_zip_path: Path,
    archive_observation: Mapping[str, Any],
    sidecar_observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Read one safe manifest and bind it to two complete observations.

    The sidecar artifact is independently observed, but a manifest records the
    archive artifact that it describes. Its ``run.artifact_*`` fields therefore
    bind to ``archive_observation``, never to the sidecar artifact.
    """
    _validate_observation(archive_observation, "archive")
    _validate_observation(sidecar_observation, "sidecar")
    for key in _SAME_PRODUCING_RUN:
        if archive_observation[key] != sidecar_observation[key]:
            raise SidecarError(f"observations disagree on {key}")
    manifest = _read_manifest(sidecar_zip_path)
    _validate_manifest(manifest, archive_observation)
    return manifest
