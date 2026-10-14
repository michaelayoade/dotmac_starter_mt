"""Execution reparses original owner input; planning is not narrowed."""

import dataclasses
import json
import tomllib

import pytest
from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.test_deployment_foundation_failure_injection import DESCRIPTOR, load


def test_parsed_snapshot_is_detached_and_private_cache_does_not_change_wire():
    original = tomllib.loads(DESCRIPTOR)
    spec = ProductDeploymentSpec.from_document(original, source="diagnostic-only")
    before = spec.to_canonical_document().canonical_bytes()
    original["roles"][0]["resources"]["cpus"] = "99"
    snapshot = spec.validated_execution_snapshot()
    assert snapshot is not spec
    assert snapshot.roles[0] is not spec.roles[0]
    assert snapshot.roles[0].resources is not spec.roles[0].resources
    assert snapshot.to_canonical_document().canonical_bytes() == before
    assert "_execution_parse_input" not in repr(spec)
    assert b"_execution_parse_input" not in before
    assert dataclasses.replace(spec).to_canonical_document().canonical_bytes() == before
    with pytest.raises(SpecError) as caught:
        dataclasses.replace(spec).validated_execution_snapshot()
    assert caught.value.code == "spec.execution_parse_input_missing"


@pytest.mark.parametrize(
    "value",
    [
        b"{",
        b"[]",
        b'{"a":1,"a":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1e999}',
        b"\xff",
        b"[" * 2000,
        bytearray(b"{}"),
        None,
    ],
)
def test_malformed_cache_is_a_structured_refusal(value):
    spec = load()
    object.__setattr__(spec, "_execution_parse_input", value)
    with pytest.raises(SpecError):
        spec.validated_execution_snapshot()


@pytest.mark.parametrize(
    "family",
    [
        "resources",
        "health",
        "scheduler",
        "environment",
        "replicas",
        "grace",
        "migration",
        "schema_hidden",
        "bool",
        "list",
    ],
)
def test_typed_mutation_cannot_bypass_the_one_owner_parser(family):
    spec = load()
    if family == "resources":
        object.__setattr__(spec.roles[0].resources, "pids", -1)
    elif family == "health":
        object.__setattr__(spec.roles[0].ready, "timeout_seconds", -1)
    elif family == "scheduler":
        object.__setattr__(spec.roles[-1], "scheduler_tick_max_age_seconds", -1)
    elif family == "environment":
        object.__setattr__(spec.roles[0], "environment", (("bad-key", "value"),))
    elif family == "replicas":
        object.__setattr__(spec.roles[0], "replicas", 999)
    elif family == "grace":
        object.__setattr__(spec.roles[0], "stop_grace_seconds", 0)
    elif family == "migration":
        object.__setattr__(spec.migration, "lock_retries", 0)
    elif family == "schema_hidden":
        object.__setattr__(spec, "material_probes", ("hidden",))
    elif family == "bool":
        object.__setattr__(spec.roles[0], "replicas", True)
    else:
        object.__setattr__(spec.roles[0], "command", list(spec.roles[0].command))
    with pytest.raises(SpecError) as caught:
        spec.validated_execution_snapshot()
    assert caught.value.code == "spec.execution_input_mismatch"


def test_forged_cache_rechecks_leaf_policy_instead_of_attesting_possession():
    spec = load()
    document = tomllib.loads(DESCRIPTOR)
    document["roles"][0]["resources"]["pids"] = -1
    object.__setattr__(spec, "_execution_parse_input", json.dumps(document).encode())
    with pytest.raises(SpecError):
        spec.validated_execution_snapshot()


def test_source_is_diagnostic_but_must_be_plain_string():
    spec = load()
    object.__setattr__(spec, "source", "/never/read/this/path")
    assert spec.validated_execution_snapshot().source == "/never/read/this/path"
    object.__setattr__(spec, "source", object())
    with pytest.raises(SpecError):
        spec.validated_execution_snapshot()


def test_each_published_descriptor_generation_keeps_identical_bytes():
    from tests.unit.test_deployment_foundation_compose_topology import DESCRIPTOR as v3
    from tests.unit.test_deployment_foundation_database_drift import (
        _descriptor_text_with_catalog,
    )
    from tests.unit.test_deployment_foundation_material_readiness import (
        descriptor as v4,
    )

    for text in (
        DESCRIPTOR,
        _descriptor_text_with_catalog("ProductDeploymentSpec.v2"),
        v3,
        v4(),
    ):
        spec = ProductDeploymentSpec.loads(text)
        fresh = spec.validated_execution_snapshot()
        assert (
            fresh.to_canonical_document().canonical_bytes()
            == spec.to_canonical_document().canonical_bytes()
        )
        assert (
            fresh.to_canonical_document().sha256_digest()
            == spec.to_canonical_document().sha256_digest()
        )
        assert (
            b"_execution_parse_input"
            not in fresh.to_canonical_document().canonical_bytes()
        )


def test_semantic_coverage_has_only_the_two_explicit_root_exclusions():
    from dotmac_deployment_foundation.spec import _execution_semantic_fields

    spec = load()
    assert {f.name for f in dataclasses.fields(spec)} - {
        f.name for f in _execution_semantic_fields(spec)
    } == {"source", "_execution_parse_input"}
    assert tuple(f.name for f in _execution_semantic_fields(spec.roles[0])) == tuple(
        f.name for f in dataclasses.fields(spec.roles[0])
    )
    manual = ProductDeploymentSpec(
        **{f.name: getattr(spec, f.name) for f in dataclasses.fields(spec) if f.init}
    )
    assert build_plan(manual).image == spec.image
    with pytest.raises(SpecError) as caught:
        manual.validated_execution_snapshot()
    assert caught.value.code == "spec.execution_parse_input_missing"


def test_owner_subclass_or_nested_subclass_is_not_a_parser_validated_value():
    from dotmac_deployment_foundation.spec import Resources

    class NotOwner(Resources):
        pass

    spec = load()
    resource = spec.roles[0].resources
    object.__setattr__(
        spec.roles[0],
        "resources",
        NotOwner(resource.cpus, resource.memory, resource.pids),
    )
    with pytest.raises(SpecError) as caught:
        spec.validated_execution_snapshot()
    assert caught.value.code == "spec.execution_input_mismatch"
