"""dotmac-deploy-agent: the lease client and its double replay Control's
`contract/target_api_v2.json` (lease section: bodies, scenarios, renew grants).

The file is vendored byte-identical under `src/dotmac_deploy_agent/contract/` and
its sha256 pinned. The bytes are the ones #1143 vendored (Control be74634); this
pin and theirs move together when Control regenerates.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from dotmac_deploy_agent.lease import (
    _BODIES,
    EXPIRED_UNRELEASED,
    RUN_MARGIN,
    STANDING_VALUES,
    Lease,
    check_body,
)
from dotmac_deploy_agent.materials import MAX_LEASE_SECONDS
from dotmac_deploy_agent.refusal import Refusal

from tests.unit.deploy_agent_lease_double import (
    DeployLeaseDouble,
    LeaseClock,
    LeaseRefused,
    install,
    renew_grant,
)
from tests.unit.deploy_agent_support import make_rig

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-deploy-agent/src/dotmac_deploy_agent"
)
VENDORED = SOURCE / "contract/target_api_v2.json"
PIN = "4d847889d516d0613e0214c7b24c718ec9828c661be8bd0170b24a8e3da9be33"
CONTRACT = json.loads(VENDORED.read_text())
TYPES = {"str": str, "int": int, "bool": bool}


def test_the_vendored_v2_file_is_the_pinned_bytes() -> None:
    assert hashlib.sha256(VENDORED.read_bytes()).hexdigest() == PIN
    assert CONTRACT["contract"] == "dotmac.target_api/2"


def test_constants_equal_the_agents() -> None:
    constants = CONTRACT["constants"]
    assert tuple(constants["standing_values"]) == STANDING_VALUES
    assert EXPIRED_UNRELEASED == "expired_unreleased"
    assert constants["lease_margin_seconds"] == RUN_MARGIN
    assert constants["lease_max_limit"] == MAX_LEASE_SECONDS
    assert constants["endpoints_added"] == [["POST", "lease/renew", "agent"]]
    assert {
        route: {
            direction: {
                k: v if isinstance(v, str) else v.__name__ for k, v in shape.items()
            }
            for direction, shape in bodies.items()
        }
        for route, bodies in _BODIES.items()
    } == constants["lease_bodies"]
    assert set(constants["lease_errors"].values()) == {409}  # all signed 409s


def test_the_renew_route_is_carried_until_v1_is_replaced() -> None:
    from dotmac_deploy_agent import wire

    assert wire.PENDING_V2_ROUTES == {"lease/renew": "POST"}
    assert "lease/renew" not in wire.ROUTES  # ROUTES still equals the v1 file


@pytest.mark.parametrize("vector", CONTRACT["lease_body_vectors"]["ok"])
def test_ok_bodies_are_accepted(vector: dict[str, object]) -> None:
    check_body(vector["route"], vector["direction"], vector["body"])  # type: ignore[arg-type]


@pytest.mark.parametrize("vector", CONTRACT["lease_body_vectors"]["refused"])
def test_refused_bodies_carry_controls_code(vector: dict[str, object]) -> None:
    with pytest.raises(Refusal) as caught:
        check_body(vector["route"], vector["direction"], vector["body"])  # type: ignore[arg-type]
    assert caught.value.code == "response_malformed"
    assert caught.value.detail == vector["code"]


@pytest.mark.parametrize("vector", CONTRACT["renew_grant_vectors"])
def test_renew_grants_replay(vector: dict[str, object]) -> None:
    args = (vector["ttl_seconds"], vector["lease_max_seconds"], vector["held_seconds"])
    if "code" in vector:
        with pytest.raises(LeaseRefused) as caught:
            renew_grant(*args)  # type: ignore[arg-type]
        assert caught.value.code == vector["code"]
    else:
        assert renew_grant(*args) == vector["grant"]  # type: ignore[arg-type]


def _scenario_names() -> list[str]:
    return [s["name"] for s in CONTRACT["lease_scenarios"]]


@pytest.mark.parametrize("name", _scenario_names())
def test_lease_scenarios_replay_through_the_signed_wire(
    tmp_path: Path, name: str
) -> None:
    """Every step goes through the agent's real signed client against the double, so
    status, error code and body are compared at the HTTP level."""
    scenario = next(s for s in CONTRACT["lease_scenarios"] if s["name"] == name)
    rig = make_rig(tmp_path)
    clock = LeaseClock()
    double = DeployLeaseDouble(clock)
    double.padded_ids = True  # the scenarios name `lease-0001`, `lease-0002`
    install(rig, double, clock)
    client = rig.launcher.client
    base = clock.t
    for step in scenario["steps"]:
        clock.t = base + step["at"]
        args = dict(step["args"])
        if step["call"] == "acquire":
            double.max_seconds = args.pop("lease_max_seconds")
            double.marker = args.pop("marker_present")
            double.renewable = args.pop("renewable", True)
        route = f"lease/{step['call']}"
        method = "GET" if step["call"] == "standing" else "POST"
        payload = None if method == "GET" else args

        def call(method: str = method, route: str = route, payload=payload):  # type: ignore[no-untyped-def]
            return client.call(method, route, payload)

        if "error" in step:
            with pytest.raises(Refusal) as caught:
                call()
            assert (caught.value.code, caught.value.detail) == (
                "cp_refused",
                step["error"],
            )
        else:
            reply = call()
            body = {k: v for k, v in reply.document.items() if k != "store_now"}
            assert reply.status == step["status"] == 200
            assert body == step["body"]


def test_the_typed_client_accepts_what_the_double_serves(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    clock = LeaseClock()
    double = DeployLeaseDouble(clock, max_seconds=1500)
    install(rig, double, clock)
    client = rig.launcher.lease
    lease = client.acquire(dispatch_id="run-1", ttl_seconds=600, max_seconds=1500)
    assert lease == Lease("lease-1", 600, lease.deadline, lease.started, 1500, 600)
    assert client.standing() == "held"
    assert client.covers(lease, dispatch_id="run-1") is True
    clock.advance(400)
    assert client.renew(lease, dispatch_id="run-1").ttl_seconds == 600
    client.release(lease, host_mutated=False)
    client.release(lease, host_mutated=False)  # the replay
    assert client.standing() == "free"
    assert client.covers(lease, dispatch_id="run-1") is False


def test_an_unknown_standing_from_cp_is_refused(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.standing = "maybe"
    with pytest.raises(Refusal) as caught:
        rig.launcher.lease.standing()
    assert caught.value.detail == "body_value"
