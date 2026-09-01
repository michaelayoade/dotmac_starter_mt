#!/usr/bin/env python3
"""Create or reconcile one annotated kernel tag after independent verification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from release_artifact_verification import canonical_json, canonical_kernel_filenames

CANONICAL_REPOSITORY = "michaelayoade/dotmac_starter_mt"


class TagRefused(ValueError):
    """The receipt or existing tag does not authorize the requested result."""


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise TagRefused(f"{name} is required")
    return value


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def validate_receipt(
    receipt: object,
    *,
    version: str,
    tag: str,
    source_sha: str,
    facility_sha: str,
    run_id: int,
) -> None:
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema",
        "authorization",
        "facility",
        "release",
        "verdict",
        "files",
    }:
        raise TagRefused("verification receipt fields differ")
    if receipt["schema"] != "KernelReleaseVerificationReceipt.v1":
        raise TagRefused("verification receipt schema differs")
    if receipt["verdict"] != "verified":
        raise TagRefused("verification receipt is not verified")
    authorization = receipt["authorization"]
    if not isinstance(authorization, dict) or set(authorization) != {
        "schema",
        "state",
        "source_sha",
        "authorization_commit",
        "authorization",
    }:
        raise TagRefused("release authorization binding fields differ")
    allocation = authorization.get("authorization")
    if (
        authorization.get("schema") != "KernelReleaseSourceBinding.v1"
        or authorization.get("state") != "allocated"
        or authorization.get("source_sha") != source_sha
        or re.fullmatch(r"[0-9a-f]{40}", str(authorization.get("authorization_commit")))
        is None
        or not isinstance(allocation, dict)
        or set(allocation)
        != {
            "latest_tag",
            "latest_tag_object",
            "latest_tag_commit",
            "base_sha",
            "target_version",
            "normalized_release_input_digest",
        }
        or allocation.get("target_version") != version
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(allocation.get("normalized_release_input_digest")),
        )
        is None
    ):
        raise TagRefused("release authorization binding differs")
    facility = receipt["facility"]
    expected_facility = {
        "repository": CANONICAL_REPOSITORY,
        "ref": "refs/heads/main",
        "source_sha": facility_sha,
        "run_id": run_id,
        "run_attempt": 1,
    }
    if facility != expected_facility:
        raise TagRefused("verification facility coordinates differ")
    release = receipt["release"]
    if not isinstance(release, dict) or set(release) != {
        "distribution",
        "version",
        "expected_tag",
        "source_sha",
        "retained_build_observation",
        "registry_observation",
    }:
        raise TagRefused("verification release coordinates differ")
    if any(
        (
            release["distribution"] != "dotmac-kernel",
            release["version"] != version,
            release["expected_tag"] != tag,
            release["source_sha"] != source_sha,
        )
    ):
        raise TagRefused("verification release binding differs")
    retained = release["retained_build_observation"]
    registry = release["registry_observation"]
    if not isinstance(retained, dict) or set(retained) != {
        "schema",
        "repository",
        "workflow_path",
        "head_branch",
        "head_sha",
        "event",
        "status",
        "conclusion",
        "run_id",
        "run_attempt",
        "artifact_id",
        "artifact_name",
        "artifact_size_in_bytes",
        "artifact_digest",
        "filenames",
    }:
        raise TagRefused("retained-build observation fields differ")
    if not isinstance(registry, dict) or set(registry) != {
        "schema",
        "index_origin",
        "observed_identity",
        "facility_http_methods",
        "files",
    }:
        raise TagRefused("registry observation fields differ")
    expected_names = canonical_kernel_filenames(version)
    if any(
        (
            retained["repository"] != CANONICAL_REPOSITORY,
            retained["schema"] != "GitHubRetainedReleaseArtifactObservation.v1",
            retained["workflow_path"] != ".github/workflows/release-kernel.yml",
            retained["head_branch"] != "main",
            retained["head_sha"] != source_sha,
            retained["event"] != "workflow_dispatch",
            retained["status"] != "completed",
            retained["conclusion"] != "success",
            not isinstance(retained["run_id"], int),
            retained["run_id"] <= 0,
            retained["run_attempt"] != 1,
            not isinstance(retained["artifact_id"], int),
            retained["artifact_id"] <= 0,
            retained["artifact_name"] != "dotmac-kernel-dist",
            not isinstance(retained["artifact_size_in_bytes"], int),
            retained["artifact_size_in_bytes"] <= 0,
            retained["filenames"] != sorted(expected_names),
            registry["schema"] != "PrivateRegistryReadObservation.v1",
            registry["index_origin"] != "https://registry.dotmac.io",
            registry["observed_identity"] != {"login": "ci-reader", "is_admin": False},
            registry["facility_http_methods"] != ["GET"],
        )
    ):
        raise TagRefused("provider observations do not bind the release")
    artifact_digest = retained["artifact_digest"]
    if artifact_digest is not None and (
        not isinstance(artifact_digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_digest) is None
    ):
        raise TagRefused("retained-build artifact digest is invalid")
    files = receipt["files"]
    if (
        not isinstance(files, list)
        or len(files) != 2
        or {item.get("name") for item in files if isinstance(item, dict)}
        != expected_names
        or not all(
            isinstance(item, dict)
            and set(item)
            == {
                "name",
                "size",
                "build_sha256",
                "registry_sha256",
                "byte_equal",
                "clean_install",
            }
            and item.get("byte_equal") is True
            and isinstance(item.get("size"), int)
            and item["size"] > 0
            and re.fullmatch(r"[0-9a-f]{64}", str(item.get("build_sha256")))
            and item.get("build_sha256") == item.get("registry_sha256")
            and isinstance(item.get("clean_install"), dict)
            and set(item["clean_install"])
            == {
                "name",
                "distribution",
                "version",
                "dependencies_resolved",
                "metadata_matches",
                "import_passed",
            }
            and item["clean_install"].get("name") == item.get("name")
            and item["clean_install"].get("distribution") == "dotmac-kernel"
            and item["clean_install"].get("version") == version
            and all(
                item["clean_install"].get(field) is True
                for field in (
                    "dependencies_resolved",
                    "metadata_matches",
                    "import_passed",
                )
            )
            for item in files
        )
    ):
        raise TagRefused("verification file proof differs")
    expected_registry_files = [
        {"name": item["name"], "size": item["size"]}
        for item in sorted(files, key=lambda value: value["name"])
    ]
    if registry["files"] != expected_registry_files:
        raise TagRefused("registry file observation differs from verified files")


def require_live_authorization(receipt: dict[str, object], path: Path) -> None:
    live_bytes = path.read_bytes()
    live = json.loads(live_bytes)
    if canonical_json(live) != live_bytes:
        raise TagRefused("live release authorization binding is not canonical")
    if live != receipt.get("authorization"):
        raise TagRefused("live release authorization binding differs from receipt")


def reconcile_tag(repo: Path, *, tag: str, source_sha: str) -> dict[str, object]:
    remote = git(repo, "ls-remote", "--tags", "origin", f"refs/tags/{tag}")
    rows = [line for line in remote.stdout.splitlines() if line.strip()]
    local = git(repo, "show-ref", "--verify", f"refs/tags/{tag}", check=False)
    if not rows:
        if local.returncode == 0:
            raise TagRefused("tag exists locally but not on the canonical remote")
        git(repo, "config", "user.name", "github-actions[bot]")
        git(
            repo,
            "config",
            "user.email",
            "github-actions[bot]@users.noreply.github.com",
        )
        git(
            repo,
            "tag",
            "-a",
            tag,
            "-m",
            (
                f"dotmac-kernel {tag.removeprefix('dotmac-kernel-v')} "
                "independently verified"
            ),
            source_sha,
        )
        git(repo, "push", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
        disposition = "CREATE"
    else:
        fetched = git(
            repo,
            "fetch",
            "origin",
            f"refs/tags/{tag}:refs/tags/{tag}",
            check=False,
        )
        if fetched.returncode != 0:
            raise TagRefused("existing remote tag conflicts with the checkout")
        disposition = "ALREADY"
    object_type = git(repo, "cat-file", "-t", tag).stdout.strip()
    peel = git(repo, "rev-parse", f"{tag}^{{commit}}").stdout.strip()
    if object_type != "tag" or peel != source_sha:
        raise TagRefused("existing tag is lightweight or identifies different bytes")
    tag_object = git(repo, "rev-parse", tag).stdout.strip()
    return {
        "schema": "KernelReleaseTagDecision.v1",
        "disposition": disposition,
        "tag": tag,
        "tag_object": tag_object,
        "source_sha": source_sha,
    }


def main() -> int:
    version = required("RELEASE_VERSION")
    tag = required("RELEASE_TAG")
    source_sha = required("RELEASE_SOURCE_SHA")
    facility_sha = required("FACILITY_SOURCE_SHA")
    run_id_text = required("FACILITY_RUN_ID")
    attempt = required("FACILITY_RUN_ATTEMPT")
    repository = required("FACILITY_REPOSITORY")
    if repository != CANONICAL_REPOSITORY or attempt != "1":
        raise TagRefused("tag facility coordinates are not canonical")
    if tag != f"dotmac-kernel-v{version}":
        raise TagRefused("tag does not derive from the release version")
    for value in (source_sha, facility_sha):
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise TagRefused("source coordinate is not canonical")
    if re.fullmatch(r"[1-9][0-9]*", run_id_text) is None:
        raise TagRefused("run coordinate is not canonical")
    receipt_path = Path(required("VERIFICATION_RECEIPT"))
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    if canonical_json(receipt) != receipt_bytes:
        raise TagRefused("verification receipt is not canonical")
    validate_receipt(
        receipt,
        version=version,
        tag=tag,
        source_sha=source_sha,
        facility_sha=facility_sha,
        run_id=int(run_id_text),
    )
    require_live_authorization(receipt, Path(required("LIVE_SOURCE_BINDING")))
    decision = reconcile_tag(Path.cwd(), tag=tag, source_sha=source_sha)
    retained = receipt["release"]["retained_build_observation"]
    decision["verification"] = {
        "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "facility_source_sha": facility_sha,
        "facility_run_id": int(run_id_text),
        "facility_run_attempt": 1,
        "publisher_run_id": retained["run_id"],
        "publisher_run_attempt": retained["run_attempt"],
        "publisher_artifact_id": retained["artifact_id"],
        "files": [
            {
                "name": item["name"],
                "size": item["size"],
                "sha256": item["build_sha256"],
            }
            for item in sorted(receipt["files"], key=lambda value: value["name"])
        ],
    }
    output = Path(required("TAG_DECISION_RECEIPT"))
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.write_bytes(canonical_json(decision))
    output.chmod(0o600)
    print(f"{decision['disposition']}: {tag} -> {source_sha}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TagRefused as failure:
        print(f"REFUSE: {failure}", file=os.sys.stderr)
        raise SystemExit(1) from failure
