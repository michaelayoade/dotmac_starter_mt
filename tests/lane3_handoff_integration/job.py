"""Synthetic job process for the handoff integration tests.

Runs as the lease's temporary UID (``setpriv``), usually inside the lease's
cgroup, exactly as the job step would. argv: ``<lease_id> <gid> <mode> ...``.
Prints ONE JSON line of fixed fields; never a token, URL or exception text.
"""

from __future__ import annotations

import dataclasses
import json
import os
import socket
import ssl
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lane3_handoff_bootstrap import load_verified_bundle  # noqa: E402

_bundle = load_verified_bundle(
    f"/run/l3h-it/run/{sys.argv[1]}/supplier",
    f"/run/l3h-it/run/{sys.argv[1]}/launch.json",
)
client = _bundle["lane3_handoff_client"]
hp = _bundle["lane3_handoff_protocol"]
source = _bundle["lane3_topology_source"]
bao = _bundle["lane3_openbao_topology"]

RUN_ROOT = "/run/l3h-it/run"
SHA = "5" * 40
REPO_ID, RUN_ID, JOB_ID, RUNNER_ID = 4101, 4102, 4103, 4104
REQUEST_URL = (
    "https://fixture.actions.githubusercontent.com/_apis/distributedtask/hubs/Actions/plans/p/jobs/j/"
    "idtoken?api-version=2.0"
)


def emit(**fields: object) -> None:
    print(json.dumps(fields, sort_keys=True), flush=True)


def challenge(lease: str, gid: int) -> dict:
    handoff = client.HandoffClient(lease_id=lease, run_root=RUN_ROOT, expected_gid=gid)
    return handoff.read_challenge()


def raw_exchange(lease: str, payload: bytes, *, stall: float = 0) -> dict:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(15)
    sock.connect(f"{RUN_ROOT}/{lease}/{hp.SOCKET_NAME}")
    if stall:
        time.sleep(stall)
    if payload:
        sock.sendall(payload)
    sock.shutdown(socket.SHUT_WR)
    try:
        return hp.read_frame(sock, deadline_s=15, require_eof=True)
    except hp.ProtocolRefused:
        return {"status": "NO_RESPONSE"}
    finally:
        sock.close()


def obtain(lease: str, gid: int, *, workflow_sha: str = SHA):
    handoff = client.HandoffClient(lease_id=lease, run_root=RUN_ROOT, expected_gid=gid)
    expectation = client.LocalExpectation(
        repository_id=REPO_ID,
        run_id=RUN_ID,
        run_attempt=1,
        workflow_sha=workflow_sha,
        starter_commit=SHA,
        job_id=JOB_ID,
        runner_id=RUNNER_ID,
        admission_digest=handoff._read_json(hp.LAUNCH_NAME)["admission_digest"],
        supplier_digest=handoff._read_json(hp.LAUNCH_NAME)["supplier_digest"],
    )
    return handoff.obtain_from_launch(request_url=REQUEST_URL, expectation=expectation)


class SyntheticKv:
    """Credential-bound in-memory server; the real bounded B7 consumer runs."""

    def __init__(self) -> None:
        self.calls = 0

    def request(self, method, path, *, body=None, token=None):
        self.calls += 1
        if self.calls == 1:
            assert method == "POST" and path == bao.LOGIN_PATH
            assert body == {"role": bao.ROLE, "jwt": "a.b.c"} and token is None
            return {
                "auth": {
                    "client_token": "synthetic-batch-canary",
                    "token_type": "batch",
                    "renewable": False,
                    "lease_duration": 60,
                    "policies": [bao.ROLE],
                    "token_policies": [bao.ROLE],
                    "identity_policies": [],
                }
            }
        assert self.calls == 2 and method == "GET"
        assert path == bao.KV_PATH + "?version=1" and token == "synthetic-batch-canary"
        return {
            "data": {
                "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                "data": {
                    "schema": "lane3.vantage-topology.v1",
                    "probe_vantage": {
                        "key": "probe-one",
                        "host": "192.0.2.1",
                        "ssh_user": "probe",
                    },
                    "inside_vantage": {
                        "host": "192.0.2.2",
                        "jump_principal": "lane3jump",
                    },
                    "observer_principal": "lane3obs",
                    "targets": {
                        "target-one": {
                            "address": "192.0.2.3",
                            "far_end": "192.0.2.3",
                            "proxmox_slot": "node/102",
                        }
                    },
                    "former_private_paths": ["192.0.2.0/24"],
                    "probe_ports": [443],
                },
            }
        }


def production_read(
    target,
    *,
    handoff=None,
    wrong_name=False,
    wrong_ca=False,
    stale=False,
    delayed=False,
):
    if wrong_name:
        target = dataclasses.replace(
            target, origin="https://other.actions.githubusercontent.com"
        )
    if stale:
        target = dataclasses.replace(target, deadline_ns=time.monotonic_ns() - 1)
    if delayed:
        target = dataclasses.replace(
            target, deadline_ns=time.monotonic_ns() + 200_000_000
        )
    transport = SyntheticKv()
    context = (
        (lambda: ssl.create_default_context())
        if wrong_ca
        else (lambda: ssl.create_default_context(cafile=os.path.join(HERE, "ca.pem")))
    )
    try:
        reader = source.github_openbao_source(
            transport=transport,
            expected_version=1,
            jwt_request_url=target.origin
            + ("/delay" if delayed else "")
            + "/synthetic/jobs/j/idtoken?api-version=2.0",
            jwt_request_token="synthetic-request-canary",
            oidc_broker_origin=target.origin,
            oidc_pinned=target,
            require_pinned=True,
            oidc_context_factory=context,
            oidc_request_started=handoff.token_request_started if handoff else None,
        )
        reading = reader.read()
        assert reading.kv_version == 1 and transport.calls == 2
        if handoff:
            handoff.record_proof("BOUNDED_PROOF")
        return "ok"
    except source.TopologySourceUnavailable:
        assert transport.calls == 0
        return "tls.refused"


def connect_result(address: str) -> str:
    try:
        with socket.create_connection((address, 443), timeout=2):
            return "connected"
    except OSError:
        return "blocked"


def main(argv: list[str]) -> None:
    lease, gid, mode, *rest = argv[1:]
    gid_n = int(gid)
    if mode == "obtain":
        try:
            target = obtain(lease, gid_n)
        except client.HandoffRefused as exc:
            emit(result="refused", label=exc.label, category=exc.category)
            return
        emit(result="granted", address=target.address)
        follow = rest[0] if rest else ""
        if follow == "tls":
            # Fresh suppliers below are synthetic transport canaries only.
            # They are not production credential retries or grant re-consumption.
            emit(
                result="granted",
                address=target.address,
                tls=production_read(
                    target,
                    handoff=client.HandoffClient(
                        lease_id=lease, run_root=RUN_ROOT, expected_gid=gid_n
                    ),
                ),
                wrong_sni=production_read(target, wrong_name=True),
                wrong_ca=production_read(target, wrong_ca=True),
                stale=production_read(target, stale=True),
                delayed=production_read(target, delayed=True),
                other=connect_result(rest[1]) if len(rest) > 1 else None,
            )
        elif follow == "handshake":
            started = time.monotonic()
            result = production_read(target, delayed=True)
            emit(
                result="granted",
                tls=result,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        elif follow == "hold":
            sock = socket.create_connection((rest[1], 443), timeout=5)
            emit(result="holding")
            for _ in range(600):
                try:
                    sock.send(b"x" * 64)
                except OSError:
                    pass
                time.sleep(0.05)
        elif follow == "second-consume":
            time.sleep(0.5)
            emit(result="granted", address=target.address)
        return
    if mode == "obtain-wrong-sha":
        try:
            obtain(lease, gid_n, workflow_sha="9" * 40)
        except client.HandoffRefused as exc:
            emit(result="refused", label=exc.label, category=exc.category)
        return
    if mode == "raw":
        kind = rest[0]
        nonce = challenge(lease, gid_n)["nonce"]
        poll = {
            "protocol": hp.PROTOCOL,
            "op": "poll",
            "lease_id": lease,
            "nonce": nonce,
        }
        body = hp.encode_message(poll)
        duplicate = body[:-1] + b',"nonce":"' + nonce.encode() + b'"}'
        payloads = {
            "duplicate": struct.pack(">I", len(duplicate)) + duplicate,
            "huge": struct.pack(">I", hp.MAX_MESSAGE + 1) + b"{" * 64,
            "truncated": struct.pack(">I", len(body)) + body[:-3],
            "mgmt": hp.frame(
                {"protocol": hp.PROTOCOL, "op": "cleanup", "lease_id": lease}
            ),
            "wrong-nonce": hp.frame({**poll, "nonce": "0" * 64}),
        }
        if kind == "stall":
            emit(reply=raw_exchange(lease, b"", stall=0))
            return
        emit(reply=raw_exchange(lease, payloads[kind]))
        return
    if mode in ("report", "report-forged-consume"):
        nonce = challenge(lease, gid_n)["nonce"]
        report = {
            "protocol": hp.PROTOCOL,
            "op": "report",
            "lease_id": lease,
            "nonce": nonce,
            "origin": "https://fixture.actions.githubusercontent.com",
            "flags": {"explicit_port_present": False, "userinfo_present": False},
            "expected": {
                "run_id": RUN_ID,
                "run_attempt": 1,
                "workflow_sha": SHA,
                "starter_commit": SHA,
            },
        }
        reply = raw_exchange(lease, hp.frame(report))
        if mode == "report":
            emit(reply=reply)
            return
        poll = {
            "protocol": hp.PROTOCOL,
            "op": "poll",
            "lease_id": lease,
            "nonce": nonce,
        }
        for _ in range(400):
            reply = raw_exchange(lease, hp.frame(poll))
            if reply.get("status") != "WAIT":
                break
            time.sleep(0.05)
        forged = {**poll, "op": "consume", "grant_digest": "e" * 64}
        emit(poll=reply, reply=raw_exchange(lease, hp.frame(forged)))
        return
    if mode == "consume-forged":
        nonce = challenge(lease, gid_n)["nonce"]
        forged = {
            "protocol": hp.PROTOCOL,
            "op": "consume",
            "lease_id": lease,
            "nonce": nonce,
            "grant_digest": "e" * 64,
        }
        emit(reply=raw_exchange(lease, hp.frame(forged)))
        return
    if mode == "report-then-consume-again":
        # A second process in the same cgroup tries to consume the grant.
        nonce = challenge(lease, gid_n)["nonce"]
        poll = {
            "protocol": hp.PROTOCOL,
            "op": "poll",
            "lease_id": lease,
            "nonce": nonce,
        }
        emit(reply=raw_exchange(lease, hp.frame(poll)))
        return
    if mode == "attack":
        directory = f"{RUN_ROOT}/{lease}"
        attempts = {}
        for name, action in (
            ("unlink_socket", lambda: os.unlink(f"{directory}/{hp.SOCKET_NAME}")),
            (
                "replace_socket",
                lambda: os.rename(f"{directory}/{hp.SOCKET_NAME}", f"{directory}/x"),
            ),
            ("create_file", lambda: open(f"{directory}/grant.json.new", "x").close()),
            (
                "write_challenge",
                lambda: open(f"{directory}/{hp.CHALLENGE_NAME}", "w").close(),
            ),
            (
                "symlink_grant",
                lambda: os.symlink(
                    f"{RUN_ROOT}/forged", f"{directory}/{hp.GRANT_NAME}"
                ),
            ),
            ("chmod_dir", lambda: os.chmod(directory, 0o777)),  # noqa: S103
            ("read_journal", lambda: open("/run/l3h-it/state/journal.json").read()),
        ):
            try:
                action()
                attempts[name] = "allowed"
            except OSError:
                attempts[name] = "denied"
        import subprocess

        controller = subprocess.run(  # noqa: S603
            [
                "/usr/bin/python3",
                "-I",
                f"{HERE}/lane3_handoff_controller.py",
                "cleanup",
            ],
            input=json.dumps(
                {"protocol": hp.PROTOCOL, "op": "cleanup", "lease_id": lease}
            ).encode(),
            capture_output=True,
            check=False,
        )
        attempts["controller_cli_exit"] = str(controller.returncode)
        emit(attempts=attempts)
        return
    if mode == "sleep":
        time.sleep(float(rest[0]))
        emit(result="slept")
        return
    emit(result="unknown-mode")


if __name__ == "__main__":
    main(sys.argv)
