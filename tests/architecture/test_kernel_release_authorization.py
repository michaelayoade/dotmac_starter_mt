"""A bare kernel version exists only inside one typed release transition."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "kernel_release_authorization.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-kernel.yml"
KERNEL_CHANGELOG = REPO_ROOT / "packages" / "dotmac-kernel" / "CHANGELOG.md"
KERNEL_PYPROJECT = REPO_ROOT / "packages" / "dotmac-kernel" / "pyproject.toml"
KERNEL_CATALOG_CELL = "[`dotmac-kernel`](../packages/dotmac-kernel/README.md)"


def _load_contract():
    spec = importlib.util.spec_from_file_location("_kernel_release_contract", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    # The executable is fixed and every argument comes from this test module's
    # literals or from a SHA emitted by this fixture repository.
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _version_files(repo: Path, version: str) -> None:
    _write(
        repo / "packages/dotmac-kernel/pyproject.toml",
        '[tool.poetry]\nname = "dotmac-kernel"\n' f'version = "{version}"\n',
    )
    _write(
        repo / "packages/dotmac-kernel/src/dotmac_kernel/__init__.py",
        '"""fixture public surface"""\n\n'
        f'__version__ = "{version}"\n\nPUBLIC = "unchanged"\n',
    )
    _write(
        repo / "poetry.lock",
        '[[package]]\nname = "dotmac-kernel"\n' f'version = "{version}"\n',
    )
    _write(
        repo / "docs/inventories/declared-publication-baseline.json",
        json.dumps(
            {
                "unpublished": {
                    "dotmac-kernel": {
                        "declared": version,
                        "reason": "fixture",
                        "state": "declared-unpublished",
                    }
                }
            }
        )
        + "\n",
    )
    _write(
        repo / "docs/MODULE_CATALOG.md",
        f"| {KERNEL_CATALOG_CELL} | universal | `{version}` |\n\n"
        f"### {KERNEL_CATALOG_CELL}\n\n"
        f"The {KERNEL_CATALOG_CELL} detail section is not a package row.\n",
    )


def _empty_authorization(contract) -> str:
    return (
        json.dumps(
            {
                "$schema": contract.SCHEMA,
                "$comment": ["fixture"],
                "active": None,
            },
            indent=2,
        )
        + "\n"
    )


@pytest.fixture
def release_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    contract = _load_contract()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.invalid")
    _version_files(repo, "0.1.0a1")
    _write(repo / "packages/dotmac-kernel/CHANGELOG.md", "## 0.1.0a2 — fixture\n")
    _write(repo / "packages/dotmac-kernel/src/dotmac_kernel/feature.py", "VALUE = 1\n")
    _write(repo / "package.json", '{"scripts":{"css:build":"true"}}\n')
    _write(repo / "package-lock.json", '{"lockfileVersion":3}\n')
    _write(
        repo / ".github/kernel-release-authorization.json",
        _empty_authorization(contract),
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "kernel a1")
    _git(repo, "tag", "-a", "dotmac-kernel-v0.1.0a1", "-m", "kernel a1")

    _version_files(repo, "0.1.0a1+dev")
    _write(repo / "packages/dotmac-kernel/src/dotmac_kernel/feature.py", "VALUE = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "post-release development")
    base = _git(repo, "rev-parse", "HEAD")

    monkeypatch.setattr(contract, "REPO_ROOT", repo)
    monkeypatch.setattr(
        contract,
        "AUTHORIZATION_PATH",
        repo / ".github/kernel-release-authorization.json",
    )
    return contract, repo, base


def _authorize(contract, repo: Path, base: str):
    record = contract.prepare(base, "0.1.0a2")
    contract.AUTHORIZATION_PATH.write_text(contract.render_document(record))
    _git(repo, "add", str(contract.AUTHORIZATION_PATH.relative_to(repo)))
    _git(repo, "commit", "-m", "authorize kernel a2")
    return record, _git(repo, "rev-parse", "HEAD")


def _allocate(repo: Path) -> str:
    _version_files(repo, "0.1.0a2")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "allocate kernel a2")
    return _git(repo, "rev-parse", "HEAD")


def test_catalog_normalization_selects_the_real_row_not_its_detail_heading() -> None:
    contract = _load_contract()
    catalog = (REPO_ROOT / "docs/MODULE_CATALOG.md").read_bytes()

    normalized = contract._normalize_catalog(catalog).decode("utf-8")

    assert normalized.count("<AUTHORIZED_KERNEL_VERSION>") == 1
    assert f"### {KERNEL_CATALOG_CELL}" in normalized
    assert f"| {KERNEL_CATALOG_CELL} |" in normalized


def test_catalog_heading_and_paragraph_mentions_are_not_rows() -> None:
    contract = _load_contract()
    mentions = (
        f"### {KERNEL_CATALOG_CELL}\n\n"
        f"The {KERNEL_CATALOG_CELL} package is described here.\n"
    ).encode()

    with pytest.raises(contract.KernelReleaseAuthorizationError) as refusal:
        contract._normalize_catalog(mentions)
    assert "expected one kernel catalogue row, found 0" in str(refusal.value)


def test_catalog_duplicate_structural_rows_are_refused() -> None:
    contract = _load_contract()
    row = f"| {KERNEL_CATALOG_CELL} | universal | `0.1.0a1+dev` |\n"
    duplicate = (row + row + f"\n### {KERNEL_CATALOG_CELL}\n").encode()

    with pytest.raises(contract.KernelReleaseAuthorizationError) as refusal:
        contract._normalize_catalog(duplicate)
    assert "expected one kernel catalogue row, found 2" in str(refusal.value)


def test_version_surface_validation_uses_the_same_structural_row(release_repo) -> None:
    contract, repo, base = release_repo
    _authorize(contract, repo, base)
    allocation = _allocate(repo)

    contract._assert_version_surfaces(allocation, "0.1.0a2")

    catalog = repo / "docs/MODULE_CATALOG.md"
    row = f"| {KERNEL_CATALOG_CELL} | universal | `0.1.0a2` |\n"
    catalog.write_text(catalog.read_text(encoding="utf-8") + row, encoding="utf-8")
    _git(repo, "add", str(catalog.relative_to(repo)))
    _git(repo, "commit", "-m", "plant duplicate kernel catalogue row")

    with pytest.raises(contract.KernelReleaseAuthorizationError) as refusal:
        contract._assert_version_surfaces("HEAD", "0.1.0a2")
    assert "expected one kernel catalogue row, found 2" in str(refusal.value)


def test_authorization_and_its_immediate_allocation_child_are_accepted(
    release_repo,
) -> None:
    contract, repo, base = release_repo
    record, authorization_commit = _authorize(contract, repo, base)

    assert contract.validate_current_state() == "authorized"
    allocation_commit = _allocate(repo)
    assert contract.validate_current_state(expected_version="0.1.0a2") == "allocated"
    assert _git(repo, "rev-parse", f"{allocation_commit}^") == authorization_commit
    assert contract.normalized_release_input_digest(allocation_commit) == (
        record.normalized_release_input_digest
    )


def test_bare_version_without_authorization_is_refused(release_repo) -> None:
    contract, repo, _ = release_repo
    _version_files(repo, "0.1.0a2")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "unowned version bump")

    with pytest.raises(contract.KernelReleaseAuthorizationError) as refusal:
        contract.validate_current_state()
    assert "no active authorization" in str(refusal.value)


def test_authorization_commit_with_a_second_path_is_refused(release_repo) -> None:
    contract, repo, base = release_repo
    record = contract.prepare(base, "0.1.0a2")
    contract.AUTHORIZATION_PATH.write_text(contract.render_document(record))
    _write(repo / "unrelated.txt", "not authorization\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "mixed authorization")

    with pytest.raises(contract.KernelReleaseAuthorizationError) as refusal:
        contract.validate_current_state()
    assert "must change only" in str(refusal.value)
    assert "unrelated.txt" in str(refusal.value)


def test_allocation_cannot_smuggle_a_release_input_change(release_repo) -> None:
    contract, repo, base = release_repo
    _authorize(contract, repo, base)
    _version_files(repo, "0.1.0a2")
    _write(
        repo / "packages/dotmac-kernel/CHANGELOG.md",
        "## 0.1.0a2 — fixture\n\n- smuggled after authorization\n",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "allocation plus code")

    with pytest.raises(contract.KernelReleaseAuthorizationError) as refusal:
        contract.validate_current_state()
    assert "CHANGELOG.md" in str(refusal.value)
    assert "unauthorized paths" in str(refusal.value)


def test_allocation_cannot_hide_an_unrelated_lock_change(release_repo) -> None:
    contract, repo, base = release_repo
    _authorize(contract, repo, base)
    _version_files(repo, "0.1.0a2")
    lock = repo / "poetry.lock"
    lock.write_text(lock.read_text() + 'metadata-version = "changed"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "allocation plus lock drift")

    with pytest.raises(contract.KernelReleaseAuthorizationError) as refusal:
        contract.validate_current_state()
    assert "poetry.lock" in str(refusal.value)
    assert "beyond its one version value" in str(refusal.value)


def test_allocation_cannot_change_an_unlisted_path(release_repo) -> None:
    contract, repo, base = release_repo
    _authorize(contract, repo, base)
    _version_files(repo, "0.1.0a2")
    _write(repo / "packages/dotmac-kernel/src/dotmac_kernel/feature.py", "VALUE = 3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "allocation plus code")

    with pytest.raises(contract.KernelReleaseAuthorizationError) as refusal:
        contract.validate_current_state()
    assert "unauthorized paths" in str(refusal.value)
    assert "feature.py" in str(refusal.value)


def test_allocation_must_be_the_immediate_child(release_repo) -> None:
    contract, repo, base = release_repo
    _authorize(contract, repo, base)
    _write(repo / "docs/spacing.md", "intervening commit\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "intervening commit")
    _allocate(repo)

    with pytest.raises(contract.KernelReleaseAuthorizationError) as refusal:
        contract.validate_current_state()
    assert "authorization commit parent" in str(refusal.value)


def test_post_tag_consumption_requires_the_bound_bytes(release_repo) -> None:
    contract, repo, base = release_repo
    _authorize(contract, repo, base)
    allocation = _allocate(repo)
    _git(repo, "tag", "-a", "dotmac-kernel-v0.1.0a2", "-m", "kernel a2")

    consumed = contract.consume_for_release(
        version="0.1.0a2", tag="dotmac-kernel-v0.1.0a2", commit=allocation
    )
    assert json.loads(consumed)["active"] is None

    with pytest.raises(contract.KernelReleaseAuthorizationError) as refusal:
        contract.consume_for_release(
            version="0.1.0a2", tag="dotmac-kernel-v0.1.0a2", commit="0" * 40
        )
    assert "tag peels" in str(refusal.value)


def test_historical_release_source_remains_provable_after_current_state_moves(
    release_repo,
) -> None:
    contract, repo, base = release_repo
    _authorize(contract, repo, base)
    allocation = _allocate(repo)
    binding = contract.validate_release_source(source_sha=allocation, version="0.1.0a2")
    assert binding["state"] == "allocated"
    assert binding["source_sha"] == allocation

    _git(repo, "tag", "-a", "dotmac-kernel-v0.1.0a2", "-m", "kernel a2")
    contract.AUTHORIZATION_PATH.write_text(contract.render_document(None))
    _git(repo, "add", str(contract.AUTHORIZATION_PATH.relative_to(repo)))
    _git(repo, "commit", "-m", "consume release authorization")
    assert (
        contract.validate_release_source(source_sha=allocation, version="0.1.0a2")[
            "state"
        ]
        == "allocated"
    )

    with pytest.raises(
        contract.KernelReleaseAuthorizationError, match="different version"
    ):
        contract.validate_release_source(source_sha=allocation, version="0.1.0a3")
    with pytest.raises(
        contract.KernelReleaseAuthorizationError, match="no active authorization"
    ):
        contract.validate_release_source(source_sha=base, version="0.1.0a2")


def test_record_shape_is_closed_and_target_is_numeric_successor(release_repo) -> None:
    contract, _, base = release_repo
    record = contract.prepare(base, "0.1.0a2")
    malformed = {**record.as_json(), "approved_by": "free text is not authority"}
    with pytest.raises(contract.KernelReleaseAuthorizationError) as refusal:
        contract.ReleaseAuthorization.parse(malformed)
    assert "fields differ" in str(refusal.value)

    with pytest.raises(contract.KernelReleaseAuthorizationError) as wrong_target:
        contract.prepare(base, "0.1.0a3")
    assert "next numeric alpha 0.1.0a2" in str(wrong_target.value)


def test_release_workflow_owns_only_build_and_publish_boundaries() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]

    def named_steps(job: str) -> dict[str, tuple[int, str]]:
        return {
            step["name"]: (index, str(step.get("run", "")))
            for index, step in enumerate(jobs[job]["steps"])
            if "name" in step
        }

    build = named_steps("build")
    publish = named_steps("publish")

    assert "--phase build" in build["Bind build to the allocated release lifecycle"][1]
    assert (
        "--phase publish"
        in publish["Bind publish to the allocated release lifecycle"][1]
    )
    assert set(jobs) == {"build", "publish"}
    assert jobs["publish"]["needs"] == "build"
    assert jobs["publish"]["permissions"] == {"contents": "read"}
    source = WORKFLOW.read_text(encoding="utf-8")
    for forbidden in (
        "--phase verify",
        "--phase tag",
        "git tag",
        "git push",
        "open_release_record_pr",
        "contents: write",
    ):
        assert forbidden not in source


def test_release_cli_refuses_unknown_boundary_name() -> None:
    contract = _load_contract()
    with pytest.raises(SystemExit) as refusal:
        contract.main(
            [
                "verify-release",
                "--phase",
                "verification-shaped-typo",
                "--version",
                "0.1.0a100",
            ]
        )
    assert refusal.value.code == 2


def test_a100_does_not_claim_credential_lifecycle_bytes_already_in_a99() -> None:
    changelog = KERNEL_CHANGELOG.read_text(encoding="utf-8")
    a100 = changelog.split("## 0.1.0a100", 1)[1].split("## 0.1.0a99", 1)[0]
    a99 = changelog.split("## 0.1.0a99", 1)[1].split("## 0.1.0a98", 1)[0]

    assert "dotmac_kernel.credential_lifecycle" not in a100
    assert "dotmac_kernel.credential_lifecycle" in a99
    assert "already in a99" in a99


def test_current_tree_is_in_its_declared_release_lifecycle_state() -> None:
    contract = _load_contract()
    package = tomllib.loads(KERNEL_PYPROJECT.read_text(encoding="utf-8"))
    version = package["tool"]["poetry"]["version"]
    active = contract.load_authorization()
    expected = {
        (False, True): "development",
        (True, True): "authorized",
        (True, False): "allocated",
        (False, False): "released",
    }[(active is not None, "+" in version)]

    assert contract.validate_current_state(expected_version=version) == expected
