"""Canonical root bridge; one live Management object and RAM-only Receipt.

Fixed protected inputs only. A disconnected caller cannot reconstruct a receipt
or extend retention. No import-time host operations.
"""

import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import selectors
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

NS = 1_000_000_000


DEPLOYMENT = None


def configure(deployment):
    """Install reviewed protected inputs; synthetic callers inject the same shape.

    The launcher must verify ownership, permissions, deployment digest and source
    acceptance before calling. These values are bindings, never authorization.
    """
    global DEPLOYMENT
    if DEPLOYMENT is not None:
        raise RuntimeError("deployment-already-configured")
    if type(deployment) is not dict or set(deployment) != {
        "old_owner",
        "input",
        "config_digest",
        "installer",
        "bundle_digest",
        "helper",
        "old_unit",
        "lock",
        "remover",
    }:
        raise ValueError("deployment-schema")
    candidate = copy.deepcopy(deployment)
    for key in {"input", "helper", "lock", "remover", "installer"}:
        value = candidate[key]
        if type(value) is not str or not Path(value).is_absolute() or "\x00" in value:
            raise ValueError("deployment-path")
        candidate[key] = Path(value)
    for key in ("bundle_digest", "config_digest"):
        if type(candidate[key]) is not str or not re.fullmatch(
            "[0-9a-f]{64}", candidate[key]
        ):
            raise ValueError("deployment-digest")
    for key in ("old_unit", "old_owner"):
        if (
            type(candidate[key]) is not str
            or not candidate[key]
            or "\x00" in candidate[key]
        ):
            raise ValueError("deployment-owner")
    if (
        candidate["helper"].name != "lane3_campaign_management.py"
        or candidate["installer"].name != "lane3_handoff_install.py"
    ):
        raise ValueError("deployment-module")
    DEPLOYMENT = candidate


def deployment_inputs():
    if DEPLOYMENT is None:
        raise RuntimeError("protected-deployment-required")
    return copy.deepcopy(DEPLOYMENT)


BRIDGE_FAILURE_CATEGORIES = frozenset(
    (
        "binding",
        "bridge-lock",
        "code-pin",
        "duplicate-key",
        "finish-already-attempted",
        "input-deadline",
        "input-schema",
        "installation-pin",
        "invocation",
        "line-too-long",
        "module-role",
        "object-required",
        "order",
        "original-clock",
        "original-expired",
        "original-timer",
        "original-timer-drift",
        "protected-file",
        "protected-parent",
        "protocol",
        "receipt-missing",
        "truncated-line",
    )
)


class BridgeRefused(RuntimeError):
    def __init__(self, category):
        category = (
            category
            if type(category) is str and category in BRIDGE_FAILURE_CATEGORIES
            else "unexpected"
        )
        super().__init__(category)
        self.category = category


def failure_diagnostic(error, bridge, phase, cleanup):
    """Recognize the measured helper's class identity, never duck-typed labels."""
    # A partially constructed bridge may not have loaded its helper yet;
    # diagnostic construction must never raise and lose the refusal frame.
    mgmt_module = getattr(bridge, "mgmt_module", None) if bridge is not None else None
    if mgmt_module is not None and type(error) is mgmt_module.Refused:
        diagnostic = mgmt_module.failure_diagnostic(error, phase)
    else:
        diagnostic = {
            "phase": phase,
            "stage": "bridge",
            "category": error.category
            if type(error) is BridgeRefused
            and error.category in BRIDGE_FAILURE_CATEGORIES
            else "unexpected",
            "cleanup": "not_attempted",
        }
    if cleanup in ("completed", "unproven") and diagnostic["cleanup"] != "unproven":
        diagnostic["cleanup"] = cleanup
    return diagnostic


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BridgeRefused("duplicate-key")
        result[key] = value
    return result


def decode(raw):
    value = json.loads(raw, object_pairs_hook=strict_object)
    if type(value) is not dict:
        raise BridgeRefused("object-required")
    return value


def parents(path):
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise BridgeRefused("protected-parent")


def protected(path, mode, limit):
    parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_gid != 0
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != mode
            or info.st_size > limit
        ):
            raise BridgeRefused("protected-file")
        raw = os.read(fd, limit + 1)
        if len(raw) > limit:
            raise BridgeRefused("protected-file")
        return raw
    finally:
        os.close(fd)


def module(name, path, expected):
    roles = {
        "_campaign_bridge_management": deployment_inputs()["helper"],
        "lane3_handoff_install": deployment_inputs()["installer"],
    }
    if roles.get(name) != path:
        raise BridgeRefused("module-role")
    raw = protected(path, 0o444, 1 << 20)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise BridgeRefused("code-pin")
    # Execute only the measured bytes; no reopen/import race or bytecode write.
    result = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location(name, path)
    )
    sys.modules[name] = result
    exec(compile(raw, str(path), "exec"), result.__dict__)  # noqa: S102
    return result


@contextlib.contextmanager
def exclusive():
    parents(deployment_inputs()["lock"])
    fd = os.open(
        deployment_inputs()["lock"],
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_gid != 0
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise BridgeRefused("bridge-lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def parameters():
    doc = decode(protected(deployment_inputs()["input"], 0o600, 4096))
    if (
        set(doc)
        != {
            "schema",
            "nonce",
            "boot_id",
            "marker_sha256",
            "removal_sha256",
            "helper_sha256",
            "installer_sha256",
        }
        or doc["schema"] != "dotmac.management-bridge-input.v1"
        or type(doc["nonce"]) is not str
        or not re.fullmatch("[0-9a-f]{32}", doc["nonce"])
        or type(doc["boot_id"]) is not str
        or not re.fullmatch(
            "[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", doc["boot_id"]
        )
        or any(
            type(doc[k]) is not str or not re.fullmatch("[0-9a-f]{64}", doc[k])
            for k in (
                "marker_sha256",
                "removal_sha256",
                "helper_sha256",
                "installer_sha256",
            )
        )
    ):
        raise BridgeRefused("input-schema")
    return doc


class Bridge:
    def __init__(self, doc):
        self.mgmt_module = module(
            "_campaign_bridge_management",
            deployment_inputs()["helper"],
            doc["helper_sha256"],
        )
        self.install_module = module(
            "lane3_handoff_install",
            deployment_inputs()["installer"],
            doc["installer_sha256"],
        )
        self.installer = self.install_module.Installer()
        # Read-only discovery of original timer; no provisional Management or
        # Receipt is constructed and no clock is reset during this discovery.
        names = SimpleNamespace(
            old_unit=deployment_inputs()["old_unit"],
            new_unit="dotmac-l3mgmt-retained-" + doc["nonce"],
        )
        probe = self.mgmt_module.SystemdBackend(names)
        before = probe.clock_ns()
        old = probe.timer(deployment_inputs()["old_unit"])
        after = probe.clock_ns()
        if (
            old is None
            or not old.waiting
            or not old.bounded
            or old.owner != deployment_inputs()["old_owner"]
            or old.argv != ("/bin/sh", str(deployment_inputs()["remover"]))
            or type(old.deadline_ns) is not int
        ):
            raise BridgeRefused("original-timer")
        started = old.deadline_ns - 220 * NS
        if not 0 < started <= before <= after < old.deadline_ns:
            raise BridgeRefused("original-clock")
        again = probe.timer(deployment_inputs()["old_unit"])
        if again != old or probe.clock_ns() >= old.deadline_ns:
            raise BridgeRefused("original-timer-drift")
        binding = self.mgmt_module.Binding(
            run_id=doc["nonce"],
            boot_id=doc["boot_id"],
            marker_sha256=doc["marker_sha256"],
            removal_sha256=doc["removal_sha256"],
            started_ns=started,
            helper_sha256=doc["helper_sha256"],
        )
        self.backend = self.mgmt_module.SystemdBackend(binding)
        self.management = self.mgmt_module.Management(binding, self.backend)
        if (
            self.backend.boot_id() != doc["boot_id"]
            or self.backend.marker_binding()
            != (doc["nonce"], "created", doc["marker_sha256"])
            or self.backend.removal_digest() != doc["removal_sha256"]
            or self.backend.helper_digest() != doc["helper_sha256"]
        ):
            raise BridgeRefused("binding")
        self.receipt = None
        self.phase = "initial"
        self.finish_attempted = False
        self.original_deadline_ns = old.deadline_ns

    @contextlib.contextmanager
    def no_local_lease(self):
        # Installer lock is controller-state lock; nested verify uses its
        # documented reentrant locked() path on this SAME Installer instance.
        with self.installer.locked():
            self.installer.no_transaction()
            self.installer.no_uninstall()
            # Fast refusal avoids a readiness systemctl call while any journal
            # exists; the authoritative no_lease check repeats before removal.
            self.installer.no_lease()
            record = self.installer.verify()
            if (
                record.get("bundle_digest") != deployment_inputs()["bundle_digest"]
                or record.get("config_digest") != deployment_inputs()["config_digest"]
            ):
                raise BridgeRefused("installation-pin")
            self.installer.entrypoint(record)
            self.installer.require_ready(record)
            self.installer.no_lease()
            yield

    def finish(self):
        if self.receipt is None:
            raise BridgeRefused("receipt-missing")
        if self.finish_attempted:
            raise BridgeRefused("finish-already-attempted")
        self.finish_attempted = True
        with self.no_local_lease():
            self.management.finish(self.receipt, active_lease=False)
        self.receipt = None
        self.phase = "finished"

    def operate(self, op):
        expected = {
            "initial": "retain",
            "retained": "gate_prepare",
            "prepared": "gate_bootstrap",
            "bootstrapped": "finish",
        }
        if op == "finish":
            if self.phase not in ("retained", "prepared", "bootstrapped"):
                raise BridgeRefused("order")
            self.finish()
            return {"status": "CLOSED", "op": "finish", "local_journal_absent": True}
        if expected.get(self.phase) != op:
            raise BridgeRefused("order")
        if op == "retain":
            # No lease while retain's own failure cleanup could remove trust.
            with self.no_local_lease():
                if self.backend.clock_ns() >= self.original_deadline_ns:
                    raise BridgeRefused("original-expired")
                self.receipt = self.management.retain()
            self.phase = "retained"
        elif op == "gate_prepare":
            self.management.gate_prepare(self.receipt)
            self.phase = "prepared"
        elif op == "gate_bootstrap":
            self.management.gate_bootstrap(self.receipt)
            self.phase = "bootstrapped"
        now = self.backend.clock_ns()
        return {
            "status": "PASS",
            "op": op,
            "deadline_ns": self.receipt.deadline_ns,
            "cleanup_due_ns": self.receipt.expiry_ns,
            "receipt_age_ms": (now - self.receipt.verified_ns) // 1_000_000,
            "remaining_ms": max(0, self.receipt.expiry_ns - now) // 1_000_000,
        }

    def on_exit(self):
        if self.receipt is None or self.finish_attempted:
            return "not_attempted"
        try:
            self.finish()
            return "completed"
        except BaseException:
            # Active/unknown local lease or stale bindings do not authorize
            # premature removal. Preserve original/retained timer ownership.
            return "unproven"


def line(deadline_ns):
    data = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(sys.stdin.buffer, selectors.EVENT_READ)
        while len(data) <= 1024:
            remaining = (deadline_ns - time.clock_gettime_ns(time.CLOCK_MONOTONIC)) / NS
            if remaining <= 0 or not selector.select(remaining):
                raise BridgeRefused("input-deadline")
            byte = os.read(sys.stdin.fileno(), 1)
            if not byte:
                if data:
                    raise BridgeRefused("truncated-line")
                return None
            data.extend(byte)
            if len(data) > 1024:
                raise BridgeRefused("line-too-long")
            if byte == b"\n":
                return bytes(data)
    raise BridgeRefused("line-too-long")


def main():
    os.umask(0o077)
    sys.dont_write_bytecode = True
    bridge = None
    phase = "unproven"
    cleanup = "not_attempted"
    try:
        if os.geteuid() != 0 or not sys.flags.isolated or sys.argv[1:]:
            raise BridgeRefused("invocation")
        if DEPLOYMENT is None:
            configure(
                decode(
                    protected(
                        Path("/etc/dotmac-lane3-campaign/bridge-deployment.json"),
                        0o444,
                        16384,
                    )
                )
            )
        with exclusive():
            bridge = None
            try:
                bridge = Bridge(parameters())
                while bridge.phase != "finished":
                    raw = line(bridge.management.binding.deadline_ns)
                    if raw is None:
                        return 1
                    doc = decode(raw)
                    if set(doc) != {"op"} or type(doc["op"]) is not str:
                        raise BridgeRefused("protocol")
                    phase = (
                        doc["op"]
                        if doc["op"]
                        in {"retain", "gate_prepare", "gate_bootstrap", "finish"}
                        else "unproven"
                    )
                    result = bridge.operate(doc["op"])
                    print(json.dumps(result, separators=(",", ":")), flush=True)
            finally:
                if bridge is not None:
                    cleanup = bridge.on_exit()
        return 0
    except BaseException as exc:
        try:
            print(
                json.dumps(
                    {
                        "status": "REFUSED",
                        "diagnostic": failure_diagnostic(exc, bridge, phase, cleanup),
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        except BaseException:  # noqa: S110 - private diagnostics suppressed
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
