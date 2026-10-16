"""Shared source-edge contracts, without a persistence or assembly import."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotmac_platform_health import (
    HealthObservationInput,
    HealthState,
    health_observation_document,
    health_observation_key,
    validate_health_observation,
)


def command() -> HealthObservationInput:
    at = datetime(2026, 10, 16, 10, tzinfo=timezone(timedelta(hours=1)))
    return HealthObservationInput(
        "source:one",
        "observation:one",
        "api",
        HealthState.HEALTHY,
        at,
        at,
        "ok",
        {"region": "west"},
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_ref", "s" * 201),
        ("observation_key", "k" * 201),
        ("component_code", "c" * 121),
        ("state", "healthy"),
        ("summary", None),
        ("observed_at", datetime(2026, 10, 16)),
        ("received_at", datetime(2026, 10, 16)),
        ("labels", {str(i): "v" for i in range(21)}),
        ("labels", {"k" * 81: "v"}),
        ("labels", {"k": "v" * 161}),
        ("labels", {1: "v"}),
        ("labels", {"k": 1}),
    ],
)
def test_validation_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        validate_health_observation(replace(command(), **{field: value}))


def test_bounds_are_inclusive_and_summary_has_no_invented_limit() -> None:
    validate_health_observation(
        replace(
            command(),
            source_ref="s" * 200,
            observation_key="k" * 200,
            component_code="c" * 120,
            summary="s" * 1000,
            labels={str(i).ljust(80, "k"): "v" * 160 for i in range(20)},
        )
    )
    with pytest.raises(ValueError, match="observed_at"):
        validate_health_observation(
            replace(
                command(),
                observed_at=command().received_at + timedelta(seconds=1),
            )
        )


def test_labels_are_snapshotted_and_document_is_an_independent_json_value() -> None:
    labels = {"region": "west"}
    original = replace(command(), labels=labels)
    labels["region"] = "east"
    assert original.labels == {"region": "west"}
    with pytest.raises(TypeError):
        original.labels["region"] = "north"  # type: ignore[index]
    document = health_observation_document(original)
    assert json.loads(json.dumps(document)) == {
        "source_ref": "source:one",
        "observation_key": "observation:one",
        "component_code": "api",
        "state": "healthy",
        "observed_at": "2026-10-16T10:00:00+01:00",
        "received_at": "2026-10-16T10:00:00+01:00",
        "summary": "ok",
        "labels": {"region": "west"},
    }
    document["labels"]["region"] = "north"
    assert original.labels["region"] == "west"


def test_document_validates_before_serializing() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        health_observation_document(replace(command(), received_at=datetime.now()))


def test_key_is_versioned_identity_only_and_unambiguously_framed() -> None:
    key = health_observation_key("source:one", "api", "heartbeat:42")
    assert key == (
        "health-observation:v1:"
        "afe6fcec87fc2438923753526b347d84b090f29a276e4b732de3cbca8cac26fd"
    )
    assert len(key) <= 200
    assert key == health_observation_key("source:one", "api", "heartbeat:42")
    assert (
        len(
            {
                key,
                health_observation_key("source:two", "api", "heartbeat:42"),
                health_observation_key("source:one", "worker", "heartbeat:42"),
                health_observation_key("source:one", "api", "heartbeat:43"),
            }
        )
        == 4
    )
    assert health_observation_key("a:b", "c", "d") != health_observation_key(
        "a",
        "b:c",
        "d",
    )
    for identity in ("", " "):
        with pytest.raises(ValueError):
            health_observation_key("source:one", "api", identity)


def test_real_package_contract_and_evidence_imports_deny_persistence() -> None:
    source = Path(__file__).resolve().parents[2] / "packages/dotmac-platform-health/src"
    script = r"""
import importlib.abc
import sys
sys.path.insert(0, sys.argv[1])
class DenyPersistence(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"sqlalchemy", "dotmac_kernel", "app"}:
            raise AssertionError("forbidden import: " + fullname)
        if fullname in {
            "dotmac_platform_health.models", "dotmac_platform_health.service",
            "dotmac_platform_health.manifest",
        }:
            raise AssertionError("eager persistence import: " + fullname)
sys.meta_path.insert(0, DenyPersistence())
import dotmac_platform_health as health
from dotmac_platform_health.contracts import HealthObservationInput
from dotmac_platform_health.evidence import canonical_health_evidence_bytes
assert health.HealthObservationInput is HealthObservationInput
assert health.canonical_health_evidence_bytes is canonical_health_evidence_bytes
assert callable(health.validate_health_observation)
assert callable(health.health_observation_document)
assert callable(health.health_observation_key)
assert "record_observation" in health.__all__
assert "module" in health.__all__
try:
    health.unpublished_name
except AttributeError:
    pass
else:
    raise AssertionError("unknown public name did not raise AttributeError")
"""
    subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", script, str(source)], check=True, timeout=30
    )


def test_validation_preserves_existing_blank_text_semantics() -> None:
    validate_health_observation(
        replace(
            command(),
            source_ref="",
            observation_key=" ",
            component_code="",
            summary="",
            labels={"": ""},
        )
    )
