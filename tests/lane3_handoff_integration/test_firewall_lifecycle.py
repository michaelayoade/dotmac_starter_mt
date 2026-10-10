"""Design row "Firewall/lifecycle" (hosted disposable network namespace).

Real UID rules preserve unrelated traffic and default DROP; an atomic partial
failure leaves nothing; every crash point recovers through the rollback path;
grant-before-readback is impossible; expiry ends an ESTABLISHED connection
and all owned processes without any conntrack flush; the deadline never
resets.
"""

from __future__ import annotations

import os
import socket
import time

import lane3_handoff_controller as hc
import lane3_handoff_protocol as hp
import pytest
from conftest import (
    BOOTSTRAP_ADDRESS,
    FW,
    PLAIN_ADDRESS,
    Env,
    Servers,
    baseline_ok,
    owned_rules,
    sh,
)


def _wait_received(servers: Servers, address: str, timeout: float = 15) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if servers.received[address] > 0:
            return servers.received[address]
        time.sleep(0.05)
    raise AssertionError("no traffic observed")


def _grow(servers: Servers, address: str, seconds: float) -> int:
    before = servers.received[address]
    time.sleep(seconds)
    return servers.received[address] - before


def test_rules_are_exact_uid_scoped_and_baseline_is_preserved(env: Env) -> None:
    lease = env.start(["sleep", "20"])
    j = env.journal()
    assert j is not None
    rules = owned_rules()
    assert len(rules) == 1
    spec = j["rules"][0]
    assert spec["address"] == BOOTSTRAP_ADDRESS
    assert hc.is_exact_accept(rules[0], FW, lease, j["uid"], j["user"], spec)
    # The accept sits BEFORE the anchor; the baseline rules are unchanged.
    comments = [x["rule"].get("comment") for x in _chain() if "rule" in x]
    assert comments.index(rules[0]["comment"]) < comments.index(FW.anchor)
    # Root (unrelated) traffic keeps working throughout.
    with socket.create_connection((PLAIN_ADDRESS, 443), timeout=2):
        pass
    env.cleanup(lease)
    record = env.assert_clean(lease)
    assert record["outcome"] == "REFUSED"  # never consumed
    with socket.create_connection((PLAIN_ADDRESS, 443), timeout=2):
        pass


def _chain() -> list:
    import json

    return json.loads(sh("nft", "-j", "list", "chain", FW.family, FW.table, FW.chain))[
        "nftables"
    ]


def test_nft_transaction_is_atomic(env: Env) -> None:
    good = (
        f"insert rule {FW.family} {FW.table} {FW.chain} meta skuid 65000 "
        'ip daddr 192.0.2.99 tcp dport 443 ct state new counter accept comment "dotmac-lane3-handoff:'
        + "f" * 32
        + ':a:0"\n'
    )
    bad = f"add rule {FW.family} {FW.table} missing_chain counter\n"
    with pytest.raises(hc.ControllerRefused):
        hc.SystemHost().nft_apply(good + bad)
    assert owned_rules() == [] and baseline_ok()
    hc.SystemHost().nft_apply(good)  # sensitivity: the good line alone applies
    assert len(owned_rules()) == 1


def test_partial_install_failure_installs_nothing_and_cleans_up(env: Env) -> None:
    lease = env.prepare()
    env.host.corrupt_apply = True

    def restore() -> None:
        env.host.corrupt_apply = False

    env.host.on_checkpoint = {"cleanup.closing": restore}
    with pytest.raises(hc.ControllerRefused):
        env.bootstrap_lease(lease, ["sleep", "20"])
    assert env.host.applied, "an install was attempted"
    record = env.assert_clean(lease)
    assert record["outcome"] == "REFUSED"
    assert env.host.jobs == [], "no runner is launched after a failed install"


def test_grant_is_never_published_before_exact_readback(env: Env) -> None:
    lease = env.start(["report"])
    seen: dict[str, bool] = {}

    def corrupt() -> None:
        env.host.corrupt_readback = True

    def observe() -> None:
        seen["grant_file_at_install"] = (
            env.paths.run_root / lease / hp.GRANT_NAME
        ).exists()

    def restore() -> None:
        env.host.corrupt_readback = False

    env.host.on_checkpoint = {
        "grant.validated": corrupt,
        "install.applied": observe,
        "cleanup.closing": restore,
    }
    with pytest.raises(hc.ControllerRefused):
        env.grant(lease)
    assert seen == {"grant_file_at_install": False}
    record = env.assert_clean(lease)
    assert record["grant_digest"] is None and record["grant"] is None


def test_grant_requires_45_seconds_after_validation(env: Env) -> None:
    lease = env.start(["report"])
    with pytest.raises(hc.ControllerRefused):
        env.grant(lease, ttl_ms=40_000)
    record = env.assert_clean(lease)
    assert record["category"] == "deadline.insufficient"


def test_unlisted_origin_is_refused_by_the_controller_too(env: Env) -> None:
    env.install(origins=["https://other-broker.example"])
    env.approve()
    lease = env.start(["report"])
    with pytest.raises(hc.ControllerRefused):
        env.grant(lease)
    record = env.assert_clean(lease)
    assert record["category"] == "origin.not_admitted"


def test_binding_mismatch_against_the_job_report_refuses(env: Env) -> None:
    lease = env.start(["report"])
    with pytest.raises(hc.ControllerRefused):
        env.grant(lease, binding=env.binding(run_attempt=2))
    assert env.assert_clean(lease)["category"] == "binding.mismatch"


def test_expiry_ends_an_established_flow_and_needs_the_guard(
    env: Env, network: Servers
) -> None:
    root_flow = socket.create_connection((PLAIN_ADDRESS, 443), timeout=2)
    lease = env.start(["obtain", "hold", PLAIN_ADDRESS])
    env.grant(lease, [{"family": 4, "address": PLAIN_ADDRESS}])
    env.wait_state(lease, {"CONSUMED"})
    _wait_received(network, PLAIN_ADDRESS)
    assert _grow(network, PLAIN_ADDRESS, 0.5) > 0
    # Sensitivity: removing the NEW accepts alone does NOT end the flow,
    # because the baseline accepts ESTABLISHED traffic.
    for rule in owned_rules():
        sh(
            "nft",
            "delete",
            "rule",
            FW.family,
            FW.table,
            FW.chain,
            "handle",
            str(rule["handle"]),
        )
    assert _grow(network, PLAIN_ADDRESS, 0.6) > 0
    window: dict[str, int] = {}

    def measure() -> None:
        time.sleep(0.2)
        window["growth"] = _grow(network, PLAIN_ADDRESS, 0.8)
        window["job_alive"] = int(env.host.jobs[0].poll() is None)

    env.host.on_checkpoint = {"cleanup.guard": measure}
    unit, seconds, argv = env.host.timers[0]
    assert argv[-2:] == ["expire", lease] and seconds <= hp.WINDOW_NS // 10**9
    # The timer fired: run exactly what it runs (time is not advanced here so
    # the serve thread cannot race the timer to the same cleanup).
    result = env.controller().expire(argv[-1])
    assert result["evidence"]["outcome"] == "EXPIRED"
    assert window == {"growth": 0, "job_alive": 1}, window
    assert env.host.jobs[0].wait(10) is not None
    env.assert_clean(lease)
    # The unrelated established root flow survived: no conntrack flush.
    before = network.received[PLAIN_ADDRESS]
    root_flow.sendall(b"still-alive")
    time.sleep(0.3)
    assert network.received[PLAIN_ADDRESS] > before
    root_flow.close()


def test_deadline_is_fixed_once_and_ends_the_lease(env: Env) -> None:
    lease = env.start(["report"])
    expires = env.journal()["expires_at_monotonic_ns"]
    env.wait_state(lease, {"REPORTED"})
    assert env.journal()["expires_at_monotonic_ns"] == expires
    env.grant(lease)
    j = env.journal()
    assert j["expires_at_monotonic_ns"] == expires
    assert j["grant"]["expires_at_monotonic_ns"] <= expires
    assert expires - j["t0_monotonic_ns"] == hp.WINDOW_NS
    env.host.offset_ns = hp.WINDOW_NS
    try:
        status = env.controller().status(
            {"protocol": hp.PROTOCOL, "op": "status", "lease_id": lease}
        )
        assert status["state"] == "CLOSED"
    except hc.ControllerRefused as exc:
        # The socket server noticed the deadline first and already cleaned up.
        assert exc.label == "lease.unknown"
    env.wait_state(lease, {"CLOSED"})
    record = env.assert_clean(lease)
    assert record["outcome"] == "EXPIRED"


def test_the_timer_ends_only_its_own_lease(env: Env) -> None:
    lease = env.start(["sleep", "20"])
    absent = env.controller().expire("e" * 32)
    assert absent["state"] == "ABSENT"
    assert env.journal()["lease_id"] == lease
    env.cleanup(lease)
    env.assert_clean(lease)


def test_rollback_is_armed_before_any_rule_change(env: Env) -> None:
    lease = env.prepare()
    order: list[str] = []
    real_apply = env.host.nft_apply

    def apply(script: str) -> None:
        order.append("nft" if not env.host.timers else "nft-after-timer")
        real_apply(script)

    env.host.nft_apply = apply  # type: ignore[method-assign]
    env.bootstrap_lease(lease, ["sleep", "20"])
    assert order and order[0] == "nft-after-timer"
    env.cleanup(lease)
    env.assert_clean(lease)


CRASH_POINTS = [
    "prepare.journal",
    "prepare.identity",
    "prepare.lease_dir",
    "prepare.workspace",
    "bootstrap.planned",
    "bootstrap.armed",
    "install.applied@bootstrap",
    "bootstrap.recorded",
    "bootstrap.served",
    "bootstrap.challenge",
    "bootstrap.launched",
    "grant.validated",
    "install.applied@grant",
    "grant.recorded",
    "grant.published",
    "grant.saved",
    "cleanup.closing",
    "cleanup.guard",
    "cleanup.killed",
    "cleanup.rules",
    "cleanup.identity",
]


@pytest.mark.parametrize("point", CRASH_POINTS)
def test_every_crash_point_recovers_through_rollback(env: Env, point: str) -> None:
    name, _, phase = point.partition("@")
    lease = None if point.startswith("prepare.") else env.prepare()
    if phase == "grant" or point.startswith(("grant.", "cleanup.")):
        env.bootstrap_lease(lease, ["report"])
        env.wait_state(lease, {"REPORTED"})
    pid = os.fork()
    if pid == 0:  # child: run until the crash point, then die without cleanup
        try:
            env.host.crash_at = name
            env.host.jobs = []
            if point.startswith("prepare."):
                env.prepare()
            elif phase == "bootstrap" or point.startswith("bootstrap."):
                env.bootstrap_lease(lease, ["sleep", "20"])
            elif phase == "grant" or point.startswith("grant."):
                env.grant(lease)
            else:
                env.grant(lease)
                env.cleanup(lease)
        finally:
            os._exit(0)
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 17, "the crash point was not reached"
    j = env.journal()
    assert j is not None, "the journal must survive the crash"
    lease = j["lease_id"]
    # Rollback is the timer path: the pinned controller's expire operation.
    env.controller().expire(lease)
    env.assert_clean(lease)
    assert baseline_ok()
