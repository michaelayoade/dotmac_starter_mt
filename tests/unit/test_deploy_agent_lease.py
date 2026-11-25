"""dotmac-deploy-agent: host lease client and the read-only reconcile observation."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest
from dotmac_deploy_agent.commands import CommandResult, ReadOnlyCommands
from dotmac_deploy_agent.lease import Lease, LeaseClient
from dotmac_deploy_agent.observation import mark_inflight
from dotmac_deploy_agent.refusal import Refusal
from dotmac_deploy_agent.runner import RunSpec, SubprocessRunner

from tests.unit.deploy_agent_lease_double import (
    DeployLeaseDouble,
    LeaseClock,
    LeaseRefused,
    install,
    renew_grant,
)
from tests.unit.deploy_agent_support import (
    IMAGE_DIGEST,
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
        "lease_held",
        "lease-host-mutated",
        "lease-foreign-run",
        "lease-ttl-invalid",
        "reconcile_marker_required",
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
    rig.cp.overrides["lease/release"] = (409, {"error": "lease_expired"})
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


def expired_host(double: DeployLeaseDouble, clock: LeaseClock) -> None:
    """A previous run's lease ran out unreleased: standing `expired_unreleased`."""
    double.acquire({"dispatch_run": "old-run", "ttl_seconds": 120})
    clock.advance(121)
    assert double.standing() == "expired_unreleased"


def expired_rig(tmp_path: Path) -> tuple[Rig, DeployLeaseDouble, LeaseClock]:
    rig, double, clock = with_double(tmp_path)
    expired_host(double, clock)
    rig.commands.script[("image", "inspect")] = CommandResult(
        0, b'["' + REPO_DIGEST.encode() + b'"]'
    )
    rig.commands.script[("inspect",)] = CommandResult(
        0, b"127.0.0.1:5000/org/app@sha256:" + b"a" * 64 + b" sha256:" + b"f" * 64
    )
    return rig, double, clock


def no_lease_use_after_refusal(double: DeployLeaseDouble) -> None:
    """A refused acquire is the end of the cycle: the only lease calls ever made are
    acquires (the host's own expired one, then the refused one)."""
    names = [name for name, _ in double.log]
    assert names and set(names) <= {"lease/acquire"}, names


def test_reconcile_observation_read_only(tmp_path: Path) -> None:
    rig, double, _ = expired_rig(tmp_path)
    result = rig.launcher.run_once()
    # The v2 standing has no "reconciled" value: the agent observes, then the
    # acquire proves the marker. Without it: signed 409, and nothing else happens.
    assert (result.outcome, result.reason) == ("refused", "lease_refused")
    reconcile, deploy = rig.cp.observations
    assert deploy["details"]["refusal_detail"] == "reconcile_marker_required"  # type: ignore[index]
    assert rig.transfer.calls == [] and rig.runner.specs == []
    assert not (rig.config.deploy_run_dir / "params.env").exists()
    assert not (rig.config.run_dir / "app").exists()
    assert rig.bao.paths == []
    no_lease_use_after_refusal(double)
    # Every command was an inspection verb.
    assert rig.commands.calls
    for argv in rig.commands.calls:
        assert argv[1] in {"inspect", "ps"} or argv[1:3] == ("image", "inspect")
    assert reconcile["kind"] == "reconcile" and reconcile["outcome"] == "reconciled"
    assert reconcile["dispatch_id"] == "old-run"  # the EXPIRED dispatch, not pending
    assert reconcile["details"] == {
        "expired_dispatch_id": "old-run",
        "configured": ["erp-app"],
        "containers": [
            {
                "name": "erp-app",
                "image_ref": REPO_DIGEST,
                "image_id": "sha256:" + "f" * 64,
                "repo_digests": [REPO_DIGEST],
            }
        ],
    }


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
    rig, _, _ = expired_rig(tmp_path)
    rig.commands.script[("inspect",)] = CommandResult(1, b"")
    assert rig.launcher.run_once().outcome == "refused"
    [observation, _] = rig.cp.observations
    assert observation["details"]["containers"] == []  # type: ignore[index]
    # The agent reports facts: never `not_needed`, never a verdict on the host.
    assert observation["outcome"] == "reconciled"


def test_an_image_that_cannot_be_read_makes_the_observation_failed(
    tmp_path: Path,
) -> None:
    rig, _, _ = expired_rig(tmp_path)
    rig.commands.script[("image", "inspect")] = CommandResult(1, b"")
    assert rig.launcher.run_once().outcome == "refused"
    observation = rig.cp.observations[0]
    assert (observation["outcome"], observation["reason"]) == (
        "failed",
        "reconcile_unavailable",
    )


def test_a_standing_that_does_not_name_the_expired_dispatch_refuses(
    tmp_path: Path,
) -> None:
    rig, double, _ = expired_rig(tmp_path)
    # An expired standing that names no dispatch (null) cannot be reconciled. Control's
    # standing vectors call that body malformed, so it is refused at the wire check,
    # before any reconcile or acquire.
    rig.cp.handlers["lease/standing"] = lambda _p: {
        "standing": "expired_unreleased",
        "dispatch_run": None,
    }
    assert rig.launcher.run_once().outcome == "refused"
    [observation] = rig.cp.observations
    assert (observation["dispatch_id"], observation["reason"]) == (
        "dispatch-1",
        "response_malformed",
    )
    assert [n for n, _ in double.log if n == "lease/acquire"] == [
        "lease/acquire"
    ]  # only expired_host's


def test_a_local_inflight_record_that_disagrees_with_the_expired_dispatch_refuses(
    tmp_path: Path,
) -> None:
    rig, _, _ = expired_rig(tmp_path)
    mark_inflight(rig.config.state_dir, "dispatch-3")
    assert rig.launcher.run_once().outcome == "refused"
    assert rig.transfer.calls == []


def test_reconcile_is_posted_once_per_expired_dispatch_and_observed_digest(
    tmp_path: Path,
) -> None:
    rig, double, _ = expired_rig(tmp_path)
    assert rig.launcher.run_once().reason == "lease_refused"
    for _ in range(3):  # every later poll sees the same facts, and the same refusal
        assert rig.launcher.run_once().reason == "lease_refused"
    kinds = [o["kind"] for o in rig.cp.observations]
    assert kinds.count("reconcile") == 1
    rig.commands.script[("inspect",)] = CommandResult(
        0, b"127.0.0.1:5000/org/app@sha256:" + b"b" * 64 + b" sha256:" + b"e" * 64
    )
    rig.launcher.run_once()  # the digest changed: posted afresh
    assert [o["kind"] for o in rig.cp.observations].count("reconcile") == 2
    assert double.row is not None
    double.row.run = "old-run-2"  # another expired dispatch posts afresh
    rig.launcher.run_once()
    assert [o["kind"] for o in rig.cp.observations].count("reconcile") == 3
    assert (
        double.standing() == "expired_unreleased"
    )  # never changes on the agent's word
    no_lease_use_after_refusal(double)


def test_a_refusal_is_reported_once_per_dispatch_and_reason_not_once_per_poll(
    tmp_path: Path,
) -> None:
    rig = ready(tmp_path)
    rig.cp.overrides["lease/standing"] = (403, {"error": "credential_revoked"})
    for _ in range(4):
        assert rig.launcher.run_once().outcome == "refused"
    assert len(rig.cp.observations) == 1


# -- WP3B: the lease client against the stateful s2.6.2 double -------------------


def with_double(
    tmp_path: Path,
    *,
    max_seconds: int | None = None,
    materials: dict[str, object] | None = None,
    **config: object,
) -> tuple[Rig, DeployLeaseDouble, LeaseClock]:
    rig = make_rig(tmp_path, **config)
    rig.cp.materials = materials or make_materials()
    clock = LeaseClock()
    double = DeployLeaseDouble(clock, max_seconds=max_seconds)
    install(rig, double, clock)
    return rig, double, clock


def spend_in_transfer(rig: Rig, clock: LeaseClock, seconds: float) -> None:
    original = rig.transfer.transfer_all

    def slow(images, *, ttl_seconds):  # type: ignore[no-untyped-def]
        clock.advance(seconds)
        return original(images, ttl_seconds=ttl_seconds)

    rig.transfer.transfer_all = slow  # type: ignore[method-assign]


def test_double_clean_cycle_acquires_covers_and_releases(tmp_path: Path) -> None:
    rig, double, _ = with_double(tmp_path)
    assert rig.launcher.run_once().outcome == "completed"
    assert [name for name, _ in double.log] == [
        "lease/acquire",
        "lease/covers",
        "lease/release",
    ]
    assert double.standing() == "free"
    assert double.row is not None and double.row.mutated is True
    # The persistent-host rule: a released lease is clean whatever host_mutated says,
    # so the next deploy on the same host is NOT refused (lease-host-mutated).
    rig.cp.materials = make_materials(dispatch_id="dispatch-2")
    assert rig.launcher.run_once().outcome == "completed"


def test_d_lease_contended_stops_clean_and_self_heals(tmp_path: Path) -> None:
    rig, double, _ = with_double(tmp_path)
    double.acquire({"dispatch_run": "other-run", "ttl_seconds": 900})
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_refused")
    detail = rig.cp.observations[0]["details"]["refusal_detail"]  # type: ignore[index]
    assert detail == "lease_held"
    # Nothing past the lease: no transfer, params, secret, run, release.
    assert rig.transfer.calls == [] and rig.runner.specs == [] and rig.bao.paths == []
    assert not (rig.config.deploy_run_dir / "params.env").exists()
    assert "lease/release" not in [n for n, _ in double.log]
    # The other run lets go; the SAME dispatch is retried with no staff step.
    double.release({"lease_id": "lease-1", "host_mutated": False})
    rig.cp.observations.clear()
    assert rig.launcher.run_once().outcome == "completed"


def test_d_lease_contended_by_a_rehearsal_class_lease(tmp_path: Path) -> None:
    # One host has one writer across both classes (shared row lock).
    rig, double, _ = with_double(tmp_path)
    double.rehearsal_live = True
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_refused")
    assert rig.runner.specs == []


def test_d_lease_expired_before_the_run_then_reconcile_heals(tmp_path: Path) -> None:
    rig, double, clock = with_double(tmp_path)
    spend_in_transfer(rig, clock, 901)  # transfer outlasts the whole lease
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_exhausted")
    assert rig.runner.specs == [] and rig.bao.paths == []
    assert not (rig.config.deploy_run_dir / "params.env").exists()
    details = rig.cp.observations[0]["details"]  # type: ignore[index]
    assert details["lease_released"] is False
    assert details["lease_release_refusal"] == "lease_expired"
    # Expired unreleased: standing says so, and a fresh acquire is refused...
    assert double.standing() == "expired_unreleased"
    with pytest.raises(LeaseRefused, match="reconcile_marker_required"):
        double.acquire({"dispatch_run": "dispatch-1", "ttl_seconds": 900})
    # ...so the next cycle observes (read-only), posts it, and is refused at acquire.
    rig.cp.observations.clear()
    rig.transfer.calls.clear()  # this cycle's own calls are what is asserted below
    del rig.transfer.transfer_all  # back to the plain fake
    rig.commands.script[("image", "inspect")] = CommandResult(
        0, b'["127.0.0.1:5000/org/app@' + IMAGE_DIGEST.encode() + b'"]\n'
    )
    rig.commands.script[("inspect",)] = CommandResult(
        0, b"127.0.0.1:5000/org/app@" + IMAGE_DIGEST.encode() + b" sha256:" + b"f" * 64
    )
    assert rig.launcher.run_once().reason == "lease_refused"
    assert [o["kind"] for o in rig.cp.observations] == ["reconcile", "deploy"]
    assert rig.transfer.calls == [] and rig.runner.specs == []
    # Control matches the observed release with the expected one: marker, no staff.
    assert double.reconcile(rig.cp.observations[0], expected=IMAGE_DIGEST)
    rig.cp.observations.clear()
    # The standing stays `expired_unreleased` until acquire succeeds. The same facts
    # are not posted twice; the acquire now goes through and the deploy completes.
    assert rig.launcher.run_once().outcome == "completed"
    assert [o["kind"] for o in rig.cp.observations] == ["deploy"]
    assert double.standing() == "free"


def test_reconcile_marker_is_refused_when_the_observed_release_differs(
    tmp_path: Path,
) -> None:
    rig, double, clock = with_double(tmp_path)
    spend_in_transfer(rig, clock, 901)
    rig.launcher.run_once()
    rig.cp.observations.clear()
    rig.commands.script[("image", "inspect")] = CommandResult(
        0, b'["127.0.0.1:5000/org/app@sha256:' + b"9" * 64 + b'"]\n'
    )
    rig.commands.script[("inspect",)] = CommandResult(
        0, b"127.0.0.1:5000/org/app@sha256:" + b"9" * 64 + b" sha256:" + b"f" * 64
    )
    rig.launcher.run_once()
    reconcile = next(o for o in rig.cp.observations if o["kind"] == "reconcile")
    assert not double.reconcile(reconcile, expected=IMAGE_DIGEST)
    assert double.standing() == "expired_unreleased"  # restore-and-fix-forward path


def test_a_plan_without_a_max_gets_one_grant_and_the_run_fits_it(
    tmp_path: Path,
) -> None:
    rig, _, clock = with_double(tmp_path)  # lease 900 s, Foundation timeout 2700 s
    spend_in_transfer(rig, clock, 100)
    assert rig.launcher.run_once().outcome == "completed"
    # lease_max (= one grant) 900 - 100 spent - 120 margin.
    assert rig.runner.specs[0].timeout_seconds == 680
    assert rig.cp.observations[0]["details"]["run_budget_seconds"] == 680  # type: ignore[index]


def test_a_shorter_configured_timeout_is_kept(tmp_path: Path) -> None:
    rig, _, _ = with_double(tmp_path, foundation_timeout_seconds=300)
    assert rig.launcher.run_once().outcome == "completed"
    assert rig.runner.specs[0].timeout_seconds == 300


def test_no_room_left_for_the_run_is_refused_before_any_secret(tmp_path: Path) -> None:
    rig, double, clock = with_double(tmp_path)
    spend_in_transfer(rig, clock, 721)  # 179 s left: under margin 120 + minimum 60
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_exhausted")
    assert rig.bao.paths == [] and rig.runner.specs == []
    # Still live, so it is released clean and the host is not left to expire.
    assert double.log[-1] == (
        "lease/release",
        {"lease_id": "lease-1", "host_mutated": False},
    )
    assert double.standing() == "free"


def test_a_run_that_outlives_its_lease_leaves_it_unreleased(tmp_path: Path) -> None:
    rig, double, clock = with_double(tmp_path)
    rig.runner.effect = lambda _spec: clock.advance(950)  # overran despite the clip
    result = rig.launcher.run_once()
    assert result.outcome == "completed"  # the outcome is never rewritten...
    details = rig.cp.observations[0]["details"]  # type: ignore[index]
    assert details["lease_released"] is False  # ...but the lease is NOT released
    assert details["lease_release_refusal"] == "lease_expired"
    assert double.standing() == "expired_unreleased"  # next acquire needs a reconcile


def test_acquire_by_the_same_run_returns_its_lease(tmp_path: Path) -> None:
    rig, _, clock = with_double(tmp_path)
    client = rig.launcher.lease
    first = client.acquire(dispatch_id="d", ttl_seconds=900)
    clock.advance(100)
    again = client.acquire(dispatch_id="d", ttl_seconds=900)
    assert again.lease_id == first.lease_id
    assert again.ttl_seconds == 800  # v2: the REMAINING ttl of the run's own lease


def test_a_replayed_acquire_counts_its_deadline_from_the_ttl_returned(
    tmp_path: Path,
) -> None:
    rig, double, clock = with_double(tmp_path)
    client = rig.launcher.lease
    first = client.acquire(dispatch_id="d", ttl_seconds=900, max_seconds=3000)
    clock.advance(400)
    again = client.acquire(dispatch_id="d", ttl_seconds=900, max_seconds=3000)
    assert again.lease_id == first.lease_id and again.ttl_seconds == 500
    # Deadline = receipt time + the ttl returned = the original end, never later.
    assert again.deadline == first.deadline
    # Its true start is unknown, so the hold is not counted beyond this grant...
    assert again.max_seconds == 500
    assert client.run_budget(again, configured_seconds=7200) == 380  # 500 - 120
    # A later renewal may still grant up to what the plan asked for (the cap), not
    # only the shortened ttl: the returned grant is not the plan's.
    rig.cp.handlers["lease/renew"] = lambda _p: {
        "lease_id": again.lease_id,
        "ttl_seconds": 900,
    }
    assert client.renew(again, dispatch_id="d").ttl_seconds == 900


def test_lease_deadline_counts_from_before_the_request_left(tmp_path: Path) -> None:
    rig, _, clock = with_double(tmp_path)
    lease = rig.launcher.lease.acquire(dispatch_id="d", ttl_seconds=900)
    assert lease.deadline == clock.t + 900  # no time passed in the double
    assert Lease("x", 5).deadline == 0.0  # built without one: already over


def test_budget_refuses_a_spent_lease_and_never_extends(tmp_path: Path) -> None:
    rig, _, clock = with_double(tmp_path)
    client = rig.launcher.lease
    lease = client.acquire(dispatch_id="d", ttl_seconds=900)
    assert client.run_budget(lease, configured_seconds=10_000) == 780
    clock.advance(721)
    with pytest.raises(Refusal) as caught:
        client.run_budget(lease, configured_seconds=10_000)
    assert caught.value.code == "lease_exhausted"


def test_the_runner_never_outlasts_the_lease_budget() -> None:
    runner = SubprocessRunner(("/bin/sleep", "30"), timeout_seconds=600)
    spec = RunSpec(
        dispatch_id="d",
        target_ref="erp/staging/host-a",
        product="erp",
        plan_digest="sha256:" + "0" * 64,
        params_env=Path("/nonexistent/params.env"),
        agent_config=None,
        timeout_seconds=1,
    )
    result = runner.run(spec)
    assert result.timed_out is True  # killed at the lease budget, not at 600 s


def test_the_lease_budget_and_the_job_bound_are_a_min_not_either_or() -> None:
    runner = SubprocessRunner(("/bin/sleep", "30"), timeout_seconds=600)
    base = {
        "dispatch_id": "d",
        "target_ref": "erp/staging/host-a",
        "product": "erp",
        "plan_digest": "sha256:" + "0" * 64,
        "params_env": Path("/nonexistent/params.env"),
        "agent_config": None,
    }
    # A generous deadline-derived bound does not lift the lease budget...
    tight = RunSpec(**base, job_bound_seconds=600, timeout_seconds=1)  # type: ignore[arg-type]
    assert runner.run(tight).timed_out is True
    # ...and a generous lease budget does not lift a tight job bound.
    bound = RunSpec(**base, job_bound_seconds=1, timeout_seconds=600)  # type: ignore[arg-type]
    assert runner.run(bound).timed_out is True


# -- ruling 4: renewal (S5) and signed replay on a second release (S6) -------------


def renew_in_transfer(rig: Rig, clock: LeaseClock, *steps: float) -> None:
    """Transfer that takes `steps` seconds in turn, ticking the keeper after each."""
    original = rig.transfer.transfer_all

    def slow(images, *, ttl_seconds):  # type: ignore[no-untyped-def]
        for step in steps:
            clock.advance(step)
            rig.launcher.lease.last_keeper.tick()  # type: ignore[union-attr]
        return original(images, ttl_seconds=ttl_seconds)

    rig.transfer.transfer_all = slow  # type: ignore[method-assign]


def long_plan(**more: object) -> dict[str, object]:
    return make_materials(lease_ttl_seconds=900, lease_max_seconds=3000, **more)


def test_the_lease_is_renewed_every_third_of_the_ttl(tmp_path: Path) -> None:
    rig, double, clock = with_double(
        tmp_path,
        max_seconds=3000,
        materials=long_plan(),
        foundation_timeout_seconds=7200,
    )
    # 100 s: not due. 200 s more (300 = ttl/3): due. Then 300 s: due again.
    renew_in_transfer(rig, clock, 100, 200, 300)
    assert rig.launcher.run_once().outcome == "completed"
    renews = [p for n, p in double.log if n == "lease/renew"]
    assert renews == [{"lease_id": "lease-1", "dispatch_run": "dispatch-1"}] * 2
    lease_details = rig.cp.observations[0]["details"]["lease"]  # type: ignore[index]
    assert lease_details["renewals"] == 2 and "lease_lost" not in lease_details
    # Budget = min(timeout, lease_max - elapsed - 120) = 3000 - 600 - 120.
    assert rig.runner.specs[0].timeout_seconds == 2280
    assert double.standing() == "free"


def test_a_lease_renewed_past_one_grant_covers_the_whole_run(tmp_path: Path) -> None:
    rig, double, clock = with_double(
        tmp_path,
        max_seconds=3000,
        materials=long_plan(),
        foundation_timeout_seconds=7200,
    )
    # The first grant ended at 900 s; renewals kept the lease live well past it.
    renew_in_transfer(rig, clock, 300, 300, 300, 300)
    rig.runner.effect = lambda _s: None
    assert rig.launcher.run_once().outcome == "completed"
    assert double.log[-1][0] == "lease/release"
    assert clock.t - 1000 == 1200  # beyond the 900 s first grant
    assert rig.cp.observations[0]["details"]["lease_released"] is True  # type: ignore[index]


def test_a_refused_renewal_records_lease_lost_and_never_kills_the_run(
    tmp_path: Path,
) -> None:
    rig, double, clock = with_double(
        tmp_path,
        max_seconds=3000,
        materials=long_plan(),
        foundation_timeout_seconds=7200,
    )
    renew_in_transfer(rig, clock, 300)  # one good renewal
    keeper_ticks: list[int] = []

    def during_run(_spec: object) -> None:
        double.row = None  # CP no longer holds the lease
        clock.advance(300)
        rig.launcher.lease.last_keeper.tick()  # type: ignore[union-attr]
        clock.advance(300)
        rig.launcher.lease.last_keeper.tick()  # type: ignore[union-attr]
        keeper_ticks.append(1)

    rig.runner.effect = during_run
    result = rig.launcher.run_once()
    assert result.outcome == "completed"  # Foundation was not stopped early
    details = rig.cp.observations[0]["details"]  # type: ignore[index]
    assert details["lease"]["lease_lost"] == "lease_not_held"
    assert details["run_budget_seconds"] == rig.runner.specs[0].timeout_seconds
    # Renewing stopped at the first refusal: one good renew, one refused, no more.
    assert [n for n, _ in double.log].count("lease/renew") == 2


def test_a_keeper_that_missed_its_window_records_lease_lost_locally(
    tmp_path: Path,
) -> None:
    rig, double, clock = with_double(tmp_path)
    client = rig.launcher.lease
    keeper = client.keeper(
        client.acquire(dispatch_id="d", ttl_seconds=300), dispatch_id="d"
    )
    clock.advance(301)  # the grant is over and nothing renewed it
    keeper.tick()
    assert keeper.lease_lost == "lease_expired_locally"
    assert [n for n, _ in double.log] == ["lease/acquire"]  # no call into a dead lease


def test_a_transient_renew_failure_is_retried_not_lost(tmp_path: Path) -> None:
    rig, double, clock = with_double(tmp_path)
    client = rig.launcher.lease
    keeper = client.keeper(
        client.acquire(dispatch_id="d", ttl_seconds=300), dispatch_id="d"
    )
    clock.advance(100)
    rig.cp.raw_status["lease/renew"] = 503
    keeper.tick()
    assert keeper.lease_lost is None and keeper.renewals == 0
    del rig.cp.raw_status["lease/renew"]
    clock.advance(10)
    keeper.tick()
    assert keeper.renewals == 1


@pytest.mark.parametrize(
    "code", ["lease_not_held", "lease_expired", "lease_max_reached"]
)
def test_each_signed_renew_refusal_is_lease_lost(tmp_path: Path, code: str) -> None:
    rig, _, clock = with_double(tmp_path)
    client = rig.launcher.lease
    keeper = client.keeper(
        client.acquire(dispatch_id="d", ttl_seconds=300), dispatch_id="d"
    )
    clock.advance(100)
    rig.cp.overrides["lease/renew"] = (409, {"error": code})
    keeper.tick()
    assert keeper.lease_lost == code


def test_the_double_refuses_renewal_at_the_max_and_after_release() -> None:
    clock = LeaseClock()
    double = DeployLeaseDouble(clock, max_seconds=600)
    double.acquire({"dispatch_run": "r", "ttl_seconds": 300})
    ask = {"lease_id": "lease-1", "dispatch_run": "r"}
    clock.advance(250)
    assert double.renew(ask) == {"lease_id": "lease-1", "ttl_seconds": 300}
    clock.advance(250)  # held 500: only 100 s of the total are left
    assert double.renew(ask) == {"lease_id": "lease-1", "ttl_seconds": 100}
    clock.advance(100)  # held 600 = max, and the last grant just ended: expiry first
    with pytest.raises(LeaseRefused, match="lease_expired"):
        double.renew(ask)
    with pytest.raises(LeaseRefused, match="lease_max_reached"):
        renew_grant(300, 600, 600)  # the grant rule alone, at the cap
    with pytest.raises(LeaseRefused, match="lease_not_held"):
        double.renew({**ask, "dispatch_run": "other"})


def test_a_renewal_may_not_grant_more_than_the_first_ttl(tmp_path: Path) -> None:
    rig, _, clock = with_double(tmp_path)
    client = rig.launcher.lease
    lease = client.acquire(dispatch_id="d", ttl_seconds=300)
    rig.cp.handlers["lease/renew"] = lambda _p: {
        "lease_id": "lease-1",
        "ttl_seconds": 900,
    }
    with pytest.raises(Refusal) as caught:
        client.renew(lease, dispatch_id="d")
    assert caught.value.detail == "renew_malformed"


def test_run_budget_comes_from_the_total_not_the_current_grant(
    tmp_path: Path,
) -> None:
    rig, _, clock = with_double(tmp_path)
    client = rig.launcher.lease
    lease = client.acquire(dispatch_id="d", ttl_seconds=900, max_seconds=3000)
    clock.advance(500)
    assert client.run_budget(lease, configured_seconds=7200) == 2380
    clock.advance(2330)  # 170 s of the total left: under margin 120 + minimum 60
    with pytest.raises(Refusal) as caught:
        client.run_budget(lease, configured_seconds=7200)
    assert caught.value.code == "lease_exhausted"


def test_a_second_release_is_a_signed_replay_and_a_4xx_is_a_refusal(
    tmp_path: Path,
) -> None:
    rig, double, _ = with_double(tmp_path)
    client = rig.launcher.lease
    lease = client.acquire(dispatch_id="d", ttl_seconds=120)
    client.release(lease, host_mutated=False)
    client.release(lease, host_mutated=False)  # signed 200 {released, replay: true}
    assert double.release({"lease_id": "lease-1", "host_mutated": False}) == {
        "released": True,
        "replay": True,
    }
    rig.cp.overrides["lease/release"] = (409, {"error": "lease-already-released"})
    with pytest.raises(Refusal) as caught:
        client.release(lease, host_mutated=False)
    assert (caught.value.code, caught.value.detail) == (
        "lease_refused",
        "lease-already-released",
    )
    rig.cp.overrides["lease/release"] = (200, {"released": False})
    with pytest.raises(Refusal):
        client.release(lease, host_mutated=False)


def test_the_keeper_thread_renews_and_stops_cleanly(tmp_path: Path) -> None:
    import time as real_time

    rig, double, clock = with_double(tmp_path)
    client = LeaseClient(
        rig.launcher.client,
        monotonic=clock.monotonic,
        background=True,
        poll_seconds=0.01,
    )
    lease = client.acquire(dispatch_id="d", ttl_seconds=300)
    keeper = client.keeper(lease, dispatch_id="d")
    clock.advance(150)  # due
    keeper.start()
    for _ in range(500):
        if keeper.renewals:
            break
        real_time.sleep(0.01)
    keeper.stop()
    assert keeper.renewals >= 1 and keeper.lease_lost is None
    assert [n for n, _ in double.log].count("lease/renew") == keeper.renewals


def test_a_plan_binding_carries_lease_max_seconds(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(lease_ttl_seconds=900, lease_max_seconds=3000)
    rig.launcher.run_once()
    assert rig.cp.lease_log[0] == (
        "lease/acquire",
        {"dispatch_run": "dispatch-1", "ttl_seconds": 900},
    )


@pytest.mark.parametrize("bad", [899, 10801, True, "3000", 0])
def test_an_invalid_lease_max_seconds_is_refused(tmp_path: Path, bad: object) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(lease_ttl_seconds=900, lease_max_seconds=bad)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_ttl_invalid")
    assert "lease/acquire" not in [n for n, _ in rig.cp.lease_log]


# -- P-SEC rev5 RQ-1: a run_job lease is never renewed ---------------------------


def test_a_run_job_never_starts_a_keeper_and_never_renews(tmp_path: Path) -> None:
    from tests.unit.test_deploy_agent_jobs import job_rig

    rig = job_rig(tmp_path)  # ttl = max = timeout + 120, a signed run_job plan
    spy: list[str] = []
    original = rig.launcher.lease.keeper

    def spying(lease, *, dispatch_id):  # type: ignore[no-untyped-def]
        spy.append(dispatch_id)
        return original(lease, dispatch_id=dispatch_id)

    rig.launcher.lease.keeper = spying  # type: ignore[method-assign]
    assert rig.launcher.run_once().outcome == "completed"
    assert spy == []  # no keeper, so no thread and no renew
    assert rig.launcher.lease.last_keeper is None
    assert "lease/renew" not in [name for name, _ in rig.cp.lease_log]
    assert rig.cp.lease_log[0][0] == "lease/acquire"
    assert "renewals" not in rig.cp.observations[-1]["details"].get("lease", {})  # type: ignore[union-attr]


def test_only_the_serving_path_names_the_keeper() -> None:
    import ast

    from dotmac_deploy_agent import launcher

    tree = ast.parse(Path(launcher.__file__).read_text())
    users = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        and any(
            isinstance(n, ast.Attribute) and n.attr == "keeper" for n in ast.walk(fn)
        )
    }
    assert users == {"_execute"}  # never `_execute_job`


# -- ruling 4: the per-grant range is for SERVING plans; run_job is ttl == max ----


@pytest.mark.parametrize(
    ("ttl", "max_seconds", "ok"),
    [
        (120, None, True),
        (900, None, True),
        (600, 10800, True),
        (119, None, False),  # under the serving floor
        (901, None, False),  # over the serving ceiling
        (600, 599, False),  # the total may not be below one grant
        (600, 10801, False),  # nor above Control's hard maximum
    ],
)
def test_a_serving_lease_is_120_to_900_per_grant_and_at_most_10800_in_total(
    tmp_path: Path, ttl: int, max_seconds: int | None, ok: bool
) -> None:
    rig, double, _ = with_double(tmp_path)
    client = rig.launcher.lease
    if ok:
        client.acquire(dispatch_id="d", ttl_seconds=ttl, max_seconds=max_seconds)
        return
    with pytest.raises(Refusal) as caught:
        client.acquire(dispatch_id="d", ttl_seconds=ttl, max_seconds=max_seconds)
    assert caught.value.code == "lease_ttl_invalid"
    assert double.log == []  # refused before anything is asked of CP


@pytest.mark.parametrize(
    ("ttl", "max_seconds", "ok"),
    [
        (720, None, True),  # a job's ttl may exceed 900 ...
        (2920, 2920, True),  # ... and equals its max
        (3600, None, True),  # the role-job ceiling
        (3601, None, False),  # above the ceiling
        (1920, 3120, False),  # ttl != max: a job is one grant
    ],
)
def test_a_run_job_lease_is_ttl_equal_max_and_at_most_3600(
    tmp_path: Path, ttl: int, max_seconds: int | None, ok: bool
) -> None:
    rig, double, _ = with_double(tmp_path)
    client = rig.launcher.lease
    if ok:
        lease = client.acquire(
            dispatch_id="d", ttl_seconds=ttl, max_seconds=max_seconds, serving=False
        )
        assert lease.max_seconds == ttl  # one grant, never renewed
        return
    with pytest.raises(Refusal) as caught:
        client.acquire(
            dispatch_id="d", ttl_seconds=ttl, max_seconds=max_seconds, serving=False
        )
    assert caught.value.code == "lease_ttl_invalid"
    assert double.log == []


def test_a_run_job_lease_refuses_renewal_with_lease_not_renewable(
    tmp_path: Path,
) -> None:
    rig, double, clock = with_double(tmp_path)
    double.renewable = False  # the plan is a run_job
    client = rig.launcher.lease
    lease = client.acquire(
        dispatch_id="d", ttl_seconds=720, serving=False
    )  # ttl = max, one grant
    clock.advance(100)
    with pytest.raises(Refusal) as caught:
        client.renew(lease, dispatch_id="d")
    assert caught.value.detail == "lease_not_renewable"
    assert client.covers(lease, dispatch_id="d") is True  # the lease itself stands
    # A keeper would record it as lost, but a run_job never starts one (RQ-1).
    keeper = client.keeper(lease, dispatch_id="d")
    clock.advance(150)  # past ttl/3 = 240 s from the grant
    keeper.tick()
    assert keeper.lease_lost == "lease_not_renewable"


# -- Control dfe7133: lease_superseded, and a refused release is not success -------


def superseded_pair(tmp_path: Path) -> tuple[Rig, DeployLeaseDouble, LeaseClock, Lease]:
    rig, double, clock = with_double(tmp_path)
    client = rig.launcher.lease
    old = client.acquire(dispatch_id="run-1", ttl_seconds=600, max_seconds=1500)
    clock.advance(700)  # run-1's lease expired unreleased...
    double.marker = True  # ...Control issued the marker...
    client.acquire(dispatch_id="run-2", ttl_seconds=600, max_seconds=1500)  # ...and
    return rig, double, clock, old  # a newer acquire superseded it


def test_a_superseded_lease_covers_nothing_and_its_keeper_records_it_lost(
    tmp_path: Path,
) -> None:
    rig, double, clock, old = superseded_pair(tmp_path)
    client = rig.launcher.lease
    assert client.covers(old, dispatch_id="run-1") is False
    keeper = client.keeper(
        Lease("lease-1", 600, clock.t + 100, clock.t - 500, 1500, 600),
        dispatch_id="run-1",
    )
    clock.advance(1)
    keeper.tick()
    assert keeper.lease_lost == "lease_superseded"
    with pytest.raises(Refusal) as caught:
        client.release(old, host_mutated=False)
    assert (caught.value.code, caught.value.detail) == (
        "lease_refused",
        "lease_superseded",
    )


def test_releasing_an_expired_lease_is_refused_and_the_host_stays_expired(
    tmp_path: Path,
) -> None:
    rig, double, clock = with_double(tmp_path)
    client = rig.launcher.lease
    lease = client.acquire(dispatch_id="run-1", ttl_seconds=600, max_seconds=1500)
    clock.advance(700)
    with pytest.raises(Refusal) as caught:
        client.release(lease, host_mutated=False)
    assert caught.value.detail == "lease_expired"
    assert double.standing() == "expired_unreleased"


def test_a_launcher_release_refused_as_superseded_is_reported_not_success(
    tmp_path: Path,
) -> None:
    rig, double, clock = with_double(tmp_path)
    original = rig.launcher.runner.run

    def supersede(spec):  # type: ignore[no-untyped-def]
        # While the run ran, the lease expired and another run took the host.
        clock.advance(1000)
        double.marker = True
        double.acquire({"dispatch_run": "other-run", "ttl_seconds": 600})
        return original(spec)

    rig.launcher.runner.run = supersede  # type: ignore[method-assign]
    assert rig.launcher.run_once().outcome == "completed"  # the outcome stands...
    details = rig.cp.observations[0]["details"]  # type: ignore[index]
    assert details["lease_released"] is False  # ...but the release did not succeed
    assert details["lease_release_refusal"] == "lease_superseded"


def reconciles(rig: Rig) -> list[dict[str, object]]:
    return [o for o in rig.cp.observations if o["kind"] == "reconcile"]


def test_r5m2_a_down_daemon_is_not_an_absent_container(tmp_path: Path) -> None:
    """`docker inspect` fails both for a missing container and for a dead daemon; only a
    successful, empty `docker ps -a` proves absence."""
    rig, _, _ = expired_rig(tmp_path)
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
    rig, _, _ = expired_rig(tmp_path)
    rig.commands.script[("inspect",)] = CommandResult(1, b"")
    rig.commands.script[("ps",)] = CommandResult(0, b"3f2a9c1b7d44\n")  # it IS there
    assert rig.launcher.run_once().outcome == "refused"
    [observation] = reconciles(rig)
    assert observation["outcome"] == "failed"


def test_r5m2_only_a_proven_absent_container_is_a_clean_observation(
    tmp_path: Path,
) -> None:
    rig, _, _ = expired_rig(tmp_path)
    rig.commands.script[("inspect",)] = CommandResult(1, b"")
    rig.commands.script[("ps",)] = CommandResult(0, b"")  # listing succeeded, empty
    assert rig.launcher.run_once().outcome == "refused"
    [observation] = reconciles(rig)
    assert (
        observation["outcome"] == "reconciled"
        and observation["details"]["containers"] == []
    )  # type: ignore[index]


# -- R8: a lost lease runs nothing; keeper stop and retry ------------------------


def test_r8m1_a_crashed_keeper_means_no_run_on_a_lease_cp_still_holds(
    tmp_path: Path,
) -> None:
    rig, double, _ = with_double(
        tmp_path,
        max_seconds=3000,
        materials=long_plan(),
        foundation_timeout_seconds=7200,
    )
    original = rig.transfer.transfer_all

    def crash_during_transfer(images, *, ttl_seconds):  # type: ignore[no-untyped-def]
        keeper = rig.launcher.lease.last_keeper
        assert keeper is not None

        def boom() -> None:
            raise RuntimeError("keeper crashed")

        keeper.tick = boom  # type: ignore[method-assign]
        keeper.run_tick()  # what the thread does: the failure is recorded
        return original(images, ttl_seconds=ttl_seconds)

    rig.transfer.transfer_all = crash_during_transfer  # type: ignore[method-assign]
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_unavailable")
    details = rig.cp.observations[0]["details"]  # type: ignore[index]
    assert details["refusal_detail"] == "lease_keeper_failed"
    assert details["lease"]["lease_lost"] == "lease_keeper_failed"
    assert "lease/covers" in [n for n, _ in double.log]  # CP still held it...
    assert rig.runner.specs == [] and rig.bao.paths == []  # ...but nothing ran
    assert double.log[-1][0] == "lease/release" and double.standing() == "free"


def test_r8m1_a_lost_renew_reply_means_no_run(tmp_path: Path) -> None:
    rig, double, clock = with_double(
        tmp_path,
        max_seconds=3000,
        materials=long_plan(),
        foundation_timeout_seconds=7200,
    )
    lease_client = rig.launcher.lease
    real_renew = lease_client.renew

    def renew_whose_reply_is_lost(lease, *, dispatch_id):  # type: ignore[no-untyped-def]
        real_renew(lease, dispatch_id=dispatch_id)  # CP extended the lease...
        raise Refusal("lease_refused", "reply_lost")  # ...the answer never arrived

    lease_client.renew = renew_whose_reply_is_lost  # type: ignore[method-assign]
    original = rig.transfer.transfer_all

    def slow(images, *, ttl_seconds):  # type: ignore[no-untyped-def]
        keeper = lease_client.last_keeper
        assert keeper is not None
        clock.advance(300)
        keeper.tick()  # a renew is due: it lands at CP, the reply is lost
        clock.advance(700)  # past the grant the agent still believes it holds
        keeper.tick()
        assert keeper.lease_lost == "lease_expired_locally"
        return original(images, ttl_seconds=ttl_seconds)

    rig.transfer.transfer_all = slow  # type: ignore[method-assign]
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "lease_unavailable")
    assert [n for n, _ in double.log].count("lease/covers") == 1  # CP says covered
    assert rig.runner.specs == [] and rig.bao.paths == []


def test_r8l2_a_failed_renew_is_retried_after_a_pause_not_every_poll(
    tmp_path: Path,
) -> None:
    rig, double, clock = with_double(tmp_path, max_seconds=3000, materials=long_plan())
    client = rig.launcher.lease
    lease = client.acquire(dispatch_id="d", ttl_seconds=900, max_seconds=3000)
    keeper = client.keeper(lease, dispatch_id="d")
    calls: list[float] = []

    def failing(_lease, *, dispatch_id):  # type: ignore[no-untyped-def]
        calls.append(clock.t)
        raise Refusal("lease_refused", "renew_unavailable")  # not a lost lease

    client.renew = failing  # type: ignore[method-assign]
    clock.advance(300)  # due
    for _ in range(40):  # the keeper polls every 2 s
        keeper.tick()
        clock.advance(2)
    # min(15, ttl/10) = 15 s apart: 80 s of polling is 6 tries, never 40.
    assert 2 <= len(calls) <= 6
    assert all(b - a >= 15 for a, b in pairwise(calls))
    assert keeper.lease_lost is None


def test_r8l1_no_renew_is_sent_after_stop(tmp_path: Path) -> None:
    rig, double, clock = with_double(tmp_path, max_seconds=3000, materials=long_plan())
    client = rig.launcher.lease
    lease = client.acquire(dispatch_id="d", ttl_seconds=900, max_seconds=3000)
    keeper = client.keeper(lease, dispatch_id="d")
    clock.advance(300)  # a renew is due
    keeper.stop()
    keeper.tick()
    assert "lease/renew" not in [n for n, _ in double.log]
    assert keeper.joined is True
    from dotmac_deploy_agent.lease import JOIN_SECONDS
    from dotmac_deploy_agent.transport import TIMEOUT_SECONDS

    assert JOIN_SECONDS >= 3 * TIMEOUT_SECONDS
