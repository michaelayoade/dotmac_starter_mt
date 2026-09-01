#!/usr/bin/env python3
"""Persist one immutable kernel verification and tag proof in the repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from release_artifact_verification import canonical_json, canonical_kernel_filenames
from tag_kernel_release_once import validate_receipt

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "docs/inventories/kernel-release-verifications"
SCHEMA = "KernelReleaseEvidence.v1"


class RecordRefused(ValueError):
    """The supplied receipts cannot extend the immutable ledger."""


def validate_persisted_record(record: object, *, version: str) -> None:
    expected_fields = {
        "schema",
        "version",
        "tag",
        "tag_object",
        "tag_disposition",
        "source_sha",
        "authorization",
        "publisher",
        "verifier",
        "verification_receipt_sha256",
        "verification_receipt_artifact",
        "tag_decision_receipt_sha256",
        "tag_decision_receipt_artifact",
        "registry",
        "files",
    }
    if not isinstance(record, dict) or set(record) != expected_fields:
        raise RecordRefused("persisted kernel evidence fields differ")
    canonical_kernel_filenames(version)
    if any(
        (
            record["schema"] != SCHEMA,
            record["version"] != version,
            record["tag"] != f"dotmac-kernel-v{version}",
            record["tag_disposition"] not in {"CREATE", "ALREADY"},
            re.fullmatch(r"[0-9a-f]{40}", str(record["tag_object"])) is None,
            re.fullmatch(r"[0-9a-f]{40}", str(record["source_sha"])) is None,
            re.fullmatch(r"[0-9a-f]{64}", str(record["verification_receipt_sha256"]))
            is None,
            re.fullmatch(r"[0-9a-f]{64}", str(record["tag_decision_receipt_sha256"]))
            is None,
            record["verification_receipt_artifact"]
            != "kernel-release-verification-receipt",
            record["tag_decision_receipt_artifact"] != "kernel-release-tag-decision",
        )
    ):
        raise RecordRefused("persisted kernel evidence identity differs")
    authorization = record["authorization"]
    if not isinstance(authorization, dict) or set(authorization) != {
        "schema",
        "state",
        "source_sha",
        "authorization_commit",
        "authorization",
    }:
        raise RecordRefused("persisted authorization binding fields differ")
    allocation = authorization["authorization"]
    if (
        authorization["schema"] != "KernelReleaseSourceBinding.v1"
        or authorization["state"] != "allocated"
        or authorization["source_sha"] != record["source_sha"]
        or re.fullmatch(r"[0-9a-f]{40}", str(authorization["authorization_commit"]))
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
        or allocation["target_version"] != version
        or re.fullmatch(r"[0-9a-f]{40}", str(allocation["latest_tag_object"])) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(allocation["latest_tag_commit"])) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(allocation["base_sha"])) is None
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(allocation["normalized_release_input_digest"]),
        )
        is None
    ):
        raise RecordRefused("persisted authorization binding differs")
    publisher = record["publisher"]
    verifier = record["verifier"]
    registry = record["registry"]
    if not isinstance(publisher, dict) or set(publisher) != {
        "repository",
        "workflow_path",
        "head_branch",
        "run_id",
        "run_attempt",
        "artifact_id",
    }:
        raise RecordRefused("persisted publisher fields differ")
    if not isinstance(verifier, dict) or set(verifier) != {
        "repository",
        "ref",
        "source_sha",
        "run_id",
        "run_attempt",
    }:
        raise RecordRefused("persisted verifier fields differ")
    if not isinstance(registry, dict) or set(registry) != {
        "index_origin",
        "observed_identity",
        "facility_http_methods",
    }:
        raise RecordRefused("persisted registry fields differ")
    if any(
        (
            publisher["repository"] != "michaelayoade/dotmac_starter_mt",
            publisher["workflow_path"] != ".github/workflows/release-kernel.yml",
            publisher["head_branch"] != "main",
            publisher["run_attempt"] != 1,
            not isinstance(publisher["run_id"], int),
            publisher["run_id"] <= 0,
            not isinstance(publisher["artifact_id"], int),
            publisher["artifact_id"] <= 0,
            verifier["repository"] != "michaelayoade/dotmac_starter_mt",
            verifier["ref"] != "refs/heads/main",
            verifier["run_attempt"] != 1,
            not isinstance(verifier["run_id"], int),
            verifier["run_id"] <= 0,
            re.fullmatch(r"[0-9a-f]{40}", str(verifier["source_sha"])) is None,
            registry["index_origin"] != "https://registry.dotmac.io",
            registry["observed_identity"] != {"login": "ci-reader", "is_admin": False},
            registry["facility_http_methods"] != ["GET"],
        )
    ):
        raise RecordRefused("persisted provider identity differs")
    files = record["files"]
    expected_names = canonical_kernel_filenames(version)
    if (
        not isinstance(files, list)
        or len(files) != 2
        or {item.get("name") for item in files if isinstance(item, dict)}
        != expected_names
        or not all(
            isinstance(item, dict)
            and set(item) == {"name", "size", "sha256"}
            and isinstance(item["size"], int)
            and item["size"] > 0
            and re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])) is not None
            for item in files
        )
    ):
        raise RecordRefused("persisted file evidence differs")


def canonical_document(path: Path) -> tuple[dict[str, object], bytes]:
    payload = path.read_bytes()
    document = json.loads(payload)
    if not isinstance(document, dict) or canonical_json(document) != payload:
        raise RecordRefused(f"{path.name} is not canonical JSON")
    return document, payload


def build_record(
    verification_path: Path,
    tag_path: Path,
    *,
    expected_version: str,
    expected_tag: str,
) -> tuple[str, dict[str, object]]:
    verification, verification_bytes = canonical_document(verification_path)
    decision, decision_bytes = canonical_document(tag_path)
    if set(verification) != {
        "schema",
        "authorization",
        "facility",
        "release",
        "verdict",
        "files",
    }:
        raise RecordRefused("verification receipt fields differ")
    if verification["schema"] != "KernelReleaseVerificationReceipt.v1":
        raise RecordRefused("verification schema differs")
    if verification["verdict"] != "verified":
        raise RecordRefused("verification verdict differs")
    if set(decision) != {
        "schema",
        "disposition",
        "tag",
        "tag_object",
        "source_sha",
        "verification",
    }:
        raise RecordRefused("tag decision fields differ")
    if decision["schema"] != "KernelReleaseTagDecision.v1" or decision[
        "disposition"
    ] not in {"CREATE", "ALREADY"}:
        raise RecordRefused("tag decision is not successful")
    release = verification["release"]
    facility = verification["facility"]
    chain = decision["verification"]
    if not all(isinstance(value, dict) for value in (release, facility, chain)):
        raise RecordRefused("receipt coordinates are not objects")
    version = release.get("version")
    if not isinstance(version, str):
        raise RecordRefused("release version is absent")
    if version != expected_version or release.get("expected_tag") != expected_tag:
        raise RecordRefused("outer release coordinates differ from the receipts")
    validate_receipt(
        verification,
        version=version,
        tag=str(release.get("expected_tag")),
        source_sha=str(release.get("source_sha")),
        facility_sha=str(facility.get("source_sha")),
        run_id=int(facility.get("run_id", 0)),
    )
    expected_names = canonical_kernel_filenames(version)
    files = verification["files"]
    if (
        not isinstance(files, list)
        or {item.get("name") for item in files if isinstance(item, dict)}
        != expected_names
    ):
        raise RecordRefused("verification file names differ")
    compact_files = [
        {"name": item["name"], "size": item["size"], "sha256": item["build_sha256"]}
        for item in sorted(files, key=lambda value: value["name"])
    ]
    retained = release.get("retained_build_observation")
    if not isinstance(retained, dict):
        raise RecordRefused("publisher observation is absent")
    expected_chain = {
        "receipt_sha256": hashlib.sha256(verification_bytes).hexdigest(),
        "facility_source_sha": facility.get("source_sha"),
        "facility_run_id": facility.get("run_id"),
        "facility_run_attempt": facility.get("run_attempt"),
        "publisher_run_id": retained.get("run_id"),
        "publisher_run_attempt": retained.get("run_attempt"),
        "publisher_artifact_id": retained.get("artifact_id"),
        "files": compact_files,
    }
    if chain != expected_chain:
        raise RecordRefused("tag decision does not preserve the verification chain")
    tag = release.get("expected_tag")
    source_sha = release.get("source_sha")
    if (
        tag != f"dotmac-kernel-v{version}"
        or decision["tag"] != tag
        or decision["source_sha"] != source_sha
        or re.fullmatch(r"[0-9a-f]{40}", str(source_sha)) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(decision["tag_object"])) is None
    ):
        raise RecordRefused("tag and release coordinates differ")
    record = {
        "schema": SCHEMA,
        "version": version,
        "tag": tag,
        "tag_object": decision["tag_object"],
        "tag_disposition": decision["disposition"],
        "source_sha": source_sha,
        "authorization": verification["authorization"],
        "publisher": {
            "repository": retained["repository"],
            "workflow_path": retained["workflow_path"],
            "head_branch": retained["head_branch"],
            "run_id": retained["run_id"],
            "run_attempt": retained["run_attempt"],
            "artifact_id": retained["artifact_id"],
        },
        "verifier": {
            "repository": facility["repository"],
            "ref": facility["ref"],
            "source_sha": facility["source_sha"],
            "run_id": facility["run_id"],
            "run_attempt": facility["run_attempt"],
        },
        "verification_receipt_sha256": hashlib.sha256(verification_bytes).hexdigest(),
        "verification_receipt_artifact": "kernel-release-verification-receipt",
        "tag_decision_receipt_sha256": hashlib.sha256(decision_bytes).hexdigest(),
        "tag_decision_receipt_artifact": "kernel-release-tag-decision",
        "registry": {
            "index_origin": release["registry_observation"]["index_origin"],
            "observed_identity": release["registry_observation"]["observed_identity"],
            "facility_http_methods": release["registry_observation"][
                "facility_http_methods"
            ],
        },
        "files": compact_files,
    }
    validate_persisted_record(record, version=version)
    return version, record


def git_output(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def verify_live_tag(record: dict[str, object], *, repo: Path) -> None:
    tag = str(record["tag"])
    tag_object = str(record["tag_object"])
    source_sha = str(record["source_sha"])
    remote = git_output(repo, "ls-remote", "--tags", "origin", f"refs/tags/{tag}")
    if remote.split() != [tag_object, f"refs/tags/{tag}"]:
        raise RecordRefused("canonical remote tag object differs")
    if git_output(repo, "cat-file", "-t", tag) != "tag":
        raise RecordRefused("canonical tag is not annotated")
    if git_output(repo, "rev-parse", tag) != tag_object:
        raise RecordRefused("canonical tag object differs")
    if git_output(repo, "rev-parse", f"{tag}^{{commit}}") != source_sha:
        raise RecordRefused("canonical tag peel differs")


def immutable_release_facts(record: dict[str, object]) -> dict[str, object]:
    facts = {
        key: record[key]
        for key in (
            "version",
            "tag",
            "tag_object",
            "source_sha",
            "authorization",
            "publisher",
            "registry",
            "files",
        )
    }
    verifier = record["verifier"]
    if not isinstance(verifier, dict):
        raise RecordRefused("verifier record is not an object")
    facts["verifier_binding"] = {
        "repository": verifier.get("repository"),
        "ref": verifier.get("ref"),
    }
    return facts


def write_record(version: str, record: dict[str, object], *, repo: Path = ROOT) -> str:
    canonical_kernel_filenames(version)
    validate_persisted_record(record, version=version)
    verify_live_tag(record, repo=repo)
    destination = RECORDS / f"{version}.json"
    expected = canonical_json(record)
    if destination.exists():
        existing_bytes = destination.read_bytes()
        existing = json.loads(existing_bytes)
        if canonical_json(existing) != existing_bytes:
            raise RecordRefused(f"kernel {version} existing record is not canonical")
        validate_persisted_record(existing, version=version)
        if immutable_release_facts(existing) != immutable_release_facts(record):
            raise RecordRefused(
                f"kernel {version} already has different immutable release facts"
            )
        return "ALREADY"
    RECORDS.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(expected)
    return "CREATE"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verification-receipt", type=Path, required=True)
    parser.add_argument("--tag-decision-receipt", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-tag", required=True)
    args = parser.parse_args()
    try:
        version, record = build_record(
            args.verification_receipt,
            args.tag_decision_receipt,
            expected_version=args.expected_version,
            expected_tag=args.expected_tag,
        )
        disposition = write_record(version, record)
    except (OSError, ValueError, json.JSONDecodeError) as failure:
        raise SystemExit(f"kernel verification record REFUSED: {failure}") from failure
    print(f"{disposition}: permanent kernel {version} verification record")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
