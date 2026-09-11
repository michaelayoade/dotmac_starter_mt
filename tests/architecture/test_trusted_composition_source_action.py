"""The pre-import composition-source check is an immutable, closed action."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/verify-composition-contract-sources"
TRUSTED_REVISION = "8b4b6d4b42e650c47fe4c04a679a5ccb51c4b2cd"
TRUSTED_PATHS = (
    "tools/composition_contract/composition_schema.py",
    "tools/composition_contract/observations.py",
    "tools/composition_contract/specs.py",
)


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "trusted_source_action", ACTION / "verify.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repository), *arguments],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_action_owns_a_closed_nonempty_trust_anchor() -> None:
    module = _module()

    assert module.TRUSTED_CONTRACT_REVISION == TRUSTED_REVISION
    assert module.TRUSTED_SEMANTIC_PATHS == TRUSTED_PATHS
    with pytest.raises(ValueError, match="protected helper set is closed"):
        module.find_source_drift(ROOT, paths=())

    action = yaml.safe_load((ACTION / "action.yml").read_text())
    assert "inputs" not in action
    step = action["runs"]["steps"][0]
    command = step["run"]
    assert "--revision" not in command
    assert "--repository" in command
    assert step["env"] == {"VERIFIED_REPOSITORY": "${{ github.workspace }}"}
    assert "${{" not in command


def test_action_detects_current_drift_against_one_git_coordinate(
    tmp_path: Path,
) -> None:
    module = _module()
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Trust Test")
    _git(repository, "config", "user.email", "trust@example.invalid")
    _git(repository, "config", "commit.gpgsign", "false")
    for relative in TRUSTED_PATHS:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "trusted fixture")
    revision = _git(repository, "rev-parse", "HEAD")

    assert module.find_source_drift(repository, revision=revision) == ()
    changed = repository / TRUSTED_PATHS[0]
    changed.write_text("# changed\n")

    assert module.find_source_drift(repository, revision=revision) == (
        TRUSTED_PATHS[0],
    )
