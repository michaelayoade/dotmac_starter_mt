"""The Lane 3 topology seam (`scripts/lane3_topology_source.py`).

The runner obtains the private vantage topology ONLY through an injected
reader, binds it by the verified plan's Fleet ``host_id`` and refuses before any
target connection. These tests pin selection, every refusal, the redaction
guarantee (no topology value in any refusal, ``repr`` or evidence) and the
shell resolver's closed output.

Fixtures use documentation address ranges only (RFC 5737 / RFC 3849).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts" / "lane3_topology_source.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("lane3_topology_source", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["lane3_topology_source"] = module
    spec.loader.exec_module(module)
    return module


ts = _load()
lt = sys.modules["lane3_topology"]

HOST = "lane3-rehearsal-target"

#: Every private value in the fixture. None may appear in a refusal or evidence.
VALUES = (
    "probe-vantage-1",
    "198.51.100.20",
    "probe-operator",
    "192.0.2.10",
    "lane3jump",
    "192.0.2.54",
    "node-a/102",
    "192.0.2.0/24",
    "2001:db8::/48",
)


def record(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": "lane3.vantage-topology.v1",
        "probe_vantage": {
            "key": "probe-vantage-1",
            "host": "198.51.100.20",
            "ssh_user": "probe-operator",
        },
        "inside_vantage": {"host": "192.0.2.10", "jump_principal": "lane3jump"},
        "observer_principal": "lane3obs",
        "targets": {
            HOST: {
                "address": "192.0.2.54",
                "far_end": "192.0.2.54",
                "proxmox_slot": "node-a/102",
            }
        },
        "former_private_paths": ["192.0.2.0/24", "2001:db8::/48"],
        "probe_ports": [443, 8443],
    }
    doc.update(overrides)
    return doc


class FixedSource:
    """An in-memory reader: the test stand-in for the B7 KV reader."""

    def __init__(self, document: Any, version: Any = 7) -> None:
        self.document = document
        self.version = version
        self.reads = 0

    def read(self) -> Any:
        self.reads += 1
        return ts.TopologyReading(
            record=copy.deepcopy(self.document), kv_version=self.version
        )


def _no_value_in(text: str) -> None:
    for value in VALUES:
        assert value not in text, "a topology value reached a message"


def _refusal(exc: BaseException) -> str:
    return str(exc) + repr(exc)


# ── binding ─────────────────────────────────────────────────────────────────


def test_binding_selects_the_plan_host_and_derives_the_vantage_ref() -> None:
    bound = ts.bind(
        FixedSource(record()).read(), host_id=HOST, jump_principal="lane3jump"
    )
    assert bound.host_id == HOST and bound.kv_version == 7
    assert bound.target_address == "192.0.2.54"
    assert bound.far_end == bound.target_address
    assert bound.proxmox_slot == "node-a/102"
    assert bound.probe_host == "198.51.100.20"
    assert bound.inside_vantage == "192.0.2.10"
    assert bound.observer_principal == "lane3obs"
    assert bound.probe_ports == (443, 8443)
    assert bound.probe_vantage_ref == "probe-vantage-1@7"


def test_bound_repr_and_evidence_carry_no_value() -> None:
    bound = ts.bind(FixedSource(record()).read(), host_id=HOST)
    _no_value_in(repr(bound))
    evidence = bound.evidence()
    assert evidence["kv_version"] == 7 and evidence["host_id"] == HOST
    assert evidence["record"] == lt.RECORD_PATH
    assert evidence["structure"] == {
        "schema": "lane3.vantage-topology.v1",
        "targets": 1,
        "former_private_paths": 2,
        "probe_ports": 2,
    }
    # `probe_vantage_ref` is the record KEY and its version: a public reference.
    text = json.dumps({k: v for k, v in evidence.items() if k != "probe_vantage_ref"})
    _no_value_in(text)


def test_a_reading_repr_hides_the_record() -> None:
    _no_value_in(repr(FixedSource(record()).read()))


# ── refusals: each names a field and no value, before any connection ────────


def _two_hosts_one_address() -> dict[str, Any]:
    doc = record()
    doc["targets"]["other-host"] = dict(doc["targets"][HOST])
    return doc


@pytest.mark.parametrize(
    ("document", "version", "host_id", "kwargs", "field"),
    [
        (record(), 7, "unknown-host", {}, "targets.<host_id>"),
        (record(), 7, "", {}, "host_id"),
        (record(), 7, f" {HOST}", {}, "host_id"),
        (record(), 0, HOST, {}, "kv_version"),
        (record(), True, HOST, {}, "kv_version"),
        (record(), "7", HOST, {}, "kv_version"),
        (record(), 7, HOST, {"expected_version": 6}, "kv_version"),
        (_two_hosts_one_address(), 7, HOST, {}, "targets.<host_id>.address"),
    ],
)
def test_a_binding_refusal_names_the_field_only(
    document: Any, version: Any, host_id: str, kwargs: dict[str, Any], field: str
) -> None:
    with pytest.raises(ts.TopologyBindingRefused) as exc:
        ts.bind(FixedSource(document, version).read(), host_id=host_id, **kwargs)
    assert exc.value.field == field
    _no_value_in(_refusal(exc.value))


def _far_end_elsewhere() -> dict[str, Any]:
    doc = record()
    doc["targets"][HOST]["far_end"] = "192.0.2.99"
    return doc


@pytest.mark.parametrize(
    ("document", "kwargs"),
    [
        (_far_end_elsewhere(), {}),
        (record(observer_principal="root"), {}),
        (record(probe_ports=[]), {}),
        (record(), {"jump_principal": "someone-else"}),
        ({**record(), "extra": 1}, {}),
    ],
)
def test_a_record_refusal_propagates_without_values(
    document: Any, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(lt.TopologyRecordRefused) as exc:
        ts.bind(FixedSource(document).read(), host_id=HOST, **kwargs)
    _no_value_in(_refusal(exc.value))


def test_the_production_reader_refuses_as_unanswerable() -> None:
    with pytest.raises(ts.TopologySourceUnavailable) as exc:
        ts.default_source().read()
    assert "not provisioned" in str(exc.value)
    assert isinstance(ts.default_source(), ts.RefusingKvTopologySource)


def test_the_seam_makes_no_network_or_openbao_call() -> None:
    """Static: the module imports nothing that could reach a network or a store."""
    text = SOURCE.read_text(encoding="utf-8")
    for forbidden in (
        "urllib",
        "http.client",
        "socket",
        "requests",
        "subprocess",
        "hvac",
    ):
        assert f"import {forbidden}" not in text and f"from {forbidden}" not in text


# ── the shell resolver ──────────────────────────────────────────────────────


def test_resolve_without_a_reader_exits_unanswerable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert ts.main(["resolve", "--host-id", HOST]) == ts.EXIT_UNANSWERABLE
    captured = capsys.readouterr()
    assert captured.out == "" and "UNANSWERABLE" in captured.err


def test_resolve_emits_exactly_the_shell_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert ts.main(["resolve", "--host-id", HOST], source=FixedSource(record())) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("=", 1)[0] for line in lines] == [k for k, _ in ts.SHELL_FIELDS]
    values = dict(line.split("=", 1) for line in lines)
    assert values["TOPOLOGY_VERSION"] == "7"
    assert values["TOPOLOGY_TARGET"] == "192.0.2.54"
    assert values["TOPOLOGY_PROBE_HOST"] == "198.51.100.20"
    assert values["TOPOLOGY_OBSERVER_USER"] == "lane3obs"


def test_resolve_refusal_exits_one_and_discloses_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = ts.main(["resolve", "--host-id", "unknown-host"], source=FixedSource(record()))
    assert rc == ts.EXIT_REFUSED
    captured = capsys.readouterr()
    assert captured.out == ""
    _no_value_in(captured.err)
