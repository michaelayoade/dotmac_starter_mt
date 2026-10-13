"""B8 SSH-only window invariants, with effects injected for hosted CI."""

from __future__ import annotations

import contextlib
import copy
import pathlib
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
import lane3_campaign_transport as t

DOC = {
    "scope": "b8-ssh-vm124",
    "runner_user": "fixture",
    "runner_uid": 65001,
    "runner_gid": 65001,
    "destinations": [
        {"purpose": p, "address": a, "port": 22}
        for p, a in zip(
            ("target", "jump", "probe"),
            ("192.0.2.1", "192.0.2.2", "192.0.2.3"),
            strict=True,
        )
    ],
}
BOOT = "01234567-89ab-cdef-0123-456789abcdef"


class Backend:
    def __init__(self):
        self.effects = []
        self.now = 1_000_000_000
        self.state = None
        self.rules = []
        self.deadline = None
        self.bad = ""
        self.lock = threading.Lock()
        self.guard_lock = threading.Lock()

    @contextlib.contextmanager
    def operation(self):
        with self.lock:
            yield

    def clock_ns(self):
        return self.now

    @contextlib.contextmanager
    def guard_operation(self):
        with self.guard_lock:
            yield

    def revocation(self, b):
        return None

    def processes_absent(self, uid):
        return self.bad != "processes"

    def guard(self, uid, comment):
        self.effects.append("guard")
        self.rules.insert(0, t.expected_guard(comment, uid, handle=100))

    def attest(self, b):
        if self.bad == "binding":
            raise t.Refused("binding-unproven")

    def owner_active(self, binding, manifest):
        return None

    def identity(self, user):
        return (65001, 65001)

    def chain(self):
        return (
            {"policy": "drop", "hook": "output"},
            [
                *copy.deepcopy(self.rules),
                {"handle": 1, "comment": t.ANCHOR, "expr": [{"drop": None}]},
            ],
        )

    def load(self):
        return copy.deepcopy(self.state)

    def save(self, state, create=False):
        if create and self.state is not None:
            raise t.Refused("already-started")
        self.state = copy.deepcopy(state)
        self.effects.append("save")

    def arm(self, deadline):
        self.effects.append("arm")
        self.deadline = deadline

    def timer_verified(self, deadline):
        return self.deadline == deadline and self.bad != "timer"

    def install(self, specs, uid, anchor):
        self.effects.append("install")
        self.rules.extend(
            [t.expected_rule(s, uid, handle=i + 2) for i, s in enumerate(specs)]
        )
        if self.bad == "lost":
            raise OSError("PRIVATE-CANARY")

    def delete(self, handles):
        self.effects.append("delete")
        if self.bad != "delete":
            self.rules = [r for r in self.rules if r["handle"] not in handles]

    def disarm(self):
        self.effects.append("disarm")
        self.deadline = None


def prepared():
    m = t.Manifest.parse(DOC)
    b = t.Binding("a" * 32, m.digest, "b" * 64, BOOT, "c" * 64, "d" * 64)
    h = Backend()
    return h, t.Transport(b, m, h)


@pytest.mark.parametrize(
    "change",
    [
        {"runner_uid": 0},
        {"runner_uid": True},
        {"runner_uid": 1.5},
        {"runner_gid": True},
        {"scope": "https"},
        {"runner_user": "root"},
    ],
)
def test_bad_manifest_identity_has_no_effects(change):
    doc = copy.deepcopy(DOC)
    doc.update(change)
    with pytest.raises(t.Refused):
        t.Manifest.parse(doc)


@pytest.mark.parametrize(
    "change",
    [
        {"address": "host.example"},
        {"address": "192.0.2.1/32"},
        {"address": "*"},
        {"port": 18443},
        {"port": 443},
        {"port": True},
        {"purpose": "https"},
    ],
)
def test_bad_destination_refuses(change):
    doc = copy.deepcopy(DOC)
    doc["destinations"][0].update(change)
    with pytest.raises(t.Refused):
        t.Manifest.parse(doc)


def test_arm_verified_before_accept_and_cleanup_matches_baseline():
    h, c = prepared()
    receipt = c.open()
    assert h.effects == ["save", "arm", "save", "save", "install", "save"]
    assert receipt.rule_count == 3
    result = c.cleanup()
    assert result.rule_count == 0
    assert h.effects[-3:] == ["delete", "save", "disarm"]
    assert h.rules == []


@pytest.mark.parametrize("bad", ["binding", "timer"])
def test_failed_binding_or_timer_never_installs(bad):
    h, c = prepared()
    h.bad = bad
    with pytest.raises(t.Refused):
        c.open()
    assert "install" not in h.effects


def test_late_uid_change_prevents_rules():
    h, c = prepared()
    h.identity = lambda user: (65002, 65001)
    with pytest.raises(t.Refused):
        c.open()
    assert "install" not in h.effects


def test_lost_open_response_reconciles_actual_owned_rules():
    h, c = prepared()
    h.bad = "lost"
    assert c.open().rule_count == 3
    assert c.cleanup().rule_count == 0


def test_foreign_expression_with_own_comment_is_never_deleted():
    h, c = prepared()
    c.open()
    h.rules[0]["expr"][0]["match"]["right"] = 65002
    with pytest.raises(t.Refused):
        c.cleanup()
    assert "delete" not in h.effects and h.deadline is not None


def test_persistent_delete_failure_preserves_timer():
    h, c = prepared()
    c.open()
    h.bad = "delete"
    with pytest.raises(t.Refused):
        c.cleanup()
    assert "disarm" not in h.effects and h.deadline is not None


def test_no_rearm_or_resume_even_after_cleanup():
    h, c = prepared()
    c.open()
    c.cleanup()
    with pytest.raises(t.Refused):
        t.Transport(c.binding, c.manifest, h).open()
    assert h.effects.count("arm") == 1


def test_foreign_baseline_drift_is_preserved_and_cleanup_unproven():
    h, c = prepared()
    c.open()
    original = h.chain

    def drift():
        chain, rules = original()
        rules.append({"handle": 99, "comment": "foreign", "expr": [{"drop": None}]})
        return chain, rules

    h.chain = drift
    with pytest.raises(t.Refused):
        c.cleanup()
    assert len(h.rules) == 1 and "disarm" not in h.effects


@pytest.mark.parametrize(
    "identity", [(0, 65001), (True, 65001), (65001, True), (65001.0, 65001)]
)
def test_late_identity_types_are_exact_and_nonroot(identity):
    h, c = prepared()
    h.identity = lambda user: identity
    with pytest.raises(t.Refused, match="identity-unproven"):
        c.open()
    assert h.effects == []


@pytest.mark.parametrize("kind", ["duplicate", "missing", "too-many"])
def test_each_purpose_count_and_unique_address(kind):
    doc = copy.deepcopy(DOC)
    if kind == "duplicate":
        doc["destinations"][1]["address"] = doc["destinations"][0]["address"]
    elif kind == "missing":
        doc["destinations"][1]["purpose"] = "target"
    else:
        doc["destinations"] *= 3
    with pytest.raises(t.Refused, match="manifest-invalid"):
        t.Manifest.parse(doc)


def test_durable_spec_drift_cannot_authorize_deletion():
    h, c = prepared()
    c.open()
    h.state["specs"][0]["address"] = "192.0.2.99"
    with pytest.raises(t.Refused, match="state-unproven"):
        c.cleanup()
    assert "delete" not in h.effects and "disarm" not in h.effects


def test_chain_structure_drift_prevents_false_baseline_restoration():
    h, c = prepared()
    c.open()
    original = h.chain

    def drift():
        chain, rules = original()
        chain["prio"] = 99
        return chain, rules

    h.chain = drift
    with pytest.raises(t.Refused, match="guarded-unproven"):
        c.cleanup()
    assert "disarm" not in h.effects


def test_installed_host_config_anchor_requires_exact_approved_bytes():
    import dataclasses
    import hashlib
    import json

    _, controller = prepared()
    doc = {
        "schema": "dotmac.lane3.handoff-host.v1",
        "firewall": {
            "family": "inet",
            "table": "dotmac_egress",
            "chain": "output",
            "anchor_comment": "dropped-egress",
        },
    }
    raw = json.dumps(doc).encode()
    binding = dataclasses.replace(
        controller.binding, host_config_sha256=hashlib.sha256(raw).hexdigest()
    )
    t.SystemBackend.validate_host_config(raw, binding)
    with pytest.raises(t.Refused, match="host-config-unproven"):
        t.SystemBackend.validate_host_config(raw + b" ", binding)
    for field, value in (
        ("family", "ip"),
        ("table", "foreign"),
        ("chain", "forward"),
        ("anchor_comment", "invented"),
    ):
        changed = copy.deepcopy(doc)
        changed["firewall"][field] = value
        material = json.dumps(changed).encode()
        matching = dataclasses.replace(
            binding, host_config_sha256=hashlib.sha256(material).hexdigest()
        )
        with pytest.raises(t.Refused, match="host-config-unproven"):
            t.SystemBackend.validate_host_config(material, matching)


def test_expiry_cleanup_cannot_pass_an_open_transaction_barrier():
    h, c = prepared()
    entering = threading.Event()
    release = threading.Event()
    cleanup_started = threading.Event()
    cleanup_done = threading.Event()
    errors = []
    original = h.install

    def paused_install(specs, uid, anchor):
        entering.set()
        assert release.wait(5)
        original(specs, uid, anchor)
        h.now = h.state["deadline_ns"] + 1

    h.install = paused_install

    def opening():
        try:
            c.open()
        except t.Refused as error:
            errors.append(str(error))

    def closing():
        cleanup_started.set()
        try:
            t.Transport(c.binding, c.manifest, h).cleanup()
        finally:
            cleanup_done.set()

    opener = threading.Thread(target=opening)
    closer = threading.Thread(target=closing)
    opener.start()
    assert entering.wait(5)
    closer.start()
    assert cleanup_started.wait(5)
    try:
        assert not cleanup_done.wait(0.05)
        assert "disarm" not in h.effects
    finally:
        release.set()
        opener.join(5)
        closer.join(5)
    assert not opener.is_alive() and not closer.is_alive()
    assert errors == ["open-unproven"]
    assert h.rules == [] and h.state["status"] == "closed"
    assert "delete" in h.effects and "disarm" not in h.effects
    assert h.deadline is not None


def test_post_open_metadata_drift_cannot_block_exact_accept_revocation():
    h, c = prepared()
    c.open()
    h.bad = "binding"
    assert c.cleanup().rule_count == 0
    assert h.rules == []


def test_live_uid_process_retains_exact_drop_guard_and_timer():
    h, c = prepared()
    c.open()
    h.bad = "processes"
    with pytest.raises(t.Refused, match="guarded-unproven"):
        c.cleanup()
    assert len(h.rules) == 1 and t.exact_guard(
        h.rules[0], c.binding.session_id, c.manifest.uid
    )
    assert h.state["status"] == "guarded" and "disarm" not in h.effects
    h.bad = ""
    assert c.cleanup().rule_count == 0


def test_initialized_closed_can_start_once_then_never_restart():
    h, c = prepared()
    assert c.initialize_closed().rule_count == 0
    assert h.state["status"] == "closed" and h.state["phase"] == "not-started"
    assert "arm" not in h.effects and "install" not in h.effects
    c.open()
    assert h.state["phase"] == "started"
    c.cleanup()
    assert h.state["phase"] == "finished"
    with pytest.raises(t.Refused, match="already-started"):
        c.open()


def test_emergency_guard_during_frozen_open_prevents_effective_accept_resume():
    h, c = prepared()
    entering = threading.Event()
    release = threading.Event()

    @contextlib.contextmanager
    def bounded_lock():
        if not h.lock.acquire(timeout=0.05):
            raise t.Refused("lock-busy")
        try:
            yield
        finally:
            h.lock.release()

    h.operation = bounded_lock
    original = h.install
    errors = []

    def paused(specs, uid, anchor):
        entering.set()
        assert release.wait(5)
        original(specs, uid, anchor)

    h.install = paused

    def opening():
        try:
            c.open()
        except t.Refused as error:
            errors.append(str(error))

    worker = threading.Thread(target=opening)
    worker.start()
    assert entering.wait(5)
    try:
        with pytest.raises(t.Refused, match="guarded-unproven"):
            c.cleanup()
        assert len(h.rules) == 1 and t.exact_guard(
            h.rules[0], c.binding.session_id, c.manifest.uid
        )
        assert "delete" not in h.effects and "disarm" not in h.effects
    finally:
        release.set()
        worker.join(5)
    assert errors == ["open-unproven"] and h.rules == []
    assert (
        h.effects.index("guard")
        < h.effects.index("install")
        < h.effects.index("delete")
    )


def test_contain_is_idempotent_and_explicitly_guarded_until_client_stops():
    h, c = prepared()
    c.open()
    h.bad = "processes"
    result = c.contain()
    assert result.verdict == "GUARDED" and result.rule_count == 1
    again = c.contain()
    assert again == result and h.effects.count("guard") == 1
    h.bad = ""
    assert c.cleanup().verdict == "CLOSED"


def test_timer_reserves_removal_margin_before_fixed_cap():
    h, c = prepared()
    receipt = c.open()
    assert h.deadline == receipt.deadline_ns - t.REMOVAL_MARGIN_SECONDS * t.NS
    assert receipt.deadline_ns - h.state["started_ns"] == 300 * t.NS


def test_late_emergency_cleanup_cannot_guard_a_closed_reusable_uid():
    h, c = prepared()
    c.initialize_closed()

    @contextlib.contextmanager
    def busy():
        raise t.Refused("lock-busy")
        yield

    h.operation = busy
    with pytest.raises(t.Refused, match="cleanup-unproven"):
        c.cleanup()
    assert h.rules == [] and "guard" not in h.effects
