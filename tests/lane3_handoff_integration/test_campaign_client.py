"""Real nonroot/cgroup/socket-skuid qualification in hosted disposable namespace.

Uses only synthetic keys, certificates, documentation inputs and an owned nft
counter. Systemd children explicitly join THIS namespace, rather than assuming
systemd-run inherits the invoking process's unshare namespace.
"""

from __future__ import annotations

import dataclasses
import grp
import hashlib
import importlib.util
import json
import os
import pwd
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from conftest import SCRIPTS, sh


@pytest.fixture
def client_fixture(request):
    name = "b8it" + uuid.uuid4().hex[:12]
    root = Path("/run") / name
    root.mkdir(mode=0o755)
    table = name
    identity_path = root / "lane3_campaign_identity.py"
    shutil.copyfile(SCRIPTS / identity_path.name, identity_path)
    identity_path.chmod(0o644)
    identity_spec = importlib.util.spec_from_file_location(
        name + "_identity", identity_path
    )
    im = importlib.util.module_from_spec(identity_spec)
    sys.modules[identity_spec.name] = im
    identity_spec.loader.exec_module(im)
    requested = 50000 + int(uuid.uuid4().hex[:4], 16)
    while (
        im.lookup(pwd.getpwuid, requested) is not None
        or im.lookup(grp.getgrgid, requested) is not None
    ):
        requested += 1
    identity_seconds = (
        2 if request.node.name.startswith("test_real_coordinator_sigkill") else 180
    )
    owned_identity = im.OwnedIdentity(
        uuid.uuid4().hex, requested, requested, seconds=identity_seconds
    )
    created = owned_identity.create()
    identity_unit = "dotmac-b8-identity-" + owned_identity.session_id + ".service"
    assert (
        sh(
            "/usr/bin/systemctl",
            "show",
            "--property=StartLimitIntervalUSec",
            "--value",
            identity_unit,
        ).strip()
        == "infinity"
    )
    assert (
        sh(
            "/usr/bin/systemctl",
            "show",
            "--property=StartLimitBurst",
            "--value",
            identity_unit,
        ).strip()
        == "30"
    )
    user = created["user"]
    account = pwd.getpwnam(user)
    module_path = root / "lane3_campaign_client.py"
    shutil.copyfile(SCRIPTS / module_path.name, module_path)
    module_path.chmod(0o644)
    spec = importlib.util.spec_from_file_location(name, module_path)
    c = importlib.util.module_from_spec(spec)
    sys.modules[name] = c
    spec.loader.exec_module(c)
    runner = root / "matrix.py"
    # The fixture executes only after production handshake GO. Sending this UDP
    # packet demonstrates nft's meta skuid sees precisely the reviewed UID.
    # The drop anchor deliberately rejects it; only that PermissionError is
    # admitted, and both the UID counter and blocked verdict are asserted.
    runner.write_text(
        "import json,os,socket\n"
        "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); blocked=False\n"
        "try:\n s.sendto(b'synthetic',(\"127.0.0.1\",39671))\n"
        "except PermissionError:\n blocked=True\n"
        "print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'udp_blocked':blocked,'full_b8_complete':False}))\n"
    )
    runner.chmod(0o644)
    import lane3_campaign_transport as transport_source

    transport_path = root / "lane3_campaign_transport.py"
    metadata = root / "transport.json"
    host_config = root / "host.json"
    source = Path(transport_source.__file__).read_text()
    source = (
        source.replace(transport_source.HELPER, str(transport_path))
        .replace(transport_source.METADATA, str(metadata))
        .replace(transport_source.HOST_CONFIG, str(host_config))
        .replace('TABLE = "dotmac_egress"', 'TABLE = "' + table + '"')
        .replace('TABLE="dotmac_egress"', 'TABLE="' + table + '"')
    )
    transport_path.write_text(source)
    transport_path.chmod(0o444)
    tspec = importlib.util.spec_from_file_location(name + "_transport", transport_path)
    t = importlib.util.module_from_spec(tspec)
    sys.modules[tspec.name] = t
    tspec.loader.exec_module(t)
    manifest = root / "manifest.json"
    manifest_document = {
        "scope": "b8-ssh-vm124",
        "runner_user": user,
        "runner_uid": account.pw_uid,
        "runner_gid": account.pw_gid,
        "destinations": [
            {"purpose": purpose, "address": address, "port": 22}
            for purpose, address in zip(
                ("target", "jump", "probe"),
                ("192.0.2.1", "192.0.2.2", "192.0.2.3"),
                strict=True,
            )
        ],
    }
    manifest.write_text(
        json.dumps(manifest_document, sort_keys=True, separators=(",", ":"))
    )
    manifest.chmod(0o600)
    identity = c.RunnerIdentity(
        user,
        account.pw_uid,
        account.pw_gid,
        hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    cfg = {
        "approved_proof_client": True,
        "session_id": owned_identity.session_id,
        "known_hosts_text": "synthetic pinned host\n",
        "probe": {"account": "b8synthetic"},
        "network_namespace": f"/proc/{os.getpid()}/ns/net",
    }
    runner_sha = hashlib.sha256(runner.read_bytes()).hexdigest()
    control = root / "control"
    workspace = root / "workspace"
    workspace.mkdir(mode=0o755)
    sh("nft", "add", "table", "inet", table)
    sh(
        "nft",
        "add",
        "chain",
        "inet",
        table,
        "output",
        "{ type filter hook output priority 0; policy drop; }",
    )
    sh(
        "nft",
        "add",
        "rule",
        "inet",
        table,
        "output",
        "meta",
        "skuid",
        str(account.pw_uid),
        "udp",
        "dport",
        "39671",
        "counter",
    )
    sh("nft", "add", "rule", "inet", table, "output", "meta", "skuid", "0", "accept")
    sh(
        "nft",
        "add",
        "rule",
        "inet",
        table,
        "output",
        "ct",
        "state",
        "established,related",
        "accept",
    )
    sh(
        "nft",
        "add",
        "rule",
        "inet",
        table,
        "output",
        "counter",
        "drop",
        "comment",
        '"dropped-egress"',
    )
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
    binding = t.Binding(
        owned_identity.session_id,
        t.Manifest.parse(manifest_document).digest,
        "b" * 64,
        Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        hashlib.sha256(transport_path.read_bytes()).hexdigest(),
        hashlib.sha256(host_config.read_bytes()).hexdigest(),
    )
    metadata.write_text(
        json.dumps({"schema": "dotmac.b8-ssh-approval.v1", **binding.document()})
    )
    metadata.chmod(0o600)

    class TransportHost(t.SystemBackend):
        def run(self, argv, data=None):
            if argv[0] == "/usr/bin/systemd-run":
                argv = [
                    argv[0],
                    "--property=NetworkNamespacePath=" + cfg["network_namespace"],
                    *argv[1:],
                ]
            return super().run(argv, data)

    class FixtureTransport(t.Transport):
        def open(self):
            try:
                return super().open()
            except t.Refused:
                # This fixture owns a unique table/session and uses only RFC5737
                # destinations. Show exact synthetic owned expressions so hosted
                # failures can distinguish nft rendering from ownership defects.
                try:
                    _, rules = self.backend.chain()
                    diagnostic = {"synthetic_nft_owned_rules": self._owned(rules)}
                except Exception:
                    diagnostic = {"synthetic_nft_readback": "unavailable"}
                print(json.dumps(diagnostic, sort_keys=True))
                raise

    transport = FixtureTransport(
        binding, t.Manifest.parse(manifest_document), TransportHost(binding)
    )
    transport.initialize_closed()
    ca = root / "ca"
    sh("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(ca))
    sessions = []

    def session(**changes):
        args = {
            "identity": identity,
            "manifest_path": manifest,
            "runner_path": runner,
            "runner_sha256": runner_sha,
            "approval_sha256": c.approval_digest(identity, cfg, runner_sha),
            "cfg": cfg,
            "custody_ready": True,
            "control_root": control,
            "workspace_root": workspace,
        }
        args.update(changes)
        client = c.Session(**args)
        sessions.append(client)
        return client

    session.transport = transport
    session.identity_owner = owned_identity
    session.transport_module = t
    session.identity_module_path = identity_path

    def certificates(client):
        certs = {}
        for key in c.NAMES:
            if key == "plain_key":
                continue
            public = root / (key + ".pub")
            public.write_bytes((client.work / (key + ".pub")).read_bytes())
            sh(
                "ssh-keygen",
                "-q",
                "-s",
                str(ca),
                "-I",
                "synthetic",
                "-n",
                "synthetic",
                "-V",
                "-2m:-1m" if key == "expired_observer" else "-1m:+3m",
                str(public),
            )
            certs[key] = (root / (key + "-cert.pub")).read_text().strip()
        client.install_certificates(certs, valid_until_epoch=time.time() + 180)

    try:
        yield c, session, certificates, identity, manifest, table
    finally:
        transport.contain()
        for client in sessions:
            try:
                client.cleanup()
            except Exception:
                # An injected failure is removed before final cleanup below.
                c.cleanup_control(client.control)
        transport.cleanup()
        owned_identity.cleanup()
        sh("nft", "delete", "table", "inet", table)
        shutil.rmtree(root)
        sys.modules.pop(name, None)
        sys.modules.pop(identity_spec.name, None)
        sys.modules.pop(tspec.name, None)


def prepare(client):
    return client.prepare(
        {"jump_principal": "lane3jump", "probe_principal": "b8synthetic"}, seconds=90
    )


def test_real_uid_cgroup_socket_uid_and_owned_nft_counter(client_fixture):
    c, make, certificates, identity, manifest, table = client_fixture
    client = make()
    request = prepare(client)
    assert len(request["public_keys"]) == 7
    cleanup_unit = client.timer + ".service"
    assert (
        sh(
            "/usr/bin/systemctl",
            "show",
            "--property=StartLimitIntervalUSec",
            "--value",
            cleanup_unit,
        ).strip()
        == "infinity"
    )
    assert (
        sh(
            "/usr/bin/systemctl",
            "show",
            "--property=StartLimitBurst",
            "--value",
            cleanup_unit,
        ).strip()
        == "30"
    )
    assert (
        client.control.stat().st_uid == 0
        and client.control.stat().st_mode & 0o777 == 0o700
    )
    assert (
        client.work.stat().st_uid == identity.uid
        and client.work.stat().st_mode & 0o777 == 0o700
    )
    assert all(
        p.stat().st_uid == identity.uid and p.stat().st_mode & 0o777 == 0o600
        for p in client.work.iterdir()
    )
    certificates(client)
    result = client.execute()
    assert result["uid"] == identity.uid and result["gid"] == identity.gid
    assert result["udp_blocked"] is True
    nft = json.loads(sh("nft", "-j", "list", "table", "inet", table))
    counters = [
        e["counter"]["packets"]
        for item in nft["nftables"]
        if "rule" in item
        for e in item["rule"]["expr"]
        if "counter" in e
        and any(
            part.get("match", {}).get("left")
            == {"payload": {"protocol": "udp", "field": "dport"}}
            for part in item["rule"]["expr"]
        )
    ]
    assert counters == [1]
    assert not client.work.exists() and not client.control.exists()
    assert (
        pwd.getpwnam(identity.user).pw_uid == identity.uid
    )  # client never removes its externally owned identity


@pytest.mark.parametrize(
    "mutation", ["manifest", "user", "ownership", "symlink", "parent", "deadline"]
)
def test_late_drift_refuses_before_worker_and_removes_material(
    client_fixture, mutation
):
    c, make, certificates, identity, manifest, table = client_fixture
    client = make()
    prepare(client)
    certificates(client)
    if mutation == "manifest":
        manifest.write_text("changed")
    elif mutation == "user":
        client.identity = dataclasses.replace(identity, uid=identity.uid + 1)
    elif mutation == "ownership":
        os.chown(client.work / "observer", 0, 0)
    elif mutation == "symlink":
        (client.work / "observer").unlink()
        (client.work / "observer").symlink_to(manifest)
    elif mutation == "parent":
        client.control_root.chmod(0o755)
    else:
        client.certificate_until = time.time() + 30
    with pytest.raises(c.ClientRefused):
        client.execute()
    if mutation == "parent":
        assert client.work.exists()  # root parent drift retains the independent timer
        client.control_root.chmod(0o700)
        client.cleanup()
    assert not client.work.exists()
    nft = json.loads(sh("nft", "-j", "list", "table", "inet", table))
    assert all(
        e["counter"]["packets"] == 0
        for item in nft["nftables"]
        if "rule" in item
        for e in item["rule"]["expr"]
        if "counter" in e
        and any(
            part.get("match", {}).get("left")
            == {"payload": {"protocol": "udp", "field": "dport"}}
            for part in item["rule"]["expr"]
        )
    )


def test_failed_stop_retains_root_timer_and_material(client_fixture, monkeypatch):
    c, make, certificates, identity, manifest, table = client_fixture
    client = make()
    prepare(client)
    timer = client.timer
    original = client.backend.stop
    monkeypatch.setattr(
        client.backend,
        "stop",
        lambda record: (_ for _ in ()).throw(c.ClientRefused("client.cleanup")),
    )
    with pytest.raises(c.ClientRefused):
        client.cleanup()
    assert client.work.exists()
    assert (
        subprocess.run(  # noqa: S603 - exact owned timer
            ["/usr/bin/systemctl", "is-active", "--quiet", timer + ".timer"]
        ).returncode
        == 0
    )
    monkeypatch.setattr(client.backend, "stop", original)
    client.cleanup()
    assert not client.work.exists()


def test_lost_launch_response_stops_actual_child_and_removes_keys(
    client_fixture, monkeypatch
):
    c, make, certificates, identity, manifest, table = client_fixture
    client = make()
    prepare(client)
    certificates(client)
    original = client.backend.start

    def lost_response(record, budget, namespace):
        original(record, budget, namespace)
        raise c.ClientRefused("client.lost_response")

    monkeypatch.setattr(client.backend, "start", lost_response)
    with pytest.raises(c.ClientRefused):
        client.execute()
    assert not client.work.exists()
    client.backend.stop({"unit": client.control.name + "-matrix"})


def test_killed_child_cannot_prove_ssh_and_is_cleaned(client_fixture, monkeypatch):
    import signal

    c, make, certificates, identity, manifest, table = client_fixture
    client = make()
    prepare(client)
    certificates(client)
    original = client.backend.start

    def kill_child(record, budget, namespace):
        original(record, budget, namespace)
        os.kill(client.backend.main_pid(record["unit"]), signal.SIGKILL)

    monkeypatch.setattr(client.backend, "start", kill_child)
    with pytest.raises(c.ClientRefused):
        client.execute()
    assert not client.work.exists()


def short_material_window(c, started_ns, seconds, duration=18):
    # Hosted-only backend seam. Operational Session/CLI expose no cleanup-margin
    # knob: production remains 90..240 seconds with the fixed 45-second reserve.
    cap = started_ns + duration * c.NS
    due = cap - 7 * c.NS
    return c.MaterialWindow(started_ns, cap, due, due - 3 * c.NS, 4, 1).validate()


def test_independent_root_timer_measures_material_destruction_before_cap(
    client_fixture, monkeypatch
):
    c, make, certificates, identity, manifest, table = client_fixture
    client = make()
    monkeypatch.setattr(
        client.backend,
        "window",
        lambda started, seconds: short_material_window(c, started, seconds),
    )
    prepare(client)
    work, control = client.work, client.control
    assert (work / "observer").exists()
    cap = client.window.material_deadline_ns
    deadline = time.monotonic() + 22
    while control.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    observed = time.monotonic_ns()
    assert not control.exists() and not work.exists()
    proof = json.loads(
        (c.CLIENT_PROOF_ROOT / client.session_id / "closed.json").read_bytes()
    )
    assert proof["material_cap"] == "ATTESTED"
    assert proof["first_cleanup"]["outcome"] == "succeeded"
    assert proof["destroyed_ns"] <= observed <= cap
    assert proof["destroyed_ns"] <= proof["material_window"]["material_deadline_ns"]


def test_umask_077_exact_bundle_mode_allows_child_and_verified_cleanup(client_fixture):
    c, make, certificates, identity, manifest, table = client_fixture
    previous = os.umask(0o077)
    try:
        client = make()
        prepare(client)
        assert client.bundle.stat().st_mode & 0o777 == 0o711
        certificates(client)
        make.transport.open()  # only after issued synthetic certificates
        result = client.execute()
        assert result["uid"] == identity.uid
        assert not client.bundle.exists() and not client.control.exists()
        make.transport.contain()
        make.transport.cleanup()
        make.identity_owner.cleanup()
        with pytest.raises(KeyError):
            pwd.getpwnam(identity.user)
    finally:
        os.umask(previous)


def test_identity_stays_reserved_until_client_and_transport_prove_closed(
    client_fixture,
):
    c, make, certificates, identity, manifest, table = client_fixture
    client = make()
    prepare(client)
    certificates(client)
    make.transport.open()
    with pytest.raises(Exception, match="identity.closure"):
        make.identity_owner.cleanup()
    assert pwd.getpwnam(identity.user).pw_uid == identity.uid
    make.transport.contain()
    client.cleanup()
    with pytest.raises(Exception, match="identity.closure"):
        make.identity_owner.cleanup()
    assert pwd.getpwnam(identity.user).pw_uid == identity.uid
    make.transport.cleanup()
    make.identity_owner.cleanup()
    with pytest.raises(KeyError):
        pwd.getpwnam(identity.user)


def test_real_coordinator_sigkill_and_reversed_timer_order_keep_uid_reserved(
    client_fixture, monkeypatch
):
    import signal

    c, make, certificates, identity, manifest, table = client_fixture
    client = make()
    client.runner_path.write_text(
        "import pathlib,sys,time\n"
        "work=pathlib.Path(sys.argv[1]).parent\n"
        "(work/'worker-ready').write_text('synthetic')\n"
        "time.sleep(60)\n"
    )
    client.runner_sha256 = hashlib.sha256(client.runner_path.read_bytes()).hexdigest()
    client.approval_sha256 = c.approval_digest(
        identity, client.cfg, client.runner_sha256
    )
    monkeypatch.setattr(
        client.backend,
        "window",
        lambda started, seconds: short_material_window(
            c, started, seconds, duration=24
        ),
    )
    prepare(client)
    certificates(client)
    transport = make.transport
    t = make.transport_module
    host = transport.backend
    original_arm, original_verified = host.arm, host.timer_verified
    monkeypatch.setattr(
        host, "arm", lambda deadline: original_arm(deadline - 252 * t.NS)
    )
    monkeypatch.setattr(
        host,
        "timer_verified",
        lambda deadline: original_verified(deadline - 252 * t.NS),
    )
    transport.open()
    pid = os.fork()
    if pid == 0:
        try:
            client.execute()
        finally:
            os._exit(1)
    try:
        deadline = time.monotonic() + 5
        while (
            not (client.work / "worker-ready").exists() and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert (client.work / "worker-ready").exists()
        worker_pid = client.backend.main_pid(client.record["unit"])
        os.kill(pid, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
        assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
        pid = None
        deadline = time.monotonic() + 5
        while host.load()["status"] != "guarded" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert host.load()["status"] == "guarded"
        assert Path("/proc", str(worker_pid)).exists()
        # Identity's actual root timer fires first (below two seconds).
        # timer then contains at three seconds, and client cleanup expires last.
        # Repeated failed identity callbacks must retain this exact UID.
        callback = subprocess.run(  # noqa: S603 - exact owned root timer service
            [
                "/usr/bin/systemctl",
                "show",
                "--property=NRestarts",
                "--value",
                "dotmac-b8-identity-" + client.session_id + ".service",
            ],
            capture_output=True,
            timeout=5,
            check=True,
        )
        assert int(callback.stdout.strip()) >= 1
        assert pwd.getpwnam(identity.user).pw_uid == identity.uid
        assert make.identity_owner.directory.exists()
        deadline = time.monotonic() + 20
        while client.control.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not client.control.exists() and not client.bundle.exists()
        assert not Path("/proc", str(worker_pid)).exists()
        # A retained guard is a safe refusal, never an account-release proof.
        # Replay the production durable cleanup if its bounded retry has not yet
        # completed; this cannot recreate accepts or extend their deadline.
        transport.cleanup()
        assert host.load()["status"] == "closed"
        make.identity_owner.cleanup()
        with pytest.raises(KeyError):
            pwd.getpwnam(identity.user)
    finally:
        if pid is not None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)


def test_root_held_socket_fd_keeps_uid_reserved_after_child_process_absence(
    client_fixture,
):
    import socket

    c, make, certificates, identity, manifest, table = client_fixture
    client = make()
    prepare(client)
    certificates(client)
    client.cleanup()
    proof = None
    try:
        os.seteuid(identity.uid)
        try:
            proof = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            proof.bind(("127.0.0.1", 0))
        finally:
            os.seteuid(0)
        # The root test process owns the descriptor now; no target-UID process
        # remains, but Linux sock_i_uid still pins the reviewed UID.
        with pytest.raises(Exception, match="identity.sockets"):
            make.identity_owner.cleanup()
        assert pwd.getpwnam(identity.user).pw_uid == identity.uid
        assert make.identity_owner.directory.exists()
    finally:
        if proof is not None:
            proof.close()
    make.identity_owner.cleanup()
    with pytest.raises(KeyError):
        pwd.getpwnam(identity.user)


def test_actual_timer_starting_at_cap_is_refused_before_any_key(
    client_fixture, monkeypatch
):
    c, make, certificates, identity, manifest, table = client_fixture
    client = make()
    original_arm = client.backend.arm
    monkeypatch.setattr(
        client.backend,
        "window",
        lambda started, seconds: short_material_window(c, started, seconds),
    )

    def late_timer(control, window):
        # A temporary adapter defect schedules cleanup at the claimed material
        # cap while retaining a valid (but different) internal timer window.
        shifted = dataclasses.replace(
            window,
            material_deadline_ns=window.material_deadline_ns + 7 * c.NS,
            cleanup_due_ns=window.material_deadline_ns,
            execution_deadline_ns=window.material_deadline_ns - 3 * c.NS,
        )
        return original_arm(control, shifted)

    monkeypatch.setattr(client.backend, "arm", late_timer)
    original_command = c.command
    keys = []

    def record_key_generation(argv, **kwargs):
        if argv[0] == "/usr/bin/ssh-keygen":
            keys.append(True)
        return original_command(argv, **kwargs)

    monkeypatch.setattr(c, "command", record_key_generation)
    with pytest.raises(c.ClientRefused, match="client.timer"):
        prepare(client)
    assert keys == []
    assert not client.bundle.exists()


def test_hung_first_destruction_remains_unproven_after_late_cleanup(
    client_fixture, monkeypatch
):
    import signal

    c, make, certificates, identity, manifest, table = client_fixture
    client = make()
    monkeypatch.setattr(
        client.backend,
        "window",
        lambda started, seconds: short_material_window(c, started, seconds),
    )
    prepare(client)
    original_stop = client.backend.stop

    def hang(record):
        while True:
            signal.pause()

    monkeypatch.setattr(client.backend, "stop", hang)
    pid = os.fork()
    if pid == 0:
        try:
            c.cleanup_control(client.control, backend=client.backend)
        finally:
            os._exit(1)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            owner = json.loads((client.control / "owner.json").read_bytes())
            if owner.get("first_cleanup", {}).get("outcome") == "started":
                break
            time.sleep(0.02)
        assert owner["first_cleanup"]["outcome"] == "started"
        while time.monotonic_ns() <= client.window.material_deadline_ns:
            time.sleep(0.02)
        assert (client.work / "observer").exists()
        assert c.material_cap_verdict(owner) == "UNPROVEN"
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        pid = None
        monkeypatch.setattr(client.backend, "stop", original_stop)
        receipt = client.cleanup()
        assert receipt == {"resource_closed": True, "material_cap": "UNPROVEN"}
        assert not client.bundle.exists()
        proof = json.loads(
            (c.CLIENT_PROOF_ROOT / client.session_id / "closed.json").read_bytes()
        )
        assert proof["first_cleanup"]["outcome"] == "failed"
        assert proof["destroyed_ns"] > client.window.material_deadline_ns
        assert proof["material_cap"] == "UNPROVEN"
    finally:
        if pid is not None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        monkeypatch.setattr(client.backend, "stop", original_stop)
