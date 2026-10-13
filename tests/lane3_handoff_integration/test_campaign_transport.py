"""Real nft UID filters and durable timer/CLI replay in an owned network ns."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import pwd
import signal
import subprocess
import time
import uuid

import lane3_campaign_transport as t
import pytest


class Host(t.SystemBackend):
    def __init__(self, binding, namespace):
        super().__init__(binding)
        self.namespace = namespace
        self.lost = False

    def run(self, argv, data=None):
        if argv[0] == "/usr/sbin/nft":
            argv = ["/usr/bin/nsenter", "--net=" + self.namespace, *argv]
        elif argv[0] == "/usr/bin/systemd-run":
            argv = [
                argv[0],
                "--property=NetworkNamespacePath=" + self.namespace,
                *argv[1:],
            ]
        return super().run(argv, data)

    def install(self, specs, uid, anchor):
        super().install(specs, uid, anchor)
        if self.lost:
            raise TimeoutError("PRIVATE-LOST-OPEN-CANARY")


@pytest.fixture
def transport(tmp_path, monkeypatch):
    table = "l3h_b8_" + uuid.uuid4().hex
    session = uuid.uuid4().hex
    user = "l3b8-" + session[:12]
    subprocess.run(  # noqa: S603 - own unique fixture account
        [
            "/usr/sbin/useradd",
            "--user-group",
            "--no-create-home",
            "--shell",
            "/usr/sbin/nologin",
            user,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ns = subprocess.Popen(  # noqa: S603 - fixed namespace keeper
        ["/usr/bin/unshare", "--net", "/bin/sleep", "120"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    namespace = f"/proc/{ns.pid}/ns/net"
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if ns.poll() is not None:
                raise AssertionError("namespace failed")
            if (
                pathlib.Path(namespace).exists()
                and pathlib.Path(namespace).readlink()
                != pathlib.Path("/proc/self/ns/net").readlink()
            ):
                break
            time.sleep(0.02)
        else:
            raise AssertionError("namespace unproven")
        helper = tmp_path / "lane3_campaign_transport.py"
        metadata = tmp_path / "transport.json"
        host_config = tmp_path / "host.json"
        host_config.write_text(
            json.dumps(
                {
                    "schema": "dotmac.lane3.handoff-host.v1",
                    "firewall": {
                        "family": "inet",
                        "table": table,
                        "chain": "output",
                        "anchor_comment": "dropped-egress",
                    },
                }
            )
        )
        host_config.chmod(0o600)
        state = tmp_path / "state"
        owner_root = tmp_path / "identities"
        owner_root.mkdir(mode=0o700)
        owner_directory = owner_root / session
        owner_directory.mkdir(mode=0o700)
        source = pathlib.Path(t.__file__).read_text()
        source = (
            source.replace(t.HELPER, str(helper))
            .replace(t.METADATA, str(metadata))
            .replace(t.HOST_CONFIG, str(host_config))
            .replace(t.STATE_ROOT, str(state))
            .replace(t.OWNER_ROOT, str(owner_root))
            .replace('TABLE="dotmac_egress"', 'TABLE="' + table + '"')
            .replace('TABLE = "dotmac_egress"', 'TABLE = "' + table + '"')
        )
        helper.write_text(source)
        helper.chmod(0o444)
        for name, value in (
            ("HELPER", str(helper)),
            ("METADATA", str(metadata)),
            ("HOST_CONFIG", str(host_config)),
            ("STATE_ROOT", str(state)),
            ("OWNER_ROOT", str(owner_root)),
            ("TABLE", table),
        ):
            monkeypatch.setattr(t, name, value)
        identity = pwd.getpwnam(user)
        (owner_directory / "owner.json").write_text(
            json.dumps(
                {
                    "session_id": session,
                    "user": user,
                    "uid": identity.pw_uid,
                    "gid": identity.pw_gid,
                    "group_attempted": True,
                    "group_created": True,
                    "user_attempted": True,
                    "user_created": True,
                }
            )
        )
        (owner_directory / "owner.json").chmod(0o600)
        manifest = t.Manifest.parse(
            {
                "scope": "b8-ssh-vm124",
                "runner_user": user,
                "runner_uid": identity.pw_uid,
                "runner_gid": identity.pw_gid,
                "destinations": [
                    {"purpose": purpose, "address": address, "port": 22}
                    for purpose, address in zip(
                        ("target", "jump", "probe"),
                        ("192.0.2.1", "192.0.2.2", "192.0.2.3"),
                        strict=True,
                    )
                ],
            }
        )
        binding = t.Binding(
            session,
            manifest.digest,
            "b" * 64,
            pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            hashlib.sha256(helper.read_bytes()).hexdigest(),
            hashlib.sha256(host_config.read_bytes()).hexdigest(),
        )
        metadata.write_text(
            json.dumps({"schema": "dotmac.b8-ssh-approval.v1", **binding.document()})
        )
        metadata.chmod(0o600)
        host = Host(binding, namespace)
        host.run(
            ["/usr/sbin/nft", "-f", "-"],
            (
                f"table inet {table} {{ chain output {{ "
                "type filter hook output priority 0; policy drop; "
                "meta skuid 0 accept; ct state established,related accept; "
                f'counter drop comment "{t.ANCHOR}"; '
                "} }\n"
            ).encode(),
        )
        host.run(
            [
                "/usr/bin/nsenter",
                "--net=" + namespace,
                "/usr/sbin/ip",
                "link",
                "set",
                "lo",
                "up",
            ]
        )
        for row in manifest.destinations:
            host.run(
                [
                    "/usr/bin/nsenter",
                    "--net=" + namespace,
                    "/usr/sbin/ip",
                    "address",
                    "add",
                    row.address + "/32",
                    "dev",
                    "lo",
                ]
            )
        try:
            yield host, t.Transport(binding, manifest, host)
        finally:
            subprocess.run(  # noqa: S603 - own unique units
                [
                    "/usr/bin/systemctl",
                    "stop",
                    host.unit + ".timer",
                    host.unit + ".service",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
    finally:
        ns.terminate()
        ns.wait(timeout=5)
        subprocess.run(  # noqa: S603 - own unique fixture account
            ["/usr/sbin/userdel", user],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def test_real_uid_rule_timer_and_durable_cli_replay(transport):
    host, controller = transport
    receipt = controller.open()
    assert receipt.rule_count == 3
    assert 295 <= (receipt.deadline_ns - host.clock_ns()) / t.NS <= 300
    rules, _ = controller._chain()
    owned = controller._owned(rules)
    assert len(owned) == 3
    assert all(r["expr"][0]["match"]["right"] == controller.manifest.uid for r in owned)
    # New controller process, no in-memory coordinator and no open receipt.
    result = host.run(
        [
            "/usr/bin/nsenter",
            "--net=" + host.namespace,
            "/usr/bin/python3",
            "-I",
            "-B",
            str(host.helper),
            "--cleanup",
            controller.binding.session_id,
        ]
    )
    assert json.loads(result)["verdict"] == "CLOSED"
    assert controller._owned(controller._chain()[0]) == []
    assert host.load()["status"] == "closed"
    with pytest.raises(t.Refused, match="already-started"):
        controller.open()


def test_real_lost_open_response_is_reconciled_and_closed(transport):
    host, controller = transport
    host.lost = True
    assert controller.open().rule_count == 3
    assert controller.cleanup().rule_count == 0


def test_real_foreign_owned_expression_is_preserved(transport):
    host, controller = transport
    controller.open()
    rules, _ = controller._chain()
    rule = controller._owned(rules)[0]
    host.run(
        ["/usr/sbin/nft", "-f", "-"],
        (
            f'replace rule inet {host.table} output handle {rule["handle"]} '
            f'counter accept comment "{rule["comment"]}"\n'
        ).encode(),
    )
    with pytest.raises(t.Refused, match="ownership-unproven"):
        controller.cleanup()
    assert host.timer_verified(host.load()["timer_ns"])
    assert len(controller._owned(controller._chain()[0])) == 4


def test_real_pinned_helper_drift_prevents_open(transport):
    host, controller = transport
    host.helper.chmod(0o644)
    host.helper.write_text("raise SystemExit(99)\n")
    host.helper.chmod(0o444)
    with pytest.raises(t.Refused, match="binding-unproven"):
        controller.open()
    assert host.load() is None


def test_real_timer_cleans_after_controller_death(transport):
    host, controller = transport
    original_arm = host.arm
    original_verify = host.timer_verified

    # Synthetic test backend shortens ONLY its real timer to three seconds;
    # production state and policy still demonstrate the fixed 300-second cap.
    def short_arm(deadline):
        return original_arm(deadline - 252 * t.NS)

    def short_verify(deadline):
        return original_verify(deadline - 252 * t.NS)

    host.arm = short_arm
    host.timer_verified = short_verify
    ready = host.directory.parent / "killed-controller-ready"
    original_install = host.install

    def install_then_block(specs, uid, anchor):
        original_install(specs, uid, anchor)
        ready.write_text("installed")
        while True:
            signal.pause()

    host.install = install_then_block
    pid = os.fork()
    if pid == 0:
        try:
            controller.open()
        finally:
            os._exit(1)
    try:
        until = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < until:
            time.sleep(0.02)
        assert ready.exists(), "child did not reach the locked partial-open barrier"
        assert host.load()["status"] == "installing"
        os.kill(pid, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
        assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
        pid = None
    finally:
        if pid is not None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if host.load()["status"] == "closed":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("root timer did not replay durable cleanup")
    assert not [
        r
        for r in host.chain()[1]
        if str(r.get("comment", "")).startswith(
            t.TAG + ":" + host.binding.session_id + ":"
        )
    ]


def test_real_uid_tcp22_match_and_other_port_hits_drop_anchor(transport):
    host, controller = transport
    controller.open()
    uid, gid = controller.manifest.uid, controller.manifest.gid
    for port in (22, 443):
        script = (
            "import socket; s=socket.socket(); s.settimeout(.2); "
            f"s.connect_ex(('192.0.2.1',{port})); s.close()"
        )
        host.run(
            [
                "/usr/bin/nsenter",
                "--net=" + host.namespace,
                "/usr/bin/setpriv",
                f"--reuid={uid}",
                f"--regid={gid}",
                "--clear-groups",
                "/usr/bin/python3",
                "-I",
                "-c",
                script,
            ]
        )
    rules, _ = controller._chain()
    owns = controller._owned(rules)
    assert (
        sum(e["counter"]["packets"] for r in owns for e in r["expr"] if "counter" in e)
        >= 1
    )
    anchor = next(r for r in rules if r.get("comment") == t.ANCHOR)
    assert sum(e["counter"]["packets"] for e in anchor["expr"] if "counter" in e) >= 1
    controller.cleanup()


def test_real_installed_config_drift_blocks_window_without_effects(transport):
    host, controller = transport
    path = pathlib.Path(t.HOST_CONFIG)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(t.Refused, match="host-config-unproven"):
        controller.open()
    assert host.load() is None


def test_real_established_stream_stops_under_retained_uid_guard(transport, tmp_path):
    host, controller = transport
    received = tmp_path / "stream-count"
    listening = tmp_path / "stream-ready"
    server_code = (
        "import pathlib,socket; s=socket.socket(); "
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
        "s.bind(('192.0.2.1',22)); s.listen(); "
        f"pathlib.Path({str(listening)!r}).write_text('ready'); c,_=s.accept(); n=0\n"
        "while True:\n"
        " data=c.recv(4096)\n"
        " if not data: break\n"
        f" n+=len(data); pathlib.Path({str(received)!r}).write_text(str(n))\n"
    )
    server = subprocess.Popen(  # noqa: S603 - owned fixture stream server
        [
            "/usr/bin/nsenter",
            "--net=" + host.namespace,
            "/usr/bin/python3",
            "-I",
            "-c",
            server_code,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = None
    try:
        until = time.monotonic() + 5
        while not listening.exists() and time.monotonic() < until:
            time.sleep(0.02)
        assert listening.exists()
        controller.open()
        code = (
            "import socket,time; s=socket.socket(); s.connect(('192.0.2.1',22))\n"
            "while True:\n s.sendall(b'x'); time.sleep(.02)\n"
        )
        client = subprocess.Popen(  # noqa: S603 - owned fixture nonroot stream
            [
                "/usr/bin/nsenter",
                "--net=" + host.namespace,
                "/usr/bin/setpriv",
                f"--reuid={controller.manifest.uid}",
                f"--regid={controller.manifest.gid}",
                "--clear-groups",
                "/usr/bin/python3",
                "-I",
                "-c",
                code,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        until = time.monotonic() + 5
        while not received.exists() and time.monotonic() < until:
            time.sleep(0.02)
        assert received.exists()
        time.sleep(0.15)
        before = int(received.read_text())
        time.sleep(0.15)
        assert int(received.read_text()) > before
        with pytest.raises(t.Refused, match="guarded-unproven"):
            controller.cleanup()
        assert host.load()["status"] == "guarded"
        time.sleep(0.3)
        before = int(received.read_text())
        time.sleep(0.3)
        assert int(received.read_text()) == before
        client.kill()
        client.wait(timeout=5)
        client = None
        assert controller.cleanup().rule_count == 0
    finally:
        if client is not None:
            client.kill()
            client.wait(timeout=5)
        server.kill()
        server.wait(timeout=5)


@pytest.mark.parametrize("which", ["metadata", "host_config", "helper"])
def test_real_post_open_input_drift_cannot_veto_revocation(transport, which):
    host, controller = transport
    controller.open()
    path = {
        "metadata": host.metadata,
        "host_config": pathlib.Path(t.HOST_CONFIG),
        "helper": host.helper,
    }[which]
    mode = path.stat().st_mode & 0o777
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b" ")
    path.chmod(mode)
    assert controller.cleanup().verdict == "CLOSED"
    assert controller._owned(controller._chain()[0]) == []


def test_real_revocation_unit_has_finite_retry_policy(transport):
    host, controller = transport
    controller.open()
    fields = dict(
        line.split("=", 1)
        for line in host.run(
            ["/usr/bin/systemctl", "show", host.unit + ".service"]
        ).splitlines()
        if "=" in line
    )
    assert fields["Restart"] == "on-failure" and fields["RestartUSec"] == "1s"
    assert fields["StartLimitBurst"] == "30"
    assert fields["StartLimitIntervalUSec"] == "infinity"
    assert fields["RestartPreventExitStatus"] == "2"
    controller.cleanup()


def test_real_hung_cleanup_has_total_attempt_limit(transport, tmp_path):
    host, controller = transport
    controller.open()
    attempts = tmp_path / "attempts"
    source = host.helper.read_text().replace(
        "    def processes_absent(self, uid: int) -> bool:\n",
        "    def processes_absent(self, uid: int) -> bool:\n"
        f"        with pathlib.Path({str(attempts)!r}).open('a') as out:\n"
        "            out.write('x')\n"
        "        time.sleep(3)\n",
    )
    host.helper.chmod(0o644)
    host.helper.write_text(source)
    host.helper.chmod(0o444)
    unit = host.unit + "-hung"
    try:
        host.run(
            [
                "/usr/bin/systemd-run",
                "--unit=" + unit,
                "--collect",
                "--service-type=exec",
                "--property=RuntimeMaxSec=1s",
                "--property=TimeoutStartSec=1s",
                "--property=TimeoutStopSec=100ms",
                "--property=KillMode=control-group",
                "--property=Restart=on-failure",
                "--property=RestartSec=50ms",
                "--property=StartLimitBurst=3",
                "--property=StartLimitIntervalSec=infinity",
                "--on-active=100ms",
                "--timer-property=AccuracySec=1us",
                "/usr/bin/python3",
                "-I",
                "-B",
                str(host.helper),
                "--cleanup",
                controller.binding.session_id,
            ]
        )
        until = time.monotonic() + 10
        while time.monotonic() < until:
            if attempts.exists() and len(attempts.read_text()) == 3:
                break
            time.sleep(0.05)
        assert attempts.exists() and len(attempts.read_text()) == 3
        time.sleep(0.75)
        assert len(attempts.read_text()) == 3
        assert host.load()["status"] == "guarded"
        assert len(controller._owned(controller._chain()[0])) == 1
    finally:
        subprocess.run(  # noqa: S603 - exact owned synthetic retry units
            ["/usr/bin/systemctl", "stop", unit + ".timer", unit + ".service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
