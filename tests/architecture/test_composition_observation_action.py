"""The public composition-observation action is a pinned, local contract."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "verify-composition-observations" / "action.yml"


def _action() -> dict:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def _run_step() -> dict:
    steps = _action()["runs"]["steps"]
    assert len(steps) == 1
    return steps[0]


def test_composition_observation_action_is_a_local_composite() -> None:
    document = _action()

    assert document["runs"]["using"] == "composite"
    assert set(document["inputs"]) == {"product"}
    assert document["inputs"]["product"]["required"] is True


def test_action_resolves_cli_from_action_path_and_workspace() -> None:
    step = _run_step()
    command = step["run"]

    assert step["shell"] == "bash"
    assert step["working-directory"] == "${{ github.workspace }}"
    assert (
        "$GITHUB_ACTION_PATH/../../../tools/composition_contract/"
        "check_product_observations.py"
    ) in command
    assert step["env"] == {"COMPOSITION_PRODUCT": "${{ inputs.product }}"}
    assert "${{ inputs.product }}" not in command
    assert '--product "$COMPOSITION_PRODUCT"' in command
    assert '--workspace "$GITHUB_WORKSPACE"' in command
    assert "record-path" not in command


def test_action_passes_identity_and_has_no_external_or_mutable_uses() -> None:
    document = _action()
    command = _run_step()["run"]

    assert '--action-repository "$GITHUB_ACTION_REPOSITORY"' in command
    assert '--action-ref "$GITHUB_ACTION_REF"' in command
    assert '--caller-repository "$GITHUB_REPOSITORY"' in command
    assert "token" not in document
    assert "secret" not in document
    assert "uses" not in document


def test_untrusted_product_input_is_one_shell_argument(tmp_path: Path) -> None:
    step = _run_step()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    captured = tmp_path / "arguments"
    python = fake_bin / "python3"
    python.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$CAPTURED_ARGUMENTS"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    marker = tmp_path / "injected"
    malicious = f'academy"; touch "{marker}'
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CAPTURED_ARGUMENTS": str(captured),
        "COMPOSITION_PRODUCT": malicious,
        "GITHUB_ACTION_PATH": str(ACTION.parent),
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_ACTION_REPOSITORY": "michaelayoade/dotmac_starter_mt",
        "GITHUB_ACTION_REF": "a" * 40,
        "GITHUB_REPOSITORY": "michaelayoade/dotmac_academy_app",
    }

    result = subprocess.run(  # noqa: S603 - checked-in Bash action command
        ["bash", "-c", step["run"]],  # noqa: S607 - test resolves PATH fixture
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert malicious in captured.read_text(encoding="utf-8").splitlines()
    assert not marker.exists()
