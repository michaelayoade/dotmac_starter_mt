#!/usr/bin/env python3
"""Verify retained and registry kernel artifacts, then emit a typed receipt."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from release_artifact_verification import (
    CleanInstallObservation,
    RegistryFile,
    RetainedBuildFile,
    canonical_json,
    canonical_kernel_filenames,
    observe_file,
    verify_release_artifacts,
)

CANONICAL_REPOSITORY = "michaelayoade/dotmac_starter_mt"
CANONICAL_WORKFLOW_PATH = ".github/workflows/release-kernel.yml"
CANONICAL_ARTIFACT_NAME = "dotmac-kernel-dist"
CANONICAL_REGISTRY_ORIGIN = "https://registry.dotmac.io"
CANONICAL_REGISTRY_LOGIN = "ci-reader"


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"kernel verification refused: {name} is required")
    return value


def exact_files(directory: Path, expected: frozenset[str]) -> list[Path]:
    observed = [path for path in directory.iterdir() if path.is_file()]
    names = {path.name for path in observed}
    if names != expected or len(observed) != len(expected):
        raise SystemExit(
            f"kernel verification refused names {sorted(names)}, "
            f"expected {sorted(expected)}"
        )
    return sorted(observed)


def require_canonical_facility(repository: str, ref: str) -> None:
    if ref != "refs/heads/main":
        raise SystemExit(
            "kernel verification refused: facility was not dispatched from main"
        )
    if repository != CANONICAL_REPOSITORY:
        raise SystemExit(
            "kernel verification refused: facility repository is not canonical"
        )


def clean_install(path: Path, *, version: str) -> CleanInstallObservation:
    with tempfile.TemporaryDirectory(prefix="dotmac-kernel-verify-") as temp:
        venv = Path(temp) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = venv / "bin" / "python"
        installation_environment = os.environ.copy()
        for name in (
            "PYTHONPATH",
            "PYTHONHOME",
            "PIP_CONFIG_FILE",
            "PIP_EXTRA_INDEX_URL",
            "PIP_INDEX_URL",
            "PIP_TRUSTED_HOST",
        ):
            installation_environment.pop(name, None)
        installation_environment.update(
            {
                "PIP_CONFIG_FILE": os.devnull,
                "PYTHONNOUSERSITE": "1",
            }
        )
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--isolated",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--index-url",
                "https://pypi.org/simple",
                str(path),
            ],
            check=True,
            env=installation_environment,
        )
        subprocess.run(
            [str(python), "-m", "pip", "check"],
            check=True,
            env=installation_environment,
        )
        probe = """
import importlib.metadata as metadata
import json
from pathlib import Path
import sys
import dotmac_kernel
from dotmac_kernel.app_factory import create_app
distribution = metadata.distribution("dotmac-kernel")
prefix = Path(sys.prefix).resolve()
module_path = Path(dotmac_kernel.__file__).resolve()
distribution_path = Path(distribution.locate_file("")).resolve()
print(json.dumps({
    "distribution": metadata.metadata("dotmac-kernel")["Name"],
    "metadata_version": metadata.version("dotmac-kernel"),
    "module_version": dotmac_kernel.__version__,
    "import_passed": callable(create_app),
    "module_in_venv": module_path.is_relative_to(prefix),
    "distribution_in_venv": distribution_path.is_relative_to(prefix),
}))
"""
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment.update(
            {
                "DATABASE_URL": "postgresql+psycopg://x:x@127.0.0.1:59999/x",
                "PLATFORM_DATABASE_URL": "postgresql+psycopg://x:x@127.0.0.1:59999/x",
                "PYTHONNOUSERSITE": "1",
            }
        )
        probe_cwd = Path(temp) / "empty-cwd"
        probe_cwd.mkdir()
        completed = subprocess.run(
            [str(python), "-c", probe],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
            env=environment,
            cwd=probe_cwd,
        )
        result = json.loads(completed.stdout)
    return CleanInstallObservation(
        name=path.name,
        distribution=str(result["distribution"]),
        version=str(result["metadata_version"]),
        dependencies_resolved=True,
        metadata_matches=(
            result["metadata_version"] == version
            and result["module_version"] == version
        ),
        import_passed=(
            result["import_passed"] is True
            and result["module_in_venv"] is True
            and result["distribution_in_venv"] is True
        ),
    )


def main() -> int:
    version = required("RELEASE_VERSION")
    expected = canonical_kernel_filenames(version)
    source_sha = required("ORIGINAL_SOURCE_SHA")
    tag_name = required("RELEASE_TAG")
    for label, value in (
        ("source SHA", source_sha),
        ("facility source SHA", required("FACILITY_SOURCE_SHA")),
    ):
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise SystemExit(f"kernel verification refused: {label} is not canonical")
    require_canonical_facility(
        required("FACILITY_REPOSITORY"), required("FACILITY_REF")
    )

    supplied_names = frozenset(filter(None, required("EXPECTED_FILENAMES").split("\n")))
    if supplied_names != expected:
        raise SystemExit("kernel verification refused: filenames are not canonical")
    retained_paths = exact_files(Path(required("RETAINED_OUTPUT_DIR")), expected)
    registry_paths = exact_files(Path(required("REGISTRY_OUTPUT_DIR")), expected)
    retained_files = [observe_file(path, RetainedBuildFile) for path in retained_paths]
    registry_files = [observe_file(path, RegistryFile) for path in registry_paths]
    github_observation = json.loads(
        Path(required("GITHUB_OBSERVATION")).read_text(encoding="utf-8")
    )
    github_keys = {
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
    }
    expected_github = {
        "schema": "GitHubRetainedReleaseArtifactObservation.v1",
        "repository": CANONICAL_REPOSITORY,
        "workflow_path": CANONICAL_WORKFLOW_PATH,
        "head_branch": "main",
        "head_sha": source_sha,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "run_id": int(required("ORIGINAL_RUN_ID")),
        "run_attempt": 1,
        "artifact_id": int(required("ORIGINAL_ARTIFACT_ID")),
        "artifact_name": CANONICAL_ARTIFACT_NAME,
        "filenames": sorted(expected),
    }
    if set(github_observation) != github_keys or any(
        github_observation.get(key) != value for key, value in expected_github.items()
    ):
        raise SystemExit("kernel verification refused: GitHub observation differs")
    if (
        not isinstance(github_observation.get("artifact_size_in_bytes"), int)
        or int(github_observation["artifact_size_in_bytes"]) <= 0
    ):
        raise SystemExit("kernel verification refused: artifact size is invalid")
    artifact_digest = github_observation.get("artifact_digest")
    if artifact_digest is not None and (
        not isinstance(artifact_digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_digest) is None
    ):
        raise SystemExit("kernel verification refused: artifact digest is invalid")
    registry_observation = json.loads(
        Path(required("REGISTRY_OBSERVATION")).read_text(encoding="utf-8")
    )
    registry_keys = {
        "schema",
        "index_origin",
        "observed_identity",
        "facility_http_methods",
        "files",
    }
    observed_registry_files = registry_observation.get("files")
    expected_registry_files = [
        {"name": file.name, "size": file.size}
        for file in sorted(registry_files, key=lambda f: f.name)
    ]
    if (
        set(registry_observation) != registry_keys
        or registry_observation.get("schema") != "PrivateRegistryReadObservation.v1"
        or registry_observation.get("index_origin") != CANONICAL_REGISTRY_ORIGIN
        or registry_observation.get("observed_identity")
        != {"login": CANONICAL_REGISTRY_LOGIN, "is_admin": False}
        or registry_observation.get("facility_http_methods") != ["GET"]
        or observed_registry_files != expected_registry_files
    ):
        raise SystemExit("kernel verification refused: registry observation differs")
    source_binding_path = Path(required("SOURCE_BINDING"))
    source_binding_bytes = source_binding_path.read_bytes()
    source_binding = json.loads(source_binding_bytes)
    if canonical_json(source_binding) != source_binding_bytes:
        raise SystemExit("kernel verification refused: source binding is not canonical")
    if (
        not isinstance(source_binding, dict)
        or set(source_binding)
        != {
            "schema",
            "state",
            "source_sha",
            "authorization_commit",
            "authorization",
        }
        or source_binding.get("schema") != "KernelReleaseSourceBinding.v1"
        or source_binding.get("state") != "allocated"
        or source_binding.get("source_sha") != source_sha
        or re.fullmatch(
            r"[0-9a-f]{40}", str(source_binding.get("authorization_commit"))
        )
        is None
        or not isinstance(source_binding.get("authorization"), dict)
        or source_binding["authorization"].get("target_version") != version
    ):
        raise SystemExit("kernel verification refused: source binding differs")
    decision = verify_release_artifacts(
        expected_names=expected,
        retained=retained_files,
        registry=registry_files,
        installs=[clean_install(path, version=version) for path in registry_paths],
        distribution="dotmac-kernel",
        version=version,
    )
    facility_run_id = required("FACILITY_RUN_ID")
    facility_attempt = required("FACILITY_RUN_ATTEMPT")
    if re.fullmatch(r"[1-9][0-9]*", facility_run_id) is None or facility_attempt != "1":
        raise SystemExit(
            "kernel verification refused: facility coordinates are invalid"
        )
    receipt = {
        "schema": "KernelReleaseVerificationReceipt.v1",
        "authorization": source_binding,
        "facility": {
            "repository": required("FACILITY_REPOSITORY"),
            "ref": required("FACILITY_REF"),
            "source_sha": required("FACILITY_SOURCE_SHA"),
            "run_id": int(facility_run_id),
            "run_attempt": 1,
        },
        "release": {
            "distribution": "dotmac-kernel",
            "version": version,
            "expected_tag": tag_name,
            "source_sha": source_sha,
            "retained_build_observation": github_observation,
            "registry_observation": registry_observation,
        },
        **decision,
    }
    output = Path(required("VERIFICATION_RECEIPT"))
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.write_bytes(canonical_json(receipt))
    output.chmod(0o600)
    print(f"kernel {version} independently verified; receipt written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
