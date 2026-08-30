"""Lane 3 gate item 5 — "the `docker-proxy` PID is NEW" — must actually bite.

A surviving pid means the container was never recreated, so the apply proved
nothing about the binding: the socket answering afterwards is the *same socket*
that answered before, and a wrong port mapping would look exactly as healthy as
a right one.

This item has a history. It was **dead**: `ObservedProxy` discarded the pid
entirely, so item 5 could only ever be closed by a human reading `ps` — which
is precisely the hand-measurement Lane 3 exists to eliminate. The capture was
fixed, but the decision built on it was never observed working, and
`scripts/exposure_rehearsal_runner.py` had no test of any kind.

That gap cannot be closed by running Lane 3: the lane is blocked on a Platform
CP issuer and has no registered runner. A unit test is the only thing that can
establish this gate bites at all, which is why `judge_proxy_recreation` was
extracted as a pure decision.

The case worth the most attention is the *no pid column* one. Comparing `None`
against `None` and calling the result "new" is how a check reports success for
having measured nothing — the same failure class as the seven IPv6
`DOCKER-USER` rules the Observability lane found on another host, every one of
them reading as containment while every port they named was open.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest
from dotmac_deployment_foundation.exposure import ObservedProxy
from dotmac_deployment_foundation.rehearsal import RequirementStatus

_RUNNER = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts"
    / "exposure_rehearsal_runner.py"
)


def _load():  # type: ignore[no-untyped-def]
    """Import the runner by path — it is a script, not an installed module."""
    spec = importlib.util.spec_from_file_location("_lane3_runner", _RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_lane3_runner"] = module
    spec.loader.exec_module(module)
    return module


judge = _load().judge_proxy_recreation


def _proxy(pid: int | None, *, host_port: int = 8003) -> ObservedProxy:
    return ObservedProxy(
        family="ipv4",
        host_ip="127.0.0.1",
        host_port=host_port,
        container_port=host_port,
        protocol="tcp",
        pid=pid,
    )


def test_every_new_pid_passes() -> None:
    """The positive control. Without it, refusing everything would score full marks."""
    status, detail = judge([_proxy(101)], [_proxy(202)])
    assert status is RequirementStatus.EXECUTED_PASSED
    assert "202" in detail and "101" in detail, (
        "the detail must record BOTH sets, so the receipt shows what was "
        "compared rather than asserting a conclusion"
    )


def test_a_surviving_pid_fails_and_is_named() -> None:
    """The core property: the container was not recreated."""
    status, detail = judge([_proxy(101)], [_proxy(101)])
    assert status is RequirementStatus.EXECUTED_FAILED
    assert "101" in detail
    assert "SURVIVED" in detail


def test_one_survivor_among_new_pids_still_fails() -> None:
    """A partial recreation is not a recreation — the survivor must dominate."""
    before = [_proxy(101), _proxy(102, host_port=9001)]
    after = [_proxy(101), _proxy(303, host_port=9001)]
    status, detail = judge(before, after)
    assert status is RequirementStatus.EXECUTED_FAILED
    assert "101" in detail


def test_a_listing_with_no_pid_column_is_blocked_not_passed() -> None:
    """`None` vs `None` must never be reported as "new".

    BLOCKED rather than FAILED because nothing was measured — and blocked rows
    do not satisfy publication, so this cannot quietly become a green gate.
    """
    status, detail = judge([_proxy(None)], [_proxy(None)])
    assert status is RequirementStatus.BLOCKED
    assert not status.satisfies_publication
    assert "no pid" in detail


def test_a_single_missing_pid_blocks_even_beside_good_ones() -> None:
    """One unmeasurable line makes the whole listing unable to answer item 5."""
    status, _ = judge([_proxy(101)], [_proxy(202), _proxy(None, host_port=9001)])
    assert status is RequirementStatus.BLOCKED


def test_no_proxy_at_all_fails() -> None:
    """Nothing is publishing the port — a different failure from a stale one."""
    status, detail = judge([_proxy(101)], [])
    assert status is RequirementStatus.EXECUTED_FAILED
    assert "no docker-proxy" in detail


def test_a_first_ever_apply_with_no_prior_proxy_passes() -> None:
    """An empty snapshot is not a survivor — nothing could have survived."""
    status, _ = judge([], [_proxy(202)])
    assert status is RequirementStatus.EXECUTED_PASSED


@pytest.mark.parametrize(
    "status",
    [RequirementStatus.EXECUTED_FAILED, RequirementStatus.BLOCKED],
)
def test_no_refusing_outcome_satisfies_publication(
    status: RequirementStatus,
) -> None:
    """The refusals must actually stop a release, not merely be recorded."""
    assert not status.satisfies_publication
