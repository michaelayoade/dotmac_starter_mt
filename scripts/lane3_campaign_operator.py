"""Canonical Mac control sequence; protected bindings are never authorization.

The separately approved owner supplies reviewed deployment inputs and owns the
attached bridge/session process groups. Its finally block must close protocol
stdin and terminate/reap its own start_new_session SSH groups with bounded
deadlines. Neither EOF nor child death proves remote closure. This module only
closes attached stdin; it never kills an attached process. No live entrypoint
or import-time actions.
"""

import copy
import hashlib
import importlib.util
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

DEPLOYMENT = None
RUNTIME_ROLES = frozenset(
    {
        "lane3_handoff_install.py",
        "lane3_campaign_bootstrap.py",
        "lane3_handoff_resolver.py",
        "lane3_wireguard_topology.py",
        "lane3_openbao_topology.py",
        "lane3_broker_origin.py",
        "lane3_handoff_coordinator.py",
        "lane3_execution.py",
        "lane3_topology.py",
        "lane3_handoff_protocol.py",
        "lane3_handoff_controller.py",
        "lane3_topology_source.py",
    }
)
CLEANUP = frozenset(
    {
        "grant_revoked",
        "processes_absent",
        "sockets_absent",
        "owned_rules_absent",
        "baseline_preserved",
        "default_drop_preserved",
        "lease_files_absent",
        "workspace_absent",
        "identity_absent",
        "timer_stopped",
    }
)


LOCAL_FAILURE_CATEGORIES = frozenset(
    (
        "actual-run-required",
        "archive-unproved",
        "b8-cleanup-unproved",
        "b8-result",
        "bootstrap-unproved",
        "cleanup-unproved",
        "closure-role",
        "code-pin",
        "coordinator-binding",
        "coordinator-role",
        "coordinator-state-present",
        "csr-schema",
        "custody-not-prepared",
        "custody-order",
        "deadline",
        "duplicate-key",
        "failure-state-unproved",
        "finish-order",
        "group-not-empty",
        "issuer-external-pin",
        "key-age",
        "key-receipt",
        "management-order",
        "management-removal-unproved",
        "management-unproved",
        "module-role",
        "nonfinite-json",
        "object-required",
        "owned-file",
        "pipe-deadline",
        "pipe-eof",
        "pipe-size",
        "prepare-unproved",
        "protected-deployment-required",
        "protected-parent",
        "state-ownership-unproved",
        "subprocess-deadline",
        "subprocess-eof",
        "subprocess-size",
        "subprocess-unproved",
        "unproven",
    )
)
# Explicit wire whitelist; does not import or execute the host management role.
MANAGEMENT_FAILURE_CATEGORIES = frozenset(
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
) | frozenset(
    (
        "activation-stop-unproven",
        "already-attempted",
        "arm-unowned",
        "binding-invalid",
        "binding-mismatch",
        "cleanup-unproven",
        "deadline-invalid",
        "file-unproven",
        "gate-unproven",
        "helper-unproven",
        "lease-active",
        "lock-busy",
        "lock-unproven",
        "marker-unproven",
        "old-stop-unproven",
        "preflight-unproven",
        "receipt-stale",
        "receipt-unproven",
        "root-required",
        "state-unproven",
        "systemd-unproven",
        "timer-collision",
        "timer-remover-active",
        "timer-unproven",
        "transition-unproven",
        "unexpected",
        "unit-unowned",
        "usage-refused",
    )
)
COORDINATOR_FAILURE_CATEGORIES = frozenset(
    (
        "approval.missing",
        "assignment.ambiguous",
        "binding.mismatch",
        "boot.mismatch",
        "deadline.insufficient",
        "frame.invalid",
        "grant.consumed",
        "grant.mismatch",
        "install.failed",
        "internal",
        "lease.expired",
        "lease.unknown",
        "nonce.mismatch",
        "origin.flags",
        "origin.invalid",
        "origin.not_admitted",
        "peer.refused",
        "protocol.mismatch",
        "report.changed",
        "schema.invalid",
        "snapshot.refused",
        "state.invalid",
    )
)
FAILURE_PHASES = frozenset(
    {
        "unproven",
        "retain",
        "gate_prepare",
        "prepare",
        "gate_bootstrap",
        "launch",
        "decide",
        "finish",
        "cleanup",
    }
)
FAILURE_STAGES = frozenset(
    {
        "unproven",
        "bridge",
        "coordinator",
        "operator",
        "preflight",
        "arm",
        "first-bindings",
        "first-retained-check",
        "old-stop",
        "old-stop-readback",
        "second-bindings",
        "second-retained-check",
    }
)
CLEANUP_OUTCOMES = frozenset({"not_attempted", "completed", "unproven"})


def _diagnostic(value, categories):
    if type(value) is not dict or set(value) != {
        "phase",
        "stage",
        "category",
        "cleanup",
    }:
        return None
    for key, allowed in (
        ("phase", FAILURE_PHASES),
        ("stage", FAILURE_STAGES),
        ("category", categories),
        ("cleanup", CLEANUP_OUTCOMES),
    ):
        if type(value[key]) is not str or value[key] not in allowed:
            return None
    return dict(value)


class CampaignRefused(RuntimeError):
    """A finite diagnostic, safe to copy through the measured owner adapter."""

    def __init__(self, category, *, diagnostic=None, categories=()):
        category = (
            category
            if type(category) is str and category in LOCAL_FAILURE_CATEGORIES
            else "unproven"
        )
        super().__init__(category)
        self.diagnostic = _diagnostic(
            diagnostic,
            LOCAL_FAILURE_CATEGORIES
            | MANAGEMENT_FAILURE_CATEGORIES
            | (frozenset(categories) & COORDINATOR_FAILURE_CATEGORIES),
        ) or {
            "phase": "unproven",
            "stage": "unproven",
            "category": category,
            "cleanup": "not_attempted",
        }


def failure_diagnostic(error):
    """Public caller adapter: never copy arbitrary exception attributes/text."""
    if type(error) is CampaignRefused:
        # Constructor validation is the sole ingress; return a defensive copy.
        value = _diagnostic(
            error.diagnostic,
            LOCAL_FAILURE_CATEGORIES
            | MANAGEMENT_FAILURE_CATEGORIES
            | COORDINATOR_FAILURE_CATEGORIES,
        )
        if value is not None:
            return value
    return {
        "phase": "unproven",
        "stage": "unproven",
        "category": "unproven",
        "cleanup": "not_attempted",
    }


def configure(deployment):
    """Freeze externally reviewed bindings once; no caller approval flag exists."""
    global DEPLOYMENT
    if DEPLOYMENT is not None:
        raise RuntimeError("deployment-already-configured")
    keys = {
        "runtime",
        "runtime_pins",
        "coordinator_config",
        "coordinator_config_sha256",
        "state_path",
        "qualification",
        "github_run_id",
        "key_receipt",
        "ssh_destination",
        "bundle_digest",
        "runner_org",
        "runner_group_id",
        "issuer_module",
        "issuer_sha256",
        "issuer_deployment",
        "issuer_deployment_sha256",
    }
    if type(deployment) is not dict or set(deployment) != keys:
        raise ValueError("deployment-schema")
    candidate = copy.deepcopy(deployment)
    for key in {
        "runtime",
        "coordinator_config",
        "state_path",
        "key_receipt",
        "issuer_module",
        "issuer_deployment",
    }:
        value = candidate[key]
        if type(value) is not str or not re.fullmatch(
            r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+", value
        ):
            raise ValueError("deployment-path")
        candidate[key] = Path(value)
    pins = candidate["runtime_pins"]
    if type(pins) is not dict or set(pins) != RUNTIME_ROLES:
        raise ValueError("deployment-roles")
    for pin in [
        *pins.values(),
        *(
            candidate[k]
            for k in (
                "coordinator_config_sha256",
                "bundle_digest",
                "issuer_sha256",
                "issuer_deployment_sha256",
            )
        ),
    ]:
        if type(pin) is not str or not re.fullmatch("[0-9a-f]{64}", pin):
            raise ValueError("deployment-pin")
    if candidate["issuer_module"].name != "lane3_campaign_issuer.py":
        raise ValueError("deployment-role")
    if type(candidate["ssh_destination"]) is not str or not re.fullmatch(
        "[a-z0-9][a-z0-9-]{0,62}", candidate["ssh_destination"]
    ):
        raise ValueError("deployment-route")
    if type(candidate["runner_org"]) is not str or not re.fullmatch(
        "[A-Za-z0-9-]{1,39}", candidate["runner_org"]
    ):
        raise ValueError("deployment-route")
    for key in ("github_run_id", "runner_group_id"):
        if type(candidate[key]) is not int or candidate[key] <= 0:
            raise ValueError("deployment-id")
    if (
        type(candidate["qualification"]) is not dict
        or candidate["qualification"].get("run_id") != candidate["github_run_id"]
    ):
        raise ValueError("deployment-qualification")
    qualification = candidate["qualification"]
    if set(qualification) != {
        "authority",
        "repository_id",
        "run_id",
        "run_attempt",
        "workflow_sha",
        "workflow_blob",
        "starter_commit",
        "environment_id",
        "approval_digest",
        "admission_digest",
        "supplier_digest",
    }:
        raise ValueError("deployment-qualification")
    authority = qualification["authority"]
    if (
        type(authority) is not dict
        or set(authority)
        != {
            "schema",
            "purpose",
            "config_digest",
            "capability_digest",
            "evidence_digest",
        }
        or authority["schema"] != "dotmac.lane3.qualification-purpose.v2"
        or authority["purpose"] != "qualification"
        or authority["evidence_digest"] is not None
        or any(
            type(authority[key]) is not str
            or re.fullmatch("[0-9a-f]{64}", authority[key]) is None
            for key in ("config_digest", "capability_digest")
        )
    ):
        raise ValueError("deployment-qualification")
    for key in ("repository_id", "run_id", "run_attempt", "environment_id"):
        if type(qualification[key]) is not int or qualification[key] <= 0:
            raise ValueError("deployment-qualification")
    if qualification["run_attempt"] != 1:
        raise ValueError("deployment-qualification")
    for key, size in (
        ("workflow_sha", 40),
        ("workflow_blob", 40),
        ("starter_commit", 40),
        ("approval_digest", 64),
        ("admission_digest", 64),
        ("supplier_digest", 64),
    ):
        if type(qualification[key]) is not str or not re.fullmatch(
            "[0-9a-f]{" + str(size) + "}", qualification[key]
        ):
            raise ValueError("deployment-qualification")
    DEPLOYMENT = candidate


def deployment_inputs():
    if DEPLOYMENT is None:
        raise CampaignRefused("protected-deployment-required")
    return copy.deepcopy(DEPLOYMENT)


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise CampaignRefused("duplicate-key")
        result[key] = value
    return result


def constant(_):
    raise CampaignRefused("nonfinite-json")


def decode(raw):
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if type(value) is not dict:
        raise CampaignRefused("object-required")
    return value


def owned(path, limit, mode=None):
    """Owner files under nonwritable owner/root directories, never symlinks."""
    path = Path(path)
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in (0, os.getuid())
            or info.st_mode & 0o022
        ):
            raise CampaignRefused("protected-parent")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or not 0 < info.st_size <= limit
            or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
        ):
            raise CampaignRefused("owned-file")
        raw = os.read(fd, limit + 1)
        if len(raw) != info.st_size:
            raise CampaignRefused("owned-file")
        return raw
    finally:
        os.close(fd)


def measured(path, pin, limit=2_000_000, mode=None):
    raw = owned(path, limit, mode)
    if hashlib.sha256(raw).hexdigest() != pin:
        raise CampaignRefused("code-pin")
    return raw


def load_module(role):
    """Only the issuer and public protocol may be loaded in this parent."""
    binding = deployment_inputs()
    roles = {
        "issuer": (binding["issuer_module"], binding["issuer_sha256"]),
        "protocol": (
            binding["runtime"] / "lane3_handoff_protocol.py",
            binding["runtime_pins"]["lane3_handoff_protocol.py"],
        ),
    }
    if role not in roles:
        raise CampaignRefused("module-role")
    path, pin = roles[role]
    raw = measured(path, pin)
    name = "_campaign_operator_" + role
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location(name, path)
    )
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def load_issuer():
    binding = deployment_inputs()
    issuer = load_module("issuer")
    deployment = decode(
        measured(
            binding["issuer_deployment"],
            binding["issuer_deployment_sha256"],
            262144,
            0o600,
        )
    )
    if deployment.get("issuer_source_sha256") != binding["issuer_sha256"]:
        raise CampaignRefused("issuer-external-pin")
    issuer.configure(deployment)
    return issuer


def remaining(deadline):
    left = deadline - time.monotonic()
    if not math.isfinite(left) or left <= 0:
        raise CampaignRefused("deadline")
    return left


class JsonPipe:
    """Attach owner-created pipes; never accept a command."""

    def __init__(self, process):
        self.process = process

    def send(self, value, deadline):
        data = (
            json.dumps(value, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        )
        fd = self.process.stdin.fileno()
        os.set_blocking(fd, False)
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_WRITE)
            while data:
                if not selector.select(remaining(deadline)):
                    raise CampaignRefused("pipe-deadline")
                try:
                    written = os.write(fd, data)
                    if written <= 0:
                        raise CampaignRefused("pipe-eof")
                    data = data[written:]
                except BlockingIOError:
                    continue

    def receive(self, deadline, limit=262144):
        fd = self.process.stdout.fileno()
        os.set_blocking(fd, False)
        raw = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            while len(raw) < limit:
                if not selector.select(remaining(deadline)):
                    raise CampaignRefused("pipe-deadline")
                try:
                    byte = os.read(fd, 1)
                except BlockingIOError:
                    continue
                if not byte:
                    raise CampaignRefused("pipe-eof")
                raw.extend(byte)
                if byte == b"\n":
                    return decode(raw)
        raise CampaignRefused("pipe-size")


def settle(process):
    # The leader can exit while a descendant retains stdout. These groups are
    # created only by _run_owned, so cleanup covers descendants too.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)
    for stream in (process.stdin, process.stdout):
        if stream is not None and not stream.closed:
            stream.close()


def _run_owned(args, deadline, request=None, *, refusal_categories=None):
    """Internal fixed-role launcher, bounded output and owned process group."""
    remaining(deadline)
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE if request is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        if request is not None:
            # Requests are a tiny fixed cleanup protocol object, not caller
            # programs. Nonblocking writes share the same absolute deadline.
            fd = process.stdin.fileno()
            os.set_blocking(fd, False)
            with selectors.DefaultSelector() as selector:
                selector.register(fd, selectors.EVENT_WRITE)
                pending = request
                while pending:
                    if not selector.select(remaining(deadline)):
                        raise CampaignRefused("subprocess-deadline")
                    try:
                        count = os.write(fd, pending)
                        if count <= 0:
                            raise CampaignRefused("subprocess-eof")
                        pending = pending[count:]
                    except BlockingIOError:
                        continue
            process.stdin.close()
        fd = process.stdout.fileno()
        os.set_blocking(fd, False)
        out = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            while True:
                if not selector.select(remaining(deadline)):
                    raise CampaignRefused("subprocess-deadline")
                try:
                    chunk = os.read(fd, min(65536, 1_000_001 - len(out)))
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                out.extend(chunk)
                if len(out) > 1_000_000:
                    raise CampaignRefused("subprocess-size")
        process.wait(timeout=remaining(deadline))
        if process.returncode != 0:
            if refusal_categories is not None:
                try:
                    reply = decode(out)
                    if (
                        set(reply) == {"status", "category"}
                        and reply["status"] == "REFUSED"
                        and type(reply["category"]) is str
                        and reply["category"] in refusal_categories
                        and reply["category"] in COORDINATOR_FAILURE_CATEGORIES
                    ):
                        raise CampaignRefused(
                            "subprocess-unproved",
                            diagnostic={
                                "phase": "unproven",
                                "stage": "coordinator",
                                "category": reply["category"],
                                "cleanup": "not_attempted",
                            },
                            categories=refusal_categories,
                        )
                except CampaignRefused as exc:
                    if exc.diagnostic["stage"] == "coordinator":
                        raise
                except (ValueError, TypeError, UnicodeError):
                    pass
            raise CampaignRefused("subprocess-unproved")
        return decode(out)
    finally:
        settle(process)


def coordinator_binding():
    """Both fresh group reads and archive route must match the pinned CLI."""
    binding = deployment_inputs()
    config = decode(
        measured(
            binding["coordinator_config"], binding["coordinator_config_sha256"], 262144
        )
    )
    dispatcher = (
        "/opt/dotmac-lane3-handoff/"
        + binding["bundle_digest"]
        + "/lane3_handoff_install.py"
    )
    if (
        config.get("state_path") != str(binding["state_path"])
        or config.get("runner_org") != binding["runner_org"]
        or type(config.get("runner_group_id")) is not int
        or config["runner_group_id"] != binding["runner_group_id"]
        or config.get("management")
        != {"ssh_alias": binding["ssh_destination"], "controller_path": dispatcher}
    ):
        raise CampaignRefused("coordinator-binding")
    authority = binding["qualification"]["authority"]
    if (
        config.get("schema") != "dotmac.lane3.handoff-coordinator.v2"
        or config.get("purpose") != authority["purpose"]
        or hashlib.sha256(
            json.dumps(
                config, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        != authority["config_digest"]
    ):
        raise CampaignRefused("coordinator-binding")
    for key in (
        "repository_id",
        "workflow_sha",
        "workflow_blob",
        "starter_commit",
        "environment_id",
    ):
        if config.get(key) != binding["qualification"].get(key):
            raise CampaignRefused("coordinator-binding")
    return config


def coordinator_cli(operation, deadline, run_id=None):
    if operation not in ("prepare", "launch", "decide", "cleanup"):
        raise CampaignRefused("coordinator-role")
    binding = deployment_inputs()
    for name, pin in binding["runtime_pins"].items():
        measured(binding["runtime"] / name, pin)
    coordinator_binding()
    args = [
        sys.executable,
        "-I",
        "-B",
        str(binding["runtime"] / "lane3_handoff_coordinator.py"),
        operation,
        "--config",
        str(binding["coordinator_config"]),
    ]
    if operation in ("prepare", "launch"):
        if type(run_id) is not int or run_id != binding["github_run_id"]:
            raise CampaignRefused("actual-run-required")
        args += ["--run-id", str(run_id)]
        if operation == "launch":
            args += ["--deadline-monotonic-ns", str(int(deadline * 1_000_000_000))]
    elif run_id is not None:
        raise CampaignRefused("coordinator-role")
    return _run_owned(args, deadline, refusal_categories=COORDINATOR_FAILURE_CATEGORIES)


def closure_read(role, deadline, request=None):
    binding = deployment_inputs()
    coordinator_binding()
    if role == "archive" and type(request) is bytes:
        protocol = load_module("protocol")
        doc = decode(request)
        if (
            set(doc) != {"protocol", "op", "lease_id"}
            or doc["protocol"] != protocol.PROTOCOL
            or doc["op"] != "cleanup"
        ):
            raise CampaignRefused("closure-role")
        protocol.lease_id(doc["lease_id"])
        command = (
            "sudo -n /usr/bin/python3 -I -B /opt/dotmac-lane3-handoff/"
            + binding["bundle_digest"]
            + "/lane3_handoff_install.py cleanup"
        )
        args = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ControlMaster=no",
            "-o",
            "ControlPath=none",
            "-o",
            "ConnectTimeout=8",
            binding["ssh_destination"],
            command,
        ]
    elif role == "group" and request is None:
        args = [
            "gh",
            "api",
            f"orgs/{binding['runner_org']}/actions/runner-groups/{binding['runner_group_id']}/runners?per_page=100",
        ]
    else:
        raise CampaignRefused("closure-role")
    return _run_owned(args, deadline, request)


def key_age(maximum):
    value = decode(owned(deployment_inputs()["key_receipt"], 4096, 0o600))
    if (
        set(value) != {"schema", "loaded_at", "lifetime_seconds"}
        or value["schema"] != "dotmac.l3mgmt.key-load.v1"
        or type(value["lifetime_seconds"]) is not int
        or value["lifetime_seconds"] != 480
        or type(value["loaded_at"]) is not float
    ):
        raise CampaignRefused("key-receipt")
    age = time.time() - value["loaded_at"]
    if not math.isfinite(age) or not 0 <= age <= maximum:
        raise CampaignRefused("key-age")
    return age


def durable_owned_state():
    """Recovery reads existing state only; never creates/adopts a remote lease."""
    binding = deployment_inputs()
    coordinator_binding()
    state = decode(owned(binding["state_path"], 262144, 0o600))
    base = {
        "schema",
        "lease_id",
        "qualification",
        "bootstrap_manifest_digest",
        "bootstrap_proposal_sha256",
    }
    if (
        not base <= set(state)
        or set(state)
        - base
        - {
            "run_id",
            "runner_id",
            "launch_intent",
            "bootstrap_manifest_digest",
            "bootstrap_proposal_sha256",
            "bootstrap_provenance_digest",
        }
        or state["schema"] != "dotmac.lane3.handoff-coordinator-state.v2"
        or state["qualification"] != binding["qualification"]
    ):
        raise CampaignRefused("state-ownership-unproved")
    protocol = load_module("protocol")
    protocol.lease_id(state["lease_id"])
    config = coordinator_binding()
    if state.get("bootstrap_manifest_digest") != config.get(
        "bootstrap_manifest_sha256"
    ) or state.get("bootstrap_proposal_sha256") != config.get(
        "bootstrap_proposal_sha256"
    ):
        raise CampaignRefused("state-ownership-unproved")
    for key in (
        "bootstrap_manifest_digest",
        "bootstrap_proposal_sha256",
        "bootstrap_provenance_digest",
    ):
        if key in state:
            protocol.hex64(state[key])
    if "launch_intent" in state and "bootstrap_provenance_digest" not in state:
        raise CampaignRefused("state-ownership-unproved")
    if "run_id" in state and (
        type(state["run_id"]) is not int or state["run_id"] != binding["github_run_id"]
    ):
        raise CampaignRefused("state-ownership-unproved")
    if "runner_id" in state and (
        type(state["runner_id"]) is not int or state["runner_id"] <= 0
    ):
        raise CampaignRefused("state-ownership-unproved")
    if "launch_intent" in state and state["launch_intent"] != {
        "name": "lane3-handoff-" + state["lease_id"],
        "runner_group_id": binding["runner_group_id"],
        "run_id": binding["github_run_id"],
        "qualification_digest": protocol.digest(binding["qualification"]),
    }:
        raise CampaignRefused("state-ownership-unproved")
    return state


class ManagementStdio:
    """Keep one bridge and its RAM receipt across both gates and finalization."""

    def __init__(self, process):
        self.process = process
        self.pipe = JsonPipe(process)
        self.phase = "initial"
        self.lease_id = None
        self.run_id = None
        self.finish_attempted = False
        self.prepare_attempted = False
        self.baseline_absent = False
        self.failure = None

    def _call(self, op, age, deadline):
        expected = {
            "initial": "retain",
            "retained": "gate_prepare",
            "prepared": "gate_bootstrap",
        }
        if expected.get(self.phase) != op:
            raise CampaignRefused("management-order")
        key_age(age)
        until = min(deadline, time.monotonic() + 10)
        self.pipe.send({"op": op}, until)
        reply = self.pipe.receive(until, 4096)
        if reply.get("status") != "PASS" or reply.get("op") != op:
            diagnostic = None
            if set(reply) == {"status", "diagnostic"} and reply["status"] == "REFUSED":
                diagnostic = _diagnostic(
                    reply["diagnostic"], MANAGEMENT_FAILURE_CATEGORIES
                )
                if diagnostic is not None and diagnostic["phase"] != op:
                    diagnostic = None
            raise CampaignRefused("management-unproved", diagnostic=diagnostic)
        self.phase = {
            "retain": "retained",
            "gate_prepare": "prepared",
            "gate_bootstrap": "bootstrapped",
        }[op]
        return reply

    def retained_launch(self):
        if self.phase != "initial":
            raise CampaignRefused("management-order")
        binding = deployment_inputs()
        coordinator_binding()
        # Absence before retain is essential: an unknown response cannot adopt
        # a pre-existing local coordinator journal from another campaign.
        try:
            binding["state_path"].lstat()
        except FileNotFoundError:
            self.baseline_absent = True
        else:
            raise CampaignRefused("coordinator-state-present")
        self.run_id = binding["github_run_id"]
        deadline = time.monotonic() + 60  # before retain; never reset by a gate
        attempted_phase = "retain"
        try:
            self._call("retain", 30, deadline)
            attempted_phase = "gate_prepare"
            self._call("gate_prepare", 30, deadline)
            attempted_phase = "prepare"
            self.prepare_attempted = True
            prepared = coordinator_cli("prepare", deadline, self.run_id)
            protocol = load_module("protocol")
            protocol.lease_id(prepared.get("lease_id"))
            if (
                set(prepared) != {"state", "lease_id"}
                or prepared["state"] != "PREPARED"
            ):
                raise CampaignRefused("prepare-unproved")
            self.lease_id = prepared["lease_id"]
            attempted_phase = "gate_bootstrap"
            self._call("gate_bootstrap", 60, deadline)
            if durable_owned_state()["lease_id"] != self.lease_id:
                raise CampaignRefused("state-ownership-unproved")
            attempted_phase = "launch"
            launched = coordinator_cli("launch", deadline, self.run_id)
            if launched != {"state": "BOOTSTRAP", "lease_id": self.lease_id}:
                raise CampaignRefused("bootstrap-unproved")
            return launched
        except BaseException as exc:
            diagnostic = failure_diagnostic(exc)
            if diagnostic["phase"] == "unproven":
                diagnostic["phase"] = attempted_phase
                if diagnostic["stage"] == "unproven":
                    diagnostic["stage"] = "operator"
            try:
                self.failure_finalize()
                if diagnostic["cleanup"] != "unproven":
                    diagnostic["cleanup"] = "completed"
            except BaseException:  # timers remain independent authority
                self.failure = "UNPROVEN"
                diagnostic["cleanup"] = "unproven"
            if type(exc) is CampaignRefused:
                exc.diagnostic = diagnostic
                raise
            raise CampaignRefused("unproven", diagnostic=diagnostic) from None

    def close(self):
        """Signal EOF only; the launcher still owns termination and reaping."""
        if (
            self.process is not None
            and self.process.stdin
            and not self.process.stdin.closed
        ):
            self.process.stdin.close()

    def failure_finalize(self):
        if self.phase not in ("retained", "prepared", "bootstrapped"):
            raise CampaignRefused("failure-state-unproved")
        if self.lease_id is None:
            if not self.prepare_attempted or not self.baseline_absent:
                # No verified archive exists. Preserve bridge and timer rather
                # than manufacturing permission to remove management trust.
                raise CampaignRefused("failure-state-unproved")
            self.lease_id = durable_owned_state()["lease_id"]
        return self.finish()

    def finish(self):
        if (
            self.phase not in ("retained", "prepared", "bootstrapped")
            or self.lease_id is None
            or self.finish_attempted
        ):
            raise CampaignRefused("finish-order")
        self.finish_attempted = True
        if durable_owned_state()["lease_id"] != self.lease_id:
            raise CampaignRefused("state-ownership-unproved")
        deadline = time.monotonic() + 180
        closed = coordinator_cli("cleanup", deadline)
        if set(closed) != {"state", "evidence"} or closed["state"] != "CLOSED":
            raise CampaignRefused("cleanup-unproved")
        protocol = load_module("protocol")
        request = json.dumps(
            {"protocol": protocol.PROTOCOL, "op": "cleanup", "lease_id": self.lease_id},
            separators=(",", ":"),
        ).encode()
        archived = closure_read("archive", deadline, request)
        if (
            set(archived) != {"protocol", "op", "lease_id", "state", "evidence"}
            or archived["protocol"] != protocol.PROTOCOL
            or archived["op"] != "cleanup"
            or archived["state"] != "CLOSED"
            or archived["lease_id"] != self.lease_id
            or archived["evidence"] != closed["evidence"]
        ):
            raise CampaignRefused("archive-unproved")
        try:
            proof = protocol.validate_evidence(archived["evidence"])
        except (ValueError, TypeError, KeyError):
            raise CampaignRefused("archive-unproved") from None
        if (
            proof["lease_id"] != self.lease_id
            or proof["cleanup_blocked"] is not None
            or type(proof["cleanup"]) is not dict
            or any(proof["cleanup"].get(key) is not True for key in CLEANUP)
        ):
            raise CampaignRefused("archive-unproved")
        members = closure_read("group", deadline)
        if (
            type(members.get("total_count")) is not int
            or members["total_count"] != 0
            or members.get("runners") != []
        ):
            raise CampaignRefused("group-not-empty")
        self.pipe.send({"op": "finish"}, deadline)
        reply = self.pipe.receive(deadline, 4096)
        if (
            set(reply) != {"status", "op", "local_journal_absent"}
            or reply["status"] != "CLOSED"
            or reply["op"] != "finish"
            or reply["local_journal_absent"] is not True
        ):
            raise CampaignRefused("management-removal-unproved")
        self.phase = "finished"
        return {"archive": archived, "group_empty": True, "management": reply}


class B8Stdio:
    """Public exchange only after same-parent prepared custody, once."""

    def __init__(self):
        self.issuer = load_issuer()
        self.prepared = None
        self.used = False

    def prepare_custody(self):
        if self.used or self.prepared is not None:
            raise CampaignRefused("custody-order")
        self.prepared = self.issuer.prepare_custody()

    def exchange(self, process):
        if self.used or self.prepared is None:
            raise CampaignRefused("custody-not-prepared")
        self.used = True
        pipe = JsonPipe(process)
        try:
            pipe.send({"op": "prepare"}, time.monotonic() + 5)
            message = pipe.receive(time.monotonic() + 60, 16384)
            received_at = time.monotonic()
            if (
                set(message) != {"op", "requests", "remaining_seconds"}
                or message["op"] != "requests"
                or type(message["requests"]) is not dict
                or type(message["remaining_seconds"]) not in (int, float)
                or not math.isfinite(message["remaining_seconds"])
                or not 0 < message["remaining_seconds"] <= 240
            ):
                raise CampaignRefused("csr-schema")
            deadline = received_at + message["remaining_seconds"]
            issued = self.issuer.issue(
                message["requests"],
                remaining_seconds=remaining(deadline),
                prepared_custody=self.prepared,
            )
            remaining(deadline)
            pipe.send({"op": "issued", "issued": issued}, deadline)
            issued = None
            reply = pipe.receive(deadline + 60)
            if set(reply) != {"op", "result"} or reply["op"] != "closed":
                raise CampaignRefused("b8-cleanup-unproved")
            result = reply["result"]
            if (
                type(result) is not dict
                or set(result)
                != {"matrix_result", "full_b8_complete", "admission_changed"}
                or type(result["matrix_result"]) is not dict
                or result["full_b8_complete"] is not False
                or result["admission_changed"] is not False
            ):
                raise CampaignRefused("b8-result")
            process.wait(timeout=min(5, remaining(deadline + 60)))
            if process.returncode != 0:
                raise CampaignRefused("b8-cleanup-unproved")
            return result
        finally:
            # EOF enters the VM's existing finally; independent timers retain
            # authority when transport or root cleanup proof is unavailable.
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            self.close()

    def close(self):
        if self.prepared is not None:
            prepared, self.prepared = self.prepared, None
            prepared.close()
