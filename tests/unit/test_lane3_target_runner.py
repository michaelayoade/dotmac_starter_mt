"""E7 / T1: the target half's local runner and its placement self-check."""

from __future__ import annotations

import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lane3_target_runner import (  # noqa: E402
    PlacementRefused,
    local_runner,
    placement_self_check,
    read_machine_id,
)

MACHINE = "0123456789abcdef0123456789abcdef"
OTHER_MACHINE = "fedcba9876543210fedcba9876543210"


def test_the_local_runner_runs_an_argv_list_in_place() -> None:
    result = local_runner([sys.executable, "-c", "print('hello')"], timeout=60)
    assert result.ok
    assert result.stdout.strip() == "hello"


def test_a_failing_command_reports_its_exit_code() -> None:
    result = local_runner([sys.executable, "-c", "raise SystemExit(3)"], timeout=60)
    assert result.exit_code == 3
    assert not result.ok


def test_a_timeout_is_124_not_an_exception() -> None:
    result = local_runner(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1
    )
    assert result.exit_code == 124
    assert "timed out" in result.stderr


def test_a_missing_binary_is_127_not_an_exception() -> None:
    result = local_runner(["/nonexistent/lane3-no-such-binary"], timeout=5)
    assert result.exit_code == 127


def test_the_machine_id_is_read_and_validated(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "machine-id"
    path.write_text(MACHINE + "\n", encoding="ascii")
    assert read_machine_id(path) == MACHINE


@pytest.mark.parametrize("content", ["", "short", "G" * 32, MACHINE.upper()])
def test_a_malformed_machine_id_refuses(tmp_path: pathlib.Path, content: str) -> None:
    path = tmp_path / "machine-id"
    path.write_text(content, encoding="ascii")
    with pytest.raises(PlacementRefused):
        read_machine_id(path)


def test_a_missing_machine_id_refuses(tmp_path: pathlib.Path) -> None:
    with pytest.raises(PlacementRefused):
        read_machine_id(tmp_path / "absent")


def _check(**overrides: str) -> None:
    values = {
        "plan_host_id": "host-a",
        "plan_host_incarnation": "incarnation-1",
        "expected_host_id": "host-a",
        "expected_host_incarnation": "incarnation-1",
        "expected_machine_id": MACHINE,
        "machine_id": MACHINE,
    }
    values.update(overrides)
    placement_self_check(**values)


def test_agreement_passes() -> None:
    _check()


@pytest.mark.parametrize(
    "overrides",
    [
        {"plan_host_id": "host-b"},
        {"plan_host_incarnation": "incarnation-2"},
        {"machine_id": OTHER_MACHINE},
    ],
)
def test_each_disagreement_refuses(overrides: dict[str, str]) -> None:
    with pytest.raises(PlacementRefused):
        _check(**overrides)


@pytest.mark.parametrize(
    "field",
    [
        "plan_host_id",
        "plan_host_incarnation",
        "expected_host_id",
        "expected_host_incarnation",
        "expected_machine_id",
        "machine_id",
    ],
)
def test_an_empty_value_refuses_even_when_both_sides_are_empty(field: str) -> None:
    with pytest.raises(PlacementRefused):
        _check(**{field: ""})


def test_a_refusal_names_the_field_and_never_a_value() -> None:
    with pytest.raises(PlacementRefused) as refusal:
        _check(machine_id=OTHER_MACHINE)
    assert OTHER_MACHINE not in str(refusal.value)
    assert MACHINE not in str(refusal.value)
