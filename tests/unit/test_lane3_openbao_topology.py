"""B7 reader sensitivity tests; synthetic material, hosted-CI execution only."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "lane3_openbao_topology", ROOT / "scripts/lane3_openbao_topology.py"
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def record() -> dict[str, Any]:
    return {
        "schema": "lane3.vantage-topology.v1",
        "probe_vantage": {"key": "probe-one", "host": "192.0.2.1", "ssh_user": "probe"},
        "inside_vantage": {"host": "192.0.2.2", "jump_principal": "lane3jump"},
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
    }


class Transport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any, Any]] = []
        self.auth: dict[str, Any] = {
            "client_token": "synthetic-batch-token",
            "token_type": "batch",
            "renewable": False,
            "lease_duration": 300,
            "token_policies": ["lane3-exposure-rehearsal"],
            "policies": ["lane3-exposure-rehearsal"],
            "identity_policies": [],
        }
        self.data: dict[str, Any] = {
            "data": record(),
            "metadata": {
                "version": 1,
                "destroyed": False,
                "deletion_time": "",
            },
        }

    def request(
        self, method: str, path: str, *, body: Any = None, token: Any = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, body, token))
        return {"auth": self.auth} if method == "POST" else {"data": self.data}


def source(t: Transport, **kw: Any) -> Any:
    return mod.OpenBaoTopologySource(
        transport=t,
        jwt_supplier=lambda audience: "synthetic-jwt",
        expected_version=1,
        **kw,
    )


def test_reads_exact_version_with_b7_identity() -> None:
    t = Transport()
    reading = source(t).read()
    assert reading.kv_version == 1
    assert reading.record == record()
    assert t.calls[0][:3] == (
        "POST",
        "/v1/auth/jwt/login",
        {"role": "lane3-exposure-rehearsal", "jwt": "synthetic-jwt"},
    )
    assert t.calls[1] == (
        "GET",
        "/v1/secret/data/dotmac/starter/lane3/vantage-topology?version=1",
        None,
        "synthetic-batch-token",
    )
    assert len(t.calls) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("token_type", "service"),
        ("renewable", True),
        ("lease_duration", 301),
        ("lease_duration", True),
        ("lease_duration", 0),
        ("client_token", ""),
        ("policies", ["root"]),
        ("token_policies", ["default", "lane3-exposure-rehearsal"]),
        ("identity_policies", ["extra"]),
    ],
)
def test_auth_widening_refuses_before_kv(field: str, value: Any) -> None:
    t = Transport()
    t.auth[field] = value
    with pytest.raises(mod.TopologySourceUnavailable, match=r"auth\.response"):
        source(t).read()
    assert len(t.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("version", True),
        ("destroyed", True),
        ("deletion_time", "2026-01-01"),
    ],
)
def test_wrong_or_deleted_version_refuses(field: str, value: Any) -> None:
    t = Transport()
    t.data["metadata"][field] = value
    with pytest.raises(mod.TopologySourceUnavailable, match=r"kv\.metadata"):
        source(t).read()


def test_schema_refusal_is_redacted() -> None:
    t = Transport()
    t.data["data"]["observer_principal"] = "PRIVATE-MARKER"
    with pytest.raises(mod.TopologySourceUnavailable) as e:
        source(t).read()
    assert "PRIVATE-MARKER" not in str(e.value)


def test_single_use_and_no_cached_read() -> None:
    t = Transport()
    s = source(t)
    s.read()
    with pytest.raises(mod.TopologySourceUnavailable, match=r"source\.consumed"):
        s.read()
    assert len(t.calls) == 2
    assert "synthetic" not in repr(s)


def test_supplier_gets_fixed_audience_and_failure_is_redacted() -> None:
    t = Transport()
    audiences = []

    def jwt(audience: str) -> str:
        audiences.append(audience)
        raise RuntimeError("PRIVATE-JWT")

    s = mod.OpenBaoTopologySource(transport=t, jwt_supplier=jwt, expected_version=1)
    with pytest.raises(mod.TopologySourceUnavailable) as e:
        s.read()
    assert audiences == ["urn:dotmac:lane3:exposure-rehearsal"]
    assert "PRIVATE-JWT" not in str(e.value)
    assert t.calls == []


def test_deadline_refuses_before_kv() -> None:
    t = Transport()
    ticks = iter([0.0, 0.0, 16.0])
    with pytest.raises(mod.TopologySourceUnavailable, match="deadline"):
        source(t, clock=lambda: next(ticks)).read()
    assert len(t.calls) == 1


@pytest.mark.parametrize("version", [0, True, -1, "1"])
def test_invalid_expected_version(version: Any) -> None:
    with pytest.raises(mod.TopologySourceUnavailable):
        mod.OpenBaoTopologySource(
            transport=Transport(),
            jwt_supplier=lambda _: "jwt",
            expected_version=version,
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.invalid",
        "https://user@example.invalid",
        "https://example.invalid/path",
        "https://example.invalid?token=private",
    ],
)
def test_https_transport_refuses_unsafe_endpoint(endpoint: str) -> None:
    with pytest.raises(mod.TopologySourceUnavailable):
        mod.HttpsOpenBaoTransport(endpoint=endpoint, ca_file="not-opened.pem")


def test_reader_is_consumed_by_existing_resolver(capsys: Any) -> None:
    t = Transport()
    s = mod.source.openbao_source(
        transport=t, jwt_supplier=lambda _: "synthetic-jwt", expected_version=1
    )
    assert mod.source.main(["resolve", "--host-id", "target-one"], source=s) == 0
    output = capsys.readouterr()
    assert "TOPOLOGY_VERSION=1" in output.out
    assert "TOPOLOGY_TARGET=192.0.2.3" in output.out
    assert output.err == ""


def test_default_execution_remains_refusing() -> None:
    assert isinstance(mod.source.default_source(), mod.source.RefusingKvTopologySource)


def test_expired_short_token_refuses_result() -> None:
    t = Transport()
    t.auth["lease_duration"] = 1
    ticks = iter([0.0, 0.0, 0.0, 0.0, 1.0, 1.0])
    with pytest.raises(mod.TopologySourceUnavailable, match=r"auth\.expired"):
        source(t, clock=lambda: next(ticks)).read()


@pytest.mark.parametrize(
    "status,payload",
    [
        (302, b"{}"),
        (403, b'{"errors":["PRIVATE-MARKER"]}'),
        (200, b"x" * 65537),
        (200, b'{"data":{},"data":{}}'),
        (200, b"[]"),
        (200, b"PRIVATE-MARKER"),
    ],
)
def test_transport_failure_closes_connection_and_redacts(
    monkeypatch: Any, status: int, payload: bytes
) -> None:
    class Response:
        def __init__(self) -> None:
            self.status = status

        def read(self, size: int) -> bytes:
            assert size == 65537
            return payload

    class Connection:
        sock = None
        closed = False
        requests = 0

        def connect(self) -> None:
            pass

        def request(self, *args: Any, **kwargs: Any) -> None:
            self.requests += 1

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(mod.http.client, "HTTPSConnection", lambda *a, **kw: connection)
    transport = mod.HttpsOpenBaoTransport.__new__(mod.HttpsOpenBaoTransport)
    transport._host = "example.invalid"
    transport._port = 443
    transport._tls = None
    with pytest.raises(mod.TopologySourceUnavailable) as e:
        transport.request(
            "POST", mod.LOGIN_PATH, body={"role": mod.ROLE, "jwt": "synthetic"}
        )
    assert "PRIVATE-MARKER" not in str(e.value)
    assert connection.closed
    assert connection.requests == 1


def test_github_factory_composes_oidc_and_reader(monkeypatch: Any) -> None:
    import lane3_github_oidc as oidc

    calls = []

    class Response:
        status = 200

        def read(self, size: int) -> bytes:
            return b'{"value":"a.b.c"}'

    class Connection:
        sock = None

        def connect(self) -> None:
            pass

        def request(self, method: str, path: str, **kwargs: Any) -> None:
            calls.append((method, path, kwargs))

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        oidc.http.client, "HTTPSConnection", lambda *a, **kw: Connection()
    )
    t = Transport()
    origin = "https://fixture.actions.githubusercontent.com"
    s = mod.source.github_openbao_source(
        transport=t,
        expected_version=1,
        jwt_request_url=origin + "/job/idtoken?api-version=2.0",
        jwt_request_token="synthetic-actions-request",
        oidc_broker_origin=origin,
    )
    assert s.read().kv_version == 1
    assert t.calls[0][2]["jwt"] == "a.b.c"
    assert len(calls) == 1
    assert "audience=urn%3Adotmac%3Alane3%3Aexposure-rehearsal" in calls[0][1]
