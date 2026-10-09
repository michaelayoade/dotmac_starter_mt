"""``lane3.vantage-topology.v1``: the record B7 writes and the D4 runner reads.

Two properties matter. The parser refuses every deviation from the closed
schema, and no refusal, repr or summary discloses a value (a target address
or vantage host must never become a log line).

Parity with the B7 provisioning validator (``b67-model.py``, kept outside the
repository with the provisioning scripts) is checked over one fixture set.
The frozen expectations below are that validator's behaviour, measured
2026-10-09. When ``LANE3_B67_MODEL_PATH`` names the file, the test also runs
it live. Fixtures in ``STARTER_STRICTER`` were divergences measured on
2026-10-09 (Starter refused, the provisioning validator accepted). The
provisioning validator was tightened the same day, so both now refuse them.
"""

from __future__ import annotations

import copy
import importlib.util
import os
import pathlib
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import lane3_topology as lt

SECRET = "zz-secret-marker"  # placed in every value position; must never surface


def base() -> dict[str, Any]:
    return {
        "schema": "lane3.vantage-topology.v1",
        "probe_vantage": {
            "key": f"probe-{SECRET}",
            "host": f"probe.{SECRET}.example.invalid",
            "ssh_user": f"u{SECRET}",
        },
        "inside_vantage": {
            "host": f"inside.{SECRET}.example.invalid",
            "jump_principal": "lane3jump",
        },
        "observer_principal": "lane3obs",
        "targets": {
            "host-0001": {
                "address": "192.0.2.10",
                "far_end": f"198.51.100.20-{SECRET}",
                "proxmox_slot": f"slot-{SECRET}",
            },
        },
        "former_private_paths": [f"/private/{SECRET}/a", f"/private/{SECRET}/b"],
        "probe_ports": [443, 8443],
    }


def mutate(fn) -> dict[str, Any]:
    d = base()
    fn(d)
    return d


def _set(path: list, value):
    def f(d):
        cur = d
        for p in path[:-1]:
            cur = cur[p]
        cur[path[-1]] = value

    return f


def _del(path: list):
    def f(d):
        cur = d
        for p in path[:-1]:
            cur = cur[p]
        del cur[path[-1]]

    return f


# name -> (record, jump_principal, accepted_by_both)
FIXTURES: dict[str, tuple[Any, str | None, bool]] = {
    "valid": (base(), None, True),
    "valid-with-matching-jump": (base(), "lane3jump", True),
    "valid-two-targets": (
        mutate(
            lambda d: d["targets"].update(
                {
                    "host-0002": {
                        "address": "2001:db8::2",
                        "far_end": "x",
                        "proxmox_slot": "y",
                    }
                }
            )
        ),
        None,
        True,
    ),
    "not-a-mapping": ([], None, False),
    "extra-top-key": (mutate(_set(["extra"], 1)), None, False),
    "missing-top-key": (mutate(_del(["probe_ports"])), None, False),
    "wrong-schema": (
        mutate(_set(["schema"], "lane3.vantage-topology.v2")),
        None,
        False,
    ),
    "probe-extra-key": (mutate(_set(["probe_vantage", "port"], "22")), None, False),
    "probe-empty-host": (mutate(_set(["probe_vantage", "host"], "")), None, False),
    "probe-padded-user": (
        mutate(_set(["probe_vantage", "ssh_user"], " u")),
        None,
        False,
    ),
    "probe-nonstring": (mutate(_set(["probe_vantage", "key"], 7)), None, False),
    "inside-missing-key": (
        mutate(_del(["inside_vantage", "jump_principal"])),
        None,
        False,
    ),
    "jump-mismatch": (base(), "otherjump", False),
    "observer-wrong": (mutate(_set(["observer_principal"], "root")), None, False),
    "targets-empty": (mutate(_set(["targets"], {})), None, False),
    "targets-list": (mutate(_set(["targets"], [])), None, False),
    "target-extra-key": (
        mutate(_set(["targets", "host-0001", "port"], "22")),
        None,
        False,
    ),
    "target-empty-address": (
        mutate(_set(["targets", "host-0001", "address"], "")),
        None,
        False,
    ),
    "paths-empty": (mutate(_set(["former_private_paths"], [])), None, False),
    "paths-duplicate": (
        mutate(_set(["former_private_paths"], ["/p", "/p"])),
        None,
        False,
    ),
    "paths-nonstring": (mutate(_set(["former_private_paths"], ["/p", 3])), None, False),
    "ports-empty": (mutate(_set(["probe_ports"], [])), None, False),
    "ports-zero": (mutate(_set(["probe_ports"], [0])), None, False),
    "ports-too-high": (mutate(_set(["probe_ports"], [65536])), None, False),
    "ports-bool": (mutate(_set(["probe_ports"], [True])), None, False),
    "ports-string": (mutate(_set(["probe_ports"], ["443"])), None, False),
    "ports-duplicate": (mutate(_set(["probe_ports"], [443, 443])), None, False),
}

# Starter refuses these; the provisioning validator (measured 2026-10-09) accepts them.
STARTER_STRICTER: dict[str, tuple[Any, str | None]] = {
    "target-padded-address": (
        mutate(_set(["targets", "host-0001", "address"], " 192.0.2.10")),
        None,
    ),
    "path-padded": (mutate(_set(["former_private_paths"], [" /p"])), None),
    "probe-key-with-at": (mutate(_set(["probe_vantage", "key"], "probe@2")), None),
    "jump-reserved-root": (
        mutate(_set(["inside_vantage", "jump_principal"], "root")),
        None,
    ),
    "jump-reserved-observer": (
        mutate(_set(["inside_vantage", "jump_principal"], "lane3obs")),
        None,
    ),
    "jump-reserved-gate0": (
        mutate(_set(["inside_vantage", "jump_principal"], "dotmac-gate0-controller")),
        None,
    ),
}


def starter_accepts(record, jump) -> bool:
    try:
        lt.parse_topology_record(copy.deepcopy(record), jump_principal=jump)
        return True
    except lt.TopologyRecordRefused:
        return False


def _b67():
    path = os.environ.get("LANE3_B67_MODEL_PATH")
    if not path or not pathlib.Path(path).is_file():
        return None
    spec = importlib.util.spec_from_file_location("b67_model", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def b67_accepts(mod, record, jump) -> bool:
    try:
        mod.validate_topology(copy.deepcopy(record), jump)
        return True
    except (AssertionError, ValueError, AttributeError, TypeError, KeyError):
        return False


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_starter_matches_frozen_expectation(name):
    record, jump, expected = FIXTURES[name]
    assert starter_accepts(record, jump) is expected


@pytest.mark.parametrize("name", sorted(STARTER_STRICTER))
def test_starter_refuses_the_documented_divergences(name):
    record, jump = STARTER_STRICTER[name]
    assert starter_accepts(record, jump) is False


def test_live_parity_with_the_provisioning_validator():
    mod = _b67()
    if mod is None:
        pytest.skip("LANE3_B67_MODEL_PATH not set; frozen expectations above stand in")
    for name, (record, jump, expected) in FIXTURES.items():
        assert b67_accepts(mod, record, jump) is expected, name
    for name, (record, jump) in STARTER_STRICTER.items():
        # Formerly divergent. b67-model.py was tightened on 2026-10-09 (explicit
        # raises, whitespace on every string, no '@' in probe_vantage.key,
        # reserved jump principals), so both validators now refuse these.
        assert b67_accepts(mod, record, jump) is False, name


@pytest.mark.parametrize(
    "name",
    sorted({**{k: v[:2] for k, v in FIXTURES.items() if not v[2]}, **STARTER_STRICTER}),
)
def test_a_refusal_never_discloses_a_value(name):
    record, jump = {**{k: v[:2] for k, v in FIXTURES.items()}, **STARTER_STRICTER}[name]
    with pytest.raises(lt.TopologyRecordRefused) as exc:
        lt.parse_topology_record(copy.deepcopy(record), jump_principal=jump)
    text = str(exc.value) + repr(exc.value)
    assert SECRET not in text and "192.0.2" not in text and "198.51.100" not in text


def test_a_parsed_record_hides_every_value():
    topo = lt.parse_topology_record(base(), jump_principal="lane3jump")
    for shown in (
        repr(topo),
        str(topo),
        repr(topo.probe_vantage),
        repr(topo.inside_vantage),
        repr(next(iter(topo.targets.values()))),
    ):
        assert (
            SECRET not in shown and "192.0.2" not in shown and "lane3jump" not in shown
        )
    assert topo.structure() == {
        "schema": lt.SCHEMA,
        "targets": 1,
        "former_private_paths": 2,
        "probe_ports": 2,
    }


def test_probe_vantage_ref_cites_the_version_not_values():
    topo = lt.parse_topology_record(base())
    assert topo.probe_vantage_ref(3) == f"probe-{SECRET}@3"
    for bad in (0, -1, True, "3"):
        with pytest.raises(lt.TopologyRecordRefused):
            topo.probe_vantage_ref(bad)


def test_the_parser_reads_nothing_but_its_argument():
    source = (pathlib.Path(lt.__file__)).read_text()
    for forbidden in (
        "import socket",
        "import urllib",
        "import http",
        "open(",
        "os.environ",
        "subprocess",
    ):
        assert forbidden not in source
