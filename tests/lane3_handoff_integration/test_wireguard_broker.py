"""Real unprivileged socket/client with NNP and root-only synthetic metadata.

The full canonical WireGuard guard runs for each callback. Kernel WireGuard
state and the complete production systemd hardening remain separate proofs.
"""

from __future__ import annotations

import base64
import os
import shlex
import subprocess
import textwrap
import time
from typing import Any

import lane3_handoff_controller as hc
import lane3_handoff_protocol as hp
import pytest
from conftest import Env
from lane3_wireguard_topology import wireguard_config_digest


def test_consumed_nnp_client_uses_fresh_root_wireguard_broker(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = base64.b64encode(b"l" * 32).decode()
    peer = base64.b64encode(b"p" * 32).decode()
    config = {
        "endpoint_address": "192.0.2.10",
        "source_address": "192.0.2.11",
        "interface": "wgtest",
        "expected_local_public_key": local,
        "expected_peer_public_key": peer,
    }
    digest = wireguard_config_digest(config)
    original_read = hc.read_protected
    original_run = env.host._run
    metadata: list[list[str]] = []

    def read(path: Any, **kw: Any) -> bytes:
        if path == hc.WIREGUARD_CONFIG:
            return hp.canonical_json(
                {**config, "oidc_broker_origin": "https://broker.example"}
            )
        return original_read(path, **kw)

    def command(argv: list[str], **kw: Any) -> bytes:
        if argv[0] not in {"/usr/bin/wg", "/usr/sbin/ip"}:
            return original_run(argv, **kw)
        assert os.geteuid() == 0 and "/usr/bin/sudo" not in argv
        metadata.append(argv)
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

    def spawn(
        unit: str,
        uid: int,
        gid: int,
        lease_prefix: str,
        argv: list[str],
        *,
        in_cgroup: bool = True,
    ) -> subprocess.Popen[bytes]:
        assert in_cgroup
        lease = env.lease_for(lease_prefix)
        cgroup = env.host.cgroup_root / env.host.unit_cgroup(unit).lstrip("/")
        cgroup.mkdir(exist_ok=True)
        script = textwrap.dedent(
            f"""
            import json,sys
            sys.path.insert(0, {str(env.jobbin)!r})
            import job
            lease,gid=sys.argv[1],int(sys.argv[2])
            h=job.client.HandoffClient(lease_id=lease,run_root=job.RUN_ROOT,expected_gid=gid)
            launch=h._read_json(job.hp.LAUNCH_NAME)
            expected=job.client.LocalExpectation(repository_id=job.REPO_ID,run_id=job.RUN_ID,
                run_attempt=1,workflow_sha=job.SHA,starter_commit=job.SHA,
                job_id=job.JOB_ID,runner_id=job.RUNNER_ID,
                admission_digest=launch['admission_digest'],supplier_digest=launch['supplier_digest'])
            h.obtain_from_launch(request_url=job.REQUEST_URL,expectation=expected)
            h.wireguard_check({digest!r})
            h.wireguard_check({digest!r})
            with open('/proc/self/status') as status:
                nnp=any(line.split()==['NoNewPrivs:','1'] for line in status)
            print(json.dumps({{'result':'broker-checked','nnp':nnp}}),flush=True)
            """
        )
        child_argv = [
            "setpriv",
            f"--reuid={uid}",
            f"--regid={gid}",
            "--clear-groups",
            "--no-new-privs",
            "/usr/bin/python3",
            "-I",
            "-c",
            script,
            lease,
            str(gid),
        ]
        shell = f"echo $$ > {shlex.quote(str(cgroup / 'cgroup.procs'))} && exec "
        shell += " ".join(shlex.quote(item) for item in child_argv)
        child = subprocess.Popen(  # noqa: S603 - synthetic disposable test job
            ["/bin/sh", "-c", shell],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        env.host.jobs.append(child)
        return child

    monkeypatch.setattr(hc, "read_protected", read)
    monkeypatch.setattr(env.host, "_run", command)
    monkeypatch.setattr(env.host, "spawn", spawn)
    lease = env.start(["synthetic-wireguard-broker"])
    env.grant(lease)
    assert env.job_output() == {"result": "broker-checked", "nnp": True}
    assert len(metadata) == 10
    env.cleanup(lease)
    env.assert_clean(lease)
