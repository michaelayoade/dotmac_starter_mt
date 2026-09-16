"""The kernel publisher must publish only the bytes produced by its build."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "verify_kernel_artifact_hashes.py"
WORKFLOW = REPO / ".github" / "workflows" / "release-kernel.yml"
VERSION = "1.2.3a1"
WHEEL = f"dotmac_kernel-{VERSION}-py3-none-any.whl"
SDIST = f"dotmac_kernel-{VERSION}.tar.gz"


def _run(dist: Path, wheel: str, sdist: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - executable and arguments are test constants
        [
            sys.executable,
            str(SCRIPT),
            "verify",
            "--dist",
            str(dist),
            "--version",
            VERSION,
            "--wheel-sha256",
            wheel,
            "--sdist-sha256",
            sdist,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _fixture(tmp_path: Path) -> tuple[Path, str, str]:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / WHEEL).write_bytes(b"wheel bytes")
    (dist / SDIST).write_bytes(b"sdist bytes")

    def digest(name: str) -> str:
        return hashlib.sha256((dist / name).read_bytes()).hexdigest()

    return dist, digest(WHEEL), digest(SDIST)


def test_guard_accepts_exact_build_bytes(tmp_path: Path) -> None:
    dist, wheel, sdist = _fixture(tmp_path)
    result = _run(dist, wheel, sdist)
    assert result.returncode == 0, result.stderr


def test_record_emits_hashes_for_exact_fixture(tmp_path: Path) -> None:
    dist, wheel, sdist = _fixture(tmp_path)
    output = tmp_path / "github-output"
    result = subprocess.run(  # noqa: S603 - executable and arguments are test constants
        [
            sys.executable,
            str(SCRIPT),
            "record",
            "--dist",
            str(dist),
            "--version",
            VERSION,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text() == f"wheel_sha256={wheel}\nsdist_sha256={sdist}\n"
    (dist / WHEEL).write_bytes(b"changed wheel bytes")
    changed_output = tmp_path / "changed-github-output"
    changed = subprocess.run(  # noqa: S603 - executable and arguments are test constants
        [
            sys.executable,
            str(SCRIPT),
            "record",
            "--dist",
            str(dist),
            "--version",
            VERSION,
            "--output",
            str(changed_output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert changed.returncode == 0
    changed_wheel = hashlib.sha256(b"changed wheel bytes").hexdigest()
    assert changed_output.read_text() == (
        f"wheel_sha256={changed_wheel}\nsdist_sha256={sdist}\n"
    )
    (dist / SDIST).unlink()
    assert (
        subprocess.run(  # noqa: S603 - executable and arguments are test constants
            [
                sys.executable,
                str(SCRIPT),
                "record",
                "--dist",
                str(dist),
                "--version",
                VERSION,
                "--output",
                str(output),
            ],
            capture_output=True,
            check=False,
        ).returncode
        != 0
    )


def test_guard_rejects_mismatch_missing_and_extra_files(tmp_path: Path) -> None:
    dist, wheel, sdist = _fixture(tmp_path)
    assert _run(dist, "0" * 64, sdist).returncode != 0

    (dist / SDIST).unlink()
    assert _run(dist, wheel, sdist).returncode != 0

    (dist / SDIST).write_bytes(b"sdist bytes")
    (dist / "unexpected.txt").write_bytes(b"extra")
    assert _run(dist, wheel, sdist).returncode != 0

    (dist / "unexpected.txt").unlink()
    assert _run(dist, "", sdist).returncode != 0
    assert _run(dist, "x" * 63, sdist).returncode != 0

    (dist / "unexpected-dir").mkdir()
    assert _run(dist, wheel, sdist).returncode != 0

    (dist / "unexpected-dir").rmdir()
    (dist / "link").symlink_to(dist / WHEEL)
    assert _run(dist, wheel, sdist).returncode != 0


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _assert_wiring(document: dict) -> None:
    build = document["jobs"]["build"]
    publish = document["jobs"]["publish"]
    assert build["outputs"] == {
        "wheel_sha256": "${{ steps.artifact_hashes.outputs.wheel_sha256 }}",
        "sdist_sha256": "${{ steps.artifact_hashes.outputs.sdist_sha256 }}",
    }
    build_steps = build["steps"]
    hash_step = next(
        step for step in build_steps if step.get("id") == "artifact_hashes"
    )
    hash_run = hash_step["run"]
    assert "verify_kernel_artifact_hashes.py record" in hash_run
    assert "--dist packages/dotmac-kernel/dist" in hash_run
    assert '"$GITHUB_OUTPUT"' in hash_run
    assert "working-directory" not in hash_step
    smoke_index = next(
        i for i, step in enumerate(build_steps) if "Smoke" in step.get("name", "")
    )
    assert build_steps.index(hash_step) > smoke_index
    publish_steps = publish["steps"]
    download_index = next(
        i
        for i, step in enumerate(publish_steps)
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    setup_index = next(
        i
        for i, step in enumerate(publish_steps)
        if step.get("uses", "").startswith("actions/setup-python@")
    )
    guard_index = next(
        i
        for i, step in enumerate(publish_steps)
        if "verify_kernel_artifact_hashes.py" in step.get("run", "")
    )
    token_index = next(
        i
        for i, step in enumerate(publish_steps)
        if "FORGEJO_PUBLISH_TOKEN" in str(step)
    )
    upload_index = next(
        i
        for i, step in enumerate(publish_steps)
        if "twine upload" in step.get("run", "")
    )
    freshness_index = next(
        i
        for i, step in enumerate(publish_steps)
        if "Re-assert exact protected main immediately before upload"
        in step.get("name", "")
    )
    assert download_index < setup_index < guard_index < freshness_index < upload_index
    assert build_steps.index(hash_step) < next(
        i
        for i, step in enumerate(build_steps)
        if "upload-artifact" in step.get("uses", "")
    )
    assert freshness_index + 1 == upload_index
    assert guard_index < token_index
    guard = publish_steps[guard_index]
    assert guard["env"] == {
        "EXPECTED_WHEEL_SHA256": "${{ needs.build.outputs.wheel_sha256 }}",
        "EXPECTED_SDIST_SHA256": "${{ needs.build.outputs.sdist_sha256 }}",
    }


def test_workflow_emits_and_checks_hashes_before_token_use() -> None:
    _assert_wiring(_workflow())


@pytest.mark.parametrize(
    "mutation",
    [
        "delete",
        "move-to-build",
        "after-token",
        "before-setup",
        "swap-freshness",
    ],
)
def test_workflow_wiring_is_sensitive_to_guard_mutations(mutation: str) -> None:
    document = deepcopy(_workflow())
    publish_steps = document["jobs"]["publish"]["steps"]
    guard_index = next(
        i
        for i, step in enumerate(publish_steps)
        if "verify_kernel_artifact_hashes.py" in step.get("run", "")
    )
    guard = publish_steps.pop(guard_index)
    if mutation == "delete":
        pass
    elif mutation == "move-to-build":
        document["jobs"]["build"]["steps"].append(guard)
    elif mutation == "before-setup":
        setup_index = next(
            i
            for i, step in enumerate(publish_steps)
            if step.get("uses", "").startswith("actions/setup-python@")
        )
        publish_steps.insert(setup_index, guard)
    elif mutation == "swap-freshness":
        freshness_index = next(
            i
            for i, step in enumerate(publish_steps)
            if "Re-assert exact protected main immediately before upload"
            in step.get("name", "")
        )
        publish_steps.insert(freshness_index + 1, guard)
    else:
        token_index = next(
            i
            for i, step in enumerate(publish_steps)
            if "FORGEJO_PUBLISH_TOKEN" in str(step)
        )
        publish_steps.insert(token_index + 1, guard)
    with pytest.raises((AssertionError, StopIteration)):
        _assert_wiring(document)


@pytest.mark.parametrize(
    "mutation", ["record-mode", "record-script", "output-path", "wrong-cwd", "binding"]
)
def test_workflow_wiring_is_sensitive_to_producer_mutations(mutation: str) -> None:
    document = deepcopy(_workflow())
    build_steps = document["jobs"]["build"]["steps"]
    hash_step = next(
        step for step in build_steps if step.get("id") == "artifact_hashes"
    )
    if mutation == "record-mode":
        hash_step["run"] = hash_step["run"].replace(" record ", " verify ")
    elif mutation == "record-script":
        hash_step["run"] = hash_step["run"].replace(
            "verify_kernel_artifact_hashes.py", "other.py"
        )
    elif mutation == "output-path":
        hash_step["run"] = hash_step["run"].replace('"$GITHUB_OUTPUT"', '"other"')
    elif mutation == "wrong-cwd":
        hash_step["working-directory"] = "packages/dotmac-kernel"
    else:
        guard = next(
            step
            for step in document["jobs"]["publish"]["steps"]
            if "verify_kernel_artifact_hashes.py" in step.get("run", "")
        )
        guard["env"]["EXPECTED_WHEEL_SHA256"] = "${{ needs.build.outputs.other }}"
    with pytest.raises(AssertionError):
        _assert_wiring(document)
