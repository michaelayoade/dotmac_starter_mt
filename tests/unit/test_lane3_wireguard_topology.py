"""Guarded WireGuard HTTP transport sensitivity; synthetic metadata, hosted CI."""

from __future__ import annotations

import base64
import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "lane3_wireguard_topology", ROOT / "scripts/lane3_wireguard_topology.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
api = sys.modules["lane3_openbao_topology"]
LOCAL = base64.b64encode(b"a" * 32).decode()
PEER = base64.b64encode(b"b" * 32).decode()


class Kernel:
    def __init__(self) -> None:
        self.local = LOCAL
        self.allowed = PEER + "\t192.0.2.1/32"
        self.handshakes = PEER + "\t990"
        self.addresses = [{"addr_info": [{"local": "192.0.2.2", "scope": "global"}]}]
        self.route = [{"dev": "wg0", "src": "192.0.2.2"}]
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[-1] == "public-key":
            return self.local
        if args[-1] == "allowed-ips":
            return self.allowed
        if args[-1] == "latest-handshakes":
            return self.handshakes
        if "address" in args:
            return json.dumps(self.addresses)
        if "route" in args:
            return json.dumps(self.route)
        raise RuntimeError("unexpected command")


def transport(k: Kernel, **kw: Any) -> Any:
    return m.WireGuardOpenBaoTransport(
        endpoint_address="192.0.2.1",
        source_address="192.0.2.2",
        interface="wg0",
        expected_local_public_key=LOCAL,
        expected_peer_public_key=PEER,
        command=k,
        clock=lambda: 1000,
        **kw,
    )


def test_exact_peer_source_route_and_recency() -> None:
    k = Kernel()
    transport(k)
    assert len(k.calls) == 5
    assert all("dump" not in call and "private-key" not in call for call in k.calls)
    assert all("synthetic-token" not in repr(call) for call in k.calls)


@pytest.mark.parametrize(
    "field,value",
    [
        ("local", PEER),
        ("allowed", PEER + "\t0.0.0.0/0"),
        ("allowed", LOCAL + "\t192.0.2.1/32"),
        ("allowed", PEER + "\t192.0.2.1/32\n" + LOCAL + "\t192.0.2.1/32"),
        ("handshakes", PEER + "\t0"),
        ("handshakes", PEER + "\t800"),
        ("handshakes", PEER + "\t1001"),
        ("route", [{"dev": "eth0", "src": "192.0.2.2"}]),
        ("route", [{"dev": "wg0", "src": "192.0.2.3"}]),
        ("addresses", []),
    ],
)
def test_changed_channel_refuses(field: str, value: Any) -> None:
    k = Kernel()
    setattr(k, field, value)
    with pytest.raises(m.TopologySourceUnavailable):
        transport(k)


def test_privilege_failure_is_redacted() -> None:
    def denied(args: list[str]) -> str:
        raise RuntimeError("PRIVATE-MARKER")

    with pytest.raises(m.TopologySourceUnavailable) as e:
        m.WireGuardOpenBaoTransport(
            endpoint_address="192.0.2.1",
            source_address="192.0.2.2",
            interface="wg0",
            expected_local_public_key=LOCAL,
            expected_peer_public_key=PEER,
            command=denied,
        )
    assert "PRIVATE-MARKER" not in str(e.value)


def test_source_bound_connection_and_recheck_before_credentials(
    monkeypatch: Any,
) -> None:
    k = Kernel()
    t = transport(k)
    events = []

    class Connection:
        sock = None

        def connect(self) -> None:
            events.append("connected")
            k.route = [{"dev": "eth0", "src": "192.0.2.2"}]

        def request(self, *a: Any, **kw: Any) -> None:
            events.append("sent")

        def close(self) -> None:
            events.append("closed")

    def connection(*a: Any, **kw: Any) -> Connection:
        assert kw["source_address"] == ("192.0.2.2", 0)
        return Connection()

    monkeypatch.setattr(m.http.client, "HTTPConnection", connection)
    with pytest.raises(m.TopologySourceUnavailable):
        t.request(
            "POST", api.LOGIN_PATH, body={"role": api.ROLE, "jwt": "synthetic-token"}
        )
    assert events == ["connected", "closed"]


def test_no_ambient_or_default_transport_activation() -> None:
    import lane3_topology_source as source

    assert isinstance(source.default_source(), source.RefusingKvTopologySource)


def test_linux_explicit_source_route_shape() -> None:
    k = Kernel()
    k.route = [{"dev": "wg0", "from": "192.0.2.2"}]
    transport(k)


def test_root_guard_digest_and_fresh_check_points_never_run_commands():
    import lane3_handoff_protocol as hp

    kernel, checks = Kernel(), []
    channel = transport(kernel, guard=lambda digest: checks.append(digest))
    expected = hp.digest(
        {
            "endpoint_address": "192.0.2.1",
            "source_address": "192.0.2.2",
            "interface": "wg0",
            "expected_local_public_key": LOCAL,
            "expected_peer_public_key": PEER,
        }
    )
    assert checks == [expected] and kernel.calls == []
    connection = channel._connection()
    connection.close()
    channel._before_send()
    assert checks == [expected] * 3 and kernel.calls == []


def test_root_guard_refusal_prevents_connection_and_send(monkeypatch):
    checks, commands, connects = [], Kernel(), []

    def guard(digest):
        checks.append(digest)
        if len(checks) > 1:
            raise RuntimeError("PRIVATE-GUARD-CANARY")

    channel = transport(commands, guard=guard)
    monkeypatch.setattr(
        m.http.client, "HTTPConnection", lambda *a, **k: connects.append(a)
    )
    with pytest.raises(m.TopologySourceUnavailable) as caught:
        channel._connection()
    assert connects == [] and commands.calls == []
    assert "PRIVATE" not in str(caught.value)
    with pytest.raises(m.TopologySourceUnavailable):
        channel._before_send()


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint_address", 123),
        ("source_address", True),
        ("interface", "eth0"),
        ("expected_local_public_key", "PRIVATE-KEY-CANARY"),
        ("expected_peer_public_key", LOCAL),
    ],
)
def test_invalid_broker_configuration_refuses_before_guard(field, value):
    checks = []
    config = {
        "endpoint_address": "192.0.2.1",
        "source_address": "192.0.2.2",
        "interface": "wg0",
        "expected_local_public_key": LOCAL,
        "expected_peer_public_key": PEER,
        "guard": lambda digest: checks.append(digest),
    }
    config[field] = value
    with pytest.raises(m.TopologySourceUnavailable):
        m.WireGuardOpenBaoTransport(**config)
    assert checks == []


def test_false_broker_callback_is_not_success_and_never_falls_back():
    commands = Kernel()
    with pytest.raises(m.TopologySourceUnavailable):
        transport(commands, guard=lambda digest: False)
    assert commands.calls == []


def test_broker_digest_excludes_no_foreign_fields():
    config = {
        "endpoint_address": "192.0.2.1",
        "source_address": "192.0.2.2",
        "interface": "wg0",
        "expected_local_public_key": LOCAL,
        "expected_peer_public_key": PEER,
    }
    assert len(m.wireguard_config_digest(config)) == 64
    with pytest.raises(m.TopologySourceUnavailable):
        m.wireguard_config_digest(
            {**config, "oidc_broker_origin": "https://fixture.example"}
        )


def test_broker_presend_refusal_canary_detects_weakened_guard(monkeypatch):
    def canary():
        checks, events, commands = [], [], Kernel()

        def guard(config_digest):
            checks.append(config_digest)
            if len(checks) == 3:
                raise RuntimeError("PRIVATE-METADATA-CANARY")

        class Connection:
            sock = None

            def connect(self):
                events.append("connected")

            def request(self, *args, **kwargs):
                events.append("credential-send")
                raise RuntimeError("synthetic-send-stop")

            def close(self):
                events.append("closed")

        monkeypatch.setattr(
            m.http.client, "HTTPConnection", lambda *a, **k: Connection()
        )
        channel = transport(commands, guard=guard)
        with pytest.raises(m.TopologySourceUnavailable):
            channel.request(
                "POST",
                api.LOGIN_PATH,
                body={"role": api.ROLE, "jwt": "synthetic-jwt-canary"},
            )
        assert events == ["connected", "closed"]
        assert checks == [checks[0]] * 3 and commands.calls == []

    canary()
    monkeypatch.setattr(m.WireGuardOpenBaoTransport, "_before_send", lambda self: None)
    with pytest.raises(AssertionError):
        canary()
