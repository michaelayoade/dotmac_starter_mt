"""The pre-import composition-source check is an immutable, closed action."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
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


def _module(name: str = "verify") -> ModuleType:
    if str(ACTION) not in sys.path:
        sys.path.insert(0, str(ACTION))
    spec = importlib.util.spec_from_file_location(
        f"trusted_source_action_{name}", ACTION / f"{name}.py"
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
    assert command == 'python -I "$GITHUB_ACTION_PATH/run_gate.py"'
    assert "${{" not in command

    runner = _module("run_gate")
    assert runner.EXPECTED_STATUS == {
        "academy": "satisfied",
        "erp": "deferred_runtime_debt",
        "sub": "satisfied",
    }
    assert runner.EXPECTED_REVISIONS == {
        "academy": "ca1f9058a6483fe52207556fa2d17c56b1e237c7",
        "erp": "dca695a7d59179fe65577ba4e72a709ff0b32cde",
        "sub": "272a897b778899b110c5790fcaf43bbb54efe27c",
    }


def test_byte_pins_refuse_a_changed_gate_or_binding(tmp_path: Path) -> None:
    runner = _module("run_gate")
    path = tmp_path / "candidate"
    path.write_bytes(b"trusted")
    expected = hashlib.sha256(b"trusted").hexdigest()

    runner._require_sha256(path, expected, label="candidate")
    path.write_bytes(b"changed")
    with pytest.raises(runner.TrustedRunnerError, match="not the pinned candidate"):
        runner._require_sha256(path, expected, label="candidate")


def test_runner_refuses_any_binding_other_than_the_three_pinned_revisions(
    tmp_path: Path,
) -> None:
    runner = _module("run_gate")
    path = tmp_path / "bindings.json"
    document = {
        "schema": "kernel-composition-compatibility-bindings.v3",
        "bindings": {
            product: {"revision": revision}
            for product, revision in runner.EXPECTED_REVISIONS.items()
        },
    }
    path.write_text(json.dumps(document))
    runner._require_exact_bindings(path)

    document["bindings"]["erp"]["revision"] = "a" * 40
    path.write_text(json.dumps(document))
    with pytest.raises(runner.TrustedRunnerError, match="bindings changed"):
        runner._require_exact_bindings(path)


def test_explicit_loader_never_executes_candidate_package_initializers(
    tmp_path: Path,
) -> None:
    runner = _module("run_gate")
    contract = tmp_path / "tools" / "composition_contract"
    contract.mkdir(parents=True)
    marker = tmp_path / "initializer-ran"
    (tmp_path / "tools" / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('tools')\n"
    )
    (contract / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('contract')\n"
    )
    for _, filename in runner._HELPER_MODULES:
        (contract / filename).write_text("VALUE = 1\n")
    (contract / "compatibility_gate.py").write_text("VALUE = 2\n")

    loaded = runner._load_verified_gate(tmp_path)

    assert loaded.VALUE == 2
    assert not marker.exists()


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
