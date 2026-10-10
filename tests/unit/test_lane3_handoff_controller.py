"""Controller logic canaries that need no root: the job-socket state machine,
exact rule recognition, the management schema and the evidence allowlist.

Root-only behaviour (real sockets, nftables, UIDs, cgroups, crash recovery) is
in ``tests/lane3_handoff_integration`` on hosted Linux. Synthetic values only.
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import lane3_handoff_controller as hc  # noqa: E402
import lane3_handoff_protocol as hp  # noqa: E402

LEASE = "1" * 32
NONCE = "2" * 64
SHA = "3" * 40
ORIGIN = "https://broker.example"
PEER = (4242, 777)
FW = hc.Firewall("inet", "synthetic_egress", "output", "synthetic-anchor")
UID = 61234
USER = "l3h-111111111111"


def journal(state: str = "BOOTSTRAP") -> dict[str, Any]:
    return {
        "lease_id": LEASE,
        "nonce": NONCE,
        "state": state,
        "history": [],
        "category": None,
        "expires_at_monotonic_ns": 300 * 10**9,
        "launched_at_monotonic_ns": 10**9,
        "report": None,
        "report_digest": None,
        "peer": None,
        "grant": None,
        "grant_digest": None,
    }


def report(**changes: Any) -> dict[str, Any]:
    value = {
        "protocol": hp.PROTOCOL,
        "op": "report",
        "lease_id": LEASE,
        "nonce": NONCE,
        "origin": ORIGIN,
        "flags": {"explicit_port_present": False, "userinfo_present": False},
        "expected": {
            "run_id": 5,
            "run_attempt": 1,
            "workflow_sha": SHA,
            "starter_commit": SHA,
        },
    }
    value.update(changes)
    return value


def poll(**changes: Any) -> dict[str, Any]:
    value = {"protocol": hp.PROTOCOL, "op": "poll", "lease_id": LEASE, "nonce": NONCE}
    value.update(changes)
    return value


def consume(digest: str, **changes: Any) -> dict[str, Any]:
    value = {**poll(), "op": "consume", "grant_digest": digest}
    value.update(changes)
    return value


def reported() -> dict[str, Any]:
    j = journal()
    reply, invalidate = hc.transition(j, report(), PEER, 2 * 10**9)
    assert reply["status"] == "WAIT" and not invalidate
    return j


def granted() -> dict[str, Any]:
    j = reported()
    j["state"] = "GRANTED"
    j["grant"] = {"expires_at_monotonic_ns": 200 * 10**9}
    j["grant_digest"] = "9" * 64
    return j


# ── state machine ──────────────────────────────────────────────────────────


def test_positive_report_poll_consume_path() -> None:
    j = reported()
    assert j["state"] == "REPORTED" and j["peer"] == list(PEER)
    assert hc.transition(j, poll(), PEER, 3 * 10**9)[0]["status"] == "WAIT"
    j = granted()
    assert hc.transition(j, poll(), PEER, 3 * 10**9)[0]["status"] == "GRANTED"
    reply, invalidate = hc.transition(j, consume("9" * 64), PEER, 4 * 10**9)
    assert reply["status"] == "CONSUMED" and not invalidate
    assert j["state"] == "CONSUMED"


def test_duplicate_exact_report_returns_recorded_status() -> None:
    j = reported()
    reply, invalidate = hc.transition(j, report(), PEER, 3 * 10**9)
    assert reply["status"] == "WAIT" and not invalidate


@pytest.mark.parametrize(
    ("make", "request_", "peer", "now", "label"),
    [
        (journal, report(nonce="f" * 64), PEER, 2 * 10**9, "nonce.mismatch"),
        (journal, report(lease_id="f" * 32), PEER, 2 * 10**9, "lease.unknown"),
        (journal, report(), PEER, 300 * 10**9, "lease.expired"),
        (journal, report(), PEER, 47 * 10**9, "deadline.insufficient"),
        (
            journal,
            report(flags={"explicit_port_present": True, "userinfo_present": False}),
            PEER,
            2 * 10**9,
            "origin.flags",
        ),
        (
            journal,
            report(flags={"explicit_port_present": False, "userinfo_present": True}),
            PEER,
            2 * 10**9,
            "origin.flags",
        ),
        (journal, poll(), PEER, 2 * 10**9, "peer.refused"),
        (
            reported,
            report(origin="https://other.example"),
            PEER,
            3 * 10**9,
            "report.changed",
        ),
        (reported, report(), (4242, 778), 3 * 10**9, "report.changed"),
        (reported, poll(), (4243, 777), 3 * 10**9, "peer.refused"),
        (reported, poll(), (4242, 778), 3 * 10**9, "peer.refused"),
        (reported, consume("9" * 64), PEER, 3 * 10**9, "state.invalid"),
        (granted, consume("8" * 64), PEER, 3 * 10**9, "grant.mismatch"),
        (granted, consume("9" * 64), PEER, 250 * 10**9, "lease.expired"),
        (lambda: journal("PREPARED"), report(), PEER, 2 * 10**9, "state.invalid"),
    ],
)
def test_each_binding_independently_refuses_and_ends_the_lease(
    make: Any, request_: dict[str, Any], peer: tuple[int, int], now: int, label: str
) -> None:
    j = make()
    reply, invalidate = hc.transition(j, request_, peer, now)
    assert reply == hp.response("REFUSED", label)
    assert invalidate is True
    assert j["state"] in ("REFUSED", "EXPIRED")
    assert j["category"] == label


def test_second_consume_refuses_and_never_issues_a_second_grant() -> None:
    j = granted()
    assert (
        hc.transition(j, consume("9" * 64), PEER, 4 * 10**9)[0]["status"] == "CONSUMED"
    )
    reply, invalidate = hc.transition(j, consume("9" * 64), PEER, 5 * 10**9)
    assert reply == hp.response("REFUSED", "grant.consumed") and invalidate
    assert hc.transition(j, poll(), PEER, 6 * 10**9)[0]["status"] == "REFUSED"


def test_terminal_lease_never_regains_authority() -> None:
    j = granted()
    j["state"], j["category"] = "EXPIRED", "lease.expired"
    for request_ in (report(), poll(), consume("9" * 64)):
        reply, _ = hc.transition(j, request_, PEER, 4 * 10**9)
        assert reply["status"] == "REFUSED"
    assert j["state"] == "EXPIRED"


# ── nftables rule recognition ──────────────────────────────────────────────


def accept_rule(handle: int = 10, **override: Any) -> dict[str, Any]:
    rule = {
        "family": "inet",
        "table": FW.table,
        "chain": FW.chain,
        "handle": handle,
        "comment": hc.comment_for(LEASE, "a", 0),
        "expr": [
            {"match": {"op": "==", "left": {"meta": {"key": "skuid"}}, "right": UID}},
            {
                "match": {
                    "op": "==",
                    "left": {"payload": {"protocol": "ip", "field": "daddr"}},
                    "right": "192.0.2.10",
                }
            },
            {
                "match": {
                    "op": "==",
                    "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                    "right": 443,
                }
            },
            {"match": {"op": "in", "left": {"ct": {"key": "state"}}, "right": "new"}},
            {"counter": {"packets": 0, "bytes": 0}},
            {"accept": None},
        ],
    }
    rule.update(override)
    return rule


SPEC = {"kind": "a", "index": 0, "family": 4, "address": "192.0.2.10", "port": 443}


def guard_rule(handle: int = 20) -> dict[str, Any]:
    return {
        "family": "inet",
        "table": FW.table,
        "chain": FW.chain,
        "handle": handle,
        "comment": hc.comment_for(LEASE, "x"),
        "expr": [
            {"match": {"op": "==", "left": {"meta": {"key": "skuid"}}, "right": UID}},
            {"counter": {"packets": 1, "bytes": 2}},
            {"drop": None},
        ],
    }


def test_exact_accept_is_recognised_by_uid_or_user_name() -> None:
    assert hc.is_exact_accept(accept_rule(), FW, LEASE, UID, USER, SPEC)
    named = accept_rule()
    named["expr"][0]["match"]["right"] = USER
    assert hc.is_exact_accept(named, FW, LEASE, UID, USER, SPEC)


@pytest.mark.parametrize(
    "weaken",
    [
        lambda r: r["expr"].pop(0),  # no UID match
        lambda r: r["expr"][0]["match"].update(right=UID + 1),
        lambda r: r["expr"][1]["match"].update(right="192.0.2.11"),
        lambda r: r["expr"][2]["match"].update(right=8443),
        lambda r: r["expr"].pop(3),  # no ct state new
        lambda r: r["expr"][3]["match"].update(right=["new", "established"]),
        lambda r: r["expr"].insert(
            0, {"match": {"op": "==", "left": {"meta": {"key": "oif"}}, "right": "lo"}}
        ),
        lambda r: r["expr"].__setitem__(5, {"drop": None}),
        lambda r: r.update(comment=hc.comment_for("f" * 32, "a", 0)),
        lambda r: r.update(table="other"),
    ],
)
def test_any_weakened_or_foreign_rule_is_not_exact(weaken: Any) -> None:
    rule = accept_rule()
    weaken(rule)
    assert not hc.is_exact_accept(rule, FW, LEASE, UID, USER, SPEC)


def test_classify_owned_deletes_only_exact_rules_and_refuses_drift() -> None:
    unrelated = {
        "family": "inet",
        "table": FW.table,
        "chain": FW.chain,
        "handle": 3,
        "comment": "keep",
        "expr": [],
    }
    handles, guard = hc.classify_owned(
        [unrelated, guard_rule(), accept_rule()], FW, LEASE, UID, USER, [SPEC], True
    )
    assert sorted(handles) == [10, 20] and guard
    drifted = accept_rule(11)
    drifted["expr"][1]["match"]["right"] = "198.51.100.1"
    for rules in (
        [accept_rule(), drifted],
        [accept_rule(), accept_rule(12)],
        [accept_rule(handle=None)],
        [accept_rule(comment=hc.comment_for("f" * 32, "a", 0))],
    ):
        with pytest.raises(hc.ControllerRefused) as caught:
            hc.classify_owned(rules, FW, LEASE, UID, USER, [SPEC], True)
        assert caught.value.label == "cleanup.drift"


def test_rendered_rules_carry_only_validated_tokens() -> None:
    line = hc.accept_line(FW, LEASE, UID, SPEC, 7)
    assert line == (
        "insert rule inet synthetic_egress output position 7 meta skuid 61234 "
        "ip daddr 192.0.2.10 tcp dport 443 ct state new counter accept "
        f'comment "dotmac-lane3-handoff:{LEASE}:a:0"'
    )
    guard = hc.guard_line(FW, LEASE, UID)
    assert "position" not in guard and guard.endswith(f':{LEASE}:x"')
    for bad in (
        lambda: hc.accept_line(FW, LEASE, 0, SPEC, 7),
        lambda: hc.accept_line(
            FW, LEASE, UID, {**SPEC, "address": "192.0.2.10; flush ruleset"}, 7
        ),
        lambda: hc.accept_line(FW, LEASE, UID, {**SPEC, "port": 22}, 7),
        lambda: hc.accept_line(
            hc.Firewall("inet", "t;x", "output", "a"), LEASE, UID, SPEC, 7
        ),
        lambda: hc.delete_line(FW, 0),
    ):
        with pytest.raises((hc.ControllerRefused, hp.ProtocolRefused)):
            bad()


def test_stable_digest_ignores_handles_and_counters_only() -> None:
    a = [accept_rule(10)]
    b = [accept_rule(99)]
    b[0]["expr"][4] = {"counter": {"packets": 5, "bytes": 900}}
    assert hc.stable_digest(a) == hc.stable_digest(b)
    c = copy.deepcopy(a)
    c[0]["comment"] = "changed"
    assert hc.stable_digest(a) != hc.stable_digest(c)


# ── manifests and the management schema ───────────────────────────────────


def docs(address: Any) -> bool:
    return address.is_private is False or str(address).startswith(
        ("192.0.2.", "2001:db8")
    )


def bootstrap(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema": hc.BOOTSTRAP_SCHEMA,
        "destinations": rows
        or [
            {
                "purpose": "runner-https",
                "origin": "https://runner.example",
                "family": 4,
                "address": "192.0.2.20",
                "port": 443,
            }
        ],
    }


def test_bootstrap_manifest_rules() -> None:
    assert hc.validate_bootstrap(bootstrap(), docs)
    row = bootstrap()["destinations"][0]
    for bad in (
        [{**row, "purpose": "oidc-https"}],
        [{**row, "port": 22}],
        [{**row, "origin": "https://runner.example:443"}],
        [row, {**row, "purpose": "module-https"}],
        [{**row, "address": "192.0.2.20", "family": 6}],
    ):
        with pytest.raises(hc.ControllerRefused):
            hc.validate_bootstrap(bootstrap(bad), docs)
    with pytest.raises(hc.ControllerRefused):
        hc.validate_bootstrap(bootstrap(), hc.hr.globally_routable)


def test_management_schema_is_fixed_data_only() -> None:
    good = {"protocol": hp.PROTOCOL, "op": "status", "lease_id": LEASE}
    assert hc.validate_mgmt(dict(good))
    for bad in (
        {**good, "path": "/etc/x"},
        {**good, "op": "shell"},
        {**good, "lease_id": "../" + LEASE[3:]},
        {
            "protocol": hp.PROTOCOL,
            "op": "bootstrap",
            "lease_id": LEASE,
            "jit_config": "a b",
        },
        {
            "protocol": hp.PROTOCOL,
            "op": "refuse",
            "lease_id": LEASE,
            "category": "free text",
        },
    ):
        with pytest.raises(hc.ControllerRefused):
            hc.validate_mgmt(bad)


def test_main_refuses_without_root_or_with_unknown_operations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for argv in (["c", "prepare"], ["c", "serve", "../x"], ["c", "sh"], ["c"]):
        assert hc.main(argv) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["category"] == "operation.invalid"


# ── evidence allowlist ─────────────────────────────────────────────────────

CANARIES = (
    "CANARY-request-token-7f3a",
    "CANARY-jit-config-91bd",
    "CANARY-url-path-query",
    "CANARY-exception-text",
    "192.0.2.77",
)


def test_evidence_projection_carries_no_canary() -> None:
    j = granted()
    j.update(
        outcome="EXPIRED",
        policy_digest="a" * 64,
        controller_digest="b" * 64,
        bootstrap_manifest_digest="c" * 64,
        manifest_digest="d" * 64,
        t0_monotonic_ns=0,
        history=[{"state": "BOOTSTRAP", "at_monotonic_ns": 10**9}],
        cleanup={
            key: False
            for key in (
                "grant_revoked",
                "guard_installed",
                "processes_absent",
                "sockets_absent",
                "owned_rules_absent",
                "baseline_preserved",
                "default_drop_preserved",
                "lease_files_absent",
                "workspace_absent",
                "identity_absent",
                "timer_stopped",
                "global_conntrack_flushed",
            )
        },
        cleanup_blocked=None,
        user="CANARY-exception-text",
        uid=UID,
        workspace="/run/CANARY-url-path-query",
        bootstrap_manifest={"destinations": [{"address": "192.0.2.77"}]},
        jit="CANARY-jit-config-91bd",
        env={"ACTIONS_ID_TOKEN_REQUEST_TOKEN": "CANARY-request-token-7f3a"},
    )
    j["grant"] = {
        "binding": {
            "repository_id": 9,
            "run_id": 5,
            "run_attempt": 1,
            "job_id": 3,
            "runner_id": 4,
            "workflow_sha": SHA,
            "workflow_blob": SHA,
            "starter_commit": SHA,
            "admission_digest": "a" * 64,
            "supplier_digest": "b" * 64,
        },
        "snapshot": {"digest": "e" * 64, "addresses": [{"address": "192.0.2.77"}]},
    }
    public = json.dumps(hc.evidence(j))
    for canary in CANARIES:
        assert canary not in public
    projected = hc.evidence(j)
    assert projected["gate0_accepted"] is False
    assert projected["snapshot_digest"] == "e" * 64
    assert projected["transitions"] == [{"state": "BOOTSTRAP", "offset_ms": 1000}]
    # Sensitivity: the canary IS present in the private journal.
    assert all(c in json.dumps(j) for c in CANARIES)


def test_refusal_output_is_a_fixed_label() -> None:
    exc = hc.ControllerRefused("cleanup.drift")
    assert str(exc) == "cleanup.drift"
    assert hc.category_of(exc) == "internal"
    assert hc.category_of(hc.ControllerRefused("lease.expired")) == "lease.expired"
    assert hc.category_of(ValueError("CANARY-exception-text")) == "internal"


def test_timing_ticket_subtracts_full_host_elapsed_and_never_revives() -> None:
    j = {"timing": {"challenge": "a" * 64, "issued_at_monotonic_ns": 10**9}}
    snapshot = {"challenge": "a" * 64, "ttl_remaining_ms": 60_000}
    assert hc.snapshot_deadline(j, snapshot, 11 * 10**9) == 61 * 10**9
    assert hc.snapshot_deadline(j, snapshot, 71 * 10**9) < 71 * 10**9
    with pytest.raises(hc.ControllerRefused):
        hc.snapshot_deadline(j, {**snapshot, "challenge": "b" * 64}, 2 * 10**9)


def test_transit_canary_detects_weakened_receipt_time_formula(monkeypatch: Any) -> None:
    j = {"timing": {"challenge": "a" * 64, "issued_at_monotonic_ns": 10**9}}
    snapshot = {"challenge": "a" * 64, "ttl_remaining_ms": 60_000}

    def canary() -> None:
        assert hc.snapshot_deadline(j, snapshot, 71 * 10**9) < 71 * 10**9

    canary()
    monkeypatch.setattr(
        hc, "snapshot_deadline", lambda j, s, now: now + s["ttl_remaining_ms"] * 10**6
    )
    with pytest.raises(AssertionError):
        canary()


def test_transport_start_is_persisted_as_unknown_and_proof_is_distinct() -> None:
    j = granted()
    j["state"] = "CONSUMED"
    started = {**poll(), "op": "token-started"}
    assert hc.transition(j, started, PEER, 5 * 10**9)[0]["status"] == "CONSUMED"
    assert j["token_request_started"] is True and j["issuance"] == "UNKNOWN"
    assert (
        hc.transition(
            j, {**poll(), "op": "proof", "outcome": "BOUNDED_PROOF"}, PEER, 6 * 10**9
        )[0]["status"]
        == "CONSUMED"
    )
    assert j["proof_outcome"] == "BOUNDED_PROOF" and j["issuance"] == "UNKNOWN"


def cleanup_journal() -> dict[str, Any]:
    j = journal("GRANTED")
    j.update(
        outcome=None,
        closing=False,
        cleanup_blocked=None,
        user=USER,
        uid=UID,
        gid=UID,
        identity_intent=hc.identity_owner(LEASE),
        workspace="/run/synthetic-workspace",
        cgroup="/system.slice/synthetic.service",
        units={
            "runner": "synthetic-runner",
            "serve": "synthetic-serve",
            "expire": "synthetic-expire",
        },
        firewall={
            "family": FW.family,
            "table": FW.table,
            "chain": FW.chain,
            "anchor": FW.anchor,
        },
        rules=[SPEC],
        planned=[],
        baseline_digest=hc.stable_digest([{"comment": "original"}]),
        timer_armed=True,
    )
    return j


def test_cleanup_baseline_drift_retains_guard_and_account(monkeypatch: Any) -> None:
    effects: list[Any] = []

    class Host:
        now_ns = staticmethod(lambda: 10**9)
        identity = staticmethod(lambda user: (UID, UID))
        stop_unit = staticmethod(lambda unit: None)
        kill_unit = staticmethod(lambda unit: None)
        kill_cgroup = staticmethod(lambda group: None)
        nft_apply = staticmethod(lambda script: effects.append(script))

    controller = hc.Controller(host=Host())
    monkeypatch.setattr(controller, "save", lambda j: None)
    monkeypatch.setattr(controller, "_remove_lease_dir", lambda *args, **kw: None)
    monkeypatch.setattr(controller, "_await_quiet", lambda j: None)
    monkeypatch.setattr(controller, "_ensure_guard", lambda *args: True)
    monkeypatch.setattr(
        controller,
        "chain",
        lambda fw: ([guard_rule(), accept_rule(), {"comment": "drift"}], 1),
    )
    j = cleanup_journal()
    with pytest.raises(hc.ControllerRefused, match="cleanup.blocked"):
        controller._cleanup_locked(j, "EXPIRED", "lease.expired")
    assert effects == []  # guard was not deleted; identity remains reserved
    assert j["cleanup_blocked"] == "cleanup.baseline"
    assert j["cleanup"]["guard_installed"] is True


def test_identity_created_before_uid_journal_recovers_exact_intent(
    monkeypatch: Any,
) -> None:
    class Host:
        now_ns = staticmethod(lambda: 10**9)
        identity = staticmethod(lambda user: (UID, UID))
        identity_matches_intent = staticmethod(
            lambda user, home, owner: owner == hc.TAG + ":" + LEASE
        )
        unit_cgroup = staticmethod(lambda unit: "/system.slice/" + unit + ".service")
        stop_unit = staticmethod(lambda unit: None)

    controller = hc.Controller(host=Host())
    monkeypatch.setattr(controller, "save", lambda j: None)
    monkeypatch.setattr(controller, "_remove_lease_dir", lambda *args, **kw: None)
    monkeypatch.setattr(controller, "_ensure_guard", lambda *args: False)
    j = cleanup_journal()
    j.update(uid=None, gid=None, cgroup=None)
    with pytest.raises(hc.ControllerRefused, match="cleanup.blocked"):
        controller._cleanup_locked(j, "EXPIRED", "lease.expired")
    assert (j["uid"], j["gid"]) == (UID, UID)
    j = cleanup_journal()
    j.update(uid=None, gid=None)
    monkeypatch.setattr(controller.host, "identity_matches_intent", lambda *args: False)
    with pytest.raises(hc.ControllerRefused, match="cleanup.blocked"):
        controller._cleanup_locked(j, "EXPIRED", "lease.expired")
    assert j["uid"] is None and j["cleanup_blocked"] == "cleanup.identity"


def test_identity_owner_is_safe_for_passwd_gecos_and_keeps_full_lease() -> None:
    owner = hc.identity_owner(LEASE)
    assert owner == hc.TAG + "-" + LEASE
    assert not any(character in owner for character in ":\n\r")
    assert owner != hc.identity_owner("f" * 32)


def test_systemd_expiry_retries_lock_collision_and_stop_needs_readback(
    monkeypatch: Any,
) -> None:
    calls: list[Any] = []
    host = hc.SystemHost()

    def run(argv: list[str], **kw: Any) -> bytes:
        calls.append(argv)
        return b"active\n" if "show" in argv else b""

    monkeypatch.setattr(host, "_run", run)
    host.arm_timer("synthetic-expiry", 1, ["/usr/bin/true"])
    assert "--property=Restart=on-failure" in calls[0]
    assert "--property=StartLimitIntervalSec=0" in calls[0]
    with pytest.raises(hc.ControllerRefused, match="cleanup.timer"):
        host.disarm_timer("synthetic-expiry")
    with pytest.raises(hc.ControllerRefused, match="cleanup.timer"):
        host.stop_unit("synthetic-serve")


@pytest.mark.parametrize("operation", ["stop_unit", "disarm_timer"])
def test_failed_stop_of_absent_unit_requires_successful_independent_readback(
    monkeypatch: Any, operation: str
) -> None:
    host = hc.SystemHost()
    calls: list[Any] = []

    def run(argv: list[str], **kw: Any) -> bytes:
        calls.append((argv, kw))
        if "stop" in argv:
            assert kw["check"] is False  # missing/collected units return nonzero
            return b""
        assert kw.get("check", True) is True
        return b"LoadState=not-found\nActiveState=inactive\n"

    monkeypatch.setattr(host, "_run", run)
    getattr(host, operation)("synthetic-missing")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "readback",
    [b"", b"LoadState=loaded\nActiveState=active\n", b"LoadState=not-found\n"],
)
def test_stop_cannot_turn_failed_or_ambiguous_readback_into_absence(
    monkeypatch: Any, readback: bytes
) -> None:
    host = hc.SystemHost()
    monkeypatch.setattr(
        host, "_run", lambda argv, **kw: readback if "show" in argv else b""
    )
    with pytest.raises(hc.ControllerRefused, match="cleanup.timer"):
        host.disarm_timer("synthetic-missing")


def test_stop_readback_command_failure_refuses(monkeypatch: Any) -> None:
    host = hc.SystemHost()

    def run(argv: list[str], **kw: Any) -> bytes:
        if "show" in argv:
            raise hc.ControllerRefused("host.command")
        return b""

    monkeypatch.setattr(host, "_run", run)
    with pytest.raises(hc.ControllerRefused, match="cleanup.timer"):
        host.stop_unit("synthetic-missing")


def test_cleanup_returns_exact_closed_archive_without_mutating_new_lease(
    monkeypatch: Any,
) -> None:
    import contextlib

    j = cleanup_journal()
    j.update(
        schema=hc.JOURNAL_SCHEMA,
        protocol=hp.PROTOCOL,
        state="CLOSED",
        outcome="EXPIRED",
        cleanup_blocked=None,
    )
    j["cleanup"] = {
        key: True
        for key in (
            "grant_revoked",
            "guard_installed",
            "processes_absent",
            "sockets_absent",
            "owned_rules_absent",
            "baseline_preserved",
            "default_drop_preserved",
            "lease_files_absent",
            "workspace_absent",
            "identity_absent",
            "timer_stopped",
        )
    }
    j["cleanup"]["global_conntrack_flushed"] = False
    controller = hc.Controller()
    monkeypatch.setattr(controller, "locked", lambda: contextlib.nullcontext())
    monkeypatch.setattr(hc, "read_protected", lambda path, **kw: hp.canonical_json(j))
    monkeypatch.setattr(
        controller,
        "_cleanup_locked",
        lambda *args: pytest.fail("archived receipt must not mutate active lease"),
    )
    for active in (None, {"lease_id": "f" * 32, "state": "BOOTSTRAP"}):
        monkeypatch.setattr(controller, "load", lambda active=active: active)
        reply = controller.cleanup(
            {"protocol": hp.PROTOCOL, "op": "cleanup", "lease_id": LEASE}
        )
        assert reply["state"] == "CLOSED" and reply["lease_id"] == LEASE
        assert reply["evidence"]["cleanup"]["identity_absent"] is True
    j["lease_id"] = "e" * 32
    with pytest.raises(hc.ControllerRefused, match="journal.unsafe"):
        controller.cleanup(
            {"protocol": hp.PROTOCOL, "op": "cleanup", "lease_id": LEASE}
        )


@pytest.mark.parametrize("guard_failure", [False, "raises"])
def test_guard_failure_still_kills_owned_execution_before_blocking(
    monkeypatch: Any, guard_failure: Any
) -> None:
    effects: list[str] = []

    class Host:
        now_ns = staticmethod(lambda: 10**9)
        identity = staticmethod(lambda user: (UID, UID))
        stop_unit = staticmethod(lambda unit: None)
        kill_unit = staticmethod(lambda unit: effects.append("unit-killed"))
        kill_cgroup = staticmethod(lambda group: effects.append("cgroup-killed"))

    controller = hc.Controller(host=Host())
    monkeypatch.setattr(controller, "save", lambda j: None)
    monkeypatch.setattr(controller, "_remove_lease_dir", lambda *args, **kw: None)
    monkeypatch.setattr(
        controller, "_await_quiet", lambda j: effects.append("uid-quiet")
    )

    def guard(*args: Any) -> bool:
        if guard_failure == "raises":
            raise hc.ControllerRefused("firewall.readback")
        return False

    monkeypatch.setattr(controller, "_ensure_guard", guard)
    j = cleanup_journal()
    with pytest.raises(hc.ControllerRefused, match="cleanup.blocked"):
        controller._cleanup_locked(j, "EXPIRED", "lease.expired")
    assert effects == ["unit-killed", "cgroup-killed", "uid-quiet"]
    assert j["cleanup_blocked"] == "cleanup.drift"


def test_runtime_controller_drift_refuses_authority_but_cleanup_remains_available(
    monkeypatch: Any,
) -> None:
    import types

    controller = hc.Controller()
    cfg = types.SimpleNamespace(controller_digest="a" * 64)
    monkeypatch.setattr(hc, "controller_digest", lambda path: "b" * 64)
    with pytest.raises(hc.ControllerRefused, match="controller.digest"):
        controller._verify_controller({"controller_digest": "a" * 64}, cfg)
    monkeypatch.setattr(hc, "controller_digest", lambda path: "a" * 64)
    controller._verify_controller({"controller_digest": "a" * 64}, cfg)
    j = reported()
    controller.host = types.SimpleNamespace(now_ns=lambda: 3 * 10**9)
    monkeypatch.setattr(
        controller,
        "_verify_controller",
        lambda j: (_ for _ in ()).throw(hc.ControllerRefused("controller.digest")),
    )
    with pytest.raises(hc.ControllerRefused, match="controller.digest"):
        controller._grant_locked(j, {})
    assert j["state"] == "REPORTED" and j["grant"] is None


def test_timing_rejects_another_valid_operation_before_lock_or_save(
    monkeypatch: Any,
) -> None:
    controller = hc.Controller()
    monkeypatch.setattr(
        controller, "locked", lambda: pytest.fail("wrong op reached lock")
    )
    with pytest.raises(hc.ControllerRefused, match="schema.invalid"):
        controller.timing({"protocol": hp.PROTOCOL, "op": "status", "lease_id": LEASE})


def test_manifest_mismatch_is_rejected_before_rule_or_grant_authority(
    monkeypatch: Any,
) -> None:
    import types

    policy = hc.parse_policy(
        {
            "schema": hc.POLICY_SCHEMA,
            "version": 1,
            "origins": {
                "schema": "dotmac.lane3.broker-origin-policy.v1",
                "origins": [ORIGIN],
            },
            "aliases": {"exact": [], "namespaces": []},
        }
    )
    j = reported()
    qualification = {
        "repository_id": 9,
        "run_id": 5,
        "run_attempt": 1,
        "workflow_sha": SHA,
        "workflow_blob": SHA,
        "starter_commit": SHA,
        "environment_id": 7,
        "approval_digest": "a" * 64,
        "admission_digest": "b" * 64,
        "supplier_digest": "c" * 64,
    }
    binding = {
        key: qualification[key]
        for key in (
            "repository_id",
            "run_id",
            "run_attempt",
            "workflow_sha",
            "workflow_blob",
            "starter_commit",
            "admission_digest",
            "supplier_digest",
        )
    }
    binding.update(job_id=3, runner_id=4)
    j.update(
        qualification=qualification,
        policy_digest=policy.digest,
        bootstrap_manifest=bootstrap(),
        timing={"challenge": "d" * 64, "issued_at_monotonic_ns": 10**9},
    )
    controller = hc.Controller(
        host=types.SimpleNamespace(now_ns=lambda: 2 * 10**9), routable=docs
    )
    monkeypatch.setattr(controller, "_verify_controller", lambda j: None)
    monkeypatch.setattr(controller, "_verify_staged_supplier", lambda j: None)
    monkeypatch.setattr(controller, "policy", lambda: policy)
    monkeypatch.setattr(
        controller, "save", lambda j: pytest.fail("mismatch mutated authority journal")
    )
    monkeypatch.setattr(
        controller,
        "_write_lease_file",
        lambda *args: pytest.fail("mismatch published grant"),
    )
    with pytest.raises(hc.ControllerRefused, match="grant.mismatch"):
        controller._grant_locked(
            j,
            {
                "report_digest": j["report_digest"],
                "binding": binding,
                "qualification": qualification,
                "origin": ORIGIN,
                "policy_digest": policy.digest,
                "expected_manifest_digest": "0" * 64,
                "snapshot": {
                    "digest": "e" * 64,
                    "challenge": "d" * 64,
                    "ttl_remaining_ms": 60_000,
                    "addresses": [{"family": 4, "address": "192.0.2.10"}],
                },
            },
        )
    assert j["state"] == "REPORTED" and j["grant"] is None


def test_wireguard_broker_requires_consumed_peer_and_fresh_live_check(
    monkeypatch: Any,
) -> None:
    import types

    controller = hc.Controller(host=types.SimpleNamespace(now_ns=lambda: 3 * 10**9))
    calls: list[Any] = []
    monkeypatch.setattr(controller, "_verify_controller", lambda j: None)
    monkeypatch.setattr(
        controller,
        "_wireguard_live",
        lambda digest, deadline: calls.append((digest, deadline)),
    )
    request = {**poll(), "op": "wireguard-check", "config_digest": "a" * 64}
    for state in ("REPORTED", "GRANTED"):
        j = granted()
        j["state"] = state
        assert controller._wireguard_request(j, request, PEER, 3 * 10**9)[1] is True
    j = granted()
    j["state"] = "CONSUMED"
    assert (
        controller._wireguard_request(j, request, (PEER[0] + 1, PEER[1]), 3 * 10**9)[1]
        is True
    )
    for changes in ({"nonce": "f" * 64}, {"lease_id": "f" * 32}):
        j = granted()
        j["state"] = "CONSUMED"
        assert (
            controller._wireguard_request(j, {**request, **changes}, PEER, 3 * 10**9)[1]
            is True
        )
    assert calls == []
    j = granted()
    j["state"] = "CONSUMED"
    assert controller._wireguard_request(j, request, PEER, 3 * 10**9) == (
        hp.response("CONSUMED"),
        False,
    )
    assert controller._wireguard_request(j, request, PEER, 3 * 10**9) == (
        hp.response("CONSUMED"),
        False,
    )
    assert len(calls) == 2  # no cached route authority


@pytest.mark.parametrize("bad", [True, 1, None, "a" * 63, "A" * 64])
def test_wireguard_wire_schema_rejects_noncanonical_digest(bad: Any) -> None:
    with pytest.raises(hp.ProtocolRefused):
        hp.validate_request({**poll(), "op": "wireguard-check", "config_digest": bad})


def test_wireguard_broker_rechecks_deadline_after_metadata(monkeypatch: Any) -> None:
    import types

    now = 3 * 10**9
    controller = hc.Controller(host=types.SimpleNamespace(now_ns=lambda: now))

    def delayed(*args: Any) -> None:
        nonlocal now
        now = 301 * 10**9

    monkeypatch.setattr(controller, "_verify_controller", lambda j: None)
    monkeypatch.setattr(controller, "_wireguard_live", delayed)
    j = granted()
    j["state"] = "CONSUMED"
    reply, invalid = controller._wireguard_request(
        j,
        {**poll(), "op": "wireguard-check", "config_digest": "a" * 64},
        PEER,
        3 * 10**9,
    )
    assert invalid and reply == hp.response("REFUSED", "lease.expired")


def test_wireguard_live_uses_fixed_protected_config_and_full_fresh_root_guard(
    monkeypatch: Any,
) -> None:
    import base64
    import time
    import types

    import lane3_wireguard_topology as wg

    local = base64.b64encode(b"l" * 32).decode()
    peer = base64.b64encode(b"p" * 32).decode()
    config = {
        "endpoint_address": "192.0.2.10",
        "source_address": "192.0.2.11",
        "interface": "wgtest",
        "expected_local_public_key": local,
        "expected_peer_public_key": peer,
    }
    digest = wg.wireguard_config_digest(config)
    paths: list[Any] = []
    commands: list[Any] = []

    def read(path: Any, **kw: Any) -> bytes:
        paths.append(path)
        assert kw["exact_mode"] == 0o644
        return hp.canonical_json({**config, "oidc_broker_origin": ORIGIN})

    def command(argv: list[str], **kw: Any) -> bytes:
        commands.append(argv)
        assert "/usr/bin/sudo" not in argv and 0 < kw["timeout"] <= 1
        if argv[-1] == "public-key":
            return local.encode()
        if argv[-1] == "allowed-ips":
            return (peer + " 192.0.2.10/32").encode()
        if argv[-1] == "latest-handshakes":
            return (peer + " " + str(int(time.time()))).encode()
        if "address" in argv:
            return hp.canonical_json(
                [{"addr_info": [{"local": "192.0.2.11", "scope": "global"}]}]
            )
        return hp.canonical_json([{"dev": "wgtest", "from": "192.0.2.11"}])

    monkeypatch.setattr(hc, "read_protected", read)
    monkeypatch.setattr(wg.os, "geteuid", lambda: 0)
    controller = hc.Controller(
        host=types.SimpleNamespace(now_ns=lambda: 3 * 10**9, _run=command)
    )
    controller._wireguard_live(digest, 4 * 10**9)
    controller._wireguard_live(digest, 4 * 10**9)
    assert paths == [hc.WIREGUARD_CONFIG, hc.WIREGUARD_CONFIG] and len(commands) == 10
    commands.clear()
    with pytest.raises(hc.ControllerRefused, match="binding.mismatch"):
        controller._wireguard_live("f" * 64, 4 * 10**9)
    assert commands == []


def test_wireguard_budget_includes_request_and_lock_time(monkeypatch: Any) -> None:
    import types

    controller = hc.Controller(host=types.SimpleNamespace(now_ns=lambda: 7 * 10**9))
    monkeypatch.setattr(
        controller,
        "_verify_controller",
        lambda j: pytest.fail("exhausted budget verified or executed commands"),
    )
    j = granted()
    j["state"] = "CONSUMED"
    reply, invalid = controller._wireguard_request(
        j,
        {**poll(), "op": "wireguard-check", "config_digest": "a" * 64},
        PEER,
        7 * 10**9,
        operation_deadline_ns=6 * 10**9,
    )
    assert invalid and reply == hp.response("REFUSED", "deadline.insufficient")
