"""Retained management policy; test effects are injected, never run locally."""

from __future__ import annotations

import dataclasses
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
import lane3_campaign_management as cm

NS = 1_000_000_000
RUN = "a" * 32
BOOT = "01234567-89ab-cdef-0123-456789abcdef"


class Backend:
    def __init__(self):
        self.now = 100 * NS
        self.effects = []
        self.boot = BOOT
        self.marker = (RUN, "created", "b" * 64)
        self.script = "c" * 64
        self.helper = "d" * 64
        self.units = {}
        self.fail = ""
        self.units["dotmac-l3mgmt-remove-live"] = cm.TimerState(
            "l3mgmt trust removal (live window)",
            ("/bin/sh", "/root/dotmac-l3mgmt/remove.sh"),
            self.now + 200 * NS,
            True,
            True,
        )

    def clock_ns(self):
        return self.now

    def boot_id(self):
        return self.boot

    def marker_binding(self):
        return self.marker

    def helper_digest(self):
        return self.helper

    def removal_digest(self):
        return self.script

    def timer(self, unit):
        return self.units.get(unit)

    def arm(self, unit, owner, deadline_ns):
        self.effects.append("arm")
        if self.fail == "arm":
            raise OSError("PRIVATE-CANARY")
        self.units[unit] = cm.TimerState(
            owner,
            ("/usr/bin/python3", "-I", "-B", cm.HELPER, "--expire", RUN),
            deadline_ns,
            True,
            True,
        )
        if self.fail == "lost":
            raise OSError("PRIVATE-CANARY")

    def stop(self, unit):
        self.effects.append("stop:" + unit)
        if self.fail != "stop":
            self.units.pop(unit, None)

    def remove(self):
        self.effects.append("remove")


def prepared():
    backend = Backend()
    binding = cm.Binding(RUN, BOOT, "b" * 64, "c" * 64, backend.now, "d" * 64)
    return backend, cm.Management(binding, backend)


def test_new_timer_is_verified_before_old_timer_stop_and_deadline_never_moves():
    b, m = prepared()
    receipt = m.retain()
    assert b.effects == ["arm", "stop:dotmac-l3mgmt-remove-live"]
    assert receipt.deadline_ns == 1000 * NS
    b.now += 10 * NS
    assert m.gate_prepare(receipt) == receipt
    assert m.gate_bootstrap(receipt) == receipt
    with pytest.raises(cm.Refused, match="already-attempted"):
        m.retain()
    assert b.effects == ["arm", "stop:dotmac-l3mgmt-remove-live"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("boot", "wrong"),
        ("marker", (RUN, "pending", "b" * 64)),
        ("marker", ("d" * 32, "created", "b" * 64)),
        ("marker", (RUN, "created", "d" * 64)),
        ("script", "d" * 64),
    ],
)
def test_every_binding_is_required_before_effects(field, value):
    b, m = prepared()
    setattr(b, field, value)
    with pytest.raises(cm.Refused):
        m.retain()
    assert not b.effects


@pytest.mark.parametrize(
    "change",
    [
        {"owner": "foreign"},
        {"argv": ("/bin/sh", "/root/unowned/other")},
        {"waiting": False},
        {"bounded": False},
        {"deadline_ns": 0},
    ],
)
def test_old_timer_ownership_before_effects(change):
    b, m = prepared()
    old = m.binding.old_unit
    b.units[old] = dataclasses.replace(b.units[old], **change)
    with pytest.raises(cm.Refused):
        m.retain()
    assert not b.effects


@pytest.mark.parametrize("failure", ["arm", "lost", "stop"])
def test_failed_or_unknown_mutation_retains_backstop_and_cleans_up(failure):
    b, m = prepared()
    b.fail = failure
    with pytest.raises(cm.Refused, match="transition-unproven") as error:
        m.retain()
    assert "PRIVATE" not in str(error.value)
    assert m.binding.old_unit in b.units
    assert b.effects[-1] == "remove"
    assert "stop:" + m.binding.new_unit not in b.effects


def test_active_lease_prevents_revocation():
    b, m = prepared()
    receipt = m.retain()
    with pytest.raises(cm.Refused, match="lease-active"):
        m.finish(receipt, active_lease=True)
    assert "remove" not in b.effects


@pytest.mark.parametrize(
    "stage,age,remaining",
    [
        ("prepare", 31, 800),
        ("prepare", 1, 599),
        ("bootstrap", 61, 800),
        ("bootstrap", 1, 419),
    ],
)
def test_launch_gates_enforce_freshness_and_remaining(stage, age, remaining):
    b, m = prepared()
    receipt = m.retain()
    b.now += age * NS
    b.units[m.binding.new_unit] = dataclasses.replace(
        b.units[m.binding.new_unit], deadline_ns=b.now + remaining * NS
    )
    with pytest.raises(cm.Refused):
        getattr(m, "gate_" + stage)(receipt)


def test_receipts_cannot_be_forged_or_resumed():
    b, m = prepared()
    receipt = m.retain()
    with pytest.raises(cm.Refused):
        m.gate_prepare(
            dataclasses.replace(receipt, verified_ns=receipt.verified_ns + 1)
        )
    with pytest.raises(cm.Refused):
        cm.Management(m.binding, b).gate_prepare(receipt)


def test_collision_cannot_replace_or_remove_an_existing_unit():
    b, m = prepared()
    b.units[m.binding.new_unit] = cm.TimerState(
        "foreign", (), b.now + 800 * NS, True, True
    )
    with pytest.raises(cm.Refused, match="timer-collision"):
        m.retain()
    assert b.effects == []


@pytest.mark.parametrize(
    "change",
    [
        {"owner": "foreign"},
        {"argv": ("/bin/sh", "/root/unowned/other")},
        {"bounded": False},
        {"waiting": False},
        {"deadline_ns": 1001 * NS},
    ],
)
def test_bad_new_readback_never_stops_old_timer(change):
    b, m = prepared()
    original = b.arm

    def corrupted(unit, owner, deadline_ns):
        original(unit, owner, deadline_ns)
        b.units[unit] = dataclasses.replace(b.units[unit], **change)

    b.arm = corrupted
    with pytest.raises(cm.Refused, match="transition-unproven"):
        m.retain()
    assert b.effects == ["arm", "remove"]
    assert m.binding.old_unit in b.units


def test_remover_drift_after_arm_is_never_executed():
    b, m = prepared()
    original = b.arm

    def drift(unit, owner, deadline_ns):
        original(unit, owner, deadline_ns)
        b.script = "d" * 64

    b.arm = drift
    with pytest.raises(cm.Refused, match="transition-unproven"):
        m.retain()
    assert b.effects == ["arm"]
    assert m.binding.old_unit in b.units
    assert m.binding.new_unit in b.units


def test_cleanup_requires_marker_absence_before_disarming_backstop():
    b, m = prepared()
    receipt = m.retain()
    with pytest.raises(cm.Refused, match="cleanup-unproven"):
        m.finish(receipt, active_lease=False)
    assert b.effects[-1] == "remove"
    assert m.binding.new_unit in b.units


def test_successful_cleanup_disarms_only_after_absence_readback():
    b, m = prepared()
    receipt = m.retain()

    def remove():
        b.effects.append("remove")
        b.marker = None

    b.remove = remove
    m.finish(receipt, active_lease=False)
    assert b.effects[-2:] == ["remove", "stop:" + m.binding.new_unit]
    assert m.binding.new_unit not in b.units


def test_fixed_binding_rejects_caller_unit_or_description():
    b, m = prepared()
    for change in (
        {"old_unit": "arbitrary.service"},
        {"old_owner": "arbitrary-owner"},
        {"started_ns": 0},
        {"run_id": "bad"},
        {"boot_id": "bad"},
        {"removal_sha256": "bad"},
    ):
        with pytest.raises(cm.Refused, match="binding-invalid"):
            dataclasses.replace(m.binding, **change)
    assert b.effects == []


def test_retained_timer_binds_callback_and_reserves_cleanup_margin():
    b, m = prepared()
    receipt = m.retain()
    state = b.units[m.binding.new_unit]
    assert state.argv == ("/usr/bin/python3", "-I", "-B", cm.HELPER, "--expire", RUN)
    assert receipt.expiry_ns == receipt.deadline_ns - 60 * NS == state.deadline_ns


def test_helper_drift_prevents_timer_creation():
    b, m = prepared()
    b.helper = "e" * 64
    with pytest.raises(cm.Refused, match="binding-mismatch"):
        m.retain()
    assert b.effects == []
