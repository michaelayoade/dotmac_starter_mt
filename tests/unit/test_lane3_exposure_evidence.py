"""The exposure-record consumer serializes privately and joins closed.

Recording is best-effort, so the consumer is where completeness is
decided: these tests pin the canonical round trip, every refusal that
keeps incomplete evidence from reading as proof, and the join against
the authorized reconciliations.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
from dotmac_deployment_foundation.execution_plan_v2 import (
    ExposureReconciliationV1,
)
from dotmac_deployment_foundation.exposure import (
    HostObservation,
    ObservedChain,
    ObservedProxy,
    ObservedRule,
    ObservedSocket,
)
from dotmac_deployment_foundation.ingress import FirewallRule

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_exposure_evidence as evidence  # noqa: E402
from lane3_exposure_recording import ExposureCallRecord  # noqa: E402


def _observation() -> HostObservation:
    return HostObservation(
        sockets=(
            ObservedSocket(
                family="ipv4", address="127.0.0.1", port=19001, process="docker-proxy"
            ),
        ),
        proxies=(
            ObservedProxy(
                family="ipv4",
                host_ip="127.0.0.1",
                host_port=19001,
                container_port=9001,
                protocol="tcp",
                pid=4242,
            ),
        ),
        chains=(
            ObservedChain(
                family="ipv4",
                name="DOCKER-USER",
                policy="-",
                rules=(
                    ObservedRule(
                        family="ipv4",
                        chain="DOCKER-USER",
                        arguments="-p tcp --ctorigdstport 19001 -j ACCEPT",
                    ),
                ),
            ),
        ),
        closed_port_behaviour="rst",
    )


def _rule(family: str = "ipv4", chain: str = "DOCKER-USER") -> FirewallRule:
    return FirewallRule(
        family=family,
        chain=chain,
        protocol="tcp",
        host_port=19001,
        action="ACCEPT",
        source_set="office",
        terminal=False,
    )


def _records() -> list[ExposureCallRecord]:
    observation = _observation()
    return [
        ExposureCallRecord(
            sequence=0, method="observe", observation=observation, duration_seconds=0.5
        ),
        ExposureCallRecord(
            sequence=1,
            method="apply_compose",
            command=("up", "-d"),
            timeout_seconds=30,
            duration_seconds=1.0,
        ),
        ExposureCallRecord(
            sequence=2,
            method="replace_rules",
            family="ipv4",
            chain="DOCKER-USER",
            rules=(_rule(),),
            duration_seconds=0.25,
        ),
        ExposureCallRecord(
            sequence=3,
            method="restore_chains",
            chains=observation.chains,
            exception_type="builtins.RuntimeError",
            duration_seconds=0.125,
        ),
    ]


def _reconciliation() -> ExposureReconciliationV1:
    return ExposureReconciliationV1(family="ipv4", chain="DOCKER-USER")


def test_serialize_parse_round_trips_exactly() -> None:
    records = _records()

    data = evidence.serialize_exposure_calls(records)

    assert evidence.parse_exposure_calls(data) == tuple(records)
    assert data.endswith(b"\n")
    assert json.loads(data)["schema"] == evidence.EXPOSURE_CALLS_SCHEMA


def test_digest_is_over_the_canonical_bytes() -> None:
    import hashlib

    records = _records()

    assert (
        evidence.exposure_calls_digest(records)
        == hashlib.sha256(evidence.serialize_exposure_calls(records)).hexdigest()
    )


def test_serialize_refuses_a_sequence_gap() -> None:
    records = _records()
    records[2] = ExposureCallRecord(sequence=7, method="observe")

    with pytest.raises(evidence.ExposureEvidenceRefused, match="not contiguous"):
        evidence.serialize_exposure_calls(records)


def test_parse_refuses_reformatted_bytes() -> None:
    data = evidence.serialize_exposure_calls(_records())
    pretty = (
        json.dumps(json.loads(data), indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )

    with pytest.raises(evidence.ExposureEvidenceRefused, match="canonical"):
        evidence.parse_exposure_calls(pretty)


def test_parse_refuses_unknown_keys_and_schema() -> None:
    document = json.loads(evidence.serialize_exposure_calls(_records()))
    document["extra"] = True
    with pytest.raises(evidence.ExposureEvidenceRefused, match="do not close"):
        evidence.parse_exposure_calls(json.dumps(document).encode("utf-8"))

    document = json.loads(evidence.serialize_exposure_calls(_records()))
    document["schema"] = "lane3.exposure-calls.v0"
    with pytest.raises(evidence.ExposureEvidenceRefused, match="schema"):
        evidence.parse_exposure_calls(json.dumps(document).encode("utf-8"))


def test_parse_refuses_non_json() -> None:
    with pytest.raises(evidence.ExposureEvidenceRefused, match="UTF-8 JSON"):
        evidence.parse_exposure_calls(b"\xff not json")


def test_join_accepts_a_complete_crossing_set() -> None:
    evidence.require_exposure_join(_records(), [_reconciliation()])


def test_join_refuses_no_records() -> None:
    with pytest.raises(evidence.ExposureEvidenceRefused, match="no successful"):
        evidence.require_exposure_join([], [_reconciliation()])


def test_join_refuses_mutation_before_observation() -> None:
    records = [
        ExposureCallRecord(
            sequence=0,
            method="replace_rules",
            family="ipv4",
            chain="DOCKER-USER",
            rules=(_rule(),),
        )
    ]

    with pytest.raises(evidence.ExposureEvidenceRefused, match="before any"):
        evidence.require_exposure_join(records, [_reconciliation()])


def test_join_refuses_a_failed_observation() -> None:
    records = _records()
    records[0] = ExposureCallRecord(
        sequence=0, method="observe", exception_type="builtins.OSError"
    )

    with pytest.raises(evidence.ExposureEvidenceRefused, match="failed"):
        evidence.require_exposure_join(records, [_reconciliation()])


def test_join_refuses_an_unjoined_reconciliation() -> None:
    other = ExposureReconciliationV1(family="ipv6", chain="INPUT")

    with pytest.raises(evidence.ExposureEvidenceRefused, match="ipv6/INPUT"):
        evidence.require_exposure_join(_records(), [_reconciliation(), other])


def test_join_refuses_when_the_only_crossing_failed() -> None:
    records = _records()
    records[2] = ExposureCallRecord(
        sequence=2,
        method="replace_rules",
        family="ipv4",
        chain="DOCKER-USER",
        rules=(_rule(),),
        exception_type="builtins.RuntimeError",
    )

    with pytest.raises(evidence.ExposureEvidenceRefused, match="no successful"):
        evidence.require_exposure_join(records, [_reconciliation()])


def test_join_refuses_rules_outside_the_named_family() -> None:
    records = _records()
    records[2] = ExposureCallRecord(
        sequence=2,
        method="replace_rules",
        family="ipv4",
        chain="DOCKER-USER",
        rules=(_rule(family="ipv6"),),
    )

    with pytest.raises(evidence.ExposureEvidenceRefused, match="carries a"):
        evidence.require_exposure_join(records, [_reconciliation()])
