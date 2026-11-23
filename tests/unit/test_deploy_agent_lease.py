"""dotmac-deploy-agent: host lease client and the read-only reconcile observation."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotmac_deploy_agent.commands import CommandResult, ReadOnlyCommands
from dotmac_deploy_agent.lease import Lease, LeaseClient
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


def expired_host(double: DeployLeaseDouble, clock: LeaseClock) -> None:
    """A previous run's lease ran out unreleased: standing `expired_unreleased`."""
    double.acquire({"dispatch_run": "old-run", "ttl_seconds": 120})
    clock.advance(121)
    assert double.standing() == "expired_unreleased"


def test_reconcile_observation_read_only(tmp_path: Path) -> None:
    rig, double, clock = with_double(tmp_path)
    expired_host(double, clock)
    rig.commands.script[("image", "inspect")] = CommandResult(
        0, b'["127.0.0.1:5000/org/app@sha256:' + b"a" * 64 + b'", "not a digest"]\n'
    )
    rig.commands.script[("inspect",)] = CommandResult(
        0,
        b"127.0.0.1:5000/org/app@sha256:" + b"a" * 64 + b" sha256:" + b"f" * 64 + b"\n",
    )
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
    # Every command was an inspection verb.
    assert rig.commands.calls
    for argv in rig.commands.calls:
        assert argv[1] in {"inspect", "ps"} or argv[1:3] == ("image", "inspect")
    assert reconcile["kind"] == "reconcile"
    details = reconcile["details"]
    assert details["standing"] == "expired_unreleased"  # type: ignore[index]
    assert details["containers"] == [  # type: ignore[index]
        {
            "name": "erp-app",
            "image_ref": "127.0.0.1:5000/org/app@sha256:" + "a" * 64,
            "image_id": "sha256:" + "f" * 64,
            "repo_digests": ["127.0.0.1:5000/org/app@sha256:" + "a" * 64],
        }
    ]


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
    rig, double, clock = with_double(tmp_path)
    expired_host(double, clock)
    rig.commands.script[("inspect",)] = CommandResult(1, b"")
    rig.launcher.run_once()
    assert rig.cp.observations[0]["kind"] == "reconcile"
    assert rig.cp.observations[0]["details"]["containers"] == []  # type: ignore[index]


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
    # The standing stays `expired_unreleased` until acquire succeeds: observe again,
    # then the acquire goes through and the deploy completes.
    assert rig.launcher.run_once().outcome == "completed"
    assert [o["kind"] for o in rig.cp.observations] == ["reconcile", "deploy"]
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
    assert again.ttl_seconds == 900  # v2: the run's own lease, with its first ttl


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
    assert not hasattr(LeaseClient, "renew")  # s2.6.2 has no renew endpoint


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


def test_reconcile_keeps_only_well_formed_repo_digests(tmp_path: Path) -> None:
    rig = ready(tmp_path)
    rig.cp.standing = "expired_unreleased"
    rig.commands.script[("image", "inspect")] = CommandResult(
        0, b'["x", "bad@sha256:zz", "r.example/a@sha256:' + b"c" * 64 + b'"]\n'
    )
    rig.commands.script[("inspect",)] = CommandResult(
        0, b"r.example/a:tag sha256:" + b"f" * 64
    )
    rig.launcher.run_once()
    [container] = rig.cp.observations[0]["details"]["containers"]  # type: ignore[index]
    assert container["repo_digests"] == ["r.example/a@sha256:" + "c" * 64]


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
    lease = client.acquire(dispatch_id="d", ttl_seconds=60)
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


@pytest.mark.parametrize(
    ("running", "last_pin", "expected"),
    [
        (b"", None, "not_needed"),  # nothing of the stack runs
        (
            b"127.0.0.1:5000/org/app@sha256:" + b"a" * 64 + b" sha256:" + b"f" * 64,
            "127.0.0.1:5000/org/app@sha256:" + "a" * 64,
            "reconciled",
        ),
        (
            b"127.0.0.1:5000/org/app@sha256:" + b"b" * 64 + b" sha256:" + b"f" * 64,
            "127.0.0.1:5000/org/app@sha256:" + "a" * 64,
            "failed",
        ),
        (
            b"127.0.0.1:5000/org/app@sha256:" + b"a" * 64 + b" sha256:" + b"f" * 64,
            None,
            "failed",
        ),
    ],
)
def test_reconcile_reports_control_s_outcome_words(
    tmp_path: Path, running: bytes, last_pin: str | None, expected: str
) -> None:
    rig, double, clock = with_double(tmp_path)
    expired_host(double, clock)
    rig.commands.script[("inspect",)] = CommandResult(0 if running else 1, running)
    if last_pin:
        (rig.config.state_dir / "last_app_image").write_text(last_pin)
    rig.launcher.run_once()
    assert rig.cp.observations[0]["kind"] == "reconcile"
    assert rig.cp.observations[0]["outcome"] == expected
