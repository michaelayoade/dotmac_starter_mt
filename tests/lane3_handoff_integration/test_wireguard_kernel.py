"""Actual kernel WireGuard metadata consumed by the canonical root guard.

Hosted/root collection is gated by conftest. Both UDP endpoints stay on the
disposable outer namespace's loopback; the peer's inner interface lives in a
second disposable namespace. No external endpoint or OpenBao is contacted.
This proves the live callback/guard, not lease authentication, systemd or reboot.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator

import lane3_handoff_controller as hc
import lane3_handoff_protocol as hp
import pytest
from conftest import ENABLED
from lane3_openbao_topology import TopologySourceUnavailable
from lane3_wireguard_topology import (
    WireGuardOpenBaoTransport,
    public_command,
    wireguard_config_digest,
)

LOCAL, PEER = "192.0.2.101", "192.0.2.102"


def run(*argv: str, data: bytes | None = None) -> bytes:
    """Bound every setup command and never include key material in errors."""
    result = subprocess.run(  # noqa: S603 - owned synthetic kernel fixtures
        argv, input=data, capture_output=True, timeout=5, check=False
    )
    if result.returncode:
        raise AssertionError("synthetic WireGuard setup command failed")
    return result.stdout


class KernelChannel:
    def __init__(self, directory: pathlib.Path) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.local, self.peer = f"wg{suffix}a", f"wg{suffix}b"
        self.directory = directory
        self.child: subprocess.Popen[bytes] | None = None
        self.created: list[str] = []
        self.moved = False
        self.route_created = False
        self.commands: list[list[str]] = []
        self.config: dict[str, str] = {}

    def peer_run(self, *argv: str) -> bytes:
        assert self.child is not None and self.child.poll() is None
        return run("nsenter", f"--net=/proc/{self.child.pid}/ns/net", "--", *argv)

    def setup(self) -> None:
        # The interface's UDP socket retains its birth namespace after a move.
        # Creating/configuring both here keeps the encrypted outer path loopback.
        run("ip", "link", "set", "lo", "up")
        public = []
        for index, name in enumerate((self.local, self.peer)):
            private = run("wg", "genkey")
            public.append(run("wg", "pubkey", data=private).decode().strip())
            key_path = self.directory / f"key-{index}"
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(private)
            run("ip", "link", "add", name, "type", "wireguard")
            self.created.append(name)
            run(
                "wg",
                "set",
                name,
                "private-key",
                str(key_path),
                "listen-port",
                str(51871 + index),
            )
        for name, key, address, port in (
            (self.local, public[1], PEER, "51872"),
            (self.peer, public[0], LOCAL, "51871"),
        ):
            run(
                "wg",
                "set",
                name,
                "peer",
                key,
                "allowed-ips",
                f"{address}/32",
                "endpoint",
                f"127.0.0.1:{port}",
            )
        self.child = subprocess.Popen(  # noqa: S603 - fixed owned namespace
            ["/usr/bin/unshare", "--net", "--", "/bin/sleep", "120"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        parent_ns = os.readlink("/proc/self/ns/net")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            assert self.child.poll() is None, "peer namespace creation failed"
            if os.readlink(f"/proc/{self.child.pid}/ns/net") != parent_ns:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("peer namespace creation timed out")
        run("ip", "link", "set", self.peer, "netns", str(self.child.pid))
        self.moved = True
        run("ip", "address", "add", f"{LOCAL}/32", "dev", self.local)
        run("ip", "link", "set", self.local, "up")
        run("ip", "route", "add", f"{PEER}/32", "dev", self.local, "src", LOCAL)
        self.route_created = True
        self.peer_run("ip", "link", "set", "lo", "up")
        self.peer_run("ip", "address", "add", f"{PEER}/32", "dev", self.peer)
        self.peer_run("ip", "link", "set", self.peer, "up")
        self.peer_run(
            "ip", "route", "add", f"{LOCAL}/32", "dev", self.peer, "src", PEER
        )
        self.config = {
            "endpoint_address": PEER,
            "source_address": LOCAL,
            "interface": self.local,
            "expected_local_public_key": public[0],
            "expected_peer_public_key": public[1],
        }
        deadline = time.monotonic() + 10
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.bind((LOCAL, 0))
            sender.settimeout(0.25)
            while time.monotonic() < deadline:
                # A closed peer port may return asynchronous ICMP; the payload
                # still drives the WireGuard handshake, so retry that condition.
                with contextlib.suppress(ConnectionRefusedError, TimeoutError):
                    sender.sendto(b"synthetic-handshake", (PEER, 9))
                rows = self.command(
                    ["/usr/bin/wg", "show", self.local, "latest-handshakes"]
                ).splitlines()
                if any(
                    fields[0] == public[1] and int(fields[1]) > 0
                    for row in rows
                    if len(fields := row.split()) == 2
                ):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("synthetic kernel WireGuard handshake timed out")

    def command(self, argv: list[str]) -> str:
        allowed = [
            ["/usr/bin/wg", "show", self.local, field]
            for field in ("public-key", "allowed-ips", "latest-handshakes")
        ] + [
            ["/usr/sbin/ip", "-json", "address", "show", "dev", self.local],
            ["/usr/sbin/ip", "-json", "route", "get", PEER, "from", LOCAL],
        ]
        assert argv in allowed, "only fixed public metadata may be read"
        self.commands.append(argv.copy())
        return public_command(argv)

    def close(self) -> None:
        # Attempt every owned resource even if an earlier deletion fails.
        with contextlib.ExitStack() as cleanup:
            for name in self.created:
                if name != self.peer or not self.moved:
                    cleanup.callback(run, "ip", "link", "delete", name)
            if self.moved and self.child is not None and self.child.poll() is None:
                cleanup.callback(self.peer_run, "ip", "link", "delete", self.peer)
            if self.route_created:
                cleanup.callback(run, "ip", "route", "delete", f"{PEER}/32")

    def stop_child(self) -> None:
        if self.child is not None:
            if self.child.poll() is None:
                self.child.terminate()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)
        for key_path in self.directory.glob("key-*"):
            key_path.unlink()


@pytest.fixture()
def channel() -> Iterator[KernelChannel]:
    assert ENABLED, "only disposable hosted root integration may create interfaces"
    # Protected-reader parents must all be root-owned and non-writable by
    # others; pytest's default /tmp parent deliberately cannot satisfy that.
    with tempfile.TemporaryDirectory(prefix="l3h-wg-", dir="/run") as directory:
        value = KernelChannel(pathlib.Path(directory))
        try:
            value.setup()
            yield value
        finally:
            try:
                value.close()
            finally:
                value.stop_child()


def live_callback(channel: KernelChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    # The real protected reader and SystemHost command implementation run.
    # Only the fixed config location points at this root-owned synthetic file.
    path = channel.directory / "wireguard.json"
    path.write_bytes(
        hp.canonical_json(
            {**channel.config, "oidc_broker_origin": "https://broker.example"}
        )
    )
    path.chmod(0o644)
    monkeypatch.setattr(hc, "WIREGUARD_CONFIG", path)
    controller = hc.Controller()
    controller._wireguard_live(
        wireguard_config_digest(channel.config), time.monotonic_ns() + 3_000_000_000
    )


def test_kernel_channel_passes_canonical_guard_and_live_callback(
    channel: KernelChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel.commands.clear()
    transport = WireGuardOpenBaoTransport(**channel.config, command=channel.command)
    assert len(channel.commands) == 5
    transport._before_send()
    assert len(channel.commands) == 10, "guard must re-read live metadata"
    live_callback(channel, monkeypatch)


@pytest.mark.parametrize("fault", ["stale", "wrong_peer", "wrong_route"])
def test_kernel_channel_drift_is_refused(
    channel: KernelChannel, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    transport = WireGuardOpenBaoTransport(**channel.config, command=channel.command)
    if fault == "stale":
        # Advance only the guard clock against the real kernel timestamp. The
        # timestamp is neither fabricated nor changed; no 181-second CI sleep.
        stamp = int(
            channel.command(
                ["/usr/bin/wg", "show", channel.local, "latest-handshakes"]
            ).split()[1]
        )
        monkeypatch.setattr(transport, "_clock", lambda: stamp + 181)
    elif fault == "wrong_peer":
        wrong = run("wg", "pubkey", data=run("wg", "genkey")).decode().strip()
        run(
            "wg",
            "set",
            channel.local,
            "peer",
            channel.config["expected_peer_public_key"],
            "remove",
        )
        run("wg", "set", channel.local, "peer", wrong, "allowed-ips", f"{PEER}/32")
    else:
        run("ip", "route", "replace", f"{PEER}/32", "dev", "lo", "src", LOCAL)
    with pytest.raises(TopologySourceUnavailable, match="transport.wireguard"):
        transport._before_send()
    if fault != "stale":
        with pytest.raises(TopologySourceUnavailable, match="transport.wireguard"):
            live_callback(channel, monkeypatch)
