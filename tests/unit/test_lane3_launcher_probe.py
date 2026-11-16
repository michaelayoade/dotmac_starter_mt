"""E7 / T2: the launcher measures items 11-16 while the target's window is open.

The measuring acts are injected, so no vantage is needed. What is pinned: the
six rows are exactly the closed set the target accepts, an unmeasured probe
raises instead of passing, a failing measurement is a failing row, and the
qualify-phase evidence the launcher later encrypts is never mutated.
"""

from __future__ import annotations

import copy
import pathlib
import sys
from typing import Any

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_launcher_probe as launcher_probe  # noqa: E402
import lane3_target_session as session  # noqa: E402

PROBE_OK = {
    "positive_v6": {
        "reachable": True,
        "positive_control_fired": True,
        "service_running": True,
    },
    "negative_v6": {
        "reachable": False,
        "positive_control_fired": True,
        "service_running": True,
    },
    "v4_pair": {
        "reachable": False,
        "positive_control_fired": True,
        "service_running": True,
    },
}
INSIDE_OK = {
    "private_port_v4": "reached",
    "private_port_v6": "reached",
    "control_unopened_port": "prohibited",
}
OBSERVED = {
    "observed_source_v4": "192.0.2.10",
    "observed_source_v6": "2001:db8::10",
}

INPUTS = launcher_probe.ProbeInputs(
    probe_host="probe.example.invalid",
    target_address="192.0.2.20",
    inside_vantage="inside.example.invalid",
    far_end="far.example.invalid",
    observer_user="observer",
    observer_key="/run/lane3/observer",
    inside_jump_key="/run/lane3/jump",
    timeout=30,
    product="demo",
    private_port=18443,
    accepted_source_set="192.0.2.0/24",
    target_v6="2001:db8::20",
)


def _measurers(
    *,
    probes: dict[str, Any] | None = None,
    behaviour: str | None = "reset",
    inside: dict[str, str] | None = None,
) -> launcher_probe.Measurers:
    phase: dict[str, Any] = {"probes": copy.deepcopy(probes or PROBE_OK)}
    if behaviour is not None:
        phase["closed_port_behaviour"] = behaviour
    return launcher_probe.Measurers(
        probe_phase=lambda _args: phase,
        inside_probe=lambda _inputs: dict(INSIDE_OK if inside is None else inside),
        far_end=lambda _args, _key: dict(OBSERVED),
    )


def _rows(**kwargs: Any) -> dict[str, dict[str, Any]]:
    rows = launcher_probe.measure_external_rows(
        INPUTS, {"vantage": {}}, measurers=_measurers(**kwargs)
    )
    return {row["code"]: row for row in rows}


def test_the_rows_are_exactly_the_closed_set_the_target_accepts() -> None:
    rows = launcher_probe.measure_external_rows(
        INPUTS, {"vantage": {}}, measurers=_measurers()
    )
    assert {row["code"] for row in rows} == session.EXTERNAL_CODES
    parsed = session.parse_external_rows({"rows": rows})
    assert len(parsed) == 6


def test_a_clean_measurement_passes_the_external_rows() -> None:
    rows = _rows()
    for code in (
        "external_positive_v6",
        "external_negative_v6",
        "external_v4",
        "closed_port_behaviour",
        "private_from_source",
    ):
        assert rows[code]["status"] == "executed_passed", code


def test_an_unreachable_positive_control_target_fails_its_row() -> None:
    probes = copy.deepcopy(PROBE_OK)
    probes["positive_v6"]["reachable"] = False
    assert _rows(probes=probes)["external_positive_v6"]["status"] == "executed_failed"


def test_an_externally_reachable_negative_fails_its_row() -> None:
    probes = copy.deepcopy(PROBE_OK)
    probes["v4_pair"]["reachable"] = True
    assert _rows(probes=probes)["external_v4"]["status"] == "executed_failed"


def test_a_missing_service_running_raises_instead_of_passing() -> None:
    probes = copy.deepcopy(PROBE_OK)
    del probes["negative_v6"]["service_running"]
    with pytest.raises(launcher_probe.controller_half.EvidenceIncomplete):
        _rows(probes=probes)


def test_an_unmeasured_probe_raises() -> None:
    probes = copy.deepcopy(PROBE_OK)
    del probes["v4_pair"]
    with pytest.raises(launcher_probe.controller_half.EvidenceIncomplete):
        _rows(probes=probes)


def test_an_unrecorded_closed_port_behaviour_fails_its_row() -> None:
    assert _rows(behaviour=None)["closed_port_behaviour"]["status"] == (
        "executed_failed"
    )


def test_a_jump_scope_control_that_did_not_fire_fails_item_sixteen() -> None:
    inside = dict(INSIDE_OK, control_unopened_port="reached")
    assert _rows(inside=inside)["private_from_source"]["status"] == "executed_failed"


def test_an_inside_probe_that_did_not_reach_fails_item_sixteen() -> None:
    inside = dict(INSIDE_OK, private_port_v6="silent")
    assert _rows(inside=inside)["private_from_source"]["status"] == "executed_failed"


def test_the_qualified_evidence_is_never_mutated() -> None:
    qualified: dict[str, Any] = {"vantage": {"address_v4": "192.0.2.1"}}
    before = copy.deepcopy(qualified)
    launcher_probe.measure_external_rows(INPUTS, qualified, measurers=_measurers())
    assert qualified == before


def test_rows_respect_the_target_side_bounds() -> None:
    row = launcher_probe._row(
        "external_v4", launcher_probe.PASSED, "d" * 9000, *(["e" * 900] * 30)
    )
    assert len(row["detail"]) == launcher_probe.MAX_DETAIL
    assert len(row["evidence"]) == launcher_probe.MAX_EVIDENCE_ITEMS
    assert all(
        len(item) == launcher_probe.MAX_EVIDENCE_TEXT for item in row["evidence"]
    )


def test_the_namespace_carries_only_what_the_collectors_read() -> None:
    names = set(vars(INPUTS.namespace()))
    assert names == {
        "probe_host",
        "target_address",
        "inside_vantage",
        "far_end",
        "observer_user",
        "observer_key",
        "timeout",
    }
