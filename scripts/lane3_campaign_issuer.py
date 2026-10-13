"""Canonical fixed-route adapter; preparation only, no import-time live operations.

Caller establishes custody readiness before client.prepare(); invoking issue is
live authority only under the separately approved concrete campaign packet.
"""

import ast
import base64
import copy
import hashlib
import importlib.util
import json
import math
import os
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

RECOVERY_SECONDS = 300
RECOVERY_TRANSIT_SECONDS = 10
# Positive operation budget only; remote recovery has a separate deadline.
# This is not a claim that the real ceremony fits, or a90-second matrix gate.
MIN_ISSUER_SECONDS = 1
REQUIRED = (
    "temporary_policy_removed",
    "signing_token_revoked",
    "root_revoked",
    "existing_roles_and_CAs_unchanged",
    "cleanup_completed_before_matrix",
)
NAMES = {
    "observer",
    "jump",
    "probe",
    "expired_observer",
    "wrong_principal",
    "gate0_observer",
    "gate0_jump",
}


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
        "remote_pins",
        "client_sha256",
        "issuer_source_sha256",
        "client_module",
        "ssh_destination",
        "custody_sha256",
        "custody_module",
        "custody_notes",
        "remote_output_directory",
        "remote_configure",
        "encrypted_shares",
    }:
        raise ValueError("deployment-schema")
    candidate = copy.deepcopy(deployment)
    for key in {
        "custody_module",
        "client_module",
        "remote_output_directory",
        "remote_configure",
        "encrypted_shares",
    }:
        value = candidate[key]
        if type(value) is not str or not Path(value).is_absolute() or "\x00" in value:
            raise ValueError("deployment-path")
        candidate[key] = Path(value)
    notes = candidate["custody_notes"]
    if (
        type(notes) is not list
        or len(notes) != 2
        or any(type(p) is not str or not Path(p).is_absolute() for p in notes)
        or notes[0] == notes[1]
    ):
        raise ValueError("deployment-notes")
    candidate["custody_notes"] = tuple(Path(p) for p in notes)
    pins = candidate["remote_pins"]
    if (
        type(pins) is not dict
        or {Path(p).name for p in pins if type(p) is str}
        != {
            "b8-issuer-configure.sh",
            "b8-proof-issuer-operation.py",
            "b8-proof-issuer-core.py",
            "b8-proof-issuer-model.py",
            "b8-proof-matrix.py",
            "b8-base-model.py",
            "b8-probe-model.py",
        }
        or len(pins) != 7
        or any(
            type(p) is not str
            or not Path(p).is_absolute()
            or type(h) is not str
            or len(h) != 64
            or any(c not in "0123456789abcdef" for c in h)
            for p, h in pins.items()
        )
    ):
        raise ValueError("deployment-pins")
    if (
        str(candidate["remote_configure"]) not in pins
        or candidate["remote_configure"].name != "b8-issuer-configure.sh"
    ):
        raise ValueError("deployment-configure-pin")
    candidate["remote_pins"] = dict(pins)
    if (
        type(candidate["ssh_destination"]) is not str
        or not candidate["ssh_destination"]
        or candidate["ssh_destination"].startswith("-")
        or any(c.isspace() for c in candidate["ssh_destination"])
    ):
        raise ValueError("deployment-destination")
    for key in ("custody_sha256", "client_sha256", "issuer_source_sha256"):
        pin = candidate[key]
        if (
            type(pin) is not str
            or len(pin) != 64
            or any(c not in "0123456789abcdef" for c in pin)
        ):
            raise ValueError("deployment-pins")
    if (
        candidate["client_module"].name != "lane3_campaign_client.py"
        or candidate["custody_module"].name != "run-from-custody.py"
    ):
        raise ValueError("deployment-module")
    DEPLOYMENT = candidate


def deployment_inputs():
    if DEPLOYMENT is None:
        raise RuntimeError("protected-deployment-required")
    return copy.deepcopy(DEPLOYMENT)


class IssuerRefused(RuntimeError):
    pass


def load_pinned(name, path):
    path = Path(path)
    deployment = deployment_inputs()
    roles = {
        "b8_pinned_client_parser": (
            deployment["client_module"],
            deployment["client_sha256"],
        ),
        "b8_approved_custody": (
            deployment["custody_module"],
            deployment["custody_sha256"],
        ),
    }
    if name not in roles or roles[name][0] != path:
        raise IssuerRefused("issuer.module_role")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or not 0 < info.st_size <= 2_000_000
        ):
            raise IssuerRefused("issuer.local_pin")
        raw = os.read(fd, info.st_size + 1)
        if (
            len(raw) != info.st_size
            or hashlib.sha256(raw).hexdigest() != roles[name][1]
        ):
            raise IssuerRefused("issuer.local_pin")
    finally:
        os.close(fd)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def custody_available():
    """Metadata only, called before client key generation; never decrypts."""
    for p in deployment_inputs()["custody_notes"]:
        s = p.lstat()
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != os.getuid()
            or s.st_nlink != 1
            or stat.S_IMODE(s.st_mode) != 0o600
        ):
            raise IssuerRefused("issuer.custody_unavailable")
    return True


def left(deadline, reserve=0):
    value = deadline - time.monotonic() - reserve
    if value <= 0:
        raise IssuerRefused("issuer.deadline_cleanup_unproved")
    return value


def receive(fd, deadline, limit):
    result = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while len(result) <= limit:
            if not selector.select(left(deadline)):
                raise IssuerRefused("issuer.deadline_cleanup_unproved")
            chunk = os.read(fd, min(4096, limit + 1 - len(result)))
            if not chunk:
                return bytes(result)
            result.extend(chunk)
    raise IssuerRefused("issuer.response")


def settle(proc, recovery_deadline):
    """Keep/drain the channel for bounded remote recovery; no success inference."""
    if proc.stdin and not proc.stdin.closed:
        proc.stdin.close()
    if proc.poll() is None:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while proc.poll() is None and time.monotonic() < recovery_deadline:
                events = selector.select(
                    min(1, max(0, recovery_deadline - time.monotonic()))
                )
                if events:
                    if not os.read(proc.stdout.fileno(), 4096):
                        break
        try:
            proc.wait(timeout=max(0.01, recovery_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            # This only closes the Mac channel; remote HUP-ignoring supervisor
            # has its own hard recovery ceiling. Neither implies host closure.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass


class PreparedCustody:
    """Process-local one-shot custody state; never serialize or log this object."""

    __slots__ = ("_consumed", "_parser", "_proc", "_recovery_deadline", "_shares")

    def __init__(self, proc, shares, parser):
        self._proc, self._shares, self._parser = proc, shares, parser
        self._consumed = False
        self._recovery_deadline = None

    def __repr__(self):
        return "<PreparedCustody redacted>"

    def __getstate__(self):
        raise IssuerRefused("issuer.custody_not_serializable")

    def close(self):
        self._shares = None
        self._consumed = True
        if self._proc and self._proc.poll() is None:
            if self._recovery_deadline is not None:
                settle(self._proc, self._recovery_deadline)
                return
            try:
                os.killpg(self._proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass


def prepare_custody():
    """Human readiness phase BEFORE client keys/material clock begin.

    Authenticate the fixed direct channel and verify staged bytes first, then
    reuse approved A/B decryption in this process. Its legacy cleanup may take
    time: no ephemeral client keys may exist during this phase. Caller closes
    the prepared state if client preparation or any later gate fails.
    """
    proc = None
    shares = bundle = None
    try:
        custody_available()
        parser = load_pinned(
            "b8_pinned_client_parser", deployment_inputs()["client_module"]
        )
        custody = load_pinned(
            "b8_approved_custody", deployment_inputs()["custody_module"]
        )
        args = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ProxyCommand=none",
            "-o",
            "ProxyJump=none",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "ConnectTimeout=20",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=2",
            deployment_inputs()["ssh_destination"],
            "python3 -I -c " + shlex.quote(remote_code()),
        ]
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            bufsize=0,
        )
        deadline = time.monotonic() + 30
        marker = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while not marker.endswith(b"\n") and len(marker) < 64:
                if not selector.select(left(deadline)):
                    raise IssuerRefused("issuer.ready_deadline")
                ch = os.read(proc.stdout.fileno(), 1)
                if not ch:
                    raise IssuerRefused("issuer.ssh_not_ready")
                marker.extend(ch)
        if bytes(marker) != b"B8_DIRECT_READY_V1\n":
            raise IssuerRefused("issuer.ready")
        bundle = json.loads(deployment_inputs()["encrypted_shares"].read_text())
        if bundle.get("recipient_order") != ["A", "B", "C"]:
            raise IssuerRefused("issuer.custody_order")
        shares = [
            custody.decrypt(
                slot,
                base64.b64decode(bundle["encrypted_shares_base64"][i], validate=True),
            )
            for i, slot in enumerate(("A", "B"))
        ]
        if len(set(shares)) != 2 or not all(isinstance(x, str) and x for x in shares):
            raise IssuerRefused("issuer.custody_distinct")
        if proc.poll() is not None:
            raise IssuerRefused("issuer.ssh_not_ready")
        prepared = PreparedCustody(proc, shares, parser)
        shares = bundle = None
        proc = None
        return prepared
    except BaseException:
        raise IssuerRefused("issuer.custody_preparation_stopped") from None
    finally:
        shares = bundle = None
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        for note in deployment_inputs()["custody_notes"]:
            try:
                note.unlink()
            except FileNotFoundError:
                pass


def issue(requests, *, remaining_seconds, prepared_custody):
    """Return actual issuance evidence; no automatic retry or credential output."""
    if (
        type(remaining_seconds) not in (int, float)
        or not math.isfinite(remaining_seconds)
        or not MIN_ISSUER_SECONDS < remaining_seconds <= 240
    ):
        raise IssuerRefused("issuer.budget_insufficient")
    deadline = time.monotonic() + remaining_seconds
    recovery_deadline = deadline + RECOVERY_SECONDS + RECOVERY_TRANSIT_SECONDS
    proc = None
    payload = shares = None
    try:
        if type(prepared_custody) is not PreparedCustody or prepared_custody._consumed:
            raise IssuerRefused("issuer.custody_not_prepared")
        prepared_custody._consumed = True
        prepared_custody._recovery_deadline = recovery_deadline
        proc, shares, parser = (
            prepared_custody._proc,
            prepared_custody._shares,
            prepared_custody._parser,
        )
        prepared_custody._shares = None
        if proc.poll() is not None:
            raise IssuerRefused("issuer.ssh_not_ready")
        if (
            type(requests) is not dict
            or set(requests) != {"jump_principal", "probe_principal", "public_keys"}
            or requests["jump_principal"] != "lane3jump"
            or type(requests["public_keys"]) is not dict
            or set(requests["public_keys"]) != NAMES
        ):
            raise IssuerRefused("issuer.requests")
        seconds = math.floor(left(deadline))
        if seconds <= MIN_ISSUER_SECONDS:
            raise IssuerRefused("issuer.budget_insufficient")
        payload = (
            json.dumps(
                {"seconds": seconds, "payload": dict(requests, shares=shares)},
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        shares = None
        # The response and receipt/certificate fetch share one deadline.
        if len(payload) > 16384:
            raise IssuerRefused("issuer.requests_size")
        os.set_blocking(proc.stdin.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdin, selectors.EVENT_WRITE)
            view = memoryview(payload)
            while view:
                if not selector.select(left(deadline)):
                    raise IssuerRefused("issuer.input_deadline")
                try:
                    view = view[os.write(proc.stdin.fileno(), view) :]
                except BlockingIOError:
                    continue
        view = None
        proc.stdin.close()
        payload = None
        raw = receive(proc.stdout.fileno(), deadline, 262144)
        proc.wait(timeout=left(deadline))
        if proc.returncode != 0:
            raise IssuerRefused("issuer.remote_cleanup_unproved")
        result = json.loads(raw)
        if set(result) != {"public_record", "certificates"}:
            raise IssuerRefused("issuer.response")
        record = result["public_record"]
        certs = result["certificates"]
        if (
            type(record) is not dict
            or type(certs) is not dict
            or set(certs) != NAMES
            or any(record.get(k) is not True for k in REQUIRED)
            or type(record.get("temporary_roles_removed")) is not int
            or record["temporary_roles_removed"] != 4
            or type(record.get("certificate_count")) is not int
            or record["certificate_count"] != 7
            or record.get("packet") != "B8-scoped-issuer"
            or record.get("matrix_executed") is not False
            or record.get("breakglass_cleanup_pending") is not False
        ):
            raise IssuerRefused("issuer.cleanup_evidence")
        intervals = {n: parser.certificate_interval(c) for n, c in certs.items()}
        if intervals["expired_observer"][1] > time.time():
            raise IssuerRefused("issuer.expiry")
        expiry = min(v[1] for n, v in intervals.items() if n != "expired_observer")
        if expiry <= time.time():
            raise IssuerRefused("issuer.expiry")
        left(deadline)
        return dict(result, breakglass_closed=True, valid_until_epoch=expiry)
    except BaseException:
        raise IssuerRefused("issuer.stopped_cleanup_requires_readback") from None
    finally:
        payload = shares = None
        if proc and proc.poll() is None:
            settle(proc, recovery_deadline)
        if type(prepared_custody) is PreparedCustody:
            prepared_custody._shares = None
            prepared_custody._consumed = True


def remote_main(deployment):
    import hashlib
    import json
    import os
    import pathlib
    import shlex
    import signal
    import stat
    import subprocess
    import sys
    import time

    # Own cleanup after channel HUP; do not depend on the Mac process surviving.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    try:
        os.setsid()
    except PermissionError:
        pass
    PINS = deployment["remote_pins"]
    for name, want in PINS.items():
        p = pathlib.Path(name)
        s = p.lstat()
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_nlink != 1
            or s.st_mode & 0o022
            or hashlib.sha256(p.read_bytes()).hexdigest() != want
        ):
            raise SystemExit(2)
    D = pathlib.Path(deployment["remote_output_directory"])
    for name in (
        "certificates-private.json",
        "b8-b8-issuer-record.json",
        "b8-b8-issuer-failure-record.json",
    ):
        if (D / name).exists():
            raise SystemExit(2)
    print("B8_DIRECT_READY_V1", flush=True)
    raw = sys.stdin.buffer.readline(131073)
    if len(raw) > 131072 or not raw.endswith(b"\n"):
        raise SystemExit(2)
    envelope = json.loads(raw)
    raw = None
    if (
        set(envelope) != {"seconds", "payload"}
        or type(envelope["seconds"]) is not int
        or envelope["seconds"] <= 1
    ):
        raise SystemExit(2)
    for name, want in PINS.items():
        p = pathlib.Path(name)
        s = p.lstat()
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_nlink != 1
            or s.st_mode & 0o022
            or hashlib.sha256(p.read_bytes()).hexdigest() != want
        ):
            raise SystemExit(2)
    accept_deadline = time.monotonic() + envelope["seconds"]
    # Hard recovery ceiling, not a total cleanup budget: nested core finally
    # can consume285s of bounded API waits before outer cleanup (up to168s).
    # Docker restart and gpgconf are additionally unbounded. At300s recovery
    # cleanup is UNPROVEN and requires independent root fallback.
    recovery_deadline = accept_deadline + 300
    expired = False
    data = json.dumps(envelope["payload"]).encode()
    envelope = None
    p = subprocess.Popen(
        [
            "/bin/bash",
            "-c",
            "APPLY=1 B8_APPROVED=1 SCOPE=b8-issuer A4_RECOVERY_FD=3 bash "
            + shlex.quote(deployment["remote_configure"])
            + " 3<&0",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    def recover():
        if p.poll() is not None:
            return
        try:
            os.killpg(p.pid, signal.SIGINT)
        except ProcessLookupError:
            return
        try:
            p.communicate(timeout=max(0.1, recovery_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            # Hard fallback boundary: cleanup unproved, explicit packet root recovery.
            os.killpg(p.pid, signal.SIGKILL)
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass

    try:
        try:
            _out, _err = p.communicate(
                data, timeout=max(0.1, accept_deadline - time.monotonic())
            )
            data = None
        except subprocess.TimeoutExpired:
            expired = True
            data = None
            recover()
            raise SystemExit(3) from None
        if p.returncode or time.monotonic() >= accept_deadline:
            raise SystemExit(3)

        def read(name):
            q = D / name
            s = q.lstat()
            if (
                not stat.S_ISREG(s.st_mode)
                or s.st_uid != 0
                or s.st_nlink != 1
                or stat.S_IMODE(s.st_mode) != 0o600
                or s.st_size > 131072
            ):
                raise SystemExit(3)
            return json.loads(q.read_text())

        record = read("b8-b8-issuer-record.json")
        certificates = read("certificates-private.json")
        if (
            record.get("breakglass_cleanup_pending") is not False
            or time.monotonic() >= accept_deadline
        ):
            raise SystemExit(3)
        print(
            json.dumps(
                {"public_record": record, "certificates": certificates},
                separators=(",", ":"),
            ),
            flush=True,
        )
    finally:
        data = None
        # Never retry cooperative signals while cleanup is already in progress.
        if not expired:
            recover()


def remote_code():
    """Render the fixed supervisor only from externally accepted source bytes.

    The pin comes from protected deployment acceptance, never this file's own
    digest. Read once, verify once, and extract from those same measured bytes.
    """
    deployment = deployment_inputs()
    fd = os.open(__file__, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or not 0 < info.st_size <= 2_000_000
        ):
            raise IssuerRefused("issuer.source_pin")
        raw = os.read(fd, info.st_size + 1)
        if (
            len(raw) != info.st_size
            or hashlib.sha256(raw).hexdigest() != deployment["issuer_source_sha256"]
        ):
            raise IssuerRefused("issuer.source_pin")
    finally:
        os.close(fd)
    source = raw.decode("utf-8")
    functions = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "remote_main"
    ]
    if len(functions) != 1 or functions[0].decorator_list:
        raise IssuerRefused("issuer.supervisor_source")
    supervisor = ast.get_source_segment(source, functions[0])
    if supervisor is None:
        raise IssuerRefused("issuer.supervisor_source")
    remote = {
        key: str(deployment[key])
        if isinstance(deployment[key], Path)
        else deployment[key]
        for key in ("remote_pins", "remote_output_directory", "remote_configure")
    }
    return supervisor + "\nremote_main(" + repr(remote) + ")\n"
