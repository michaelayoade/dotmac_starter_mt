"""Canonical public CSR/certificate session bridge.

Only the separately approved Mac owner invokes this after custody readiness,
OIDC lease/JIT cleanup and management removal. No custody material is accepted.
"""

import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import selectors
import stat
import sys
import time
from pathlib import Path

NAMES = {
    "observer",
    "jump",
    "probe",
    "expired_observer",
    "wrong_principal",
    "gate0_observer",
    "gate0_jump",
}
REQUIRED = (
    "temporary_policy_removed",
    "signing_token_revoked",
    "root_revoked",
    "existing_roles_and_CAs_unchanged",
    "cleanup_completed_before_matrix",
)


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
        "source_head",
        "libexec",
        "input",
        "manifest",
        "lock",
        "pins",
    }:
        raise ValueError("deployment-schema")
    candidate = copy.deepcopy(deployment)
    for key in {"libexec", "input", "lock", "manifest"}:
        value = candidate[key]
        if type(value) is not str or not Path(value).is_absolute() or "\x00" in value:
            raise ValueError("deployment-path")
        candidate[key] = Path(value)
    pins = candidate["pins"]
    if type(pins) is not dict or set(pins) != {
        "lane3_campaign_composition.py",
        "lane3_campaign_identity.py",
        "lane3_campaign_transport.py",
        "b8-source-qualification.py",
        "lane3_campaign_client.py",
        "b8-proof-matrix.py",
        "lane3_campaign_runner.py",
    }:
        raise ValueError("deployment-pins")
    if any(
        type(pin) is not str
        or len(pin) != 64
        or any(c not in "0123456789abcdef" for c in pin)
        for pin in pins.values()
    ):
        raise ValueError("deployment-pins")
    candidate["pins"] = dict(pins)
    head = candidate["source_head"]
    if (
        type(head) is not str
        or len(head) != 40
        or any(c not in "0123456789abcdef" for c in head)
    ):
        raise ValueError("deployment-source")
    DEPLOYMENT = candidate


def deployment_inputs():
    if DEPLOYMENT is None:
        raise RuntimeError("protected-deployment-required")
    return copy.deepcopy(DEPLOYMENT)


class RemoteRefused(RuntimeError):
    pass


def pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise RemoteRefused("duplicate-key")
        value[key] = item
    return value


def reject_constant(_):
    raise RemoteRefused("nonfinite-json")


def decode(raw):
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)
    if type(value) is not dict:
        raise RemoteRefused("object-required")
    return value


def parents(path):
    for directory in (path.parent, *path.parent.parents):
        s = directory.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise RemoteRefused("protected-parent")


def read(path, mode, limit):
    parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        s = os.fstat(fd)
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_gid != 0
            or s.st_nlink != 1
            or stat.S_IMODE(s.st_mode) != mode
            or not 0 < s.st_size <= limit
        ):
            raise RemoteRefused("protected-file")
        raw = os.read(fd, limit + 1)
        if len(raw) != s.st_size:
            raise RemoteRefused("protected-file")
        return raw
    finally:
        os.close(fd)


@contextlib.contextmanager
def exclusive():
    parents(deployment_inputs()["lock"])
    fd = os.open(
        deployment_inputs()["lock"],
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        s = os.fstat(fd)
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_gid != 0
            or s.st_nlink != 1
            or stat.S_IMODE(s.st_mode) != 0o600
        ):
            raise RemoteRefused("lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def modules():
    verified = {}
    for name, pin in deployment_inputs()["pins"].items():
        mode = (
            0o644
            if name
            in (
                "lane3_campaign_runner.py",
                "b8-proof-matrix.py",
                "b8-source-qualification.py",
            )
            else 0o444
        )
        raw = read(deployment_inputs()["libexec"] / name, mode, 2_000_000)
        if hashlib.sha256(raw).hexdigest() != pin:
            raise RemoteRefused("code-pin")
        verified[name] = raw
    loaded = {}
    for name in (
        "lane3_campaign_client.py",
        "lane3_campaign_identity.py",
        "lane3_campaign_transport.py",
        "lane3_campaign_composition.py",
    ):
        module_name = "_private_b8_" + name.replace(".py", "").replace("-", "_")
        spec = importlib.util.spec_from_file_location(
            module_name, deployment_inputs()["libexec"] / name
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        exec(  # noqa: S102 - only hash-checked fixed-role bytes
            compile(verified[name], str(deployment_inputs()["libexec"] / name), "exec"),
            module.__dict__,
        )
        loaded[name] = module
    return loaded


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise RemoteRefused("deadline")
    return seconds


def receive(deadline, limit):
    raw = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(sys.stdin.buffer, selectors.EVENT_READ)
        while len(raw) < limit:
            if not selector.select(remaining(deadline)):
                raise RemoteRefused("deadline")
            chunk = os.read(sys.stdin.fileno(), 1)
            if not chunk:
                raise RemoteRefused("eof")
            raw.extend(chunk)
            if chunk == b"\n":
                return decode(bytes(raw))
    raise RemoteRefused("input-size")


def emit(value, deadline):
    data = (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        + b"\n"
    )
    fd = sys.stdout.fileno()
    os.set_blocking(fd, False)
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_WRITE)
        while data:
            if not selector.select(remaining(deadline)):
                raise RemoteRefused("output-deadline")
            try:
                data = data[os.write(fd, data) :]
            except BlockingIOError:
                continue


def inputs(c, t):
    doc = decode(read(deployment_inputs()["input"], 0o600, 262144))
    if (
        set(doc)
        != {
            "schema",
            "source_head",
            "manifest_sha256",
            "client_approval_sha256",
            "transport_binding",
            "cfg",
            "principals",
        }
        or doc["schema"] != "dotmac.b8.remote-session.v1"
        or doc["source_head"] != deployment_inputs()["source_head"]
        or type(doc["cfg"]) is not dict
        or type(doc["principals"]) is not dict
        or type(doc["transport_binding"]) is not dict
    ):
        raise RemoteRefused("input-schema")
    raw = read(deployment_inputs()["manifest"], 0o600, 16384)
    manifest = t.Manifest.parse(decode(raw))
    if (
        raw != c.canonical(manifest.document())
        or hashlib.sha256(raw).hexdigest() != manifest.digest
        or doc["manifest_sha256"] != manifest.digest
    ):
        raise RemoteRefused("manifest-binding")
    binding = t.Binding(**doc["transport_binding"])
    cfg = doc["cfg"]
    session = binding.session_id
    if (
        binding.manifest_sha256 != manifest.digest
        or binding.helper_sha256
        != deployment_inputs()["pins"]["lane3_campaign_transport.py"]
        or cfg.get("session_id") != session
        or manifest.user != "l3b8-" + session[:12]
        or cfg.get("network_namespace") != "/proc/1/ns/net"
    ):
        raise RemoteRefused("session-binding")
    for purpose in ("target", "jump", "probe"):
        endpoint = cfg.get(purpose)
        addresses = {d.address for d in manifest.destinations if d.purpose == purpose}
        if (
            type(endpoint) is not dict
            or endpoint.get("host") not in addresses
            or type(endpoint.get("port")) is not int
            or endpoint["port"] != 22
        ):
            raise RemoteRefused("endpoint-binding")
    identity = c.RunnerIdentity(
        manifest.user, manifest.uid, manifest.gid, manifest.digest
    )
    identity.validate()
    if (
        c.approval_digest(
            identity, cfg, deployment_inputs()["pins"]["lane3_campaign_runner.py"]
        )
        != doc["client_approval_sha256"]
    ):
        raise RemoteRefused("client-approval")
    return doc, manifest, binding, identity


def issuer_exchange(client, requests, *, remaining_seconds):
    # CSR public keys only. Private keys remain in the independently timed VM
    # workspace; shares, tokens and issuer credentials never cross this channel.
    deadline = client.window.execution_deadline_ns / 1_000_000_000
    if not math.isfinite(remaining_seconds) or remaining_seconds <= 0:
        raise RemoteRefused("deadline")
    emit(
        {
            "op": "requests",
            "requests": requests,
            "remaining_seconds": remaining_seconds,
        },
        deadline,
    )
    message = receive(deadline, 262144)
    if (
        set(message) != {"op", "issued"}
        or message["op"] != "issued"
        or type(message["issued"]) is not dict
    ):
        raise RemoteRefused("issued-schema")
    issued = message["issued"]
    if set(issued) != {
        "certificates",
        "public_record",
        "breakglass_closed",
        "valid_until_epoch",
    }:
        raise RemoteRefused("issued-schema")
    record = issued["public_record"]
    certificates = issued["certificates"]
    if (
        type(record) is not dict
        or set(record)
        != set(REQUIRED)
        | {
            "certificate_count",
            "temporary_roles_removed",
            "packet",
            "matrix_executed",
            "breakglass_cleanup_pending",
        }
        or type(certificates) is not dict
        or set(certificates) != NAMES
        or any(
            type(value) is not str
            or len(value) > 16384
            or not value.startswith("ssh-ed25519-cert-v01@openssh.com ")
            for value in certificates.values()
        )
        or any(record.get(k) is not True for k in REQUIRED)
        or type(record.get("certificate_count")) is not int
        or record["certificate_count"] != 7
        or type(record.get("temporary_roles_removed")) is not int
        or record["temporary_roles_removed"] != 4
        or issued["breakglass_closed"] is not True
        or record.get("breakglass_cleanup_pending") is not False
        or record.get("packet") != "B8-scoped-issuer"
        or record.get("matrix_executed") is not False
        or type(issued["valid_until_epoch"]) not in (int, float)
        or not math.isfinite(issued["valid_until_epoch"])
    ):
        raise RemoteRefused("issuer-cleanup-unproved")
    client.remaining()
    return issued


def execute():
    loaded = modules()
    c = loaded["lane3_campaign_client.py"]
    i = loaded["lane3_campaign_identity.py"]
    t = loaded["lane3_campaign_transport.py"]
    composition = loaded["lane3_campaign_composition.py"]
    doc, manifest, binding, identity = inputs(c, t)
    # This authenticated one-shot command is sent by the Mac owner only after
    # prepare_custody completed. It conveys phase, never authorizes deployment.
    if receive(time.monotonic() + 30, 1024) != {"op": "prepare"}:
        raise RemoteRefused("prepare-schema")
    owned = i.OwnedIdentity(binding.session_id, identity.uid, identity.gid, seconds=240)
    transport = t.Transport(binding, manifest, t.SystemBackend(binding))
    client = c.Session(
        identity,
        deployment_inputs()["manifest"],
        (deployment_inputs()["libexec"] / "lane3_campaign_runner.py"),
        deployment_inputs()["pins"]["lane3_campaign_runner.py"],
        doc["client_approval_sha256"],
        doc["cfg"],
        custody_ready=True,
    )
    attempted = False
    try:
        created = owned.create()
        if created != {"user": identity.user, "uid": identity.uid, "gid": identity.gid}:
            raise RemoteRefused("identity-unproved")
        attempted = True
        result = composition.run_b8(
            client=client,
            transport=transport,
            owned_identity=owned,
            issuer=lambda requests, remaining_seconds: issuer_exchange(
                client, requests, remaining_seconds=remaining_seconds
            ),
            write_execution_deadline=lambda live, data: c.write_at(
                live.workfd,
                "execution.json",
                data,
                live.identity.uid,
                live.identity.gid,
            ),
            principal_input=doc["principals"],
        )
        emit({"op": "closed", "result": result}, time.monotonic() + 5)
    finally:
        if not attempted:
            owned.cleanup()


def main():
    os.umask(0o077)
    sys.dont_write_bytecode = True
    try:
        if os.geteuid() != 0 or not sys.flags.isolated or sys.argv[1:]:
            raise RemoteRefused("invocation")
        if DEPLOYMENT is None:
            configure(
                decode(
                    read(
                        Path("/etc/dotmac-lane3-campaign/remote-deployment.json"),
                        0o444,
                        16384,
                    )
                )
            )
        with exclusive():
            execute()
        return 0
    except BaseException:
        try:
            emit(
                {"status": "REFUSED", "category": "remote-session-unproved"},
                time.monotonic() + 1,
            )
        except BaseException:  # noqa: S110 - private diagnostics suppressed
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
