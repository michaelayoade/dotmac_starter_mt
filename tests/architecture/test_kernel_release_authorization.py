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
        f"| [`dotmac-kernel`](kernel) | universal | `{version}` |\n",
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


def test_release_workflow_rechecks_the_contract_at_all_four_boundaries() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]

    def named_steps(job: str) -> dict[str, tuple[int, str]]:
        return {
            step["name"]: (index, str(step.get("run", "")))
            for index, step in enumerate(jobs[job]["steps"])
            if "name" in step
        }

    build = named_steps("build")
    publish = named_steps("publish")
    verify = named_steps("verify")

    assert (
        "--phase build"
        in build["Bind build to the active kernel release authorization"][1]
    )
    assert (
        "--phase publish"
        in publish["Re-bind publish to the active kernel release authorization"][1]
    )
    assert all("--phase verify" not in run for _, run in publish.values())

    readback_freshness = verify[
        "Re-assert the run SHA is still protected main before read-back"
    ]
    readback_binding = verify[
        "Bind registry verification to the active kernel release authorization"
    ]
    registry_wait = verify["Wait for the release on the Forgejo index"]
    registry_install = verify["Install from the Forgejo registry and verify"]
    tag_freshness = verify[
        "Re-assert the run SHA is still protected main before tagging"
    ]
    tag = verify["Tag the verified release"]

    assert "assert_current_main.sh" in readback_freshness[1]
    assert "--phase verify" in readback_binding[1]
    assert "assert_current_main.sh" in tag_freshness[1]
    assert "--phase tag" in tag[1]
    assert "git tag -a" in tag[1]
    assert readback_binding[0] == readback_freshness[0] + 1
    assert registry_wait[0] == readback_binding[0] + 1
    assert registry_wait[0] < registry_install[0] < tag_freshness[0]
    assert tag[0] == tag_freshness[0] + 1


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
