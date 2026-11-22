"""dotmac-deploy-agent: host lease client and the read-only reconcile observation."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotmac_deploy_agent.commands import CommandResult, ReadOnlyCommands
from dotmac_deploy_agent.refusal import Refusal

from tests.unit.deploy_agent_support import (
    FakeCommands,
    Rig,
    make_materials,
    make_rig,
)


def ready(tmp_path: Path) -> Rig:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials()
    return rig


def endpoints(rig: Rig) -> list[str]:
    return [name for name, _ in rig.cp.lease_log]


def test_lease_order_acquire_transfer_run_release(tmp_path: Path) -> None:
    rig = ready(tmp_path)
    result = rig.launcher.run_once()
    assert result.outcome == "completed"
    assert endpoints(rig) == ["lease/acquire", "lease/covers", "lease/release"]
    acquire, covers, release = (payload for _, payload in rig.cp.lease_log)
    assert acquire == {"dispatch_run": "dispatch-1", "ttl_seconds": 900}
    assert covers == {"lease_id": "lease-1", "dispatch_run": "dispatch-1"}
    assert release == {"lease_id": "lease-1", "host_mutated": True}
    assert rig.runner.specs[0].lease_id == "lease-1"
    assert rig.transfer.calls == [("app",)]
    assert rig.cp.observations[0]["details"]["lease_released"] is True  # type: ignore[index]


@pytest.mark.parametrize(
    "code",
    [
        "lease-held-by-other-run",
        "lease-host-mutated",
        "lease-foreign-run",
        "lease-double-release",
    ],
)
def test_agent_stops_on_lease_refusal(tmp_path: Path, code: str) -> None:
    rig = ready(tmp_path)
    rig.cp.overrides["lease/acquire"] = (409, {"error": code})
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_refused")
    # Nothing past the lease happened: no transfer, params, secrets, run or release.
    assert rig.transfer.calls == []
    assert rig.runner.specs == []
    assert not (rig.config.deploy_run_dir / "params.env").exists()
    assert "lease/release" not in endpoints(rig)
    assert rig.cp.observations[0]["details"]["refusal_detail"] == code  # type: ignore[index]


def test_lease_released_clean_when_the_run_never_started(tmp_path: Path) -> None:
    rig = ready(tmp_path)
    rig.transfer.error = Refusal("transfer_digest_mismatch")
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "transfer_digest_mismatch")
    assert rig.cp.lease_log[-1] == (
        "lease/release",
        {"lease_id": "lease-1", "host_mutated": False},
    )
    assert rig.runner.specs == []


def test_a_started_run_releases_as_host_mutated_even_when_it_fails(
    tmp_path: Path,
) -> None:
    rig = ready(tmp_path)
    rig.runner.exit_code = 9
    rig.launcher.run_once()
    assert rig.cp.lease_log[-1][1]["host_mutated"] is True


def test_consume_without_cover_stops_before_the_run(tmp_path: Path) -> None:
    rig = ready(tmp_path)
    rig.cp.handlers["lease/covers"] = lambda _p: {"covers": False}
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_unavailable")
    assert rig.runner.specs == []


def test_release_failure_is_reported_and_never_hides_the_outcome(
    tmp_path: Path,
) -> None:
    rig = ready(tmp_path)
    rig.cp.overrides["lease/release"] = (409, {"error": "lease-expired"})
    result = rig.launcher.run_once()
    assert result.outcome == "completed"
    assert rig.cp.observations[0]["details"]["lease_released"] is False  # type: ignore[index]


@pytest.mark.parametrize("ttl", [30, 3601, 0, True, "900"])
def test_lease_ttl_outside_the_cap_is_refused(tmp_path: Path, ttl: object) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(lease_ttl_seconds=ttl)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_ttl_invalid")
    assert "lease/acquire" not in endpoints(rig)


def test_a_longer_grant_than_planned_is_refused_and_returned(tmp_path: Path) -> None:
    rig = ready(tmp_path)
    rig.cp.handlers["lease/acquire"] = lambda _p: {
        "lease_id": "lease-1",
        "ttl_seconds": 5000,
    }
    result = rig.launcher.run_once()
    assert result.reason == "lease_ttl_invalid"
    assert rig.runner.specs == []


def test_standing_refusal_stops_the_cycle(tmp_path: Path) -> None:
    rig = ready(tmp_path)
    rig.cp.overrides["lease/standing"] = (403, {"error": "credential_revoked"})
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_refused")
    assert rig.runner.specs == []


def test_reconcile_observation_read_only(tmp_path: Path) -> None:
    rig = ready(tmp_path)
    rig.cp.standing = "EXPIRED_UNRELEASED"
    rig.commands.script[("inspect",)] = CommandResult(
        0,
        b"127.0.0.1:5000/org/app@sha256:" + b"a" * 64 + b" sha256:" + b"f" * 64 + b"\n",
    )
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("reconcile_reported", None)
    # No lease, no transfer, no run, no params, no secrets: only the observation.
    assert endpoints(rig) == []
    assert rig.transfer.calls == [] and rig.runner.specs == []
    assert not (rig.config.deploy_run_dir / "params.env").exists()
    assert not (rig.config.run_dir / "app").exists()
    assert rig.bao.paths == []
    # Every command was an inspection verb.
    assert rig.commands.calls
    for argv in rig.commands.calls:
        assert argv[1] in {"inspect", "ps"} or argv[1:3] == ("image", "inspect")
    [observation] = rig.cp.observations
    assert observation["kind"] == "reconcile"
    details = observation["details"]
    assert details["standing"] == "EXPIRED_UNRELEASED"  # type: ignore[index]
    assert details["containers"] == [  # type: ignore[index]
        {
            "name": "erp-app",
            "image_ref": "127.0.0.1:5000/org/app@sha256:" + "a" * 64,
            "image_id": "sha256:" + "f" * 64,
        }
    ]


@pytest.mark.parametrize(
    "argv",
    [
        ("/usr/bin/docker", "rm", "-f", "erp-app"),
        ("/usr/bin/docker", "pull", "x"),
        ("/usr/bin/docker", "image", "rm", "x"),
        ("/usr/bin/docker", "compose", "up"),
        ("/usr/bin/docker",),
        ("/bin/sh", "-c", "true"),
    ],
)
def test_read_only_commands_refuse_everything_that_mutates(
    argv: tuple[str, ...],
) -> None:
    inner = FakeCommands()
    guarded = ReadOnlyCommands(inner)
    with pytest.raises(Refusal) as caught:
        guarded.run(argv, timeout=1.0)
    assert caught.value.code == "reconcile_unavailable"
    assert inner.calls == []


def test_read_only_commands_pass_inspection() -> None:
    inner = FakeCommands()
    guarded = ReadOnlyCommands(inner)
    for argv in (
        ("/usr/bin/docker", "inspect", "x"),
        ("/usr/bin/docker", "image", "inspect", "x"),
        ("/usr/bin/docker", "ps"),
    ):
        guarded.run(argv, timeout=1.0)
    assert len(inner.calls) == 3


def test_reconcile_reports_even_with_no_container_found(tmp_path: Path) -> None:
    rig = ready(tmp_path)
    rig.cp.standing = "EXPIRED_UNRELEASED"
    rig.commands.script[("inspect",)] = CommandResult(1, b"")
    assert rig.launcher.run_once().outcome == "reconcile_reported"
    assert rig.cp.observations[0]["details"]["containers"] == []  # type: ignore[index]
