#!/usr/bin/env python3
"""Bind a bundle run to identities observed from GitHub's Actions API."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from github_actions_transport import get_json

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_ARTIFACT_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class TrustedProducerPolicy:
    repository_id: int
    repository: str
    workflow_path: str
    artifact_name: str

    def __post_init__(self) -> None:
        _positive(self.repository_id, "repository_id")
        for value, field in (
            (self.repository, "repository"),
            (self.workflow_path, "workflow_path"),
            (self.artifact_name, "artifact_name"),
        ):
            _text(value, field)
        if (
            not re.fullmatch(r"[^/?:#]+/[^/?:#]+", self.repository)
            or ".." in self.repository
        ):
            raise GitHubObservationError(
                "repository must be owner/name without URL syntax"
            )


class GitHubObservationError(ValueError):
    """Raised when GitHub's observed identities do not bind to the policy."""


def _positive(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise GitHubObservationError(f"{field} must be a positive integer")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise GitHubObservationError(f"{field} must be a non-empty string")
    return value


def _same(
    observed: Mapping[str, Any], expected: Mapping[str, Any], fields: tuple[str, ...]
) -> None:
    for field in fields:
        if observed.get(field) != expected.get(field):
            raise GitHubObservationError(f"{field} does not match GitHub observation")


def observe(
    run_id: int,
    artifact_id: int,
    token: str,
    policy: TrustedProducerPolicy,
    *,
    manifest_run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch and validate one run, artifact, and workflow by positive IDs."""

    run_id = _positive(run_id, "run_id")
    artifact_id = _positive(artifact_id, "artifact_id")
    run = get_json(f"/repos/{policy.repository}/actions/runs/{run_id}", token)
    artifact = get_json(
        f"/repos/{policy.repository}/actions/artifacts/{artifact_id}", token
    )
    workflow_id = _positive(run.get("workflow_id"), "run.workflow_id")
    workflow = get_json(
        f"/repos/{policy.repository}/actions/workflows/{workflow_id}", token
    )

    repository = run.get("repository")
    head_repository = run.get("head_repository")
    workflow_run = artifact.get("workflow_run")
    if not isinstance(repository, dict) or not isinstance(head_repository, dict):
        raise GitHubObservationError("run repository identities are missing")
    if not isinstance(workflow_run, dict):
        raise GitHubObservationError("artifact workflow_run identity is missing")
    observed = {
        "repository_id": _positive(repository.get("id"), "repository.id"),
        "repository_full_name": _text(
            repository.get("full_name"), "repository.full_name"
        ),
        "head_repository_full_name": _text(
            head_repository.get("full_name"), "head_repository.full_name"
        ),
        "head_repository_id": _positive(
            head_repository.get("id"), "head_repository.id"
        ),
        "run_id": _positive(run.get("id"), "run.id"),
        "run_attempt": _positive(run.get("run_attempt"), "run.run_attempt"),
        "head_sha": _text(run.get("head_sha"), "run.head_sha"),
        "status": _text(run.get("status"), "run.status"),
        "conclusion": _text(run.get("conclusion"), "run.conclusion"),
        "head_branch": _text(run.get("head_branch"), "run.head_branch"),
        "event": _text(run.get("event"), "run.event"),
        "workflow_id": _positive(run.get("workflow_id"), "run.workflow_id"),
        "workflow_path": _text(run.get("path"), "run.path"),
        "artifact_id": _positive(artifact.get("id"), "artifact.id"),
        "artifact_name": _text(artifact.get("name"), "artifact.name"),
        "artifact_digest": _text(artifact.get("digest"), "artifact.digest"),
        "artifact_size_in_bytes": _positive(
            artifact.get("size_in_bytes"), "artifact.size_in_bytes"
        ),
        "artifact_run_id": _positive(
            workflow_run.get("id"), "artifact.workflow_run.id"
        ),
        "artifact_repository_id": _positive(
            workflow_run.get("repository_id"), "artifact.workflow_run.repository_id"
        ),
        "artifact_head_repository_id": _positive(
            workflow_run.get("head_repository_id"),
            "artifact.workflow_run.head_repository_id",
        ),
        "artifact_head_sha": _text(
            workflow_run.get("head_sha"), "artifact.workflow_run.head_sha"
        ),
        "artifact_head_branch": _text(
            workflow_run.get("head_branch"), "artifact.workflow_run.head_branch"
        ),
        "workflow_observed_id": _positive(workflow.get("id"), "workflow.id"),
        "workflow_observed_path": _text(workflow.get("path"), "workflow.path"),
        "environment_name": manifest_run.get("environment_name")
        if manifest_run
        else None,
        "environment_verified": False,
    }
    if not _SHA.fullmatch(observed["head_sha"]) or observed["head_sha"] == "0" * 40:
        raise GitHubObservationError("run.head_sha must be a non-zero lowercase SHA")
    if observed["repository_id"] != policy.repository_id:
        raise GitHubObservationError("repository ID is not trusted")
    if observed["head_repository_id"] != policy.repository_id:
        raise GitHubObservationError("head repository ID is not trusted")
    if (
        observed["status"] != "completed"
        or observed["conclusion"] != "success"
        or observed["run_attempt"] != 1
    ):
        raise GitHubObservationError("run is not completed successfully")
    if (
        observed["repository_full_name"] != policy.repository
        or observed["head_repository_full_name"] != policy.repository
    ):
        raise GitHubObservationError("run is not from the trusted repository")
    if observed["head_branch"] != "main" or observed["event"] != "workflow_dispatch":
        raise GitHubObservationError("run branch or event is not trusted")
    if (
        observed["workflow_path"]
        not in {
            policy.workflow_path,
            policy.workflow_path + "@main",
        }
        or observed["workflow_observed_path"] != policy.workflow_path
    ):
        raise GitHubObservationError("workflow path is not canonical")
    if (
        observed["workflow_id"] != workflow_id
        or observed["workflow_observed_id"] != workflow_id
    ):
        raise GitHubObservationError("workflow ID does not match")
    if observed["run_id"] != run_id or observed["artifact_id"] != artifact_id:
        raise GitHubObservationError("locator does not match observed identity")
    if (
        observed["artifact_name"] != policy.artifact_name
        or artifact.get("expired") is not False
    ):
        raise GitHubObservationError("artifact identity or expiry is not trusted")
    if not _ARTIFACT_DIGEST.fullmatch(observed["artifact_digest"]):
        raise GitHubObservationError("artifact digest is not a canonical sha256")
    if (
        observed["artifact_run_id"] != run_id
        or observed["artifact_repository_id"] != observed["repository_id"]
        or observed["artifact_head_repository_id"] != observed["head_repository_id"]
    ):
        raise GitHubObservationError("artifact workflow run does not match")
    if (
        observed["artifact_head_sha"] != observed["head_sha"]
        or observed["artifact_head_branch"] != "main"
    ):
        raise GitHubObservationError("artifact workflow identity does not match")
    if manifest_run is not None:
        _same(
            observed,
            manifest_run,
            (
                "run_id",
                "artifact_id",
                "run_attempt",
                "repository_full_name",
                "artifact_name",
                "artifact_run_id",
            ),
        )
        if manifest_run.get("workflow_path") != policy.workflow_path:
            raise GitHubObservationError("manifest workflow path does not match policy")
        if manifest_run.get("trusted_workflow_sha") != observed["head_sha"]:
            raise GitHubObservationError("trusted workflow SHA does not match")
        for field in ("repository_id",):
            if manifest_run.get(field) != observed.get(field):
                raise GitHubObservationError(
                    f"manifest run.{field} does not match observation"
                )
    return observed
