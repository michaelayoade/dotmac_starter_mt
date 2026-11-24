"""dotmac-deploy-agent: host lease client and the read-only reconcile observation."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotmac_deploy_agent.commands import CommandResult, ReadOnlyCommands
from dotmac_deploy_agent.observation import mark_inflight
from dotmac_deploy_agent.refusal import Refusal

from tests.unit.deploy_agent_support import (
    FakeCommands,
    Rig,
    make_materials,
    make_rig,
)

REPO_DIGEST = "127.0.0.1:5000/org/app@sha256:" + "a" * 64


def ready(tmp_path: Path) -> Rig:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials()
    return rig


MARKER = (409, {"error": "reconcile_marker_required"})


def reconciles(rig: Rig) -> list[dict[str, object]]:
    return [o for o in rig.cp.observations if o["kind"] == "reconcile"]


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


@pytest.mark.parametrize("ttl", [3601, 0, -5, True, "900"])
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
    rig.cp.expired_run = "dispatch-0"
    rig.commands.script[("image", "inspect")] = CommandResult(
        0, b'["' + REPO_DIGEST.encode() + b'"]'
    )
    rig.commands.script[("inspect",)] = CommandResult(
        0,
        b"127.0.0.1:5000/org/app@sha256:" + b"a" * 64 + b" sha256:" + b"f" * 64 + b"\n",
    )
    rig.cp.overrides["lease/acquire"] = MARKER  # CP withholds the marker
    result = rig.launcher.run_once()
    # The facts are posted, then acquire is attempted and CP's refusal stops the cycle.
    assert (result.outcome, result.reason) == ("refused", "lease_refused")
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
    [observation] = reconciles(rig)
    assert observation["kind"] == "reconcile" and observation["outcome"] == "reconciled"
    assert (
        observation["dispatch_id"] == "dispatch-0"
    )  # the EXPIRED dispatch, not the pending one
    assert observation["details"] == {
        "expired_dispatch_id": "dispatch-0",
        "configured": ["erp-app"],
        "containers": [
            {
                "name": "erp-app",
                "image_ref": "127.0.0.1:5000/org/app@sha256:" + "a" * 64,
                "image_id": "sha256:" + "f" * 64,
                "repo_digests": [REPO_DIGEST],
            }
        ],
    }


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
    rig.cp.expired_run = "dispatch-0"
    rig.commands.script[("inspect",)] = CommandResult(1, b"")
    rig.cp.overrides["lease/acquire"] = MARKER
    assert rig.launcher.run_once().outcome == "refused"
    [observation] = reconciles(rig)
    assert observation["details"]["containers"] == []  # type: ignore[index]
    # the agent reports facts: never `not_needed`, never a verdict on the host
    assert observation["outcome"] == "reconciled"


def expired_rig(tmp_path: Path) -> Rig:
    rig = ready(tmp_path)
    rig.cp.standing = "EXPIRED_UNRELEASED"
    rig.cp.expired_run = "dispatch-0"
    rig.cp.overrides["lease/acquire"] = MARKER
    rig.commands.script[("image", "inspect")] = CommandResult(
        0, b'["' + REPO_DIGEST.encode() + b'"]'
    )
    rig.commands.script[("inspect",)] = CommandResult(
        0, b"127.0.0.1:5000/org/app@sha256:" + b"a" * 64 + b" sha256:" + b"f" * 64
    )
    return rig


def test_an_image_that_cannot_be_read_makes_the_observation_failed(
    tmp_path: Path,
) -> None:
    rig = expired_rig(tmp_path)
    rig.commands.script[("image", "inspect")] = CommandResult(1, b"")
    assert rig.launcher.run_once().outcome == "refused"
    [observation] = reconciles(rig)
    assert (observation["outcome"], observation["reason"]) == (
        "failed",
        "reconcile_unavailable",
    )


def test_the_expired_dispatch_comes_from_the_standing_and_a_missing_one_refuses(
    tmp_path: Path,
) -> None:
    rig = expired_rig(tmp_path)
    rig.cp.expired_run = None  # an old Control that does not name it
    assert rig.launcher.run_once().outcome == "refused"
    [observation] = rig.cp.observations
    assert (
        observation["dispatch_id"] == "dispatch-1"
        and observation["reason"] == "reconcile_unavailable"
    )


def test_a_local_inflight_record_that_disagrees_with_the_expired_dispatch_refuses(
    tmp_path: Path,
) -> None:
    rig = expired_rig(tmp_path)
    rig.cp.expired_run = "dispatch-7"
    mark_inflight(rig.config.state_dir, "dispatch-3")
    assert rig.launcher.run_once().outcome == "refused"


def test_reconcile_is_posted_once_per_expired_dispatch_and_observed_digest(
    tmp_path: Path,
) -> None:
    rig = expired_rig(tmp_path)
    for _ in range(
        4
    ):  # CP keeps withholding the marker; every poll sees the same facts
        assert rig.launcher.run_once().outcome == "refused"
    assert len(reconciles(rig)) == 1
    rig.commands.script[("inspect",)] = CommandResult(
        0,
        b"127.0.0.1:5000/org/app@sha256:" + b"b" * 64 + b" sha256:" + b"e" * 64,
    )
    rig.launcher.run_once()
    assert len(reconciles(rig)) == 2  # the digest changed
    rig.cp.expired_run = "dispatch-9"  # another expired dispatch posts afresh
    rig.launcher.run_once()
    assert len(reconciles(rig)) == 3


def test_a_marker_that_control_issued_lets_the_same_cycle_acquire_and_run(
    tmp_path: Path,
) -> None:
    rig = expired_rig(tmp_path)
    del rig.cp.overrides["lease/acquire"]  # CP's acquire now succeeds
    assert rig.launcher.run_once().outcome == "completed"
    assert len(reconciles(rig)) == 1 and len(rig.runner.specs) == 1


def test_the_standing_never_changes_on_the_agents_word(tmp_path: Path) -> None:
    rig = expired_rig(tmp_path)
    rig.launcher.run_once()
    assert rig.cp.standing == "EXPIRED_UNRELEASED"
    assert [e for e, _ in rig.cp.lease_log] == []  # no acquire, no release


def test_a_refusal_is_reported_once_per_dispatch_and_reason_not_once_per_poll(
    tmp_path: Path,
) -> None:
    rig = ready(tmp_path)
    rig.cp.overrides["lease/standing"] = (403, {"error": "credential_revoked"})
    for _ in range(4):
        assert rig.launcher.run_once().outcome == "refused"
    assert len(rig.cp.observations) == 1


def test_r5m2_a_down_daemon_is_not_an_absent_container(tmp_path: Path) -> None:
    """`docker inspect` fails both for a missing container and for a dead daemon; only a
    successful, empty `docker ps -a` proves absence."""
    rig = expired_rig(tmp_path)
    rig.commands.script[("inspect",)] = CommandResult(1, b"")
    rig.commands.script[("ps",)] = CommandResult(1, b"")  # the listing fails too
    assert rig.launcher.run_once().outcome == "refused"
    [observation] = reconciles(rig)
    assert (observation["outcome"], observation["reason"]) == (
        "failed",
        "reconcile_unavailable",
    )


def test_r5m2_an_inspect_failure_with_the_container_listed_is_not_absence(
    tmp_path: Path,
) -> None:
    rig = expired_rig(tmp_path)
    rig.commands.script[("inspect",)] = CommandResult(1, b"")
    rig.commands.script[("ps",)] = CommandResult(0, b"3f2a9c1b7d44\n")  # it IS there
    assert rig.launcher.run_once().outcome == "refused"
    [observation] = reconciles(rig)
    assert observation["outcome"] == "failed"


def test_r5m2_only_a_proven_absent_container_is_a_clean_observation(
    tmp_path: Path,
) -> None:
    rig = expired_rig(tmp_path)
    rig.commands.script[("inspect",)] = CommandResult(1, b"")
    rig.commands.script[("ps",)] = CommandResult(0, b"")  # listing succeeded, empty
    assert rig.launcher.run_once().outcome == "refused"
    [observation] = reconciles(rig)
    assert (
        observation["outcome"] == "reconciled"
        and observation["details"]["containers"] == []
    )  # type: ignore[index]
