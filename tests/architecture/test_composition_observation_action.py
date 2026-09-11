"""The public composition-observation action is a pinned, local contract."""

from __future__ import annotations

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
    assert '--product "${{ inputs.product }}"' in command
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
