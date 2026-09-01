#!/usr/bin/env python3
"""Collect one immutable GitHub Actions artifact after binding its run."""

from __future__ import annotations

import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from release_artifact_verification import canonical_kernel_filenames

API = "https://api.github.com"
CANONICAL_REPOSITORY = "michaelayoade/dotmac_starter_mt"
CANONICAL_WORKFLOW_PATH = ".github/workflows/release-kernel.yml"
CANONICAL_ARTIFACT_NAME = "dotmac-kernel-dist"


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    """Allow signed HTTPS downloads without forwarding the GitHub token."""

    def redirect_request(self, request, fp, code, message, headers, new_url):
        parsed = urllib.parse.urlsplit(new_url)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise urllib.error.HTTPError(new_url, code, "unsafe redirect", headers, fp)
        redirected = super().redirect_request(
            request, fp, code, message, headers, new_url
        )
        if redirected is not None and parsed.netloc != "api.github.com":
            redirected.remove_header("Authorization")
        return redirected


OPENER = urllib.request.build_opener(GitHubRedirect())


def get_json(path: str, token: str) -> dict[str, object]:
    request = urllib.request.Request(  # noqa: S310 -- API is a fixed HTTPS origin.
        API + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
        },
    )
    with OPENER.open(request, timeout=30) as response:
        return json.load(response)


def get_bytes(path: str, token: str) -> bytes:
    request = urllib.request.Request(  # noqa: S310 -- API is a fixed HTTPS origin.
        API + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
        },
    )
    with OPENER.open(request, timeout=60) as response:
        return response.read()


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"GitHub artifact collection refused: {name} is required")
    return value


def require_canonical_workflow(run: dict[str, object]) -> None:
    if run.get("path") != CANONICAL_WORKFLOW_PATH:
        raise SystemExit("GitHub artifact collection refused: workflow path differs")


def main() -> int:
    token = required("GITHUB_TOKEN")
    repository = CANONICAL_REPOSITORY
    run_id = required("ORIGINAL_RUN_ID")
    attempt_text = required("ORIGINAL_RUN_ATTEMPT")
    if attempt_text != "1":
        raise SystemExit(
            "GitHub artifact collection refused: original attempt must be 1"
        )
    run_attempt = 1
    source_sha = required("ORIGINAL_SOURCE_SHA")
    if re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise SystemExit(
            "GitHub artifact collection refused: source SHA is not canonical"
        )
    artifact_id = required("ORIGINAL_ARTIFACT_ID")
    artifact_name = CANONICAL_ARTIFACT_NAME
    version = required("RELEASE_VERSION")
    expected = frozenset(filter(None, required("EXPECTED_FILENAMES").split("\n")))
    if expected != canonical_kernel_filenames(version):
        raise SystemExit(
            "GitHub artifact collection refused: filenames are not canonical"
        )
    if (
        re.fullmatch(r"[1-9][0-9]*", run_id) is None
        or re.fullmatch(r"[1-9][0-9]*", artifact_id) is None
    ):
        raise SystemExit("GitHub artifact collection refused: IDs are not canonical")
    output = Path(required("RETAINED_OUTPUT_DIR"))

    run = get_json(f"/repos/{repository}/actions/runs/{run_id}", token)
    if run.get("head_sha") != source_sha:
        raise SystemExit("GitHub artifact collection refused: source SHA differs")
    if (
        run.get("event") != "workflow_dispatch"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run.get("head_branch") != "main"
    ):
        raise SystemExit(
            "GitHub artifact collection refused: original run is not a "
            "successful completed main dispatch"
        )
    if int(run.get("run_attempt", 0)) != run_attempt:
        raise SystemExit("GitHub artifact collection refused: run attempt differs")
    require_canonical_workflow(run)
    head_repository = run.get("head_repository") or {}
    if (
        not isinstance(head_repository, dict)
        or head_repository.get("full_name") != repository
    ):
        raise SystemExit("GitHub artifact collection refused: fork-origin run")

    artifact = get_json(f"/repos/{repository}/actions/artifacts/{artifact_id}", token)
    workflow_run = artifact.get("workflow_run") or {}
    if artifact.get("name") != artifact_name or artifact.get("expired") is not False:
        raise SystemExit(
            "GitHub artifact collection refused: artifact identity differs"
        )
    if not isinstance(workflow_run, dict) or str(workflow_run.get("id")) != run_id:
        raise SystemExit(
            "GitHub artifact collection refused: artifact belongs to another run"
        )
    archive = get_bytes(
        f"/repos/{repository}/actions/artifacts/{artifact_id}/zip", token
    )
    token = ""
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        members = [member for member in bundle.infolist() if not member.is_dir()]
        names = {PurePosixPath(member.filename).name for member in members}
        if names != expected or len(members) != len(expected):
            raise SystemExit(
                "GitHub artifact collection refused names "
                f"{sorted(names)}, expected {sorted(expected)}"
            )
        for member in members:
            name = PurePosixPath(member.filename).name
            if member.filename != name:
                raise SystemExit(
                    "GitHub artifact collection refused a nested artifact path"
                )
            destination = output / name
            destination.write_bytes(bundle.read(member))
            destination.chmod(0o600)
    observation = {
        "schema": "GitHubRetainedReleaseArtifactObservation.v1",
        "repository": str(head_repository["full_name"]),
        "workflow_path": CANONICAL_WORKFLOW_PATH,
        "head_branch": str(run["head_branch"]),
        "head_sha": str(run["head_sha"]),
        "event": str(run["event"]),
        "status": str(run["status"]),
        "conclusion": str(run["conclusion"]),
        "run_id": int(run_id),
        "run_attempt": run_attempt,
        "artifact_id": int(artifact_id),
        "artifact_name": str(artifact["name"]),
        "artifact_size_in_bytes": int(artifact["size_in_bytes"]),
        "artifact_digest": artifact.get("digest"),
        "filenames": sorted(expected),
    }
    observation_path = Path(required("GITHUB_OBSERVATION"))
    observation_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    observation_path.write_text(
        json.dumps(observation, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    observation_path.chmod(0o600)
    print(
        f"collected {len(expected)} retained build files from run {run_id}, "
        f"artifact {artifact_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
