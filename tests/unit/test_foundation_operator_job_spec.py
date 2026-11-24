"""T12 (spec parse refusals) and T13 (existing descriptors unchanged)."""

from __future__ import annotations

import json
import re
import tomllib

import pytest
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.render.compose import render_compose
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.foundation_operator_job_support import (
    JOB_TOML,
    make_document,
    make_spec,
)
from tests.unit.test_deployment_foundation_compose_topology import DESCRIPTOR
from tests.unit.test_deployment_foundation_live_instance import (
    document as v5_document,
)
from tests.unit.test_deployment_foundation_material_readiness import (
    descriptor as material_readiness_descriptor,
)

PLAIN_JOB = JOB_TOML.split("[compose_topology.jobs.operator]")[0]


def test_operator_table_parses_into_a_frozen_value() -> None:
    job = make_spec().compose_topology.jobs[-1]
    assert job.operator is not None
    assert job.operator.command_for("import_execute") == (
        "python",
        "-m",
        "importer",
        "execute",
    )
    assert job.operator.command_for("nope") is None
    assert job.operator.inputs_target == "/job/in"
    assert job.operator.outputs_target == "/job/out"


def _mutated(old: str, new: str) -> str:
    assert old in JOB_TOML, old
    return JOB_TOML.replace(old, new)


@pytest.mark.parametrize(
    "job_toml",
    [
        pytest.param(
            _mutated(
                'profiles = ["ops"]', 'profiles = ["ops"]\nrun_during_deploy = true'
            ),
            id="run_during_deploy",
        ),
        pytest.param(
            _mutated('image_source = "product"', 'image_source = "dependency:db"'),
            id="image_source",
        ),
        pytest.param(
            _mutated(
                'profiles = ["ops"]',
                'profiles = ["ops"]\nroot_justification = "x"\nroot_approved_by = "y"',
            ),
            id="root_justification",
        ),
        pytest.param(_mutated("entrypoint = []\n", ""), id="entrypoint_unset"),
        pytest.param(
            _mutated('import_execute = ["python", "-m", "importer", "execute"]', ""),
            id="commands_empty",
        ),
        pytest.param(
            _mutated("import_execute =", "Import-Execute ="), id="bad_command_name"
        ),
        pytest.param(
            _mutated(
                '["python", "-m", "importer", "execute"]', '["python", "-m", "x"]'
            ),
            id="command_not_prefixed",
        ),
        pytest.param(
            _mutated(
                'inputs_root_material = "JOB_INPUTS_ROOT"',
                'inputs_root_material = "JOB_INPUTS_ROOT"\ninputs_target = "/in"',
            ),
            id="inputs_target",
        ),
        pytest.param(
            _mutated(
                'inputs_root_material = "JOB_INPUTS_ROOT"',
                'inputs_root_material = "JOB_INPUTS_ROOT"\noutputs_target = "/out"',
            ),
            id="outputs_target",
        ),
        pytest.param(
            _mutated('"/run/secrets/dotmac"', '"run/secrets"'), id="secrets_relative"
        ),
        pytest.param(
            _mutated('"/run/secrets/dotmac"', '"/run/../etc"'), id="secrets_dotdot"
        ),
        pytest.param(_mutated('profiles = ["ops"]', "profiles = []"), id="profiles"),
        pytest.param(
            _mutated('networks = ["back"]', 'network_mode = "none"'), id="network_mode"
        ),
        pytest.param(
            _mutated("timeout_seconds = 2400", "timeout_seconds = 3601"), id="timeout"
        ),
        pytest.param(
            _mutated('inputs_root_material = "JOB_INPUTS_ROOT"', ""),
            id="inputs_root_missing",
        ),
    ],
)
def test_t12_each_parse_refusal(job_toml: str) -> None:
    with pytest.raises(SpecError):
        make_spec(job_toml)


def test_t12_an_unknown_operator_key_is_refused() -> None:
    with pytest.raises(SpecError):
        make_spec(JOB_TOML + "surprise = 1\n")


def test_t13_existing_descriptor_has_no_operator_and_is_unchanged() -> None:
    spec = ProductDeploymentSpec.loads(DESCRIPTOR)
    assert all(job.operator is None for job in spec.compose_topology.jobs)


def test_t13_a_job_without_the_table_defaults_off() -> None:
    spec = ProductDeploymentSpec.loads(DESCRIPTOR + PLAIN_JOB)
    assert spec.compose_topology.jobs[-1].operator is None


def _digest(doc: dict) -> str:
    return (
        ProductDeploymentSpec.from_document(doc).to_canonical_document().sha256_digest()
    )


V3, V4, V5 = (f"ProductDeploymentSpec.v{n}" for n in (3, 4, 5))
_COMPOSE_SCHEMAS = [V3, V4, V5]


def _doc(schema: str, job_toml: str) -> dict:
    """A valid descriptor document of `schema` with the given job tables appended."""
    if schema == V5:
        return make_document(job_toml)
    base = DESCRIPTOR if schema == V3 else material_readiness_descriptor()
    doc = tomllib.loads(base)
    assert doc["schema"] == schema
    doc["compose_topology"]["jobs"] = [
        *doc["compose_topology"]["jobs"],
        *tomllib.loads(job_toml)["compose_topology"]["jobs"],
    ]
    return doc


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def _canonical_keys(spec: ProductDeploymentSpec) -> set[str]:
    return _keys(json.loads(spec.to_canonical_document().canonical_bytes()))


@pytest.mark.parametrize("schema", _COMPOSE_SCHEMAS)
def test_t13_an_operatorless_descriptor_has_no_operator_key_in_its_bytes(
    schema: str,
) -> None:
    # compose jobs, no table: the canonical bytes are exactly what they were
    # before the field existed (the key is OMITTED, not UNSET)
    spec = ProductDeploymentSpec.from_document(_doc(schema, PLAIN_JOB))
    assert "operator" not in _canonical_keys(spec)
    assert b"operator" not in spec.to_canonical_document().canonical_bytes()


def test_t13_the_live_instance_fixture_has_no_operator_key() -> None:
    spec = ProductDeploymentSpec.from_document(v5_document())
    assert any(job for job in spec.compose_topology.jobs)
    assert "operator" not in _canonical_keys(spec)


@pytest.mark.parametrize("schema", _COMPOSE_SCHEMAS)
def test_t13_a_declared_table_is_in_the_bytes_and_in_the_digest(schema: str) -> None:
    plain = ProductDeploymentSpec.from_document(_doc(schema, PLAIN_JOB))
    declared = ProductDeploymentSpec.from_document(_doc(schema, JOB_TOML))
    assert "operator" in _canonical_keys(declared)
    assert (
        declared.to_canonical_document().sha256_digest()
        != plain.to_canonical_document().sha256_digest()
    )


@pytest.mark.parametrize("schema", _COMPOSE_SCHEMAS)
def test_t13_the_render_differs_only_by_the_configuration_digest_label(
    schema: str,
) -> None:
    plain = ProductDeploymentSpec.from_document(_doc(schema, PLAIN_JOB))
    declared = ProductDeploymentSpec.from_document(_doc(schema, JOB_TOML))
    strip = lambda text: _CONFIG_DIGEST_LINE.sub("", text)  # noqa: E731
    assert strip(render_compose(plain)) == strip(render_compose(declared))
    assert render_compose(plain) != render_compose(declared)


_CONFIG_DIGEST_LINE = re.compile(
    r"^.*io\.dotmac\.deployment\.configuration\.digest:.*$", re.M
)


def test_t13_a_null_operator_is_the_same_single_absent_state() -> None:
    plain = _doc(V5, PLAIN_JOB)
    nulled = _doc(V5, PLAIN_JOB)
    nulled["compose_topology"]["jobs"][-1]["operator"] = None
    assert _digest(nulled) == _digest(plain)
    assert "operator" not in _canonical_keys(
        ProductDeploymentSpec.from_document(nulled)
    )


_COMMANDS = (
    "[compose_topology.jobs.operator.commands]\n"
    'import_execute = ["python", "-m", "importer", "execute"]\n'
)


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param("[compose_topology.jobs.operator]\n", id="empty_table"),
        pytest.param(
            '[compose_topology.jobs.operator]\nsecrets_target = "/run/secrets/x"\n'
            'inputs_root_material = "JOB_INPUTS_ROOT"\n',
            id="no_commands",
        ),
        pytest.param(
            "[compose_topology.jobs.operator]\n"
            'inputs_root_material = "JOB_INPUTS_ROOT"\n' + _COMMANDS,
            id="no_secrets_target",
        ),
        pytest.param(
            '[compose_topology.jobs.operator]\nsecrets_target = "/run/secrets/x"\n'
            + _COMMANDS,
            id="no_inputs_root_material",
        ),
    ],
)
def test_t13_an_empty_or_partial_table_is_refused(tail: str) -> None:
    for schema in _COMPOSE_SCHEMAS:
        with pytest.raises(SpecError):
            ProductDeploymentSpec.from_document(_doc(schema, PLAIN_JOB + tail))


@pytest.mark.parametrize(
    "schema", ["ProductDeploymentSpec.v1", "ProductDeploymentSpec.v2"]
)
def test_t13_an_operator_table_is_refused_where_there_is_no_compose_topology(
    schema: str,
) -> None:
    for job_toml in (PLAIN_JOB, JOB_TOML):
        doc = _doc(V3, job_toml)
        doc["schema"] = schema
        with pytest.raises(SpecError):
            ProductDeploymentSpec.from_document(doc)


def _operator(doc: dict) -> dict:
    return doc["compose_topology"]["jobs"][-1]["operator"]


@pytest.mark.parametrize("schema", _COMPOSE_SCHEMAS)
@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda op: op["commands"].update(
                import_execute=["python", "-m", "importer", "execute", "--apply"]
            ),
            id="command_entry_changed",
        ),
        pytest.param(
            lambda op: op["commands"].update(
                import_dryrun=["python", "-m", "importer", "execute"]
            ),
            id="command_entry_added",
        ),
        pytest.param(
            lambda op: op.update(secrets_target="/run/secrets/other"),
            id="secrets_target",
        ),
        pytest.param(
            lambda op: op.update(inputs_root_material="OTHER_INPUTS_ROOT"),
            id="inputs_root_material",
        ),
    ],
)
def test_t13_every_operator_field_is_inside_the_digest(schema: str, mutate) -> None:
    base = _doc(schema, JOB_TOML)
    changed = _doc(schema, JOB_TOML)
    mutate(_operator(changed))
    assert _digest(changed) != _digest(base)
    assert _digest(_doc(schema, JOB_TOML)) == _digest(base)  # deterministic control
