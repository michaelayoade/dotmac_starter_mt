"""Synthetic job process for the handoff integration tests.

Runs as the lease's temporary UID (``setpriv``), usually inside the lease's
cgroup, exactly as the job step would. argv: ``<lease_id> <gid> <mode> ...``.
Prints ONE JSON line of fixed fields; never a token, URL or exception text.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import lane3_handoff_client as client  # noqa: E402
import lane3_handoff_protocol as hp  # noqa: E402

RUN_ROOT = "/run/l3h-it/run"
SHA = "5" * 40
REPO_ID, RUN_ID, JOB_ID, RUNNER_ID = 4101, 4102, 4103, 4104
REQUEST_URL = (
    "https://broker.example/_apis/distributedtask/hubs/Actions/plans/p/jobs/j/"
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
    )
    return handoff.obtain(
        request_url=REQUEST_URL, expectation=expectation, launch_ns=time.monotonic_ns()
    )


def tls_get(address: str, server_name: str, host_header: str) -> str:
    """Numeric connect, assert the peer, then TLS with SNI = approved host."""
    raw = socket.create_connection((address, 443), timeout=5)
    try:
        if raw.getpeername()[0] != address:
            return "peer.mismatch"
        context = ssl.create_default_context(cafile=os.path.join(HERE, "ca.pem"))
        with context.wrap_socket(raw, server_hostname=server_name) as tls:
            tls.sendall(
                f"GET /idtoken HTTP/1.1\r\nHost: {host_header}\r\n\r\n".encode()
            )
            reply = tls.recv(4096)
            return "ok" if reply.startswith(b"HTTP/1.1 200") else "http.refused"
    except ssl.SSLError:
        return "tls.refused"
    finally:
        raw.close()


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
            emit(
                result="granted",
                address=target.address,
                tls=tls_get(target.address, "broker.example", "broker.example"),
                wrong_sni=tls_get(target.address, "other.example", "broker.example"),
                other=connect_result(rest[1]) if len(rest) > 1 else None,
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
            "origin": "https://broker.example",
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
                lambda: os.symlink("/tmp/forged", f"{directory}/{hp.GRANT_NAME}"),
            ),
            ("chmod_dir", lambda: os.chmod(directory, 0o777)),
            ("read_journal", lambda: open("/run/l3h-it/state/journal.json").read()),
        ):
            try:
                action()
                attempts[name] = "allowed"
            except OSError:
                attempts[name] = "denied"
        import subprocess

        controller = subprocess.run(
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
