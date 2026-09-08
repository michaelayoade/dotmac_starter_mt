#!/usr/bin/env python3
"""Verify retained and registry kernel artifacts, then emit a typed receipt."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
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
PUBLIC_EXPORTS_MEMBER = "dotmac_kernel/public_exports.json"
PUBLIC_EXPORTS_SCHEMA = "dotmac.kernel-public-exports.v1"
HISTORICAL_WITHOUT_PUBLIC_EXPORTS = frozenset({"0.1.0a101", "0.1.0a102"})


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


def _strict_catalogue(payload: bytes, *, label: str) -> dict[str, object]:
    """Parse the shipped catalogue without importing the package being verified."""

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise SystemExit(
                    f"kernel verification refused: duplicate catalogue key in {label}"
                )
            result[key] = value
        return result

    try:
        document = json.loads(payload, object_pairs_hook=pairs)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"kernel verification refused: catalogue {label} is invalid JSON"
        ) from exc
    if (
        not isinstance(document, dict)
        or document.get("schema") != PUBLIC_EXPORTS_SCHEMA
    ):
        raise SystemExit(
            f"kernel verification refused: catalogue {label} schema differs"
        )
    if set(document) != {
        "schema",
        "supported_modules",
        "internal_modules",
        "root_exports",
        "modules",
    } or not isinstance(document["modules"], dict):
        raise SystemExit(
            f"kernel verification refused: catalogue {label} fields differ"
        )
    if (
        not document["modules"]
        or not isinstance(document["supported_modules"], list)
        or not isinstance(document["internal_modules"], list)
        or not isinstance(document["root_exports"], list)
        or set(document["supported_modules"]) & set(document["internal_modules"])
        or document["supported_modules"] != sorted(set(document["supported_modules"]))
        or document["internal_modules"] != sorted(set(document["internal_modules"]))
        or document["root_exports"] != sorted(set(document["root_exports"]))
        or any(
            not isinstance(name, str)
            for name in (
                *document["supported_modules"],
                *document["internal_modules"],
                *document["root_exports"],
            )
        )
        or set(document["modules"])
        != set(document["supported_modules"]) | set(document["internal_modules"])
    ):
        raise SystemExit(
            f"kernel verification refused: catalogue {label} has no modules"
        )
    for module, entry in document["modules"].items():
        if not isinstance(module, str) or (
            module != "dotmac_kernel" and not module.startswith("dotmac_kernel.")
        ):
            raise SystemExit(
                f"kernel verification refused: catalogue {label} module is invalid"
            )
        if not isinstance(entry, dict) or set(entry) != {
            "classification",
            "exports",
            "status",
        }:
            raise SystemExit(
                f"kernel verification refused: catalogue {label} entry fields differ"
            )
        status = entry["status"]
        exports = entry["exports"]
        classification = entry["classification"]
        if classification not in {"supported", "internal"} or (
            classification == "supported"
        ) != (module in document["supported_modules"]):
            raise SystemExit(
                f"kernel verification refused: catalogue {label} classification differs"
            )
        if status == "unavailable":
            if exports is not None:
                raise SystemExit(
                    "kernel verification refused: "
                    f"catalogue {label} unavailable exports differ"
                )
        elif status == "declared":
            if (
                not isinstance(exports, list)
                or any(not isinstance(name, str) for name in exports)
                or exports != sorted(set(exports))
            ):
                raise SystemExit(
                    f"kernel verification refused: catalogue {label} exports differ"
                )
        else:
            raise SystemExit(
                f"kernel verification refused: catalogue {label} status differs"
            )
    if canonical_json(document, indent=2) != payload:
        raise SystemExit(
            f"kernel verification refused: catalogue {label} is not canonical"
        )
    return document


def _catalogue_member(path: Path, *, label: str) -> bytes | None:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            wheel_matches = [
                info
                for info in archive.infolist()
                if info.filename == PUBLIC_EXPORTS_MEMBER
            ]
            if not wheel_matches:
                return None
            if (
                len(wheel_matches) != 1
                or wheel_matches[0].is_dir()
                or wheel_matches[0].filename.startswith("/")
                or stat.S_ISLNK(wheel_matches[0].external_attr >> 16)
            ):
                raise SystemExit(
                    "kernel verification refused: "
                    f"catalogue member in {label} is unsafe"
                )
            return archive.read(wheel_matches[0])
    with tarfile.open(path, "r:*") as archive:
        tar_matches = [
            member
            for member in archive.getmembers()
            if not Path(member.name).is_absolute()
            and len(Path(member.name).parts) == 4
            and Path(member.name).parts[1:]
            == (
                "src",
                "dotmac_kernel",
                "public_exports.json",
            )
        ]
        if not tar_matches:
            return None
        if (
            len(tar_matches) != 1
            or not tar_matches[0].isreg()
            or ".." in Path(tar_matches[0].name).parts
        ):
            raise SystemExit(
                f"kernel verification refused: catalogue member in {label} is unsafe"
            )
        extracted = archive.extractfile(tar_matches[0])
        if extracted is None:
            raise SystemExit(
                "kernel verification refused: "
                f"catalogue member in {label} is unreadable"
            )
        return extracted.read()


def public_exports_evidence(
    *,
    version: str,
    wheel: Path,
    sdist: Path,
    source: Path | None,
    source_payload: bytes | None = None,
    source_present: bool | None = None,
) -> dict[str, object] | None:
    """Bind successor archive members to the exact source-tree bytes.

    A successor source tree carrying the catalogue requires it in both
    artifacts. A missing or partial member is never interpreted as historical;
    historical V1 records remain valid in the record writer.
    """

    wheel_bytes = _catalogue_member(wheel, label=wheel.name)
    sdist_bytes = _catalogue_member(sdist, label=sdist.name)
    if source_present is None:
        source_present = source_payload is not None or (
            source is not None and source.is_file()
        )
    if not source_present:
        if wheel_bytes is not None or sdist_bytes is not None:
            raise SystemExit(
                "kernel verification refused: catalogue exists only in an artifact"
            )
        if version not in HISTORICAL_WITHOUT_PUBLIC_EXPORTS:
            raise SystemExit(
                "kernel verification refused: successor source catalogue is absent"
            )
        return None
    if wheel_bytes is None or sdist_bytes is None:
        raise SystemExit(
            "kernel verification refused: catalogue member is missing from an artifact"
        )
    if source_payload is None and source is not None and source.is_file():
        source_payload = source.read_bytes()
    if source_payload is None:
        if source is None or not source.is_file():
            raise SystemExit(
                "kernel verification refused: source catalogue is unavailable"
            )
        source_payload = source.read_bytes()
    if source_payload is None:
        raise SystemExit("kernel verification refused: source catalogue is unavailable")
    source_bytes = source_payload
    _strict_catalogue(source_bytes, label="source")
    _strict_catalogue(wheel_bytes, label=wheel.name)
    _strict_catalogue(sdist_bytes, label=sdist.name)
    if wheel_bytes != sdist_bytes or wheel_bytes != source_bytes:
        raise SystemExit(
            "kernel verification refused: catalogue bytes differ between "
            "source and artifacts"
        )
    return {
        "name": PUBLIC_EXPORTS_MEMBER,
        "size": len(source_bytes),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "schema": PUBLIC_EXPORTS_SCHEMA,
    }


def source_catalogue_at_commit(source_sha: str) -> tuple[bool, bytes | None]:
    path = "packages/dotmac-kernel/src/dotmac_kernel/public_exports.json"
    subprocess.run(
        ["git", "cat-file", "-e", f"{source_sha}^{{commit}}"],
        check=True,
    )
    listing = subprocess.run(
        ["git", "ls-tree", "--name-only", source_sha, "--", path],
        check=True,
        capture_output=True,
        text=True,
    )
    if listing.stdout.strip() != path:
        return False, None
    completed = subprocess.run(
        ["git", "show", f"{source_sha}:{path}"], check=True, capture_output=True
    )
    return True, completed.stdout


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
    source_has_catalogue, source_catalogue = source_catalogue_at_commit(source_sha)
    public_exports = public_exports_evidence(
        version=version,
        wheel=next(path for path in retained_paths if path.suffix == ".whl"),
        sdist=next(path for path in retained_paths if path.name.endswith(".tar.gz")),
        source=None,
        source_payload=source_catalogue,
        source_present=source_has_catalogue,
    )
    if public_exports is not None:
        decision["public_exports"] = public_exports
    facility_run_id = required("FACILITY_RUN_ID")
    facility_attempt = required("FACILITY_RUN_ATTEMPT")
    if re.fullmatch(r"[1-9][0-9]*", facility_run_id) is None or facility_attempt != "1":
        raise SystemExit(
            "kernel verification refused: facility coordinates are invalid"
        )
    receipt = {
        "schema": (
            "KernelReleaseVerificationReceipt.v2"
            if public_exports is not None
            else "KernelReleaseVerificationReceipt.v1"
        ),
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
