"""Hosted-Linux integration harness for the Lane 3 broker handoff controller.

Runs ONLY under ``.github/workflows/lane3-handoff-integration.yml``: as root,
inside a disposable network namespace, with ``LANE3_HANDOFF_IT=1``. Anywhere
else (a developer machine, the Postgres integration job) every module here is
ignored at collection, so nothing touches a real host.

What is real: the controller code, nftables in the namespace, useradd/userdel
identities, tmpfs workspaces, the root-owned lease directory, the Unix socket
with ``SO_PEERCRED``, ``/proc`` start-time and cgroup v2 membership checks, the
job-side client (``lane3_handoff_client``) running as the temporary UID, TLS.
What is substituted (``TestHost``): systemd is replaced by a cgroup the test
creates and a serve thread; the rollback timer is recorded and fired by the
test; monotonic time can be advanced. Synthetic documentation addresses
(RFC 5737 / RFC 3849) are admitted through the ``routable`` seam only here.

No OpenBao, GitHub, protected workflow or fleet host is ever contacted.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import io
import ipaddress
import json
import os
import pathlib
import pwd
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

ENABLED = (
    os.environ.get("LANE3_HANDOFF_IT") == "1"
    and sys.platform.startswith("linux")
    and hasattr(os, "geteuid")
    and os.geteuid() == 0
)
if not ENABLED:
    collect_ignore_glob = ["test_*.py"]

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_handoff_controller as hc  # noqa: E402
import lane3_handoff_protocol as hp  # noqa: E402

BASE = pathlib.Path("/run/l3h-it")
CGROUP_PARENT = "/l3h-it"
FW = hc.Firewall("inet", "l3h_it_egress", "output", "l3h-it-anchor")
SHA = "5" * 40
BLOB = "6" * 40
REPO_ID, RUN_ID, JOB_ID, RUNNER_ID = 4101, 4102, 4103, 4104
BROKER_HOST = "broker.example"
BROKER = "https://" + BROKER_HOST
TLS_ADDRESS = "192.0.2.30"
PLAIN_ADDRESS = "192.0.2.32"
OTHER_ADDRESS = "192.0.2.31"
BOOTSTRAP_ADDRESS = "198.51.100.20"
DOC_NETS = tuple(
    ipaddress.ip_network(n)
    for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
)


def documentation_routable(address: Any) -> bool:
    """Integration-only seam: ONLY the documentation ranges count as routable."""
    return any(address in n for n in DOC_NETS if n.version == address.version)


def sh(*argv: str, data: bytes | None = None, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - fixed test argv
        argv, input=data, capture_output=True, check=False
    )
    if check and result.returncode:
        raise AssertionError(f"{argv[0]} failed: {result.stderr.decode()[-400:]}")
    return result.stdout.decode()


BASELINE = f"""
table inet {FW.table} {{
  chain {FW.chain} {{
    type filter hook output priority 0; policy drop;
    ct state established,related accept comment "l3h-it-established"
    meta skuid 0 accept comment "l3h-it-root"
    counter comment "{FW.anchor}"
  }}
}}
"""


def reset_firewall() -> None:
    sh("nft", "flush", "ruleset")
    sh("nft", "-f", "-", data=BASELINE.encode())


# ── the substituted host ───────────────────────────────────────────────────


class TestHost(hc.SystemHost):
    """Real nft/identities/tmpfs; systemd replaced by a test cgroup + thread."""

    __test__ = False
    cgroup_root = pathlib.Path("/sys/fs/cgroup")

    def __init__(self, env: Env) -> None:
        self.env = env
        self.offset_ns = 0
        self.timers: list[tuple[str, int, list[str]]] = []
        self.disarmed: list[str] = []
        self.crash_at: str | None = None
        self.on_checkpoint: dict[str, Any] = {}
        self.job_argv: list[str] = ["obtain"]
        self.jobs: list[subprocess.Popen[bytes]] = []
        self.threads: list[threading.Thread] = []
        self.serve_errors: list[BaseException] = []
        self.corrupt_apply = False
        self.corrupt_readback = False
        self.applied: list[str] = []

    def now_ns(self) -> int:
        return time.monotonic_ns() + self.offset_ns

    def checkpoint(self, name: str) -> None:
        if name in self.on_checkpoint:
            self.on_checkpoint[name]()
        if name == self.crash_at:
            os._exit(17)

    def nft_apply(self, script: str) -> None:
        self.applied.append(script)
        if self.corrupt_apply:
            script += f"add rule inet {FW.table} missing_chain counter\n"
        super().nft_apply(script)

    def nft_list(self, fw: hc.Firewall) -> list[Any]:
        doc = super().nft_list(fw)
        if self.corrupt_readback:
            for item in doc:
                rule = item.get("rule") if isinstance(item, dict) else None
                if rule and hc.owned(rule):
                    rule["expr"][1]["match"]["right"] = OTHER_ADDRESS
        return doc

    def arm_timer(self, unit: str, seconds: int, argv: list[str]) -> None:
        self.timers.append((unit, seconds, argv))

    def disarm_timer(self, unit: str) -> None:
        self.disarmed.append(unit)

    def unit_cgroup(self, unit: str) -> str:
        return f"{CGROUP_PARENT}/{unit}"

    def kill_unit(self, unit: str) -> None:
        return None

    def stop_unit(self, unit: str) -> None:
        return None

    def start_service(self, unit: str, argv: list[str], properties: list[str]) -> None:
        assert argv[-2] == "serve", "only the socket server is a service here"
        lease = argv[-1]
        controller = self.env.controller()

        def run() -> None:
            try:
                controller.serve(lease)
            except BaseException as exc:  # recorded, never printed
                self.serve_errors.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.threads.append(thread)

    def launch_runner(
        self, unit: str, properties: list[str], python: str, payload: bytes
    ) -> None:
        values = dict(p.split("=", 1) for p in properties if "=" in p)
        user = pwd.getpwnam(values["User"])
        lease = unit.split("-")[-2]
        self.spawn(unit, user.pw_uid, user.pw_gid, lease, self.job_argv)

    def spawn(
        self,
        unit: str,
        uid: int,
        gid: int,
        lease_prefix: str,
        argv: list[str],
        *,
        in_cgroup: bool = True,
    ) -> subprocess.Popen[bytes]:
        cgroup = self.cgroup_root / self.unit_cgroup(unit).lstrip("/")
        cgroup.mkdir(exist_ok=True)
        enter = f"echo $$ > {cgroup}/cgroup.procs && " if in_cgroup else ""
        lease = self.env.lease_for(lease_prefix)
        command = (
            enter
            + f"exec setpriv --reuid={uid} --regid={gid} --clear-groups "
            + f"--no-new-privs /usr/bin/python3 -I {self.env.jobbin}/job.py "
            + " ".join([lease, str(gid), *argv])
        )
        child = subprocess.Popen(  # noqa: S603 - synthetic test job
            ["/bin/sh", "-c", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        self.jobs.append(child)
        return child


# ── environment ────────────────────────────────────────────────────────────


class Env:
    def __init__(self) -> None:
        self.base = BASE
        self.paths = hc.Paths(
            run_root=BASE / "run",
            state_root=BASE / "state",
            config_root=BASE / "etc",
            work_root=BASE / "work",
            python="/usr/bin/python3",
        )
        self.jobbin = BASE / "jobbin"
        self.host = TestHost(self)
        self.policy: dict[str, Any] = {}
        self.bootstrap: dict[str, Any] = {}

    def controller(self) -> hc.Controller:
        return hc.Controller(
            paths=self.paths,
            host=self.host,
            routable=documentation_routable,
            module_dir=SCRIPTS,
        )

    def lease_for(self, prefix: str) -> str:
        j = self.controller().load()
        assert j is not None and j["lease_id"].startswith(prefix)
        return j["lease_id"]

    def journal(self) -> dict[str, Any] | None:
        return self.controller().load()

    def closed(self, lease: str) -> dict[str, Any]:
        raw = (self.paths.state_root / "closed" / f"{lease}.json").read_bytes()
        return json.loads(raw)

    # setup ---------------------------------------------------------------

    def install(self, *, origins: list[str] | None = None) -> None:
        if BASE.exists():
            shutil.rmtree(BASE)
        for path in (BASE, self.paths.config_root, self.jobbin):
            path.mkdir(mode=0o755)
            os.chown(path, 0, 0)
            path.chmod(0o755)
        for item in SCRIPTS.glob("*.py"):
            target = self.jobbin / item.name
            shutil.copyfile(item, target)
            target.chmod(0o644)
        shutil.copyfile(
            pathlib.Path(__file__).with_name("job.py"), self.jobbin / "job.py"
        )
        (self.jobbin / "job.py").chmod(0o644)
        (self.jobbin / "ca.pem").write_bytes(CA_PEM.read_bytes())
        (self.jobbin / "ca.pem").chmod(0o644)
        bundle = io.BytesIO()
        with tarfile.open(fileobj=bundle, mode="w:gz") as tar:
            data = b"#!/bin/sh\nexit 0\n"
            info = tarfile.TarInfo("run.sh")
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
        archive = self.paths.config_root / "runner.tar.gz"
        self._write(archive, bundle.getvalue())
        self.policy = {
            "schema": hc.POLICY_SCHEMA,
            "version": 1,
            "origins": {
                "schema": "dotmac.lane3.broker-origin-policy.v1",
                "origins": origins or [BROKER],
            },
            "aliases": {"exact": [], "namespaces": []},
        }
        self._write(
            self.paths.config_root / "policy.json", json.dumps(self.policy).encode()
        )
        self.bootstrap = {
            "schema": hc.BOOTSTRAP_SCHEMA,
            "destinations": [
                {
                    "purpose": "runner-https",
                    "origin": "https://runner.example",
                    "family": 4,
                    "address": BOOTSTRAP_ADDRESS,
                    "port": 443,
                }
            ],
        }
        cdigest = hc.controller_digest(SCRIPTS)
        host = {
            "schema": hc.HOST_SCHEMA,
            "firewall": {
                "family": FW.family,
                "table": FW.table,
                "chain": FW.chain,
                "anchor_comment": FW.anchor,
            },
            "policy_digest": hp.digest(self.policy),
            "controller_digest": cdigest,
            "runner": {
                "archive": str(archive),
                "archive_sha256": hashlib.sha256(bundle.getvalue()).hexdigest(),
                "workspace_mb": 16,
                "memory_mb": 256,
            },
        }
        self._write(self.paths.config_root / "host.json", json.dumps(host).encode())
        self.approve()
        reset_firewall()
        (self.host.cgroup_root / CGROUP_PARENT.lstrip("/")).mkdir(exist_ok=True)

    def approve(self, **changes: Any) -> None:
        expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=10)
        approval = {
            "schema": hc.APPROVAL_SCHEMA,
            "approved": True,
            "manifest_digest": hp.digest(self.bootstrap),
            "policy_digest": hp.digest(self.policy),
            "controller_digest": hc.controller_digest(SCRIPTS),
            "expires_at": expires.isoformat(),
        }
        approval.update(changes)
        self._write(
            self.paths.config_root / "approval.json", json.dumps(approval).encode()
        )

    @staticmethod
    def _write(path: pathlib.Path, data: bytes) -> None:
        path.write_bytes(data)
        os.chown(path, 0, 0)
        path.chmod(0o644)

    # lease helpers -------------------------------------------------------

    def prepare(self) -> str:
        reply = self.controller().prepare(
            {
                "protocol": hp.PROTOCOL,
                "op": "prepare",
                "bootstrap_manifest": self.bootstrap,
                "policy_digest": hp.digest(self.policy),
            }
        )
        return reply["lease_id"]

    def bootstrap_lease(self, lease: str, job: list[str] | None = None) -> None:
        if job is not None:
            self.host.job_argv = job
        self.controller().bootstrap(
            {
                "protocol": hp.PROTOCOL,
                "op": "bootstrap",
                "lease_id": lease,
                "jit_config": "c3ludGhldGljLWppdA==",
            }
        )

    def start(self, job: list[str] | None = None) -> str:
        lease = self.prepare()
        self.bootstrap_lease(lease, job)
        return lease

    def wait_state(
        self, lease: str, states: set[str], timeout: float = 20
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            j = self.journal()
            if j is None:
                return self.closed(lease)
            if j["state"] in states:
                return j
            time.sleep(0.05)
        raise AssertionError("state not reached")

    def binding(self, **changes: Any) -> dict[str, Any]:
        value = {
            "repository_id": REPO_ID,
            "run_id": RUN_ID,
            "run_attempt": 1,
            "job_id": JOB_ID,
            "runner_id": RUNNER_ID,
            "workflow_sha": SHA,
            "workflow_blob": BLOB,
            "starter_commit": SHA,
        }
        value.update(changes)
        return value

    def grant(
        self,
        lease: str,
        addresses: list[dict[str, Any]] | None = None,
        *,
        ttl_ms: int = 60_000,
        **changes: Any,
    ) -> dict[str, Any]:
        j = self.wait_state(lease, {"REPORTED"})
        rows = addresses or [{"family": 4, "address": TLS_ADDRESS}]
        request = {
            "protocol": hp.PROTOCOL,
            "op": "grant",
            "lease_id": lease,
            "report_digest": j["report_digest"],
            "binding": self.binding(),
            "origin": BROKER,
            "snapshot": {
                "digest": "7" * 64,
                "ttl_remaining_ms": ttl_ms,
                "addresses": rows,
            },
            "policy_digest": hp.digest(self.policy),
        }
        request.update(changes)
        return self.controller().grant(request)

    def cleanup(self, lease: str) -> dict[str, Any]:
        return self.controller().cleanup(
            {"protocol": hp.PROTOCOL, "op": "cleanup", "lease_id": lease}
        )

    def job_output(self, index: int = -1, timeout: float = 30) -> dict[str, Any]:
        child = self.host.jobs[index]
        out, err = child.communicate(timeout=timeout)
        lines = [line for line in out.decode().splitlines() if line.startswith("{")]
        if lines:
            return json.loads(lines[-1])
        # Synthetic job only: its stderr carries no secret and aids diagnosis.
        return {"exit": child.returncode, "stderr": err.decode()[-1500:]}

    # invariants ----------------------------------------------------------

    def assert_clean(self, lease: str) -> dict[str, Any]:
        assert self.journal() is None, "journal must be archived after closure"
        record = self.closed(lease)
        assert record["state"] == "CLOSED"
        rules = owned_rules()
        assert rules == [], "no owned rule may remain"
        assert baseline_ok()
        assert not (self.paths.run_root / lease).exists()
        assert not (self.paths.work_root / lease).exists()
        with pytest.raises(KeyError):
            pwd.getpwnam(record["user"])
        assert hc.uid_processes(record["uid"]) == []
        cleanup = record["cleanup"]
        assert cleanup["global_conntrack_flushed"] is False
        # The guard is only needed (and only installed) while the UID exists;
        # a crash before identity creation or after its removal needs none.
        exempt = {"global_conntrack_flushed", "guard_installed"}
        assert all(v for k, v in cleanup.items() if k not in exempt), cleanup
        return record


def chain_doc() -> list[dict[str, Any]]:
    return json.loads(sh("nft", "-j", "list", "chain", FW.family, FW.table, FW.chain))[
        "nftables"
    ]


def owned_rules() -> list[dict[str, Any]]:
    return [x["rule"] for x in chain_doc() if "rule" in x and hc.owned(x["rule"])]


def baseline_ok() -> bool:
    doc = chain_doc()
    chain = next(x["chain"] for x in doc if "chain" in x)
    comments = [x["rule"].get("comment") for x in doc if "rule" in x]
    return chain["policy"] == "drop" and comments == [
        "l3h-it-established",
        "l3h-it-root",
        FW.anchor,
    ]


# ── TLS material (generated per session; never committed) ──────────────────

CERTS = BASE.with_name("l3h-it-certs")
CA_PEM = CERTS / "ca.pem"


def make_certs() -> None:
    if CERTS.exists():
        shutil.rmtree(CERTS)
    CERTS.mkdir(mode=0o700)
    run = lambda *a: sh("openssl", *a)  # noqa: E731
    run(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=l3h-it synthetic CA",
        "-keyout",
        str(CERTS / "ca.key"),
        "-out",
        str(CA_PEM),
    )
    run(
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        f"/CN={BROKER_HOST}",
        "-keyout",
        str(CERTS / "server.key"),
        "-out",
        str(CERTS / "server.csr"),
    )
    ext = CERTS / "ext.cnf"
    ext.write_text(f"subjectAltName=DNS:{BROKER_HOST}\n")
    run(
        "x509",
        "-req",
        "-in",
        str(CERTS / "server.csr"),
        "-CA",
        str(CA_PEM),
        "-CAkey",
        str(CERTS / "ca.key"),
        "-CAcreateserial",
        "-days",
        "1",
        "-extfile",
        str(ext),
        "-out",
        str(CERTS / "server.pem"),
    )


# ── servers in the namespace (root-owned; root egress is baseline-allowed) ─


class Servers:
    def __init__(self) -> None:
        import socket
        import ssl

        self.received: dict[str, int] = {PLAIN_ADDRESS: 0, OTHER_ADDRESS: 0}
        self.tls_accepts = 0
        self._stop = False
        self.sockets = []
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(CERTS / "server.pem", CERTS / "server.key")
        for address in (TLS_ADDRESS, PLAIN_ADDRESS, OTHER_ADDRESS):
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((address, 443))
            server.listen(16)
            server.settimeout(0.2)
            self.sockets.append(server)
            target = self._tls if address == TLS_ADDRESS else self._plain
            threading.Thread(
                target=target, args=(server, address, context), daemon=True
            ).start()

    def _plain(self, server: Any, address: str, _context: Any) -> None:
        while not self._stop:
            try:
                conn, _ = server.accept()
            except OSError:
                continue
            threading.Thread(
                target=self._drain, args=(conn, address), daemon=True
            ).start()

    def _drain(self, conn: Any, address: str) -> None:
        with conn:
            conn.settimeout(30)
            while True:
                try:
                    data = conn.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                self.received[address] += len(data)

    def _tls(self, server: Any, address: str, context: Any) -> None:
        while not self._stop:
            try:
                conn, _ = server.accept()
            except OSError:
                continue
            try:
                with context.wrap_socket(conn, server_side=True) as tls:
                    tls.settimeout(5)
                    request = tls.recv(4096)
                    self.tls_accepts += 1
                    host = b"Host: " + BROKER_HOST.encode() + b"\r\n"
                    body = b'{"value":"a.b.c"}'
                    status = b"200 OK" if host in request else b"421 Misdirected"
                    tls.sendall(
                        b"HTTP/1.1 "
                        + status
                        + b"\r\nContent-Length: "
                        + str(len(body)).encode()
                        + b"\r\nConnection: close\r\n\r\n"
                        + body
                    )
            except OSError:
                continue

    def stop(self) -> None:
        self._stop = True
        for server in self.sockets:
            server.close()


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def network() -> Iterator[Servers]:
    sh("ip", "link", "set", "lo", "up")
    if "l3hit0" not in sh("ip", "link", "show"):
        sh("ip", "link", "add", "l3hit0", "type", "dummy")
        sh("ip", "link", "set", "l3hit0", "up")
        for address in (TLS_ADDRESS, PLAIN_ADDRESS, OTHER_ADDRESS, BOOTSTRAP_ADDRESS):
            sh("ip", "addr", "add", f"{address}/32", "dev", "l3hit0")
    make_certs()
    servers = Servers()
    yield servers
    servers.stop()


@pytest.fixture()
def env(network: Servers) -> Iterator[Env]:
    environment = Env()
    environment.install()
    yield environment
    for child in environment.host.jobs:
        if child.poll() is None:
            child.kill()
            child.wait(5)
    j = environment.journal()
    if j is not None:
        with contextlib.suppress(Exception):
            environment.controller().expire(j["lease_id"])
    for thread in environment.host.threads:
        thread.join(10)
    parent = environment.host.cgroup_root / CGROUP_PARENT.lstrip("/")
    for child_dir in parent.glob("*"):
        if child_dir.is_dir():
            with contextlib.suppress(OSError):
                (child_dir / "cgroup.kill").write_text("1")
                time.sleep(0.1)
                child_dir.rmdir()
    for entry in pwd.getpwall():
        if entry.pw_name.startswith(hc.USER_PREFIX) or entry.pw_name == "l3hitother":
            subprocess.run(  # noqa: S603
                ["/usr/sbin/userdel", entry.pw_name],
                check=False,
                capture_output=True,
            )
